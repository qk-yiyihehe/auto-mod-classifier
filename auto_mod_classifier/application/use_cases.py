from typing import Any, Callable, Optional

from .contracts import ModScanService, ServerBuildService, SourceImporterRegistry
from .models import BuildServerRequest, ScanModsRequest, TaskEmitter


class ScanModsUseCase:
    """前端无关的模组筛选用例。"""

    def __init__(self, importer_registry: SourceImporterRegistry, mod_scan_service: ModScanService):
        self.importer_registry = importer_registry
        self.mod_scan_service = mod_scan_service

    def execute(
        self,
        request: ScanModsRequest,
        emit: TaskEmitter,
        set_runtime_ref: Callable[[Any], None],
    ) -> None:
        # 这里先把输入整理成统一格式，再交给真正的筛选服务。
        source = self.importer_registry.prepare_mod_scan(request)
        try:
            self.mod_scan_service.run(source, request, emit, set_runtime_ref)
        finally:
            source.dispose()


class BuildServerUseCase:
    """前端无关的一键开服用例。"""

    def __init__(self, importer_registry: SourceImporterRegistry, server_build_service: ServerBuildService):
        self.importer_registry = importer_registry
        self.server_build_service = server_build_service

    def execute(
        self,
        request: BuildServerRequest,
        emit: TaskEmitter,
        set_runtime_ref: Callable[[Any], None],
        request_version_choice: Callable[[list], Optional[Any]],
        request_checklist: Callable[[str, str, list], Optional[list]],
    ) -> None:
        # 一键开服同样先走输入整理，这样后面才能轻松支持目录、mrpack、zip 等来源。
        source = self.importer_registry.prepare_server_build(request)
        try:
            self.server_build_service.run(
                source,
                request,
                emit,
                set_runtime_ref,
                request_version_choice,
                request_checklist,
            )
        finally:
            source.dispose()
