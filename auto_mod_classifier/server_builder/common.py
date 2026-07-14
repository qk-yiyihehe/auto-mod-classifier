from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from ..download_support import DownloadStatsReporter, http_download, http_get_json, http_get_text, http_probe
from ..shared import *
from .context import ServerBuilderRuntime


class ServerBuilderCommonService:
    """一键开服通用工具与共享操作。"""

    STAGE_EVENT_MAP = {
        TaskStage.PRECHECK: "precheck",
        TaskStage.CLIENT_SCAN: "scan",
        TaskStage.DOWNLOAD_INSTALLER: "installer",
        TaskStage.INSTALL_SERVER: "install",
        TaskStage.CLASSIFY_MODS: "classify",
        TaskStage.COPY_MODS: "classify",
        TaskStage.COPY_CONFIGS: "classify",
        TaskStage.PREPARE_LAUNCH: "verify",
        TaskStage.FIRST_BOOT: "verify",
        TaskStage.PATCH_CONFIG: "verify",
        TaskStage.VERIFY_BOOT: "verify",
        TaskStage.COMPLETE: "verify",
    }

    def __init__(self, runtime: ServerBuilderRuntime):
        self.runtime = runtime

    def log_line(self, message: str) -> None:
        # 构建日志统一从这里走，避免每个服务自己维护日志状态。
        self.runtime.build_log_lines.append(message)
        self.runtime.log(message)

    def set_stage(self, stage: TaskStage, progress: float, detail: str) -> None:
        # 阶段更新也统一收口，界面和日志会一起同步。
        self.runtime.raise_if_cancelled()
        self.runtime.set_progress(progress)
        stage_key = self.STAGE_EVENT_MAP.get(stage)
        if stage_key:
            self.runtime.emit_stage(stage_key, detail)
        self.runtime.set_status(f"{stage.value}：{detail}")
        self.log_line(f"[{stage.value}] {detail}")

    def http_get_text(self, url: str) -> str:
        cache_key = f"text::{url}"
        if cache_key in self.runtime.network_cache:
            return self.runtime.network_cache[cache_key]
        text = http_get_text(url, self.runtime.download_source, timeout=10, retry_rounds=2)
        self.runtime.network_cache[cache_key] = text
        return text

    def http_get_json(self, url: str) -> Any:
        cache_key = f"json::{url}"
        if cache_key in self.runtime.network_cache:
            return self.runtime.network_cache[cache_key]
        data = http_get_json(url, self.runtime.download_source, timeout=10, retry_rounds=2)
        self.runtime.network_cache[cache_key] = data
        return data

    def http_probe(self, url: str) -> bool:
        cache_key = f"probe::{url}"
        if cache_key in self.runtime.network_cache:
            return bool(self.runtime.network_cache[cache_key])
        available = http_probe(url, self.runtime.download_source, timeout=8, retry_rounds=1)
        self.runtime.network_cache[cache_key] = available
        return available

    def http_download(
        self,
        url: str,
        destination: Path,
        reporter: Optional[DownloadStatsReporter] = None,
        display_name: Optional[str] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        *,
        timeout: int = 15,
        retry_rounds: int = 2,
        minimum_speed_bytes: int = 0,
    ) -> None:
        http_download(
            url,
            destination,
            self.runtime.download_source,
            reporter=reporter,
            timeout=timeout,
            display_name=display_name,
            log_callback=log_callback,
            retry_rounds=retry_rounds,
            cancel_check=self.runtime.raise_if_cancelled,
            minimum_speed_bytes=minimum_speed_bytes,
        )

    def get_application_dir(self) -> Path:
        base = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        return base.resolve().parent

    def parse_release_version(self, version_text: str) -> Optional[Tuple[int, int, int]]:
        cleaned = str(version_text or "").strip()
        match = re.match(r"^\D*(\d+)\.(\d+)(?:\.(\d+))?", cleaned)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)

    def natural_sort_key(self, value: str) -> Tuple[Tuple[int, object], ...]:
        parts = re.findall(r"\d+|[a-z]+", str(value or "").lower())
        if not parts:
            return ((1, ""),)
        key = []
        for part in parts:
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return tuple(key)

    def version_candidate_sort_key(self, candidate: VersionCandidate) -> Tuple[Tuple[Tuple[int, object], ...], ...]:
        return (
            self.natural_sort_key(candidate.minecraft_version),
            self.natural_sort_key(candidate.loader),
            self.natural_sort_key(candidate.loader_version),
            self.natural_sort_key(candidate.version_id),
        )

    def is_same_or_nested_path(self, base_path: Path, candidate_path: Path) -> bool:
        try:
            candidate_path.relative_to(base_path)
            return True
        except ValueError:
            return False
