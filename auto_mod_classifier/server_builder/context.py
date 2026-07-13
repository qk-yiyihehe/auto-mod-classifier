import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..classifier import ClassifierCore
from ..shared import SUBPROCESS_CREATIONFLAGS, ReviewItem, VersionCandidate


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
    java_selection_mode: str
    prepared_version_candidates: List[VersionCandidate] = field(default_factory=list)
    # 下面这些是多个服务会共用的运行期状态，所以集中放在这里。
    network_cache: Dict[str, Any] = field(default_factory=dict)
    build_log_lines: List[str] = field(default_factory=list)
    install_log_lines: List[str] = field(default_factory=list)
    _cancel_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _process_lock: Any = field(default_factory=threading.Lock, init=False, repr=False)
    _active_process: Optional[subprocess.Popen[Any]] = field(default=None, init=False, repr=False)

    def request_cancel(self) -> None:
        self._cancel_event.set()
        with self._process_lock:
            process = self._active_process
            self._active_process = None
        if process is not None:
            self._terminate_process_tree(process)

    def raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise RuntimeError("任务已取消。")

    def set_active_process(self, process: subprocess.Popen[Any]) -> None:
        with self._process_lock:
            if self._cancel_event.is_set():
                should_terminate = True
            else:
                self._active_process = process
                should_terminate = False
        if should_terminate:
            self._terminate_process_tree(process)

    def clear_active_process(self, process: subprocess.Popen[Any]) -> None:
        with self._process_lock:
            if self._active_process is process:
                self._active_process = None

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                if process.poll() is None:
                    process.wait(timeout=2)
            else:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
