from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..classifier import ClassifierCore
from ..shared import ReviewItem, VersionCandidate


@dataclass
class ServerBuilderRuntime:
    """一键开服流程的共享运行时状态。"""

    classifier: ClassifierCore
    log: Callable[[str], None]
    set_status: Callable[[str], None]
    set_progress: Callable[[float], None]
    set_download_status: Callable[[str], None]
    emit_stage: Callable[[str, str], None]
    request_version_choice: Callable[[List[VersionCandidate]], Optional[VersionCandidate]]
    request_checklist: Callable[[str, str, List[ReviewItem]], Optional[List[str]]]
    request_continue_wait: Callable[[str, str, int], bool]
    download_source: str
    use_mcmod: bool
    use_offline_database: bool
    enable_second_pass: bool
    auto_download_java: bool
    boot_timeout_mode: str
    prepared_version_candidates: List[VersionCandidate] = field(default_factory=list)
    # 下面这些是多个服务会共用的运行期状态，所以集中放在这里。
    network_cache: Dict[str, Any] = field(default_factory=dict)
    build_log_lines: List[str] = field(default_factory=list)
    install_log_lines: List[str] = field(default_factory=list)
