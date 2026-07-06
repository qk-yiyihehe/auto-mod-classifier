from .contracts import SourceImporter
from .models import BuildServerRequest, PreparedModScanSource, PreparedServerSource, ScanModsRequest


class ImporterRegistry:
    """统一选择输入源导入器，后续扩展 mrpack/zip 只需要新增 importer。"""

    def __init__(self, importers: list[SourceImporter]):
        self.importers = importers

    def _find_importer(self, source_path):
        """按顺序找到第一个能处理当前输入的导入器。"""
        for importer in self.importers:
            if importer.supports(source_path):
                return importer
        if source_path.exists() and source_path.is_file():
            raise RuntimeError(f"暂不支持该文件类型：{source_path.suffix or '无后缀'}。目前只支持目录、.zip 和 .mrpack。")
        raise RuntimeError(f"暂不支持该输入源：{source_path}")

    def prepare_mod_scan(self, request: ScanModsRequest, emit) -> PreparedModScanSource:
        importer = self._find_importer(request.source_path)
        return importer.prepare_mod_scan(request, emit)

    def prepare_server_build(self, request: BuildServerRequest, emit) -> PreparedServerSource:
        importer = self._find_importer(request.source_path)
        return importer.prepare_server_build(request, emit)
