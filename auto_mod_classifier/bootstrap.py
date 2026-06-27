from dataclasses import dataclass

from .application.contracts import ModScanService, ServerBuildService, SourceImporter
from .application.importers import ImporterRegistry
from .application.use_cases import BuildServerUseCase, ScanModsUseCase
from .infrastructure.importers import DirectorySourceImporter, MrpackSourceImporter, ZipModpackSourceImporter
from .infrastructure.legacy_services import LegacyModScanService, LegacyServerBuildService


@dataclass
class AppContainer:
    """应用层容器：把当前前端会用到的用例对象集中放在一起。"""

    scan_mods_use_case: ScanModsUseCase
    build_server_use_case: BuildServerUseCase


def create_container(
    importers: list[SourceImporter],
    mod_scan_service: ModScanService,
    server_build_service: ServerBuildService,
) -> AppContainer:
    """统一装配应用层依赖，方便替换前端、导入器和服务实现。"""

    importer_registry = ImporterRegistry(importers)
    return AppContainer(
        scan_mods_use_case=ScanModsUseCase(importer_registry, mod_scan_service),
        build_server_use_case=BuildServerUseCase(importer_registry, server_build_service),
    )


def create_default_container() -> AppContainer:
    """桌面版当前使用的默认容器。"""

    return create_container(
        [
            DirectorySourceImporter(),
            MrpackSourceImporter(),
            ZipModpackSourceImporter(),
        ],
        LegacyModScanService(),
        LegacyServerBuildService(),
    )
