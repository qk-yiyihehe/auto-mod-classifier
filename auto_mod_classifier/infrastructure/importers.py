from pathlib import Path

from ..application.models import BuildServerRequest, PreparedModScanSource, PreparedServerSource, ScanModsRequest


class DirectorySourceImporter:
    """当前唯一完整实现的导入器：本地目录。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.exists() and source_path.is_dir()

    def prepare_mod_scan(self, request: ScanModsRequest) -> PreparedModScanSource:
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

    def prepare_server_build(self, request: BuildServerRequest) -> PreparedServerSource:
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
    """为后续 mrpack 支持预留导入器接口。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.lower() == ".mrpack"

    def prepare_mod_scan(self, request: ScanModsRequest) -> PreparedModScanSource:
        raise NotImplementedError("MRPACK 导入器接口已预留，后续可直接在该模块中实现解析与下载。")

    def prepare_server_build(self, request: BuildServerRequest) -> PreparedServerSource:
        raise NotImplementedError("MRPACK 导入器接口已预留，后续可直接在该模块中实现解析与下载。")


class ZipModpackSourceImporter:
    """为后续整合包 zip 支持预留导入器接口。"""

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.lower() == ".zip"

    def prepare_mod_scan(self, request: ScanModsRequest) -> PreparedModScanSource:
        raise NotImplementedError("ZIP 整合包导入器接口已预留，后续可直接在该模块中实现解压与实例识别。")

    def prepare_server_build(self, request: BuildServerRequest) -> PreparedServerSource:
        raise NotImplementedError("ZIP 整合包导入器接口已预留，后续可直接在该模块中实现解压与实例识别。")
