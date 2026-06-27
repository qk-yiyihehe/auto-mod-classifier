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
        request_version_choice: Callable[[List[VersionCandidate]], Optional[VersionCandidate]],
        request_checklist: Callable[[str, str, List[ReviewItem]], Optional[List[str]]],
        use_mcmod: bool,
        enable_second_pass: bool,
    ):
        # core 现在更像“装配中心”，而不是过去那种什么都自己干的大对象。
        self.runtime = ServerBuilderRuntime(
            classifier=classifier,
            log=log,
            set_status=set_status,
            set_progress=set_progress,
            request_version_choice=request_version_choice,
            request_checklist=request_checklist,
            use_mcmod=use_mcmod,
            enable_second_pass=enable_second_pass,
        )
        self.common = ServerBuilderCommonService(self.runtime)
        self.versioning = ServerVersionService(self.runtime, self.common)
        self.java = ServerJavaService(self.common)
        self.install = ServerInstallService(self.runtime, self.common)
        self.mods = ServerModService()
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
