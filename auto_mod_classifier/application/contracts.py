from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .models import BuildServerRequest, PreparedModScanSource, PreparedServerSource, ScanModsRequest, TaskEmitter


class SourceImporter(Protocol):
    """把“用户给的输入路径”整理成后续流程能直接使用的标准输入。"""

    def supports(self, source_path: Path) -> bool:
        ...

    def prepare_mod_scan(self, request: ScanModsRequest, emit: TaskEmitter) -> PreparedModScanSource:
        ...

    def prepare_server_build(self, request: BuildServerRequest, emit: TaskEmitter) -> PreparedServerSource:
        ...


class SourceImporterRegistry(Protocol):
    """输入源选择器接口。"""

    def prepare_mod_scan(self, request: ScanModsRequest, emit: TaskEmitter) -> PreparedModScanSource:
        ...

    def prepare_server_build(self, request: BuildServerRequest, emit: TaskEmitter) -> PreparedServerSource:
        ...


class ModScanService(Protocol):
    """真正执行模组筛选的服务接口。"""

    def run(
        self,
        source: PreparedModScanSource,
        request: ScanModsRequest,
        emit: TaskEmitter,
        set_runtime_ref: Callable[[Any], None],
    ) -> None:
        ...


class ServerBuildService(Protocol):
    """真正执行一键开服的服务接口。"""

    def run(
        self,
        source: PreparedServerSource,
        request: BuildServerRequest,
        emit: TaskEmitter,
        set_runtime_ref: Callable[[Any], None],
        request_version_choice: Callable[[list], Optional[Any]],
        request_checklist: Callable[[str, str, list], Optional[list]],
    ) -> None:
        ...
