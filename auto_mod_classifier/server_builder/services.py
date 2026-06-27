from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..classifier import classify_jars_parallel, rerun_unknown_classifications
from ..shared import *
from .common import ServerBuilderCommonService
from .context import ServerBuilderRuntime


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

    def resolve_forge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        metadata = ET.fromstring(self.common.http_get_text("https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"))
        wanted = f"{candidate.minecraft_version}-{candidate.loader_version}"
        versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
        if wanted not in versions:
            raise RuntimeError(f"Forge 官方源未找到完全同版本服务端安装器：{wanted}")
        return InstallerSpec(
            loader=LoaderType.FORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=wanted,
            download_url=f"https://maven.minecraftforge.net/net/minecraftforge/forge/{wanted}/forge-{wanted}-installer.jar",
            file_name=f"forge-{wanted}-installer.jar",
        )

    def resolve_neoforge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        metadata = ET.fromstring(self.common.http_get_text("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"))
        versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
        if candidate.loader_version not in versions:
            raise RuntimeError(f"NeoForge 官方源未找到完全同版本服务端安装器：{candidate.loader_version}")
        return InstallerSpec(
            loader=LoaderType.NEOFORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=candidate.loader_version,
            download_url=f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{candidate.loader_version}/neoforge-{candidate.loader_version}-installer.jar",
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

    def ensure_java(self, client_dir: Path, game_root: Path, candidate: VersionCandidate) -> JavaRuntime:
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

    def run_process_capture(self, args: Sequence[str], cwd: Path, timeout_seconds: int, install_log_only: bool = False) -> Tuple[int, List[str]]:
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
        self.common.http_download(spec.download_url, destination)
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
        code, _ = self.run_process_capture(args, output_root, DEFAULT_INSTALL_TIMEOUT_SECONDS)
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
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
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

    def run_server_script(self, output_root: Path, launch_script: Path, mode: str) -> None:
        try:
            launch_target = launch_script.relative_to(output_root).as_posix().replace("/", "\\")
        except ValueError:
            launch_target = str(launch_script)
        process = subprocess.Popen(
            ["cmd.exe", "/c", launch_target],
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
        deadline = time.time() + DEFAULT_SERVER_TIMEOUT_SECONDS
        eula_path = output_root / "eula.txt"
        properties_path = output_root / "server.properties"
        saw_success = False
        stream_closed = False
        generated_at: Optional[float] = None

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
                self.runtime.install_log_lines.append(item)
                self.common.log_line(item)
                if "Done (" in item or "For help, type" in item:
                    saw_success = True
                    if mode == "verify":
                        self.stop_process(process)

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

            if time.time() > deadline:
                if mode == "verify" and saw_success:
                    self.stop_process(process)
                    break
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=SUBPROCESS_CREATIONFLAGS)
                raise RuntimeError(f"服务端{('首次启动' if mode == 'init' else '验证启动')}超过 {DEFAULT_SERVER_TIMEOUT_SECONDS} 秒仍未完成。")

        if mode == "init":
            if not eula_path.exists() and not properties_path.exists():
                raise RuntimeError("首次启动后未生成 eula.txt 或 server.properties。")
            return

        if not saw_success:
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
            self.common.set_stage(TaskStage.PRECHECK, 2, "校验目录")
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

            self.common.set_stage(TaskStage.CLIENT_SCAN, 10, "识别客户端实例根目录")
            game_root = self.versioning.normalize_client_root(client_dir)
            self.common.log_line(f"客户端实例根目录：{game_root}")

            self.common.set_stage(TaskStage.CLIENT_SCAN, 18, "扫描版本清单")
            candidates = self.versioning.find_version_candidates(game_root)
            chosen = self.versioning.choose_version_candidate(candidates)
            self.common.log_line(f"目标版本：{chosen.display_name} | 版本清单 Java {chosen.java_major}")
            if chosen.loader == LoaderType.QUILT.value:
                raise RuntimeError("检测到 Quilt 客户端，3.00 的一键制作服务端模式暂不支持 Quilt。")

            required_java_major = self.java.get_required_java_major(chosen)
            self.common.set_stage(TaskStage.PRECHECK, 24, f"匹配 Java {required_java_major}")
            java_runtime = self.java.ensure_java(client_dir, game_root, chosen)
            self.common.log_line(f"Minecraft {chosen.minecraft_version} 需要 Java {required_java_major}")
            self.common.log_line(f"已选 Java：{java_runtime.summary} | 来源：{java_runtime.source}")

            self.common.set_stage(TaskStage.DOWNLOAD_INSTALLER, 26, "解析官方安装器地址")
            installer_spec = self.versioning.resolve_installer_spec(chosen)
            installer_path = self.install.download_installer(installer_spec, temp_workspace)

            self.common.set_stage(TaskStage.INSTALL_SERVER, 40, "安装服务端")
            self.install.install_server(output_root, chosen, installer_path, java_runtime)

            self.common.set_stage(TaskStage.CLASSIFY_MODS, 52, "分析客户端 mods")
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

            self.common.set_stage(TaskStage.COPY_MODS, 63, "复制服务端模组")
            copied_mods = self.mods.copy_selected_mods(mod_results, selected_mod_keys, output_root / "mods")
            self.common.log_line(f"已复制 {copied_mods} 个模组到服务端。")

            self.common.set_stage(TaskStage.COPY_CONFIGS, 72, "收集配置目录候选")
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

            self.common.set_stage(TaskStage.COPY_CONFIGS, 78, "复制配置目录")
            directory_summary = self.mods.copy_selected_directories(game_root, output_root, selected_directories)
            self.common.log_line(f"已复制 {sum(1 for row in directory_summary if row['Copied'])} 个目录到服务端。")

            self.common.set_stage(TaskStage.PREPARE_LAUNCH, 84, "生成统一启动脚本")
            copied_mod_count = sum(1 for row in mod_results if row.get("SelectedForServer"))
            xms, xmx = self.launch.estimate_memory_settings(copied_mod_count)
            self.common.log_line(f"按 {copied_mod_count} 个模组分配内存：Xms={xms}, Xmx={xmx}")
            launch_scripts = self.launch.write_launch_scripts(output_root, chosen, xms, xmx, java_runtime)

            self.common.set_stage(TaskStage.FIRST_BOOT, 89, "首次启动生成服务器配置")
            self.launch.run_server_script(output_root, launch_scripts.internal_script, "init")

            self.common.set_stage(TaskStage.PATCH_CONFIG, 93, "写入 eula 与 server.properties")
            self.launch.set_eula_true(output_root)
            self.launch.set_online_mode_false(output_root)

            self.common.set_stage(TaskStage.VERIFY_BOOT, 97, "第二次启动验证")
            self.launch.run_server_script(output_root, launch_scripts.internal_script, "verify")

            report_dir.mkdir(parents=True, exist_ok=True)
            self.reporting.write_mod_reports(report_dir, mod_results)
            self.reporting.write_directory_summary(report_dir, directory_summary)
            _, install_log_path = self.reporting.write_logs(report_dir)

            self.common.set_stage(TaskStage.COMPLETE, 100, "服务端制作完成")
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
