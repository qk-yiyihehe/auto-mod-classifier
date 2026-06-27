import atexit
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..application.models import BuildServerRequest, PreparedModScanSource, PreparedServerSource, ScanModsRequest
from ..shared import (
    DOWNLOAD_SOURCE_DOMESTIC,
    IMPORT_CACHE_DIR_NAME,
    LoaderType,
    USER_AGENT,
    VersionCandidate,
)

_ACTIVE_IMPORT_WORKSPACES: set[Path] = set()
_IMPORT_ATEXIT_REGISTERED = False


def get_import_cache_root() -> Path:
    """返回整合包导入工作区的统一缓存根目录。"""
    return Path(tempfile.gettempdir()) / IMPORT_CACHE_DIR_NAME


def cleanup_import_workspace(workspace_root: Path) -> None:
    """删除单个导入工作区，并从活动集合里移除。"""
    _ACTIVE_IMPORT_WORKSPACES.discard(workspace_root)
    shutil.rmtree(workspace_root, ignore_errors=True)


def cleanup_stale_import_workspaces() -> None:
    """清理遗留的导入缓存目录，避免上次异常退出后残留垃圾文件。"""
    cache_root = get_import_cache_root()
    if not cache_root.exists():
        return
    for child in cache_root.iterdir():
        if child in _ACTIVE_IMPORT_WORKSPACES:
            continue
        shutil.rmtree(child, ignore_errors=True)


def _cleanup_active_import_workspaces() -> None:
    for workspace_root in list(_ACTIVE_IMPORT_WORKSPACES):
        cleanup_import_workspace(workspace_root)


def _ensure_import_cleanup_registered() -> None:
    global _IMPORT_ATEXIT_REGISTERED
    if _IMPORT_ATEXIT_REGISTERED:
        return
    atexit.register(_cleanup_active_import_workspaces)
    _IMPORT_ATEXIT_REGISTERED = True


def _register_import_workspace(workspace_root: Path) -> None:
    _ensure_import_cleanup_registered()
    _ACTIVE_IMPORT_WORKSPACES.add(workspace_root)


def _make_import_workspace(source_path: Path) -> Dict[str, Path]:
    cache_root = get_import_cache_root()
    cache_root.mkdir(parents=True, exist_ok=True)
    workspace_root = Path(tempfile.mkdtemp(prefix="session-", dir=str(cache_root)))
    _register_import_workspace(workspace_root)
    safe_name = source_path.stem or source_path.name or "modpack"
    return {
        "workspace_root": workspace_root,
        "downloads_root": workspace_root / "downloads",
        "extracted_root": workspace_root / "extracted",
        "client_root": workspace_root / "client" / safe_name,
    }


def _emit(emit, kind: str, payload: str) -> None:
    if emit is not None:
        emit(kind, payload)


def _rewrite_download_url(url: str, download_source: str) -> str:
    """按用户选择切换官方源或国内镜像。"""
    if download_source != DOWNLOAD_SOURCE_DOMESTIC:
        return url

    replacements = (
        ("https://api.modrinth.com/", "https://mod.mcimirror.top/modrinth/"),
        ("https://cdn.modrinth.com/", "https://mod.mcimirror.top/"),
        ("https://api.curseforge.com/", "https://mod.mcimirror.top/curseforge/"),
        ("https://edge.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("https://mediafilez.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("http://edge.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("http://mediafilez.forgecdn.net/", "https://mod.mcimirror.top/"),
    )
    for before, after in replacements:
        if url.startswith(before):
            return after + url[len(before) :]
    return url


def _build_download_candidates(url: str, download_source: str) -> List[str]:
    rewritten = _rewrite_download_url(url, download_source)
    if rewritten == url:
        return [url]
    return [rewritten, url]


def _http_get_json(url: str, download_source: str) -> Any:
    last_error: Optional[Exception] = None
    for candidate in _build_download_candidates(url, download_source):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"获取 JSON 失败：{url}\n{last_error}")


def _http_download(url: str, destination: Path, download_source: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None
    for candidate in _build_download_candidates(url, download_source):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                with destination.open("wb") as fp:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fp.write(chunk)
                return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"下载失败：{url}\n{last_error}")


def _verify_download_hash(file_path: Path, hashes: Dict[str, str]) -> None:
    """优先校验 sha512 / sha1，避免镜像异常时导入脏文件。"""
    supported = [name for name in ("sha512", "sha1") if hashes.get(name)]
    if not supported:
        return

    digesters = {name: hashlib.new(name) for name in supported}
    with file_path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            for digester in digesters.values():
                digester.update(chunk)

    for name in supported:
        actual = digesters[name].hexdigest().lower()
        expected = str(hashes[name]).strip().lower()
        if actual != expected:
            raise RuntimeError(f"{file_path.name} 的 {name} 校验失败。")


def _copy_tree_if_exists(source_root: Path, source_name: str, target_root: Path) -> None:
    candidate = source_root / source_name
    if candidate.exists():
        shutil.copytree(candidate, target_root, dirs_exist_ok=True)


def _find_nested_root(base_dir: Path, accepted_names: Iterable[str]) -> Optional[Path]:
    accepted = {name.lower() for name in accepted_names}
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir() and child.name.lower() in accepted:
            return child
    return None


def _looks_like_client_root(path: Path) -> bool:
    if (path / ".minecraft").is_dir():
        return True
    has_mods = (path / "mods").is_dir()
    has_versions = (path / "versions").is_dir()
    has_config = (path / "config").is_dir()
    has_manifest = (path / "manifest.json").is_file() or any((path / f"{item.stem}.jar").exists() for item in path.glob("*.json"))
    return has_mods and (has_versions or has_config or has_manifest)


def _find_zip_client_root(extracted_root: Path) -> Optional[Path]:
    if _looks_like_client_root(extracted_root):
        return extracted_root

    for child in sorted(extracted_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if _looks_like_client_root(child):
            return child
        minecraft_root = child / ".minecraft"
        if minecraft_root.is_dir():
            return child
    return None


def _find_mod_scan_root(extracted_root: Path) -> Optional[Path]:
    if (extracted_root / "mods").is_dir():
        return extracted_root

    nested_minecraft = extracted_root / ".minecraft"
    if (nested_minecraft / "mods").is_dir():
        return nested_minecraft

    for child in sorted(extracted_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if (child / "mods").is_dir():
            return child
        if child.name.lower() == "mods":
            return extracted_root
        nested = child / ".minecraft"
        if (nested / "mods").is_dir():
            return nested
    return None


def _parse_curseforge_loader_id(loader_id: str, minecraft_version: str, json_path: Path) -> Optional[VersionCandidate]:
    lowered = loader_id.strip().lower()
    prefixes = (
        ("fabric-", LoaderType.FABRIC.value),
        ("quilt-", LoaderType.QUILT.value),
        ("quilt-loader-", LoaderType.QUILT.value),
        ("forge-", LoaderType.FORGE.value),
        ("neoforge-", LoaderType.NEOFORGE.value),
    )
    for prefix, loader in prefixes:
        if lowered.startswith(prefix):
            loader_version = loader_id[len(prefix) :].strip()
            if loader_version:
                return VersionCandidate(loader_id, minecraft_version, loader, loader_version, 21, json_path)
    return None


def _build_mrpack_candidates(manifest: Dict[str, Any], json_path: Path) -> List[VersionCandidate]:
    dependencies = manifest.get("dependencies") or {}
    minecraft_version = str(dependencies.get("minecraft") or "").strip()
    if not minecraft_version:
        return []

    dependency_map = (
        ("fabric-loader", LoaderType.FABRIC.value),
        ("quilt-loader", LoaderType.QUILT.value),
        ("forge", LoaderType.FORGE.value),
        ("neoforge", LoaderType.NEOFORGE.value),
    )
    candidates: List[VersionCandidate] = []
    for key, loader in dependency_map:
        loader_version = str(dependencies.get(key) or "").strip()
        if not loader_version:
            continue
        version_id = f"{minecraft_version}-{loader}-{loader_version}"
        candidates.append(VersionCandidate(version_id, minecraft_version, loader, loader_version, 21, json_path))
    return candidates


def _build_curseforge_candidates(manifest: Dict[str, Any], json_path: Path) -> List[VersionCandidate]:
    minecraft = manifest.get("minecraft") or {}
    minecraft_version = str(minecraft.get("version") or "").strip()
    if not minecraft_version:
        return []

    loaders = minecraft.get("modLoaders") or []
    primary = [item for item in loaders if isinstance(item, dict) and item.get("primary")]
    ordered = primary or [item for item in loaders if isinstance(item, dict)]

    candidates: List[VersionCandidate] = []
    for item in ordered:
        candidate = _parse_curseforge_loader_id(str(item.get("id") or ""), minecraft_version, json_path)
        if candidate:
            candidates.append(candidate)
    return candidates


def _copy_mrpack_overrides(extracted_root: Path, client_root: Path) -> None:
    for name in ("overrides", "client-overrides", "server-overrides"):
        _copy_tree_if_exists(extracted_root, name, client_root)


def _prepare_workspace_cleanup(workspace_root: Path):
    def _cleanup() -> None:
        cleanup_import_workspace(workspace_root)

    return _cleanup


class DirectorySourceImporter:
    """本地目录导入器，支持直接选择现成 mods 目录或客户端实例目录。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.exists() and source_path.is_dir()

    def prepare_mod_scan(self, request: ScanModsRequest, emit) -> PreparedModScanSource:
        source_path = request.source_path.resolve()
        mods_path = source_path / "mods" if (source_path / "mods").is_dir() else source_path
        if not mods_path.exists() or not mods_path.is_dir():
            raise RuntimeError("未找到可用于筛选的 mods 目录。")
        return PreparedModScanSource(
            source_kind="directory",
            display_path=request.source_path,
            workspace_root=source_path,
            mods_path=mods_path,
            report_root=mods_path,
            allow_file_move=True,
        )

    def prepare_server_build(self, request: BuildServerRequest, emit) -> PreparedServerSource:
        source_path = request.source_path.resolve()
        if not source_path.exists() or not source_path.is_dir():
            raise RuntimeError("客户端实例目录不存在。")
        return PreparedServerSource(
            source_kind="directory",
            display_path=request.source_path,
            workspace_root=source_path,
            client_dir=source_path,
        )


class MrpackSourceImporter:
    """Modrinth mrpack 导入器：下载文件、展开 overrides，并整理成完整客户端工作区。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.lower() == ".mrpack"

    def _prepare_client_workspace(self, source_path: Path, download_source: str, emit) -> Dict[str, Any]:
        workspace = _make_import_workspace(source_path)
        extracted_root = workspace["extracted_root"]
        client_root = workspace["client_root"]
        downloads_root = workspace["downloads_root"]

        _emit(emit, "status", "正在解析 MRPACK 整合包…")
        extracted_root.mkdir(parents=True, exist_ok=True)
        client_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source_path, "r") as archive:
            archive.extractall(extracted_root)

        manifest_path = extracted_root / "modrinth.index.json"
        if not manifest_path.exists():
            raise RuntimeError("该 MRPACK 中未找到 modrinth.index.json。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        _copy_mrpack_overrides(extracted_root, client_root)

        files = manifest.get("files") or []
        if not isinstance(files, list):
            raise RuntimeError("MRPACK 清单中的 files 字段格式不正确。")

        total = len(files)
        for index, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                continue
            relative_path = str(item.get("path") or "").strip()
            download_urls = [str(url).strip() for url in item.get("downloads") or [] if str(url).strip()]
            if not relative_path or not download_urls:
                continue
            target_path = client_root / Path(relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_download = downloads_root / Path(relative_path).name
            _emit(emit, "status", f"正在下载 MRPACK 文件 [{index}/{max(total, 1)}]：{Path(relative_path).name}")
            _emit(emit, "log", f"[MRPACK] 下载 {relative_path}")
            last_error: Optional[Exception] = None
            for url in download_urls:
                try:
                    _http_download(url, temp_download, download_source)
                    break
                except Exception as exc:
                    last_error = exc
            else:
                raise RuntimeError(f"无法下载 MRPACK 文件：{relative_path}\n{last_error}")
            _verify_download_hash(temp_download, item.get("hashes") or {})
            shutil.move(str(temp_download), str(target_path))

        version_candidates = _build_mrpack_candidates(manifest, manifest_path)
        return {
            "workspace_root": workspace["workspace_root"],
            "client_root": client_root,
            "manifest_path": manifest_path,
            "version_candidates": version_candidates,
            "metadata": {
                "manifest_type": "mrpack",
                "manifest_name": manifest.get("name") or source_path.stem,
                "manifest_version_id": manifest.get("versionId") or "",
            },
        }

    def prepare_mod_scan(self, request: ScanModsRequest, emit) -> PreparedModScanSource:
        source_path = request.source_path.resolve()
        prepared = self._prepare_client_workspace(source_path, request.download_source, emit)
        mods_path = prepared["client_root"] / "mods"
        if not mods_path.is_dir():
            raise RuntimeError("导入后的 MRPACK 中未找到 mods 目录。")
        return PreparedModScanSource(
            source_kind="mrpack",
            display_path=request.source_path,
            workspace_root=prepared["workspace_root"],
            mods_path=mods_path,
            report_root=request.source_path.parent.resolve(),
            allow_file_move=True,
            cleanup=_prepare_workspace_cleanup(prepared["workspace_root"]),
            metadata={**prepared["metadata"], "export_all_categories": True},
        )

    def prepare_server_build(self, request: BuildServerRequest, emit) -> PreparedServerSource:
        source_path = request.source_path.resolve()
        prepared = self._prepare_client_workspace(source_path, request.download_source, emit)
        return PreparedServerSource(
            source_kind="mrpack",
            display_path=request.source_path,
            workspace_root=prepared["workspace_root"],
            client_dir=prepared["client_root"],
            cleanup=_prepare_workspace_cleanup(prepared["workspace_root"]),
            metadata=prepared["metadata"],
            version_candidates=prepared["version_candidates"],
        )


class ZipModpackSourceImporter:
    """ZIP 整合包导入器：优先兼容 CurseForge 导出包和完整客户端压缩包。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.lower() == ".zip"

    def _extract_zip(self, source_path: Path) -> Dict[str, Path]:
        workspace = _make_import_workspace(source_path)
        extracted_root = workspace["extracted_root"]
        extracted_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source_path, "r") as archive:
            archive.extractall(extracted_root)
        return workspace

    def _prepare_curseforge_workspace(self, source_path: Path, request_download_source: str, emit) -> Dict[str, Any]:
        workspace = self._extract_zip(source_path)
        extracted_root = workspace["extracted_root"]
        client_root = workspace["client_root"]
        downloads_root = workspace["downloads_root"]
        client_root.mkdir(parents=True, exist_ok=True)

        manifest_path = extracted_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files") or []
        if not isinstance(files, list):
            raise RuntimeError("CurseForge 整合包 manifest.json 中的 files 字段格式不正确。")

        _copy_tree_if_exists(extracted_root, "overrides", client_root)

        total = len(files)
        mods_dir = client_root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                continue
            project_id = int(item.get("projectID") or item.get("projectId") or 0)
            file_id = int(item.get("fileID") or item.get("fileId") or 0)
            if not project_id or not file_id:
                continue
            meta_url = f"https://api.curseforge.com/v1/mods/{project_id}/files/{file_id}"
            _emit(emit, "status", f"正在下载 CurseForge 文件 [{index}/{max(total, 1)}]：{project_id}/{file_id}")
            file_meta = _http_get_json(meta_url, request_download_source).get("data") or {}
            download_url = str(file_meta.get("downloadUrl") or "").strip()
            file_name = str(file_meta.get("fileName") or f"{project_id}-{file_id}.jar").strip()
            if not download_url:
                mirror_url = f"https://api.curseforge.com/v1/mods/{project_id}/files/{file_id}/download-url"
                download_url = str((_http_get_json(mirror_url, DOWNLOAD_SOURCE_DOMESTIC).get("data") or "")).strip()
            if not download_url:
                raise RuntimeError(f"无法获取 CurseForge 文件下载地址：{project_id}/{file_id}")
            destination = mods_dir / file_name
            _emit(emit, "log", f"[CurseForge] 下载 {file_name}")
            _http_download(download_url, destination, request_download_source)

        version_candidates = _build_curseforge_candidates(manifest, manifest_path)
        return {
            "workspace_root": workspace["workspace_root"],
            "client_root": client_root,
            "manifest_path": manifest_path,
            "version_candidates": version_candidates,
            "metadata": {
                "manifest_type": "curseforge",
                "manifest_name": manifest.get("name") or source_path.stem,
                "manifest_version": manifest.get("version") or "",
            },
        }

    def _prepare_generic_zip_workspace(self, source_path: Path) -> Dict[str, Any]:
        workspace = self._extract_zip(source_path)
        extracted_root = workspace["extracted_root"]
        client_root = _find_zip_client_root(extracted_root)
        mod_scan_root = _find_mod_scan_root(extracted_root)
        return {
            "workspace_root": workspace["workspace_root"],
            "client_root": client_root,
            "mod_scan_root": mod_scan_root,
            "metadata": {"manifest_type": "zip"},
        }

    def _prepare_zip_workspace(self, source_path: Path, download_source: str, emit) -> Dict[str, Any]:
        probe_workspace = self._extract_zip(source_path)
        extracted_root = probe_workspace["extracted_root"]
        manifest_path = extracted_root / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
            if isinstance(manifest, dict) and "minecraft" in manifest and "files" in manifest:
                cleanup_import_workspace(probe_workspace["workspace_root"])
                _emit(emit, "status", "识别为 CurseForge 整合包，正在下载缺失文件…")
                return self._prepare_curseforge_workspace(source_path, download_source, emit)

        return self._prepare_generic_zip_workspace(source_path)

    def prepare_mod_scan(self, request: ScanModsRequest, emit) -> PreparedModScanSource:
        source_path = request.source_path.resolve()
        prepared = self._prepare_zip_workspace(source_path, request.download_source, emit)
        mod_scan_root = prepared.get("mod_scan_root") or prepared.get("client_root")
        if not mod_scan_root:
            cleanup_import_workspace(prepared["workspace_root"])
            raise RuntimeError("该 ZIP 中未找到可用于筛模组的 mods 目录或客户端结构。")

        mods_path = mod_scan_root / "mods" if (mod_scan_root / "mods").is_dir() else mod_scan_root
        return PreparedModScanSource(
            source_kind="zip",
            display_path=request.source_path,
            workspace_root=prepared["workspace_root"],
            mods_path=mods_path,
            report_root=request.source_path.parent.resolve(),
            allow_file_move=True,
            cleanup=_prepare_workspace_cleanup(prepared["workspace_root"]),
            metadata={**prepared.get("metadata", {}), "export_all_categories": True},
        )

    def prepare_server_build(self, request: BuildServerRequest, emit) -> PreparedServerSource:
        source_path = request.source_path.resolve()
        prepared = self._prepare_zip_workspace(source_path, request.download_source, emit)
        client_root = prepared.get("client_root")
        if not client_root:
            cleanup_import_workspace(prepared["workspace_root"])
            raise RuntimeError("该 ZIP 中未找到可用于一键开服的客户端结构。")
        return PreparedServerSource(
            source_kind="zip",
            display_path=request.source_path,
            workspace_root=prepared["workspace_root"],
            client_dir=client_root,
            cleanup=_prepare_workspace_cleanup(prepared["workspace_root"]),
            metadata=prepared.get("metadata", {}),
            version_candidates=prepared.get("version_candidates", []),
        )
