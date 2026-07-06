import inspect
import traceback
from typing import Any, Callable, Optional

from .contracts import ModScanService, ServerBuildService, SourceImporterRegistry
from .models import BuildServerRequest, ScanModsRequest, TaskEmitter


def _wrap_progress_emit(emit: TaskEmitter, progress_offset: int) -> TaskEmitter:
    """给后续正式流程预留一段进度，避免整合包下载后进度条回退到 0。"""
    if progress_offset <= 0:
        return emit

    progress_offset = max(0, min(progress_offset, 95))
    progress_span = 100 - progress_offset

    def _wrapped(kind: str, payload: Any) -> None:
        if kind != "progress":
            emit(kind, payload)
            return
        try:
            raw_value = float(payload)
        except Exception:
            emit(kind, payload)
            return
        normalized = max(0.0, min(100.0, raw_value))
        emit(kind, progress_offset + normalized * progress_span / 100.0)

    return _wrapped


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
        try:
            source = self.importer_registry.prepare_mod_scan(request, emit)
        except Exception as exc:
            emit("log", traceback.format_exc())
            emit("error", str(exc))
            return
        try:
            metadata = source.metadata if isinstance(source.metadata, dict) else {}
            progress_offset = int(metadata.get("service_progress_offset", 0))
            service_emit = _wrap_progress_emit(emit, progress_offset)
            self.mod_scan_service.run(source, request, service_emit, set_runtime_ref)
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
        request_continue_wait: Optional[Callable[[str, str, int], bool]] = None,
    ) -> None:
        # 一键开服同样先走输入整理，这样后面才能轻松支持目录、mrpack、zip 等来源。
        emit("stage", {"stage_key": "scan", "detail": "正在准备一键开服输入源"})
        emit("status", "正在准备一键开服输入源…")
        emit("log", f"开始准备一键开服输入源：{request.source_path}")
        try:
            source = self.importer_registry.prepare_server_build(request, emit)
        except Exception as exc:
            emit("log", traceback.format_exc())
            emit("error", str(exc))
            return
        try:
            metadata = source.metadata if isinstance(source.metadata, dict) else {}
            progress_offset = int(metadata.get("service_progress_offset", 0))
            emit("log", f"输入源准备完成：{source.client_dir}")
            if source.version_candidates:
                emit("log", f"预解析到 {len(source.version_candidates)} 个版本候选。")
            emit("stage", {"stage_key": "scan", "detail": "输入源准备完成，正在启动制作流程"})
            emit("status", "输入源准备完成，正在启动制作流程…")
            service_emit = _wrap_progress_emit(emit, progress_offset)
            # 兼容旧服务签名：早期实现没有 request_continue_wait 回调。
            run_signature = inspect.signature(self.server_build_service.run)
            if "request_continue_wait" in run_signature.parameters:
                continue_wait = request_continue_wait or (lambda _title, _message, _seconds: True)
                self.server_build_service.run(
                    source,
                    request,
                    service_emit,
                    set_runtime_ref,
                    request_version_choice,
                    request_checklist,
                    continue_wait,
                )
            else:
                self.server_build_service.run(
                    source,
                    request,
                    service_emit,
                    set_runtime_ref,
                    request_version_choice,
                    request_checklist,
                )
        finally:
            source.dispose()
