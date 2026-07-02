import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..classifier import classify_jars_parallel, rerun_unknown_classifications
from ..download_support import DownloadStatsReporter, build_idle_download_status_text
from ..shared import *
from .common import ServerBuilderCommonService
from .context import ServerBuilderRuntime


_FAILURE_FINDING_TITLES = {
    "dependency": "缺少前置依赖",
    "conflict": "模组冲突或重复安装",
    "platform-version": "Minecraft / Forge / Fabric 版本不匹配",
    "client-only": "纯客户端模组混入服务端",
    "java": "Java 版本不对",
}

_FAILURE_FINDING_ORDER = [
    "dependency",
    "conflict",
    "platform-version",
    "client-only",
    "java",
]

_PLATFORM_NAMES = {
    "minecraft": "Minecraft",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "fabric": "Fabric",
    "fabricloader": "Fabric Loader",
    "fabric-loader": "Fabric Loader",
}


def _normalize_failure_text(text: str) -> str:
    return str(text or "").strip()


def _normalize_failure_mod_name(name: str) -> str:
    cleaned = _normalize_failure_text(name)
    return cleaned.strip("'\"")


def _humanize_expected_range(range_text: str) -> str:
    cleaned = _normalize_failure_text(range_text)
    if not cleaned:
        return ""
    match = re.fullmatch(r"\[([^,\]]+),\)", cleaned)
    if match:
        return f">= {match.group(1).strip()}"
    match = re.fullmatch(r"\(([^,\]]+),\)", cleaned)
    if match:
        return f"> {match.group(1).strip()}"
    match = re.fullmatch(r"\[([^,\]]+),([^\]]+)\]", cleaned)
    if match:
        return f"{match.group(1).strip()} 到 {match.group(2).strip()}"
    match = re.fullmatch(r"\[([^,\]]+),([^\]]+)\)", cleaned)
    if match:
        return f">= {match.group(1).strip()} 且 < {match.group(2).strip()}"
    match = re.fullmatch(r"\(([^,\]]+),([^\]]+)\]", cleaned)
    if match:
        return f"> {match.group(1).strip()} 且 <= {match.group(2).strip()}"
    if cleaned.startswith("[") and cleaned.endswith("]") and "," not in cleaned:
        return cleaned[1:-1].strip()
    return cleaned


def _class_file_major_to_java(major_text: str) -> Optional[int]:
    match = re.search(r"\d+", str(major_text))
    if not match:
        return None
    major = int(match.group(0))
    java_version = major - 44
    if 1 <= java_version <= 99:
        return java_version
    return None


def _add_failure_finding(groups: Dict[str, Dict[str, Any]], kind: str, detail: str) -> None:
    cleaned = _normalize_failure_text(detail)
    if not cleaned:
        return
    group = groups.setdefault(
        kind,
        {
            "kind": kind,
            "title": _FAILURE_FINDING_TITLES[kind],
            "details": [],
        },
    )
    if cleaned not in group["details"]:
        group["details"].append(cleaned)


def _build_failure_snippet_lines(lines: Sequence[str], interesting_indexes: Sequence[int]) -> List[str]:
    if not lines:
        return []
    if not interesting_indexes:
        return list(lines[-min(len(lines), 180):])

    # “关键报错片段”主要是给用户复制到 AI，所以这里优先保留更完整的连续上下文，
    # 而不是只截几段命中窗口，避免关键信息被省掉。
    first_anchor = interesting_indexes[max(0, len(interesting_indexes) - 4)]
    last_anchor = interesting_indexes[-1]
    start = max(0, first_anchor - 40)
    end = min(len(lines), last_anchor + 120)
    desired_length = min(len(lines), 160)
    current_length = end - start
    if current_length < desired_length:
        missing = desired_length - current_length
        start = max(0, start - missing)
        current_length = end - start
        if current_length < desired_length:
            end = min(len(lines), end + (desired_length - current_length))
    snippet_lines = list(lines[start:end])

    if len(snippet_lines) <= 200:
        return snippet_lines
    head = snippet_lines[:100]
    tail = snippet_lines[-99:]
    return head + ["..."] + tail


def _normalize_failure_lookup_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _iter_failure_lookup_tokens(text: str) -> List[str]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    generic_tokens = {
        "minecraft",
        "forge",
        "fabric",
        "neoforge",
        "quilt",
        "java",
        "client",
        "server",
        "mixin",
        "mod",
    }
    values = [raw_text]
    if "." in raw_text:
        values.append(Path(raw_text).stem)

    tokens: List[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_failure_lookup_key(value)
        if normalized and not normalized.isdigit() and normalized not in generic_tokens and normalized not in seen:
            tokens.append(normalized)
            seen.add(normalized)
        for part in re.split(r"[^a-z0-9]+", value.lower()):
            if len(part) < 3 or part.isdigit() or part in generic_tokens or part in seen:
                continue
            tokens.append(part)
            seen.add(part)
    return tokens


def _extract_client_only_clue_tokens(snippet_text: str) -> List[str]:
    patterns: List[Tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
        (
            re.compile(r"Mixin apply for mod\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?\s+failed", re.IGNORECASE),
            lambda match: match.group("mod"),
        ),
        (
            re.compile(r"provided by\s+'(?P<mod>[A-Za-z0-9_.\-]+)'", re.IGNORECASE),
            lambda match: match.group("mod"),
        ),
        (
            re.compile(r"from mod\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?", re.IGNORECASE),
            lambda match: match.group("mod"),
        ),
        (
            re.compile(r"Mixin config\s+(?P<config>[A-Za-z0-9_.\-]+)", re.IGNORECASE),
            lambda match: match.group("config").split(".mixins", 1)[0].split(".mixin", 1)[0],
        ),
        (
            re.compile(r"\[(?P<config>[A-Za-z0-9_.\-]+\.mixins?[^]]*)\]", re.IGNORECASE),
            lambda match: match.group("config").split(".mixins", 1)[0].split(".mixin", 1)[0],
        ),
    ]

    clue_tokens: List[str] = []
    seen: set[str] = set()
    for pattern, extractor in patterns:
        for match in pattern.finditer(snippet_text):
            token = _normalize_failure_text(extractor(match))
            for candidate in _iter_failure_lookup_tokens(token):
                if candidate not in seen:
                    clue_tokens.append(candidate)
                    seen.add(candidate)
    return clue_tokens


def _row_has_client_hint(row: Dict[str, Any]) -> bool:
    if str(row.get("Environment") or "").strip().lower() == "client":
        return True
    entrypoints = str(row.get("Entrypoints") or "").lower()
    if any(token in entrypoints for token in CLIENT_ENTRYPOINT_TOKEN_HINTS):
        return True
    reason = str(row.get("Reason") or "").lower()
    return "client" in reason or "客户端" in reason


def _row_lookup_tokens(row: Dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for value in (
        row.get("ModId"),
        row.get("ModName"),
        row.get("FileName"),
        row.get("Path"),
    ):
        for token in _iter_failure_lookup_tokens(str(value or "")):
            tokens.add(token)
    return tokens


def _match_selected_rows_by_clues(mod_results: Sequence[Dict[str, Any]], clue_tokens: Sequence[str]) -> List[Dict[str, Any]]:
    if not clue_tokens:
        return []

    matched_rows: List[Dict[str, Any]] = []
    seen_files: set[str] = set()
    clue_set = {token for token in clue_tokens if token}
    for row in mod_results:
        if not row.get("SelectedForServer"):
            continue
        row_tokens = _row_lookup_tokens(row)
        if not row_tokens.intersection(clue_set):
            continue
        file_name = str(row.get("FileName") or row.get("Path") or "")
        if not file_name or file_name in seen_files:
            continue
        matched_rows.append(row)
        seen_files.add(file_name)
    return matched_rows


def _format_failure_mod_label(row: Dict[str, Any]) -> str:
    file_name = str(row.get("FileName") or row.get("Path") or "未知模组")
    mod_name = _normalize_failure_text(str(row.get("ModName") or ""))
    if mod_name and mod_name.lower() != Path(file_name).stem.lower():
        return f"**{mod_name}**（{file_name}）"
    return f"**{file_name}**"


def _format_failure_mod_reason(row: Dict[str, Any]) -> str:
    reason = _normalize_failure_text(str(row.get("Reason") or ""))
    if not reason:
        return ""
    return f"线索：{reason}。"


def _append_client_only_suspects(
    findings_map: Dict[str, Dict[str, Any]],
    snippet_text: str,
    mod_results: Optional[Sequence[Dict[str, Any]]],
) -> None:
    client_only_pattern = re.compile(
        r"(NoClassDefFoundError|ClassNotFoundException|Attempted to load class).*?(net[/\.]minecraft[/\.]client)",
        re.IGNORECASE,
    )
    if not client_only_pattern.search(snippet_text):
        return

    _add_failure_finding(findings_map, "client-only", "日志里出现了 **net/minecraft/client/** 相关类。")
    if not mod_results:
        return

    clue_rows = _match_selected_rows_by_clues(mod_results, _extract_client_only_clue_tokens(snippet_text))
    selected_client_only_rows = [
        row
        for row in mod_results
        if row.get("SelectedForServer") and str(row.get("Category") or "") == "client-only"
    ]
    selected_unknown_rows = [
        row
        for row in mod_results
        if row.get("SelectedForServer") and str(row.get("Category") or "") == "unknown" and _row_has_client_hint(row)
    ]

    if clue_rows:
        labels = "、".join(_format_failure_mod_label(row) for row in clue_rows[:4])
        _add_failure_finding(findings_map, "client-only", f"疑似纯客户端模组：{labels}。")
        return

    remaining_client_only = [
        row for row in selected_client_only_rows if row not in clue_rows
    ]
    if remaining_client_only:
        labels = "、".join(_format_failure_mod_label(row) for row in remaining_client_only[:4])
        _add_failure_finding(findings_map, "client-only", f"疑似纯客户端模组：{labels}。")
        return

    remaining_unknown = [
        row for row in selected_unknown_rows if row not in clue_rows and row not in remaining_client_only
    ]
    if remaining_unknown:
        labels = "、".join(_format_failure_mod_label(row) for row in remaining_unknown[:4])
        _add_failure_finding(findings_map, "client-only", f"疑似纯客户端模组：{labels}。")


def collect_server_failure_context(
    install_log_lines: Sequence[str],
    mod_results: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """从启动/安装日志里提取更适合给用户看的失败摘要。"""
    lines = [str(line).rstrip() for line in install_log_lines if str(line).strip()]
    if not lines:
        return {
            "summary": "服务端制作大部分已完成，但没有读取到足够的启动报错信息。",
            "findings": [],
            "snippet": "",
        }

    keywords = (
        "exception",
        "error",
        "failed",
        "caused by",
        "mixin",
        "modresolutionexception",
        "missing dependency",
        "missing or unsupported mandatory dependencies",
        "requires",
        "conflict",
        "duplicate",
        "unsupported class file major version",
        "unsupportedclassversionerror",
        "noclassdeffounderror",
        "classnotfoundexception",
    )
    interesting_indexes: List[int] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            interesting_indexes.append(index)

    snippet_lines = _build_failure_snippet_lines(lines, interesting_indexes)
    snippet = "\n".join(snippet_lines)
    snippet_text = "\n".join(snippet_lines)

    findings_map: Dict[str, Dict[str, Any]] = {}

    dependency_pattern = re.compile(
        r"Mod ID:\s*'(?P<mod>[^']+)'\s*,\s*Requested by:\s*'(?P<requester>[^']+)'\s*,\s*Expected range:\s*'(?P<expected>[^']*)'"
        r"(?:\s*,\s*Actual version:\s*'(?P<actual>[^']*)')?",
        re.IGNORECASE,
    )
    for match in dependency_pattern.finditer(snippet_text):
        mod_name = _normalize_failure_mod_name(match.group("mod"))
        requester = _normalize_failure_mod_name(match.group("requester"))
        expected = _humanize_expected_range(match.group("expected"))
        actual = _normalize_failure_mod_name(match.group("actual"))
        actual_missing = actual.lower() in {"", "none", "null", "missing", "not found"}
        platform_name = _PLATFORM_NAMES.get(mod_name.lower())
        if platform_name:
            requester_prefix = f"**{requester}** 需要 " if requester and requester.lower() != "the game" else ""
            detail = f"{requester_prefix}**{platform_name} {expected or match.group('expected')}**。"
            _add_failure_finding(findings_map, "platform-version", detail)
            if actual_missing:
                _add_failure_finding(findings_map, "platform-version", f"当前没有检测到可用的 **{platform_name}** 版本信息。")
            elif actual:
                _add_failure_finding(findings_map, "platform-version", f"当前环境实际是 **{actual}**。")
            continue

        requirement = expected or _normalize_failure_text(match.group("expected"))
        requester_name = requester or "某个模组"
        _add_failure_finding(findings_map, "dependency", f"**{requester_name}** 需要 **{mod_name} {requirement}**。")
        if actual_missing:
            _add_failure_finding(findings_map, "dependency", f"当前没有检测到 **{mod_name}**，这个前置还没装上。")
        elif actual:
            _add_failure_finding(findings_map, "dependency", f"当前检测到的版本是 **{actual}**，前置版本不够。")

    fabric_dependency_pattern = re.compile(
        r"Mod\s+(?P<requester>[A-Za-z0-9_.\-]+)\s+requires\s+(?P<mod>[A-Za-z0-9_.\-]+)\s+(?P<expected>[^\s,;]+)"
        r"(?:\s+or later)?(?:.*?(?:but\s+(?:only\s+)?has|but\s+found|found)\s+(?P<actual>[^\s,;]+))?",
        re.IGNORECASE,
    )
    for match in fabric_dependency_pattern.finditer(snippet_text):
        requester = _normalize_failure_mod_name(match.group("requester"))
        mod_name = _normalize_failure_mod_name(match.group("mod"))
        expected = _normalize_failure_text(match.group("expected"))
        actual = _normalize_failure_text(match.group("actual"))
        if not requester or not mod_name or requester.lower() == mod_name.lower():
            continue
        platform_name = _PLATFORM_NAMES.get(mod_name.lower())
        if platform_name:
            _add_failure_finding(findings_map, "platform-version", f"**{requester}** 需要 **{platform_name} {expected}**。")
            if actual:
                _add_failure_finding(findings_map, "platform-version", f"当前环境实际是 **{actual}**。")
            continue
        _add_failure_finding(findings_map, "dependency", f"**{requester}** 需要 **{mod_name} {expected}**。")
        if actual:
            _add_failure_finding(findings_map, "dependency", f"当前检测到的版本是 **{actual}**，前置版本不够。")

    generic_dependency_patterns = (
        re.compile(r"could not find required mod:?\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?", re.IGNORECASE),
        re.compile(r"missing dependency:?\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?", re.IGNORECASE),
        re.compile(r"requires?\s+mod:?\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?\s+which\s+is\s+missing", re.IGNORECASE),
    )
    for pattern in generic_dependency_patterns:
        for match in pattern.finditer(snippet_text):
            mod_name = _normalize_failure_mod_name(match.group("mod"))
            if mod_name:
                _add_failure_finding(findings_map, "dependency", f"服务端里没有找到 **{mod_name}**。")

    forge_missing_dependency_pattern = re.compile(
        r"Missing mandatory dependencies:?\s*(?P<requester>[A-Za-z0-9_.\-]+)\s+requires\s+(?P<mod>[A-Za-z0-9_.\-]+)(?:\s+version\s+(?P<expected>[^\s,;]+))?",
        re.IGNORECASE,
    )
    for match in forge_missing_dependency_pattern.finditer(snippet_text):
        requester = _normalize_failure_mod_name(match.group("requester"))
        mod_name = _normalize_failure_mod_name(match.group("mod"))
        expected = _normalize_failure_text(match.group("expected"))
        if requester and mod_name:
            requirement = f"{mod_name} {expected}".strip()
            _add_failure_finding(findings_map, "dependency", f"**{requester}** 需要 **{requirement}**。")
            _add_failure_finding(findings_map, "dependency", f"当前没有检测到 **{mod_name}**，这个前置还没装上。")

    conflict_patterns = (
        re.compile(r"duplicate mod(?:s)?\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?", re.IGNORECASE),
        re.compile(r"found a duplicate mod\s+'?(?P<mod>[A-Za-z0-9_.\-]+)'?", re.IGNORECASE),
        re.compile(r"mod\s+'(?P<left>[^']+)'\s+conflicts?\s+with\s+mod\s+'(?P<right>[^']+)'", re.IGNORECASE),
        re.compile(r"Mod\s+(?P<left>[A-Za-z0-9_.\-]+)\s+is\s+incompatible\s+with\s+(?P<right>[A-Za-z0-9_.\-]+)", re.IGNORECASE),
        re.compile(r"Conflicting mods found[:\s]+(?P<left>[A-Za-z0-9_.\-]+)\s*(?:,|with)\s*(?P<right>[A-Za-z0-9_.\-]+)", re.IGNORECASE),
        re.compile(r"mod resolution encountered an incompatible mod set", re.IGNORECASE),
    )
    for pattern in conflict_patterns:
        for match in pattern.finditer(snippet_text):
            groups = match.groupdict()
            if groups.get("mod"):
                mod_name = _normalize_failure_mod_name(groups["mod"])
                _add_failure_finding(findings_map, "conflict", f"服务端里重复出现了 **{mod_name}**，同一个模组可能放了两个版本。")
            elif groups.get("left") and groups.get("right"):
                left = _normalize_failure_mod_name(groups.get("left", ""))
                right = _normalize_failure_mod_name(groups.get("right", ""))
                if left and right:
                    _add_failure_finding(findings_map, "conflict", f"**{left}** 和 **{right}** 不能同时加载。")
            else:
                _add_failure_finding(findings_map, "conflict", "日志里出现了 **incompatible mod set**，通常是模组集合里有互斥项。")
    if "conflict" in snippet_text.lower() and "conflict" not in findings_map:
        _add_failure_finding(findings_map, "conflict", "日志里明确提到了 **conflict**，通常是两个模组互斥，或者同一个模组重复放入。")

    fabric_breaks_pattern = re.compile(
        r"Mod\s+(?P<left>[A-Za-z0-9_.\-]+)\s+breaks\s+(?P<right>[A-Za-z0-9_.\-]+)\s+(?P<expected>[^\s,;]+)",
        re.IGNORECASE,
    )
    for match in fabric_breaks_pattern.finditer(snippet_text):
        left = _normalize_failure_mod_name(match.group("left"))
        right = _normalize_failure_mod_name(match.group("right"))
        expected = _normalize_failure_text(match.group("expected"))
        if left and right:
            _add_failure_finding(findings_map, "conflict", f"**{left}** 和 **{right}** 存在版本冲突（日志要求 {expected}）。")

    platform_requirement_pattern = re.compile(
        r"(?P<requester>[A-Za-z0-9_.\-]+)\s+requires\s+(?P<platform>minecraft|forge|neoforge|fabric(?:\s+loader)?)\s+(?P<expected>[^\s,;]+)"
        r"(?:.*?(?:current|actual|found|but\s+is)\s+(?P<actual>[^\s,;]+))?",
        re.IGNORECASE,
    )
    for match in platform_requirement_pattern.finditer(snippet_text):
        requester = _normalize_failure_mod_name(match.group("requester"))
        platform_key = match.group("platform").replace(" ", "").lower()
        platform_name = _PLATFORM_NAMES.get(platform_key, match.group("platform"))
        expected = _normalize_failure_text(match.group("expected"))
        actual = _normalize_failure_text(match.group("actual"))
        _add_failure_finding(findings_map, "platform-version", f"**{requester}** 需要 **{platform_name} {expected}**。")
        if actual:
            _add_failure_finding(findings_map, "platform-version", f"当前环境实际是 **{actual}**。")

    fabric_loader_mismatch_pattern = re.compile(
        r"requires\s+fabric\s+loader\s+(?P<expected>[^\s,;]+)(?:.*?(?:but\s+(?:only\s+)?has|found)\s+(?P<actual>[^\s,;]+))?",
        re.IGNORECASE,
    )
    for match in fabric_loader_mismatch_pattern.finditer(snippet_text):
        expected = _normalize_failure_text(match.group("expected"))
        actual = _normalize_failure_text(match.group("actual"))
        if expected:
            _add_failure_finding(findings_map, "platform-version", f"当前环境需要 **Fabric Loader {expected}**。")
        if actual:
            _add_failure_finding(findings_map, "platform-version", f"当前环境实际是 **{actual}**。")

    forge_loader_mismatch_pattern = re.compile(
        r"requires\s+(?P<platform>forge|neoforge)\s+(?P<expected>[^\s,;]+)(?:.*?(?:but\s+(?:only\s+)?has|found|is)\s+(?P<actual>[^\s,;]+))?",
        re.IGNORECASE,
    )
    for match in forge_loader_mismatch_pattern.finditer(snippet_text):
        platform_key = _normalize_failure_text(match.group("platform")).lower()
        platform_name = _PLATFORM_NAMES.get(platform_key, match.group("platform"))
        expected = _normalize_failure_text(match.group("expected"))
        actual = _normalize_failure_text(match.group("actual"))
        if expected:
            _add_failure_finding(findings_map, "platform-version", f"当前环境需要 **{platform_name} {expected}**。")
        if actual:
            _add_failure_finding(findings_map, "platform-version", f"当前环境实际是 **{actual}**。")

    _append_client_only_suspects(findings_map, snippet_text, mod_results)

    java_runtime_pattern = re.compile(
        r"class file version\s+(?P<required>\d+(?:\.\d+)?)\s*,\s*this version of the Java Runtime only recognizes class file versions up to\s+(?P<actual>\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    java_match = java_runtime_pattern.search(snippet_text)
    if java_match:
        required_java = _class_file_major_to_java(java_match.group("required"))
        actual_java = _class_file_major_to_java(java_match.group("actual"))
        if required_java and actual_java:
            _add_failure_finding(findings_map, "java", f"当前 Java 最多只支持 **Java {actual_java}**，但报错文件需要 **Java {required_java}**。")

    unsupported_major_pattern = re.compile(r"unsupported class file major version\s+(?P<major>\d+)", re.IGNORECASE)
    major_match = unsupported_major_pattern.search(snippet_text)
    if major_match:
        required_java = _class_file_major_to_java(major_match.group("major"))
        if required_java:
            _add_failure_finding(findings_map, "java", f"日志提到了 **class file major version {major_match.group('major')}**，通常至少需要 **Java {required_java}**。")
        else:
            _add_failure_finding(findings_map, "java", f"日志提到了 **class file major version {major_match.group('major')}**，当前 Java 版本大概率不对。")
    elif "unsupportedclassversionerror" in snippet_text.lower():
        _add_failure_finding(findings_map, "java", "日志明确报了 **UnsupportedClassVersionError**，当前 Java 版本和服务端要求对不上。")

    findings = [findings_map[kind] for kind in _FAILURE_FINDING_ORDER if kind in findings_map]
    return {
        "summary": "服务端制作大部分已完成，当前主要卡在最终启动验证。",
        "findings": findings,
        "snippet": snippet,
    }


class ServerVersionService:
    """识别客户端版本，并解析应该下载哪个官方安装器。"""

    def __init__(self, runtime: ServerBuilderRuntime, common: ServerBuilderCommonService):
        self.runtime = runtime
        self.common = common

    def normalize_client_root(self, client_dir: Path) -> Path:
        minecraft_dir = client_dir / ".minecraft"
        if minecraft_dir.exists() and minecraft_dir.is_dir():
            return minecraft_dir

        has_mods = (client_dir / "mods").is_dir()
        has_versions = (client_dir / "versions").is_dir()
        has_config = (client_dir / "config").is_dir()
        has_root_version_manifest = any((client_dir / f"{item.stem}.jar").exists() for item in client_dir.glob("*.json"))
        if has_mods and (has_versions or has_config or has_root_version_manifest):
            return client_dir

        raise RuntimeError("所选目录不是可识别的客户端实例目录。")

    def extract_minecraft_version_hint(self, text: str) -> str:
        if not text:
            return ""
        match = re.search(r"\b1\.\d+(?:\.\d+)?\b", text)
        return match.group(0) if match else ""

    def parse_library_coordinate(self, coordinate: str) -> Tuple[str, str, str, str]:
        parts = [part.strip() for part in coordinate.split(":")]
        if len(parts) < 3:
            return "", "", "", ""
        group = parts[0]
        artifact = parts[1]
        version = parts[2]
        classifier = ":".join(parts[3:]) if len(parts) > 3 else ""
        if "@" in version:
            version = version.split("@", 1)[0].strip()
        if classifier and "@" in classifier:
            classifier = classifier.split("@", 1)[0].strip()
        return group, artifact, version, classifier

    def split_forge_library_version(self, library_version: str) -> Tuple[str, str]:
        if "-" not in library_version:
            return "", ""
        minecraft_version, loader_version = library_version.split("-", 1)
        return minecraft_version.strip(), loader_version.strip()

    def parse_minecraft_version_from_neoforge(self, loader_version: str, fallback: str = "") -> str:
        fallback_version = self.extract_minecraft_version_hint(fallback)
        if fallback_version:
            return fallback_version
        match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", loader_version)
        if not match:
            return ""
        major = match.group(1)
        minor = match.group(2)
        if minor == "0":
            return f"1.{major}"
        return f"1.{major}.{minor}"

    def parse_version_candidate(self, json_path: Path, data: dict) -> Optional[VersionCandidate]:
        libraries = [str(item.get("name", "")) for item in data.get("libraries", []) if isinstance(item, dict)]
        inherits_from = str(data.get("inheritsFrom") or "").strip()
        version_id = str(data.get("id") or json_path.stem).strip()
        java_major = int((data.get("javaVersion") or {}).get("majorVersion") or 21)
        inherited_minecraft_version = self.extract_minecraft_version_hint(inherits_from)
        version_hint = self.extract_minecraft_version_hint(version_id)

        for name in libraries:
            group, artifact, library_version, _classifier = self.parse_library_coordinate(name)

            if group == "net.fabricmc" and artifact == "fabric-loader":
                loader_version = library_version
                minecraft_version = inherited_minecraft_version
                if not minecraft_version:
                    for library_name in libraries:
                        lib_group, lib_artifact, lib_version, _ = self.parse_library_coordinate(library_name)
                        if lib_group == "net.fabricmc" and lib_artifact == "intermediary":
                            minecraft_version = lib_version
                            break
                if not minecraft_version:
                    minecraft_version = version_hint
                return VersionCandidate(version_id, minecraft_version, LoaderType.FABRIC.value, loader_version, java_major, json_path)

            if group == "org.quiltmc" and artifact == "quilt-loader":
                loader_version = library_version
                minecraft_version = inherited_minecraft_version or version_hint
                return VersionCandidate(version_id, minecraft_version, LoaderType.QUILT.value, loader_version, java_major, json_path)

            if group == "net.minecraftforge" and artifact in {"forge", "fmlloader"}:
                minecraft_version, loader_version = self.split_forge_library_version(library_version)
                if not minecraft_version or not loader_version:
                    continue
                return VersionCandidate(version_id, minecraft_version, LoaderType.FORGE.value, loader_version, java_major, json_path)

            if group == "net.neoforged" and artifact == "neoforge":
                loader_version = library_version
                minecraft_version = self.parse_minecraft_version_from_neoforge(loader_version, inherits_from or version_id)
                return VersionCandidate(version_id, minecraft_version, LoaderType.NEOFORGE.value, loader_version, java_major, json_path)
        return None

    def is_probable_version_manifest(self, json_path: Path, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        if not isinstance(data.get("libraries"), list):
            return False
        if not data.get("mainClass") and not data.get("downloads"):
            return False
        sibling_jar = json_path.with_suffix(".jar")
        if sibling_jar.exists():
            return True
        return any(
            str(item.get("name", "")).startswith(
                (
                    "net.fabricmc:fabric-loader:",
                    "org.quiltmc:quilt-loader:",
                    "net.minecraftforge:forge:",
                    "net.minecraftforge:fmlloader:",
                    "net.neoforged:neoforge:",
                    "net.neoforged:fancymodloader:",
                )
            )
            for item in data.get("libraries", [])
            if isinstance(item, dict)
        )

    def find_root_level_version_candidates(self, game_root: Path) -> List[VersionCandidate]:
        candidates: List[VersionCandidate] = []
        for json_path in sorted(game_root.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not self.is_probable_version_manifest(json_path, data):
                continue
            candidate = self.parse_version_candidate(json_path, data)
            if candidate:
                candidates.append(candidate)
        return candidates

    def find_version_candidates(self, game_root: Path) -> List[VersionCandidate]:
        prepared_candidates = list(self.runtime.prepared_version_candidates or [])
        if prepared_candidates:
            unique: Dict[Tuple[str, str, str, str], VersionCandidate] = {}
            for candidate in prepared_candidates:
                key = (candidate.version_id, candidate.minecraft_version, candidate.loader, candidate.loader_version)
                unique[key] = candidate
            final_candidates = sorted(unique.values(), key=self.common.version_candidate_sort_key)
            if final_candidates:
                self.common.log_line(f"已使用导入清单预解析出的 {len(final_candidates)} 个版本候选。")
                return final_candidates

        candidates = self.find_root_level_version_candidates(game_root)
        versions_dir = game_root / "versions"
        if versions_dir.is_dir():
            for json_path in versions_dir.rglob("*.json"):
                if json_path.name in {"usercache.json", "debug-profile.json", "log.json"}:
                    continue
                if json_path.stem != json_path.parent.name:
                    continue
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                candidate = self.parse_version_candidate(json_path, data)
                if candidate:
                    candidates.append(candidate)

        unique: Dict[Tuple[str, str, str, str], VersionCandidate] = {}
        for candidate in candidates:
            key = (candidate.version_id, candidate.minecraft_version, candidate.loader, candidate.loader_version)
            unique[key] = candidate

        final_candidates = sorted(unique.values(), key=self.common.version_candidate_sort_key)
        if not final_candidates:
            raise RuntimeError("未能从当前根目录或 versions 目录中的版本清单识别出 Fabric / Forge / NeoForge 版本。")
        return final_candidates

    def choose_version_candidate(self, candidates: List[VersionCandidate]) -> VersionCandidate:
        if len(candidates) == 1:
            return candidates[0]
        selected = self.runtime.request_version_choice(candidates)
        if not selected:
            raise RuntimeError("已取消版本选择。")
        return selected

    def resolve_fabric_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        versions = self.common.http_get_json("https://meta.fabricmc.net/v2/versions/installer")
        stable_versions = [item for item in versions if item.get("stable")]
        if not stable_versions:
            raise RuntimeError("Fabric 官方未返回可用的 installer 版本。")
        installer_version = str(stable_versions[0]["version"])
        return InstallerSpec(
            loader=LoaderType.FABRIC.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=installer_version,
            download_url=f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{installer_version}/fabric-installer-{installer_version}.jar",
            file_name=f"fabric-installer-{installer_version}.jar",
        )

    def _can_use_installer_with_probe(self, loader_name: str, installer_version: str, download_url: str) -> bool:
        if self.common.http_probe(download_url):
            self.common.log_line(
                f"{loader_name} 安装器元数据暂未命中 {installer_version}，"
                "但安装器地址可访问，已改为直接继续下载。"
            )
            return True
        return False

    def resolve_forge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        wanted = f"{candidate.minecraft_version}-{candidate.loader_version}"
        download_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{wanted}/forge-{wanted}-installer.jar"
        metadata_hit = False
        try:
            metadata = ET.fromstring(self.common.http_get_text("https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"))
            versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
            metadata_hit = wanted in versions
        except Exception as exc:
            self.common.log_line(f"Forge 安装器元数据读取失败，改为直接探测安装器地址：{exc}")
        if not metadata_hit and not self._can_use_installer_with_probe("Forge", wanted, download_url):
            raise RuntimeError(f"Forge 安装器解析失败：未在当前元数据中找到 {wanted}，且安装器地址也无法访问。")
        return InstallerSpec(
            loader=LoaderType.FORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=wanted,
            download_url=download_url,
            file_name=f"forge-{wanted}-installer.jar",
        )

    def resolve_neoforge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        download_url = (
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
            f"{candidate.loader_version}/neoforge-{candidate.loader_version}-installer.jar"
        )
        metadata_hit = False
        try:
            metadata = ET.fromstring(self.common.http_get_text("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"))
            versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
            metadata_hit = candidate.loader_version in versions
        except Exception as exc:
            self.common.log_line(f"NeoForge 安装器元数据读取失败，改为直接探测安装器地址：{exc}")
        if not metadata_hit and not self._can_use_installer_with_probe("NeoForge", candidate.loader_version, download_url):
            raise RuntimeError(
                f"NeoForge 安装器解析失败：未在当前元数据中找到 {candidate.loader_version}，且安装器地址也无法访问。"
            )
        return InstallerSpec(
            loader=LoaderType.NEOFORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=candidate.loader_version,
            download_url=download_url,
            file_name=f"neoforge-{candidate.loader_version}-installer.jar",
        )

    def resolve_installer_spec(self, candidate: VersionCandidate) -> InstallerSpec:
        if candidate.loader == LoaderType.FABRIC.value:
            return self.resolve_fabric_installer(candidate)
        if candidate.loader == LoaderType.FORGE.value:
            return self.resolve_forge_installer(candidate)
        if candidate.loader == LoaderType.NEOFORGE.value:
            return self.resolve_neoforge_installer(candidate)
        if candidate.loader == LoaderType.QUILT.value:
            raise RuntimeError("3.00 的一键制作服务端模式暂不支持 Quilt。")
        raise RuntimeError(f"暂不支持自动制作 {candidate.loader} 服务端。")


class ServerJavaService:
    """负责找 Java、验 Java、决定需要哪个 Java 版本。"""

    def __init__(self, common: ServerBuilderCommonService):
        self.common = common

    def _build_adoptium_assets_url(self, required_major: int) -> str:
        return (
            f"https://api.adoptium.net/v3/assets/latest/{required_major}/hotspot"
            f"?architecture=x64&image_type=jdk&os=windows"
        )

    def _resolve_download_target_root(self, output_root: Path, required_major: int) -> Path:
        return output_root / "runtime" / f"temurin-jdk-{required_major}"

    def _find_java_home_from_directory(self, base_dir: Path) -> Optional[Path]:
        direct_java = base_dir / "bin" / "java.exe"
        if direct_java.exists():
            return base_dir
        for java_path in sorted(base_dir.rglob("java.exe"), key=lambda item: str(item).lower()):
            if java_path.parent.name.lower() == "bin":
                return java_path.parent.parent
        return None

    def _resolve_adoptium_package(self, required_major: int) -> Dict[str, str]:
        assets = self.common.http_get_json(self._build_adoptium_assets_url(required_major))
        if not isinstance(assets, list) or not assets:
            raise RuntimeError(f"官方源未返回可用的 Java {required_major} 下载信息。")

        first_item = assets[0] if isinstance(assets[0], dict) else {}
        binary = first_item.get("binary") or {}
        package = binary.get("package") or {}
        package_link = str(package.get("link") or "").strip()
        package_name = str(package.get("name") or "").strip()
        release_name = str(first_item.get("release_name") or f"jdk-{required_major}").strip()
        if not package_link or not package_name:
            raise RuntimeError(f"官方源返回的 Java {required_major} 下载信息不完整。")
        return {
            "link": package_link,
            "name": package_name,
            "release_name": release_name,
        }

    def _download_java_runtime(self, required_major: int, output_root: Path) -> JavaRuntime:
        runtime_root = self._resolve_download_target_root(output_root, required_major)
        existing_runtime = self.inspect_java_runtime(runtime_root / "bin" / "java.exe", f"自动下载/Temurin JDK {required_major}")
        if existing_runtime and existing_runtime.major == required_major:
            self.common.log_line(f"复用已下载的 Java：{existing_runtime.summary}")
            return existing_runtime

        package = self._resolve_adoptium_package(required_major)
        tool_root = output_root / TOOL_DIR_NAME / "java_downloads"
        archive_path = tool_root / package["name"]
        extract_root = tool_root / f"extract_jdk_{required_major}"
        runtime_root.parent.mkdir(parents=True, exist_ok=True)
        tool_root.mkdir(parents=True, exist_ok=True)

        self.common.log_line(f"未找到匹配 Java，准备自动下载 Java {required_major}：{package['release_name']}")
        reporter = DownloadStatsReporter(self.common.runtime.set_download_status, total_files=1, thread_limit=1)
        try:
            self.common.http_download(
                package["link"],
                archive_path,
                reporter=reporter,
                display_name=package["name"],
                log_callback=self.common.log_line,
            )
        finally:
            reporter.close()

        self.common.runtime.set_download_status(f"Java 下载完成，正在解压：{package['name']}")
        self.common.log_line(f"Java 下载完成，开始解压：{archive_path.name}")
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                archive.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"自动下载的 Java 压缩包损坏：{archive_path.name}") from exc
        finally:
            try:
                archive_path.unlink()
            except OSError:
                pass

        java_home = self._find_java_home_from_directory(extract_root)
        if java_home is None:
            raise RuntimeError("Java 压缩包已经下载完成，但解压后没找到 bin/java.exe。")

        if runtime_root.exists():
            shutil.rmtree(runtime_root, ignore_errors=True)
        shutil.move(str(java_home), str(runtime_root))
        shutil.rmtree(extract_root, ignore_errors=True)

        runtime = self.inspect_java_runtime(runtime_root / "bin" / "java.exe", f"自动下载/Temurin JDK {required_major}")
        if runtime is None or runtime.major != required_major:
            raise RuntimeError(f"自动下载的 Java 校验失败，未得到可用的 Java {required_major}。")
        self.common.log_line(f"自动下载 Java 完成：{runtime.summary}")
        self.common.runtime.set_download_status(build_idle_download_status_text())
        return runtime

    def get_required_java_major(self, candidate: VersionCandidate) -> int:
        release = self.common.parse_release_version(candidate.minecraft_version)
        if release:
            if release >= (26, 1, 0):
                return 25
            if release[0] == 1:
                if release >= (1, 20, 5):
                    return 21
                if release >= (1, 18, 0):
                    return 17
                if release >= (1, 17, 0):
                    return 16
                return 8
        return candidate.java_major if candidate.java_major >= 8 else 8

    def java_requires_64bit(self, required_major: int) -> bool:
        return required_major >= 21

    def collect_candidate_java_paths(self, client_dir: Path, game_root: Path) -> List[Tuple[Path, str]]:
        candidates: List[Tuple[Path, str]] = []
        seen: set[str] = set()
        java_path_pattern = re.compile(r'([A-Za-z]:\\[^\r\n"<>|?*]*?java\.exe)', flags=re.I)

        def add_java_path(path: Path, source: str) -> None:
            try:
                resolved = path.resolve()
            except OSError:
                return
            normalized = str(resolved).lower()
            if normalized in seen or not resolved.exists() or not resolved.is_file():
                return
            seen.add(normalized)
            candidates.append((resolved, source))

        def add_java_root(root: Path, source: str) -> None:
            if not root.exists() or not root.is_dir():
                return
            add_java_path(root / "bin" / "java.exe", source)
            for pattern in ("*/bin/java.exe", "*/*/bin/java.exe", "*/*/*/bin/java.exe"):
                for java_path in root.glob(pattern):
                    add_java_path(java_path, source)

        def add_java_paths_from_records(base_dir: Path, source: str) -> None:
            if not base_dir.exists() or not base_dir.is_dir():
                return
            scan_dirs = [base_dir]
            for name in ("Cache", "Config", "Logs", "Log"):
                child = base_dir / name
                if child.exists() and child.is_dir():
                    scan_dirs.append(child)

            scanned_files = 0
            for folder in scan_dirs:
                for pattern in ("*.ini", "*.cfg", "*.json", "*.log", "*.txt"):
                    iterator = folder.rglob(pattern) if folder != base_dir else folder.glob(pattern)
                    for text_file in iterator:
                        scanned_files += 1
                        if scanned_files > 160:
                            return
                        try:
                            if text_file.stat().st_size > 2 * 1024 * 1024:
                                continue
                            content = text_file.read_text(encoding="utf-8", errors="ignore")
                        except OSError:
                            continue
                        for raw_path in java_path_pattern.findall(content):
                            add_java_path(Path(raw_path), f"{source}记录/{text_file.name}")

        def add_drive_hint_roots(*paths: Path) -> None:
            drive_roots: List[Path] = []
            for path in paths:
                anchor = path.anchor.strip() if path.anchor else ""
                if not anchor:
                    continue
                root = Path(anchor)
                if root not in drive_roots:
                    drive_roots.append(root)

            name_tokens = (
                "download",
                "java",
                "jdk",
                "jre",
                "jvm",
                "graal",
                "openjdk",
                "temurin",
                "adoptium",
                "adoptopenjdk",
                "corretto",
                "zulu",
                "bellsoft",
                "liberica",
                "semeru",
            )
            exact_names = {
                "downloads",
                "download",
                "edgedownload",
                "java",
                "jdk",
                "jre",
                "runtime",
                "tools",
                "software",
                "softwares",
                "downloads2",
                "下载",
            }

            for drive_root in drive_roots:
                try:
                    children = list(drive_root.iterdir())
                except OSError:
                    continue
                for child in children:
                    if not child.is_dir():
                        continue
                    lowered = child.name.lower()
                    if lowered in exact_names or any(token in lowered for token in name_tokens):
                        add_java_root(child, f"盘符常见目录/{child.name}")

        app_dir = self.common.get_application_dir()
        for name in ("runtime", "jdk", "jre"):
            add_java_root(app_dir / name, f"程序目录/{name}")

        minecraft_home = Path(os.environ.get("APPDATA", "")) / ".minecraft"
        add_java_root(minecraft_home / "runtime", "默认 .minecraft/runtime")

        pcl_temp_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "PCL"
        add_java_root(pcl_temp_root, "PCL 临时目录")
        add_java_paths_from_records(Path(os.environ.get("APPDATA", "")) / "PCL", "PCL 配置")
        add_java_paths_from_records(pcl_temp_root, "PCL 临时目录")

        related_bases: List[Path] = []
        for base in (client_dir.resolve(), game_root.resolve()):
            if base not in related_bases:
                related_bases.append(base)
            for parent in list(base.parents)[:3]:
                if parent not in related_bases:
                    related_bases.append(parent)
        for base in related_bases:
            for name in ("runtime", "jdk", "jre"):
                add_java_root(base / name, f"实例相关目录/{name}")

        for base in related_bases:
            add_java_root(base / "PCL", "实例相关目录/PCL")
            add_java_paths_from_records(base / "PCL", "实例相关目录/PCL")

        java_home = os.environ.get("JAVA_HOME", "").strip()
        if java_home:
            add_java_path(Path(java_home) / "bin" / "java.exe", "JAVA_HOME")

        which_java = shutil.which("java.exe") or shutil.which("java")
        if which_java:
            add_java_path(Path(which_java), "PATH")

        env_roots = [
            Path(os.environ.get("ProgramFiles", "")) / "Java",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Java",
            Path(os.environ.get("ProgramFiles", "")) / "Eclipse Adoptium",
            Path(os.environ.get("ProgramFiles", "")) / "AdoptOpenJDK",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft",
            Path(os.environ.get("ProgramFiles", "")) / "BellSoft",
            Path(os.environ.get("ProgramFiles", "")) / "Zulu",
            Path(os.environ.get("ProgramFiles", "")) / "Amazon Corretto",
            Path(os.environ.get("ProgramFiles", "")) / "Semeru",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        ]
        for root in env_roots:
            add_java_root(root, f"常见安装目录/{root.name}")

        user_home = Path.home()
        for root in (
            user_home / "Desktop",
            user_home / "Downloads",
            user_home / "Documents",
            user_home / "AppData" / "Roaming" / ".minecraft" / "PCL",
        ):
            add_java_root(root, f"用户常见目录/{root.name}")
            add_java_paths_from_records(root, f"用户常见目录/{root.name}")

        add_drive_hint_roots(app_dir.resolve(), client_dir.resolve(), game_root.resolve(), user_home)
        return candidates

    def inspect_java_runtime(self, java_path: Path, source: str) -> Optional[JavaRuntime]:
        try:
            proc = subprocess.run(
                [str(java_path), "-XshowSettings:properties", "-version"],
                capture_output=True,
                text=True,
                encoding=SYSTEM_ENCODING,
                errors="replace",
                timeout=15,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        except (FileNotFoundError, OSError):
            return None

        output_parts = [part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()]
        version_text = "\n".join(output_parts).strip()
        match = re.search(r'version "(\d+)(?:\.(\d+))?', version_text)
        if not match:
            return None
        lines = [line.strip() for line in version_text.splitlines() if line.strip()]
        first_line = next((line for line in lines if 'version "' in line.lower()), lines[0] if lines else "")
        is_64bit = bool(re.search(r"(?im)(64-Bit|sun\.arch\.data\.model\s*=\s*64|os\.arch\s*=\s*(amd64|x86_64))", version_text))
        return JavaRuntime(
            path=java_path.resolve(),
            major=int(match.group(1)),
            source=source,
            version_text=first_line or version_text,
            is_64bit=is_64bit,
        )

    def format_java_runtime_list(self, runtimes: List[JavaRuntime]) -> str:
        if not runtimes:
            return "未检测到任何可用 Java。"
        lines = [f"- Java {item.major} | {item.source} | {item.path}" for item in runtimes[:8]]
        if len(runtimes) > 8:
            lines.append(f"- 其余 {len(runtimes) - 8} 个结果已省略")
        return "\n".join(lines)

    def ensure_java(self, client_dir: Path, game_root: Path, candidate: VersionCandidate, output_root: Path) -> JavaRuntime:
        required_major = self.get_required_java_major(candidate)
        require_64bit = self.java_requires_64bit(required_major)

        runtimes: List[JavaRuntime] = []
        for java_path, source in self.collect_candidate_java_paths(client_dir, game_root):
            runtime = self.inspect_java_runtime(java_path, source)
            if runtime:
                runtimes.append(runtime)

        matched = [item for item in runtimes if item.major == required_major and (item.is_64bit or not require_64bit)]
        if matched:
            return matched[0]

        if self.common.runtime.auto_download_java:
            self.common.log_line(f"当前未找到可直接使用的 Java {required_major}，将尝试自动下载到输出目录。")
            downloaded_runtime = self._download_java_runtime(required_major, output_root)
            if downloaded_runtime.is_64bit or not require_64bit:
                return downloaded_runtime
            raise RuntimeError(
                f"自动下载完成，但下载到的 Java {required_major} 仍然不满足 64 位要求。\n"
                f"{downloaded_runtime.summary}"
            )

        if require_64bit and any(item.major == required_major for item in runtimes):
            raise RuntimeError(
                f"Minecraft {candidate.minecraft_version} 需要 64 位 Java {required_major}，"
                f"但当前扫描到的同版本 Java 不满足 64 位要求。\n{self.format_java_runtime_list(runtimes)}"
            )

        raise RuntimeError(
            f"Minecraft {candidate.minecraft_version} 需要 Java {required_major}，"
            f"但当前未找到完全匹配的 java.exe。\n{self.format_java_runtime_list(runtimes)}"
        )


class ServerModService:
    """负责整理模组复核项，以及复制模组和配置目录。"""

    def build_mod_review_items(self, mod_results: List[Dict[str, Any]]) -> List[ReviewItem]:
        items: List[ReviewItem] = []
        grouped_rows: Dict[str, List[Dict[str, Any]]] = {}
        for row in mod_results:
            grouped_rows.setdefault(row["Category"], []).append(row)

        for category in sorted(grouped_rows, key=lambda item: (CATEGORY_SORT_ORDER.get(item, 99), item)):
            group = sorted(grouped_rows[category], key=lambda item: (0 if item.get("JarStatus") == "damaged" else 1, item["FileName"].lower()))
            items.append(
                ReviewItem(
                    key=f"__header__{category}",
                    label=f"==== {get_category_label(category)}（{len(group)}） ====",
                    detail="待人工确认项默认排在最前；左键点击文件名可直接复制；纯客户端默认不勾选，但仍会展示给你复核。",
                    checked=False,
                    enabled=False,
                )
            )
            for row in group:
                detail_parts = [f"来源：{row['DecisionSource']}", f"原因：{row['Reason']}"]
                if row.get("JarStatus") == "damaged":
                    detail_parts.insert(0, f"Jar：损坏（{row.get('JarIssue') or '读取异常'}）")
                if row.get("ModName") and row["ModName"] != row["FileName"]:
                    detail_parts.insert(0, f"名称：{row['ModName']}")
                if row.get("EvidenceUrl"):
                    detail_parts.append(f"链接：{row['EvidenceUrl']}")
                items.append(
                    ReviewItem(
                        key=row["FileName"],
                        label=row["FileName"],
                        detail=" | ".join(detail_parts),
                        checked=category != "client-only",
                    )
                )
        return items

    def copy_selected_mods(self, mod_results: List[Dict[str, Any]], selected_keys: List[str], mods_target_dir: Path) -> int:
        mods_target_dir.mkdir(parents=True, exist_ok=True)
        selected_set = set(selected_keys)
        copied = 0
        for row in mod_results:
            source_path = row["Path"]
            is_selected = row["FileName"] in selected_set
            row["SelectedForServer"] = is_selected
            if is_selected:
                shutil.copy2(source_path, mods_target_dir / source_path.name)
                copied += 1
        return copied

    def enumerate_copyable_directories(self, game_root: Path) -> List[ReviewItem]:
        items: List[ReviewItem] = []
        for child in sorted(game_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            lowered = child.name.lower()
            if lowered.startswith(".") or lowered in DEFAULT_SKIP_DIRS or "xaero" in lowered:
                continue
            items.append(ReviewItem(key=child.name, label=child.name, detail=str(child), checked=True))
        return items

    def copy_selected_directories(self, game_root: Path, output_root: Path, selected_keys: List[str]) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        selected_set = set(selected_keys)
        for child in sorted(game_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            if child.name in selected_set:
                shutil.copytree(child, output_root / child.name, dirs_exist_ok=True)
                summary.append({"Directory": child.name, "Copied": True})
            elif child.name.lower() not in DEFAULT_SKIP_DIRS and "xaero" not in child.name.lower() and not child.name.startswith(".") and child.name != "mods":
                summary.append({"Directory": child.name, "Copied": False})
        return summary


class ServerInstallService:
    """负责执行安装器，以及在开服流程中调用模组筛选。"""

    def __init__(self, runtime: ServerBuilderRuntime, common: ServerBuilderCommonService):
        self.runtime = runtime
        self.common = common

    def run_process_capture(
        self,
        args: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
        install_log_only: bool = False,
        line_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[int, List[str]]:
        lines: List[str] = []
        display = " ".join(str(item) for item in args)
        self.common.log_line(f"执行命令：{display}")
        process = subprocess.Popen(
            list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            encoding=SYSTEM_ENCODING,
            errors="replace",
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )

        stream_queue: "queue.Queue[Optional[str]]" = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stream_queue.put(line.rstrip("\r\n"))
            stream_queue.put(None)

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.time() + timeout_seconds
        stream_closed = False
        while True:
            try:
                item = stream_queue.get(timeout=0.2)
            except queue.Empty:
                if time.time() > deadline:
                    break
                item = ""
            if item == "":
                pass
            elif item is None:
                if not stream_closed:
                    stream_closed = True
            else:
                lines.append(item)
                self.runtime.install_log_lines.append(item)
                if line_callback is not None:
                    try:
                        line_callback(item)
                    except Exception:
                        pass
                if not install_log_only:
                    self.common.log_line(item)
            if process.poll() is not None and stream_closed and stream_queue.empty():
                break
            if time.time() > deadline:
                break

        if process.poll() is None:
            process.kill()
            raise RuntimeError(f"命令执行超时：{display}")
        return process.returncode, lines

    def download_installer(self, spec: InstallerSpec, temp_dir: Path) -> Path:
        destination = temp_dir / spec.file_name
        self.common.log_line(f"下载官方安装器：{spec.download_url}")
        reporter = DownloadStatsReporter(self.runtime.set_download_status, total_files=1, thread_limit=1)
        try:
            self.common.http_download(
                spec.download_url,
                destination,
                reporter=reporter,
                log_callback=self.common.log_line,
                display_name=spec.file_name,
            )
        finally:
            reporter.close()
        return destination

    def install_server(self, output_root: Path, candidate: VersionCandidate, installer_path: Path, java_runtime: JavaRuntime) -> None:
        if candidate.loader == LoaderType.FABRIC.value:
            args = [
                str(java_runtime.path),
                "-jar",
                str(installer_path),
                "server",
                "-dir",
                str(output_root),
                "-mcversion",
                candidate.minecraft_version,
                "-loader",
                candidate.loader_version,
                "-downloadMinecraft",
            ]
        else:
            args = [str(java_runtime.path), "-jar", str(installer_path), "--installServer", str(output_root)]
        install_step = 0

        def update_install_download_status(line: str) -> None:
            nonlocal install_step
            text = line.strip()
            if not text:
                return
            lowered = text.lower()
            if "下载" not in text and "download" not in lowered:
                return
            install_step += 1
            self.runtime.set_download_status(f"安装器内部下载 [{install_step}]：{text}")

        self.runtime.set_download_status("安装器已启动，正在准备下载服务端环境…")
        try:
            code, _ = self.run_process_capture(
                args,
                output_root,
                DEFAULT_INSTALL_TIMEOUT_SECONDS,
                line_callback=update_install_download_status,
            )
        finally:
            self.runtime.set_download_status(build_idle_download_status_text())
        if code != 0:
            raise RuntimeError("服务端安装器执行失败。")

    def classify_mod_directory(self, mods_dir: Path) -> List[Dict[str, Any]]:
        # 一键开服里的模组筛选，复用外面的筛选主链，而不是另写一套规则。
        if not mods_dir.is_dir():
            return []

        jar_files = sorted(mods_dir.glob("*.jar"), key=lambda item: item.name.lower())
        total = len(jar_files)
        self.common.log_line(f"开始分析客户端 mods：共 {total} 个 jar 模组。")
        worker_count = get_classification_worker_count(total)
        if total > 1:
            self.common.log_line(f"联网分类使用 {worker_count} 个并发线程。")
        if getattr(self.runtime.classifier, "use_offline_database", False):
            if self.runtime.classifier.offline_database.is_available():
                self.common.log_line(f"已启用本地离线库优先查询：{self.runtime.classifier.offline_database.db_path}")
            else:
                self.common.log_line("已启用本地离线库优先查询，但程序目录旁未找到 db.sqlite，本次自动回退到联网查询。")

        first_span = 6 if self.runtime.enable_second_pass else 9

        def first_pass_progress(completed: int, inner_total: int, jar: Path) -> None:
            percent = completed / max(inner_total, 1)
            self.runtime.set_progress(52 + percent * first_span)
            self.runtime.set_status(f"{TaskStage.CLASSIFY_MODS.value}：正在汇总 [{completed}/{inner_total}] {jar.name}")

        def first_pass_result(completed: int, inner_total: int, jar: Path, row: Dict[str, Any]) -> None:
            self.common.log_line(f"[模组 {completed}/{inner_total}] {jar.name} -> {get_category_label(row['Category'])} | {row['Reason']}")

        results = classify_jars_parallel(
            self.runtime.classifier,
            jar_files,
            self.runtime.use_mcmod,
            getattr(self.runtime.classifier, "use_curseforge", False),
            getattr(self.runtime.classifier, "use_offline_database", False),
            self.runtime.download_source,
            progress_callback=first_pass_progress,
            result_callback=first_pass_result,
        )

        unknown_rows = [row for row in results if row["Category"] == "unknown"]
        if self.runtime.enable_second_pass:
            if unknown_rows:
                self.runtime.classifier.close_browser()
                retry_total = len(unknown_rows)
                retry_worker_count = get_classification_worker_count(retry_total)
                self.common.log_line(f"开始进行 2次筛选：仅重试首轮未确定的 {retry_total} 个模组。")
                if retry_total > 1:
                    self.common.log_line(f"2次筛选使用 {retry_worker_count} 个并发线程。")

                def second_pass_progress(completed: int, inner_total: int, jar: Path) -> None:
                    percent = completed / max(inner_total, 1)
                    self.runtime.set_progress(58 + percent * 3)
                    self.runtime.set_status(f"{TaskStage.CLASSIFY_MODS.value}：正在进行 2次筛选 [{completed}/{inner_total}] {jar.name}")

                def second_pass_result(completed: int, inner_total: int, jar: Path, row: Dict[str, Any]) -> None:
                    self.common.log_line(f"[2次筛选 {completed}/{inner_total}] {jar.name} -> {get_category_label(row['Category'])} | {row['Reason']}")

                recovered = rerun_unknown_classifications(
                    results,
                    self.runtime.use_mcmod,
                    getattr(self.runtime.classifier, "use_curseforge", False),
                    getattr(self.runtime.classifier, "use_offline_database", False),
                    self.runtime.download_source,
                    progress_callback=second_pass_progress,
                    result_callback=second_pass_result,
                )
                remaining_unknown = sum(1 for row in results if row["Category"] == "unknown")
                self.common.log_line(f"2次筛选完成：回补 {recovered} 个，仍待人工确认 {remaining_unknown} 个。")
            else:
                self.common.log_line("已开启 2次筛选，但首轮没有 unknown 模组，跳过重试。")

        unknown_rows = [row for row in results if row["Category"] == "unknown"]
        server_keep_rows = [row for row in results if row["Category"] == "server-keep"]
        client_only_rows = [row for row in results if row["Category"] == "client-only"]
        damaged_rows = [row for row in results if row.get("JarStatus") == "damaged"]
        self.common.log_line(
            "模组自动筛选完成："
            f"服务端保留 {len(server_keep_rows)} 个，"
            f"待人工确认 {len(unknown_rows)} 个，"
            f"纯客户端 {len(client_only_rows)} 个。"
        )
        if damaged_rows:
            self.common.log_line(f"检测到 {len(damaged_rows)} 个损坏或元数据损坏的 Jar，已在报告中单独标记：")
            for row in damaged_rows:
                self.common.log_line(f" - {row['FileName']} | {row.get('JarIssue') or row['Reason']}")
        if unknown_rows:
            self.common.log_line("以下模组未能自动确认，人工核查时会优先展示：")
            for row in unknown_rows:
                self.common.log_line(f" - {row['FileName']} | {row['Reason']}")
        return results


class ServerLaunchService:
    """负责生成启动脚本、首次启动、修配置、再次验证启动。"""

    def __init__(self, runtime: ServerBuilderRuntime, common: ServerBuilderCommonService):
        self.runtime = runtime
        self.common = common

    def estimate_memory_settings(self, mod_count: int) -> Tuple[str, str]:
        if mod_count <= 50:
            xmx = "4G"
        elif mod_count <= 100:
            xmx = "6G"
        elif mod_count <= 180:
            xmx = "8G"
        elif mod_count <= 260:
            xmx = "10G"
        else:
            xmx = "12G"
        return "2G", xmx

    def write_user_jvm_args(self, output_root: Path, xms: str, xmx: str) -> None:
        (output_root / "user_jvm_args.txt").write_text(
            "\n".join(["# 自动筛选模组分类器 3.00 生成", f"-Xms{xms}", f"-Xmx{xmx}"]) + "\n",
            encoding="utf-8",
        )

    def format_java_executable(self, java_runtime: JavaRuntime) -> str:
        return f'"{java_runtime.path}"'

    def get_batch_root_cd_line(self, script_depth: int = 0) -> str:
        if script_depth <= 0:
            return "cd /d %~dp0"
        return 'cd /d "%~dp0\\' + ("..\\" * (script_depth - 1)) + '.."'

    def build_fabric_launch_lines(self, output_root: Path, xms: str, xmx: str, java_runtime: JavaRuntime, script_depth: int = 0) -> List[str]:
        launch_jar = output_root / "fabric-server-launch.jar"
        if not launch_jar.exists():
            raise RuntimeError("Fabric 服务端安装后未找到 fabric-server-launch.jar。")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(script_depth),
            f"{self.format_java_executable(java_runtime)} -Xms{xms} -Xmx{xmx} -jar fabric-server-launch.jar nogui",
        ]

    def write_batch_script(self, script_path: Path, lines: Sequence[str]) -> Path:
        # 批处理在中文目录里被 cmd 直接拉起时，先切到 UTF-8 代码页更稳。
        normalized_lines = list(lines)
        if normalized_lines and normalized_lines[0].strip().lower() == "@echo off":
            if len(normalized_lines) == 1 or normalized_lines[1].strip().lower() != "chcp 65001>nul":
                normalized_lines.insert(1, "chcp 65001>nul")
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("\r\n".join(normalized_lines) + "\r\n", encoding="utf-8")
        return script_path

    def get_run_bat_path(self, output_root: Path) -> Path:
        run_bat = output_root / "run.bat"
        if not run_bat.exists():
            raise RuntimeError("服务端安装后未找到官方 run.bat。")
        return run_bat

    def build_run_bat_wrapper_lines(self, output_root: Path, java_runtime: JavaRuntime) -> List[str]:
        run_bat = self.get_run_bat_path(output_root)
        java_bin_dir = java_runtime.path.parent
        java_home = java_bin_dir.parent
        run_target = run_bat.relative_to(output_root).as_posix().replace("/", "\\")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(),
            f'set "JAVA_HOME={java_home}"',
            f'set "PATH={java_bin_dir};%PATH%"',
            f'call "{run_target}" nogui %*',
        ]

    def get_expected_win_args_relative_path(self, candidate: VersionCandidate) -> Path:
        if candidate.loader == LoaderType.FORGE.value:
            return Path("libraries") / "net" / "minecraftforge" / "forge" / f"{candidate.minecraft_version}-{candidate.loader_version}" / "win_args.txt"
        if candidate.loader == LoaderType.NEOFORGE.value:
            return Path("libraries") / "net" / "neoforged" / "neoforge" / candidate.loader_version / "win_args.txt"
        raise RuntimeError(f"{candidate.loader} 不使用 win_args.txt 启动链路。")

    def parse_win_args_path_from_run_bat(self, output_root: Path) -> Optional[Path]:
        run_bat = output_root / "run.bat"
        if not run_bat.exists():
            return None
        pattern = re.compile(r'@"?(?P<path>[^"\s]+win_args\.txt)"?', re.IGNORECASE)
        for line in run_bat.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = pattern.search(line)
            if not match:
                continue
            relative_text = match.group("path").replace("/", "\\")
            candidate_path = output_root / Path(relative_text)
            try:
                resolved_path = candidate_path.resolve()
            except Exception:
                continue
            if self.common.is_same_or_nested_path(output_root.resolve(), resolved_path) and resolved_path.exists():
                return resolved_path
        return None

    def resolve_win_args_file(self, output_root: Path, candidate: VersionCandidate) -> Path:
        exact_path = output_root / self.get_expected_win_args_relative_path(candidate)
        if exact_path.exists():
            return exact_path
        fallback_path = self.parse_win_args_path_from_run_bat(output_root)
        if fallback_path:
            self.common.log_line(f"未在标准位置找到 win_args.txt，已改为按官方 run.bat 解析：{fallback_path}")
            return fallback_path
        raise RuntimeError("未找到与当前 Forge / NeoForge 版本匹配的 win_args.txt。")

    def build_win_args_launch_lines(self, output_root: Path, candidate: VersionCandidate, java_runtime: JavaRuntime) -> List[str]:
        win_args = self.resolve_win_args_file(output_root, candidate)
        relative = win_args.relative_to(output_root).as_posix().replace("/", "\\")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(1),
            f"{self.format_java_executable(java_runtime)} @user_jvm_args.txt @{relative} nogui",
        ]

    def write_launch_scripts(self, output_root: Path, candidate: VersionCandidate, xms: str, xmx: str, java_runtime: JavaRuntime) -> LaunchScripts:
        report_dir = output_root / TOOL_DIR_NAME
        if candidate.loader == LoaderType.FABRIC.value:
            user_lines = self.build_fabric_launch_lines(output_root, xms, xmx, java_runtime)
            internal_lines = self.build_fabric_launch_lines(output_root, xms, xmx, java_runtime, script_depth=1)
        else:
            self.write_user_jvm_args(output_root, xms, xmx)
            user_lines = self.build_run_bat_wrapper_lines(output_root, java_runtime)
            internal_lines = self.build_win_args_launch_lines(output_root, candidate, java_runtime)
        return LaunchScripts(
            user_script=self.write_batch_script(output_root / "启动服务器.bat", user_lines),
            internal_script=self.write_batch_script(report_dir / "内部验证启动.bat", internal_lines),
        )

    def set_eula_true(self, output_root: Path) -> None:
        path = output_root / "eula.txt"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            content = re.sub(r"(?im)^eula\s*=\s*false\s*$", "eula=true", content)
            if "eula=true" not in content:
                content += "\neula=true\n"
        else:
            content = "eula=true\n"
        path.write_text(content, encoding="utf-8")

    def set_online_mode_false(self, output_root: Path) -> None:
        path = output_root / "server.properties"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"(?im)^online-mode=", content):
                content = re.sub(r"(?im)^online-mode=.*$", "online-mode=false", content)
            else:
                content += "\nonline-mode=false\n"
        else:
            content = "online-mode=false\n"
        path.write_text(content, encoding="utf-8")

    def stop_process(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdin:
                process.stdin.write("stop\n")
                process.stdin.flush()
        except Exception:
            pass
        try:
            process.wait(timeout=15)
        except Exception:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=SUBPROCESS_CREATIONFLAGS)

    def get_server_boot_timeout_config(self, mode: str) -> Tuple[int, int]:
        """返回启动阶段的超时策略：(无新输出超时，总兜底超时)。"""
        if self.runtime.boot_timeout_mode == SERVER_BOOT_TIMEOUT_STRICT:
            return DEFAULT_SERVER_TIMEOUT_SECONDS, DEFAULT_SERVER_TIMEOUT_SECONDS
        if mode == "verify":
            return DEFAULT_SERVER_TIMEOUT_SECONDS, DEFAULT_SERVER_VERIFY_MAX_TIMEOUT_SECONDS
        return DEFAULT_SERVER_TIMEOUT_SECONDS, DEFAULT_SERVER_INIT_MAX_TIMEOUT_SECONDS

    def should_continue_waiting(self, mode: str, idle_timeout_seconds: int) -> bool:
        if self.runtime.boot_timeout_mode != SERVER_BOOT_TIMEOUT_SMART:
            return False
        extend_seconds = 120
        phase_text = "首次启动" if mode == "init" else "验证启动"
        return self.runtime.request_continue_wait(
            f"{phase_text}较慢",
            f"服务端可能仍在慢启动，已经连续 {idle_timeout_seconds} 秒没有新日志输出。\n\n要继续等待 {extend_seconds} 秒吗？",
            extend_seconds,
        )

    def run_server_script(self, output_root: Path, launch_script: Path, mode: str) -> None:
        launch_path = launch_script.resolve()
        if not launch_path.exists():
            raise RuntimeError(f"启动脚本不存在：{launch_path}")

        self.common.log_line(f"准备执行{('首次启动' if mode == 'init' else '验证启动')}脚本：{launch_path}")
        self.common.log_line(f"启动目录：{output_root}")
        eula_path = output_root / "eula.txt"
        properties_path = output_root / "server.properties"
        last_path_error = False

        for attempt_index in range(2):
            if attempt_index > 0:
                self.common.log_line("检测到批处理启动阶段疑似路径异常，1 秒后重试一次。")
                time.sleep(1)

            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", "call", str(launch_path)],
                cwd=str(output_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                encoding=SYSTEM_ENCODING,
                errors="replace",
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )

            line_queue: "queue.Queue[Optional[str]]" = queue.Queue()

            def reader() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    line_queue.put(line.rstrip("\r\n"))
                line_queue.put(None)

            threading.Thread(target=reader, daemon=True).start()
            idle_timeout_seconds, max_timeout_seconds = self.get_server_boot_timeout_config(mode)
            started_at = time.time()
            last_output_at = started_at
            self.common.log_line(
                f"{('首次启动' if mode == 'init' else '验证启动')}超时策略：连续 {idle_timeout_seconds} 秒无新输出才判定卡住，最长等待 {max_timeout_seconds} 秒。"
            )
            saw_success = False
            stream_closed = False
            generated_at: Optional[float] = None
            path_error_detected = False

            while True:
                try:
                    item = line_queue.get(timeout=0.2)
                except queue.Empty:
                    item = ""

                if item == "":
                    pass
                elif item is None:
                    if not stream_closed and process.poll() is not None:
                        stream_closed = True
                else:
                    last_output_at = time.time()
                    self.runtime.install_log_lines.append(item)
                    self.common.log_line(item)
                    if "Done (" in item or "For help, type" in item:
                        saw_success = True
                        if mode == "verify":
                            self.stop_process(process)
                    if "系统找不到指定的路径" in item or "The system cannot find the path specified." in item:
                        path_error_detected = True

                if mode == "init":
                    if process.poll() is not None:
                        break
                    if eula_path.exists() or properties_path.exists():
                        if generated_at is None:
                            generated_at = time.time()
                            self.common.log_line("检测到服务端已生成初始化配置，准备优雅结束首次启动。")
                        if time.time() - generated_at >= 5:
                            self.stop_process(process)
                            break

                if mode == "verify" and saw_success and process.poll() is not None:
                    break

                if process.poll() is not None and stream_closed and line_queue.empty():
                    break

                now = time.time()
                if now - started_at > max_timeout_seconds:
                    if mode == "verify" and saw_success:
                        self.stop_process(process)
                        break
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=SUBPROCESS_CREATIONFLAGS)
                    raise RuntimeError(
                        f"服务端{('首次启动' if mode == 'init' else '验证启动')}超过 {max_timeout_seconds} 秒仍未完成。"
                    )
                if now - last_output_at > idle_timeout_seconds:
                    if mode == "verify" and saw_success:
                        self.stop_process(process)
                        break
                    if self.should_continue_waiting(mode, idle_timeout_seconds):
                        last_output_at = time.time()
                        max_timeout_seconds += 120
                        self.common.log_line("已按你的选择继续等待 120 秒，仍会持续观察新的启动日志。")
                        continue
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=SUBPROCESS_CREATIONFLAGS)
                    raise RuntimeError(
                        f"服务端{('首次启动' if mode == 'init' else '验证启动')}已连续 {idle_timeout_seconds} 秒没有新输出，判定为卡住。"
                    )

            if mode == "init":
                if eula_path.exists() or properties_path.exists():
                    self.common.log_line(
                        f"首次启动已生成：eula={eula_path.exists()}，server.properties={properties_path.exists()}"
                    )
                    return
                last_path_error = path_error_detected
                if path_error_detected and attempt_index == 0:
                    continue
                self.common.log_line("首次启动结束，但未检测到 eula.txt 或 server.properties。")
                raise RuntimeError("首次启动后未生成 eula.txt 或 server.properties。")

            if saw_success:
                return
            last_path_error = path_error_detected
            if path_error_detected and attempt_index == 0:
                continue
            self.common.log_line("第二次启动结束，但没有检测到 Done/For help 启动完成标志。")
            raise RuntimeError("第二次启动未检测到服务端启动完成标志。")

        if mode == "init":
            if last_path_error:
                self.common.log_line("重试后仍然出现批处理路径错误。")
            raise RuntimeError("首次启动后未生成 eula.txt 或 server.properties。")
        if last_path_error:
            self.common.log_line("重试后仍然出现批处理路径错误。")
        raise RuntimeError("第二次启动未检测到服务端启动完成标志。")


class ServerReportingService:
    """负责把构建过程产生的报告和日志写到磁盘。"""

    def __init__(self, runtime: ServerBuilderRuntime):
        self.runtime = runtime

    def write_mod_reports(self, report_dir: Path, mod_results: List[Dict[str, Any]]) -> None:
        json_path = report_dir / f"{MOD_REPORT_BASENAME}.json"
        csv_path = report_dir / f"{MOD_REPORT_BASENAME}.csv"
        txt_path = report_dir / f"{MOD_REPORT_BASENAME}.txt"
        serializable = []
        for row in mod_results:
            copy_row = dict(row)
            path_value = copy_row.get("Path")
            if isinstance(path_value, Path):
                copy_row["Path"] = str(path_value)
            serializable.append(copy_row)
        json_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv_with_labels(csv_path, serializable)
        summary_lines = [
            f"服务端保留: {sum(1 for row in mod_results if row['Category'] == 'server-keep')}",
            f"纯客户端: {sum(1 for row in mod_results if row['Category'] == 'client-only')}",
            f"无法分类: {sum(1 for row in mod_results if row['Category'] == 'unknown')}",
            f"损坏Jar: {sum(1 for row in mod_results if row.get('JarStatus') == 'damaged')}",
            f"最终复制: {sum(1 for row in mod_results if row.get('SelectedForServer'))}",
        ]
        txt_path.write_text("\n".join(summary_lines), encoding="utf-8")

    def write_directory_summary(self, report_dir: Path, summary_rows: List[Dict[str, Any]]) -> None:
        (report_dir / CONFIG_COPY_SUMMARY_NAME).write_text(json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_logs(self, report_dir: Path) -> Tuple[Path, Path]:
        build_log_path = report_dir / BUILD_LOG_NAME
        install_log_path = report_dir / INSTALL_LOG_NAME
        build_log_path.write_text("\n".join(self.runtime.build_log_lines) + "\n", encoding="utf-8")
        install_log_path.write_text("\n".join(self.runtime.install_log_lines) + "\n", encoding="utf-8")
        return build_log_path, install_log_path


class ServerWorkflowService:
    """一键开服总流程编排器。真正的“先做什么、后做什么”写在这里。"""

    def __init__(
        self,
        runtime: ServerBuilderRuntime,
        common: ServerBuilderCommonService,
        versioning: ServerVersionService,
        java: ServerJavaService,
        install: ServerInstallService,
        mods: ServerModService,
        launch: ServerLaunchService,
        reporting: ServerReportingService,
    ):
        self.runtime = runtime
        self.common = common
        self.versioning = versioning
        self.java = java
        self.install = install
        self.mods = mods
        self.launch = launch
        self.reporting = reporting

    def build_server(self, client_dir: Path, output_root: Path) -> Dict[str, Path]:
        # 这里像导演，自己不深挖细节，而是按步骤调用各个服务。
        temp_workspace = Path(tempfile.mkdtemp(prefix="auto-mod-classifier-"))
        report_dir = output_root / TOOL_DIR_NAME
        mod_results: Optional[List[Dict[str, Any]]] = None
        directory_summary: Optional[List[Dict[str, Any]]] = None
        try:
            self.common.set_stage(TaskStage.PRECHECK, 2, "正在检查输入和输出目录")
            if not client_dir.exists() or not client_dir.is_dir():
                raise RuntimeError("客户端目录不存在。")
            if output_root.exists():
                if not output_root.is_dir():
                    raise RuntimeError("服务端输出路径不是目录。")
                if any(output_root.iterdir()):
                    raise RuntimeError("服务端输出目录必须是新的空目录。")
            else:
                output_root.mkdir(parents=True, exist_ok=True)

            client_resolved = client_dir.resolve()
            output_resolved = output_root.resolve()
            if client_resolved == output_resolved or self.common.is_same_or_nested_path(client_resolved, output_resolved):
                raise RuntimeError("服务端输出目录不能与客户端目录相同，也不能位于客户端目录内部。")

            self.common.set_stage(TaskStage.CLIENT_SCAN, 10, "正在识别客户端根目录")
            game_root = self.versioning.normalize_client_root(client_dir)
            self.common.log_line(f"客户端实例根目录：{game_root}")

            self.common.set_stage(TaskStage.CLIENT_SCAN, 18, "正在读取版本信息")
            candidates = self.versioning.find_version_candidates(game_root)
            chosen = self.versioning.choose_version_candidate(candidates)
            self.common.log_line(f"目标版本：{chosen.display_name} | 版本清单 Java {chosen.java_major}")
            if chosen.loader == LoaderType.QUILT.value:
                raise RuntimeError("检测到 Quilt 客户端，3.00 的一键制作服务端模式暂不支持 Quilt。")

            required_java_major = self.java.get_required_java_major(chosen)
            self.common.set_stage(TaskStage.PRECHECK, 24, f"正在匹配 Java {required_java_major}")
            java_runtime = self.java.ensure_java(client_dir, game_root, chosen, output_root)
            self.common.log_line(f"Minecraft {chosen.minecraft_version} 需要 Java {required_java_major}")
            self.common.log_line(f"已选 Java：{java_runtime.summary} | 来源：{java_runtime.source}")

            self.common.set_stage(TaskStage.DOWNLOAD_INSTALLER, 26, "正在解析安装器地址")
            installer_spec = self.versioning.resolve_installer_spec(chosen)
            installer_path = self.install.download_installer(installer_spec, temp_workspace)

            self.common.set_stage(TaskStage.INSTALL_SERVER, 40, "正在安装服务端核心")
            self.install.install_server(output_root, chosen, installer_path, java_runtime)

            self.common.set_stage(TaskStage.CLASSIFY_MODS, 52, "正在筛选客户端模组")
            mod_results = self.install.classify_mod_directory(game_root / "mods")
            mod_review_items = self.mods.build_mod_review_items(mod_results)
            if mod_review_items:
                selected_mod_keys = self.runtime.request_checklist(
                    "Mod复制核查",
                    "已按分类展示全部模组。待人工确认项排在最前并默认勾选；纯客户端默认不勾选，但会展示给你复核。",
                    mod_review_items,
                )
                if selected_mod_keys is None:
                    raise RuntimeError("已取消模组复制核查。")
            else:
                selected_mod_keys = []
                self.common.log_line("客户端 mods 目录中没有需要复制的模组。")

            self.common.set_stage(TaskStage.COPY_MODS, 63, "正在复制服务端模组")
            copied_mods = self.mods.copy_selected_mods(mod_results, selected_mod_keys, output_root / "mods")
            self.common.log_line(f"已复制 {copied_mods} 个模组到服务端。")

            self.common.set_stage(TaskStage.COPY_CONFIGS, 72, "正在整理配置目录候选")
            config_review_items = self.mods.enumerate_copyable_directories(game_root)
            if config_review_items:
                selected_directories = self.runtime.request_checklist(
                    "配置目录复制核查",
                    "默认勾选所有配置类目录；取消勾选的目录不会复制到服务端。",
                    config_review_items,
                )
                if selected_directories is None:
                    raise RuntimeError("已取消配置目录复制核查。")
            else:
                selected_directories = []
                self.common.log_line("没有可复制的顶层配置目录。")

            self.common.set_stage(TaskStage.COPY_CONFIGS, 78, "正在复制配置目录")
            directory_summary = self.mods.copy_selected_directories(game_root, output_root, selected_directories)
            self.common.log_line(f"已复制 {sum(1 for row in directory_summary if row['Copied'])} 个目录到服务端。")

            self.common.set_stage(TaskStage.PREPARE_LAUNCH, 84, "正在生成启动脚本")
            copied_mod_count = sum(1 for row in mod_results if row.get("SelectedForServer"))
            xms, xmx = self.launch.estimate_memory_settings(copied_mod_count)
            self.common.log_line(f"按 {copied_mod_count} 个模组分配内存：Xms={xms}, Xmx={xmx}")
            launch_scripts = self.launch.write_launch_scripts(output_root, chosen, xms, xmx, java_runtime)

            self.common.set_stage(TaskStage.FIRST_BOOT, 89, "正在首次启动并生成配置")
            self.launch.run_server_script(output_root, launch_scripts.internal_script, "init")

            self.common.set_stage(TaskStage.PATCH_CONFIG, 93, "正在写入服务器配置")
            self.launch.set_eula_true(output_root)
            self.launch.set_online_mode_false(output_root)

            self.common.set_stage(TaskStage.VERIFY_BOOT, 97, "正在进行第二次启动验证")
            self.launch.run_server_script(output_root, launch_scripts.internal_script, "verify")

            report_dir.mkdir(parents=True, exist_ok=True)
            self.reporting.write_mod_reports(report_dir, mod_results)
            self.reporting.write_directory_summary(report_dir, directory_summary)
            _, install_log_path = self.reporting.write_logs(report_dir)

            self.common.set_stage(TaskStage.COMPLETE, 100, "服务端制作完成，正在收尾")
            return {
                "server_root": output_root,
                "report_dir": report_dir,
                "launch_script": launch_scripts.user_script,
                "install_log_path": install_log_path,
            }
        except Exception:
            if output_root.exists():
                report_dir.mkdir(parents=True, exist_ok=True)
                if mod_results is not None:
                    self.reporting.write_mod_reports(report_dir, mod_results)
                if directory_summary is not None:
                    self.reporting.write_directory_summary(report_dir, directory_summary)
                self.reporting.write_logs(report_dir)
            raise
        finally:
            shutil.rmtree(temp_workspace, ignore_errors=True)
