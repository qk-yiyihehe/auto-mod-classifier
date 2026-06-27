from pathlib import Path
from typing import Any, Optional, Tuple

from ..shared import *
from .context import ServerBuilderRuntime


class ServerBuilderCommonService:
    """一键开服通用工具与共享操作。"""

    def __init__(self, runtime: ServerBuilderRuntime):
        self.runtime = runtime

    def log_line(self, message: str) -> None:
        # 构建日志统一从这里走，避免每个服务自己维护日志状态。
        self.runtime.build_log_lines.append(message)
        self.runtime.log(message)

    def set_stage(self, stage: TaskStage, progress: float, detail: str) -> None:
        # 阶段更新也统一收口，界面和日志会一起同步。
        self.runtime.set_progress(progress)
        self.runtime.set_status(f"{stage.value}：{detail}")
        self.log_line(f"[{stage.value}] {detail}")

    def http_get_text(self, url: str) -> str:
        cache_key = f"text::{url}"
        if cache_key in self.runtime.network_cache:
            return self.runtime.network_cache[cache_key]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        self.runtime.network_cache[cache_key] = text
        return text

    def http_get_json(self, url: str) -> Any:
        cache_key = f"json::{url}"
        if cache_key in self.runtime.network_cache:
            return self.runtime.network_cache[cache_key]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.runtime.network_cache[cache_key] = data
        return data

    def http_download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with destination.open("wb") as fp:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)

    def get_application_dir(self) -> Path:
        base = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        return base.resolve().parent

    def parse_release_version(self, version_text: str) -> Optional[Tuple[int, int, int]]:
        cleaned = str(version_text or "").strip()
        match = re.match(r"^\D*(\d+)\.(\d+)(?:\.(\d+))?", cleaned)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)

    def natural_sort_key(self, value: str) -> Tuple[Tuple[int, object], ...]:
        parts = re.findall(r"\d+|[a-z]+", str(value or "").lower())
        if not parts:
            return ((1, ""),)
        key = []
        for part in parts:
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return tuple(key)

    def version_candidate_sort_key(self, candidate: VersionCandidate) -> Tuple[Tuple[Tuple[int, object], ...], ...]:
        return (
            self.natural_sort_key(candidate.minecraft_version),
            self.natural_sort_key(candidate.loader),
            self.natural_sort_key(candidate.loader_version),
            self.natural_sort_key(candidate.version_id),
        )

    def is_same_or_nested_path(self, base_path: Path, candidate_path: Path) -> bool:
        try:
            candidate_path.relative_to(base_path)
            return True
        except ValueError:
            return False
