from typing import Any, Callable, List, Optional

from .application import BuildServerRequest, ScanModsRequest
from .bootstrap import AppContainer, create_default_container
from .shared import ModTaskOptions, ReviewItem, ServerTaskOptions, VersionCandidate


def _build_scan_request(options: ModTaskOptions) -> ScanModsRequest:
    """把桌面前端入参转换为应用层请求。"""

    return ScanModsRequest(
        source_path=options.mods_path,
        output_dir=options.output_dir,
        download_source=options.download_source,
        dry_run=options.dry_run,
        use_mcmod=options.use_mcmod,
        use_curseforge=options.use_curseforge,
        use_curseforge_api=options.use_curseforge_api,
        use_offline_database=options.use_offline_database,
        auto_update_offline_database=options.auto_update_offline_database,
        enable_second_pass=options.enable_second_pass,
    )


def _build_server_request(options: ServerTaskOptions) -> BuildServerRequest:
    """把桌面前端入参转换为应用层请求。"""

    return BuildServerRequest(
        source_path=options.client_dir,
        output_dir=options.output_dir,
        download_source=options.download_source,
        use_mcmod=options.use_mcmod,
        use_curseforge=options.use_curseforge,
        use_curseforge_api=options.use_curseforge_api,
        use_offline_database=options.use_offline_database,
        auto_update_offline_database=options.auto_update_offline_database,
        enable_second_pass=options.enable_second_pass,
        auto_download_java=options.auto_download_java,
        boot_timeout_mode=options.boot_timeout_mode,
        java_selection_mode=options.java_selection_mode,
    )


def run_mod_task(
    options: ModTaskOptions,
    emit: Callable[[str, Any], None],
    set_runtime_ref: Callable[[Any], None],
    container: Optional[AppContainer] = None,
) -> None:
    # tasks 这一层只负责“转接”，不再直接写业务逻辑。
    active_container = container or create_default_container()
    request = _build_scan_request(options)
    active_container.scan_mods_use_case.execute(request, emit, set_runtime_ref)


def run_server_task(
    options: ServerTaskOptions,
    emit: Callable[[str, Any], None],
    set_runtime_ref: Callable[[Any], None],
    request_version_choice: Callable[[List[VersionCandidate]], Optional[VersionCandidate]],
    request_checklist: Callable[[str, str, List[ReviewItem]], Optional[List[str]]],
    request_continue_wait: Callable[[str, str, int], bool],
    container: Optional[AppContainer] = None,
) -> None:
    # 这样做的好处是：以后换 Tk、Web、CLI 前端，任务入口都能复用。
    active_container = container or create_default_container()
    request = _build_server_request(options)
    active_container.build_server_use_case.execute(
        request,
        emit,
        set_runtime_ref,
        request_version_choice,
        request_checklist,
        request_continue_wait,
    )
