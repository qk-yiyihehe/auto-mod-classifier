from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional


TaskEmitter = Callable[[str, Any], None]
CleanupCallback = Callable[[], None]


def _noop_cleanup() -> None:
    """默认清理函数，占位用。"""
    return None


@dataclass
class ScanModsRequest:
    """模组筛选任务的统一入参。"""

    source_path: Path
    download_source: str
    dry_run: bool
    use_mcmod: bool
    use_curseforge: bool
    enable_second_pass: bool


@dataclass
class BuildServerRequest:
    """一键开服任务的统一入参。"""

    source_path: Path
    output_dir: Path
    download_source: str
    use_mcmod: bool
    use_curseforge: bool
    enable_second_pass: bool


@dataclass
class PreparedSource:
    """导入器整理后的标准输入对象。"""

    source_kind: str
    display_path: Path
    workspace_root: Path
    cleanup: CleanupCallback = _noop_cleanup
    metadata: Dict[str, Any] = field(default_factory=dict)

    def dispose(self) -> None:
        """任务结束后的统一收尾入口。"""
        self.cleanup()


@dataclass
class PreparedModScanSource(PreparedSource):
    """模组筛选阶段真正会用到的输入信息。"""

    mods_path: Path = field(default_factory=lambda: Path("."))
    report_root: Path = field(default_factory=lambda: Path("."))
    allow_file_move: bool = True


@dataclass
class PreparedServerSource(PreparedSource):
    """一键开服阶段真正会用到的输入信息。"""

    client_dir: Path = field(default_factory=lambda: Path("."))
    version_candidates: list[Any] = field(default_factory=list)
