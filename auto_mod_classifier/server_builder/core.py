from ..shared import *
from ..classifier import ClassifierCore
from .common import ServerBuilderCommonService
from .context import ServerBuilderRuntime
from .services import (
    ServerInstallService,
    ServerJavaService,
    ServerLaunchService,
    ServerModService,
    ServerReportingService,
    ServerVersionService,
    ServerWorkflowService,
)


class ServerBuilderCore:
    """一键开服总入口。自己不做重活，只负责把各个服务组装起来。"""

    def __init__(
        self,
        classifier: ClassifierCore,
        log: Callable[[str], None],
        set_status: Callable[[str], None],
        set_progress: Callable[[float], None],
        set_download_status: Callable[[str], None],
        emit_stage: Callable[[str, str], None],
        request_version_choice: Callable[[List[VersionCandidate]], Optional[VersionCandidate]],
        request_checklist: Callable[[str, str, List[ReviewItem]], Optional[List[str]]],
        request_continue_wait: Callable[[str, str, int], bool],
        download_source: str,
        use_mcmod: bool,
        use_offline_database: bool,
        enable_second_pass: bool,
        auto_download_java: bool,
        boot_timeout_mode: str,
        java_selection_mode: str = JAVA_SELECTION_AUTO,
        prepared_version_candidates: Optional[List[VersionCandidate]] = None,
    ):
        # core 现在更像“装配中心”，而不是过去那种什么都自己干的大对象。
        self.runtime = ServerBuilderRuntime(
            classifier=classifier,
            log=log,
            set_status=set_status,
            set_progress=set_progress,
            set_download_status=set_download_status,
            emit_stage=emit_stage,
            request_version_choice=request_version_choice,
            request_checklist=request_checklist,
            request_continue_wait=request_continue_wait,
            download_source=download_source,
            use_mcmod=use_mcmod,
            use_offline_database=use_offline_database,
            enable_second_pass=enable_second_pass,
            auto_download_java=auto_download_java,
            boot_timeout_mode=boot_timeout_mode,
            java_selection_mode=java_selection_mode,
            prepared_version_candidates=list(prepared_version_candidates or []),
        )
        self.common = ServerBuilderCommonService(self.runtime)
        self.versioning = ServerVersionService(self.runtime, self.common)
        self.java = ServerJavaService(self.common)
        self.install = ServerInstallService(self.runtime, self.common)
        self.mods = ServerModService(self.common)
        self.launch = ServerLaunchService(self.runtime, self.common)
        self.reporting = ServerReportingService(self.runtime)
        self.workflow = ServerWorkflowService(
            self.runtime,
            self.common,
            self.versioning,
            self.java,
            self.install,
            self.mods,
            self.launch,
            self.reporting,
        )

    def build_server(self, client_dir: Path, output_root: Path) -> Dict[str, Path]:
        return self.workflow.build_server(client_dir, output_root)

    def cancel(self) -> None:
        self.runtime.request_cancel()
        try:
            self.runtime.classifier.close_browser()
        except Exception:
            pass
