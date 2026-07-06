import atexit
import csv
import concurrent.futures
import difflib
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    HAS_DRISSIONPAGE = True
except ImportError:
    HAS_DRISSIONPAGE = False
    ChromiumPage = None
    ChromiumOptions = None

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


APP_TITLE = "自动筛选模组分类器 3.01"
USER_AGENT = "AutoModClassifier/3.01 (+Codex)"
SYSTEM_ENCODING = locale.getpreferredencoding(False) or "utf-8"
SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
TOOL_DIR_NAME = "_自动筛选模组分类器"
OFFLINE_DB_FILE_NAME = "db.sqlite"
MOD_REPORT_BASENAME = "模组筛选报告"
CONFIG_COPY_SUMMARY_NAME = "目录复制摘要.json"
BUILD_LOG_NAME = "制作日志.txt"
INSTALL_LOG_NAME = "安装阶段日志.txt"
SERVER_FAILURE_SNIPPET_NAME = "启动失败关键信息.txt"
IMPORT_CACHE_DIR_NAME = "_auto_mod_classifier_import_cache"
DOWNLOAD_SOURCE_SMART = "smart"
DOWNLOAD_SOURCE_OFFICIAL = "official"
DOWNLOAD_SOURCE_BMCLAPI = "bmclapi"
DOWNLOAD_SOURCE_MCIM = "mcim"
DOWNLOAD_SOURCE_DOMESTIC = "domestic"
DOWNLOAD_SOURCE_LABELS = {
    DOWNLOAD_SOURCE_SMART: "智能优选",
    DOWNLOAD_SOURCE_OFFICIAL: "官方源",
    DOWNLOAD_SOURCE_BMCLAPI: "BMCLAPI 优先",
    DOWNLOAD_SOURCE_MCIM: "MCIM 优先",
    DOWNLOAD_SOURCE_DOMESTIC: "国内镜像",
}
DOWNLOAD_SOURCE_OPTIONS = [
    (DOWNLOAD_SOURCE_SMART, "智能优选"),
    (DOWNLOAD_SOURCE_OFFICIAL, "官方源"),
    (DOWNLOAD_SOURCE_BMCLAPI, "BMCLAPI 优先"),
    (DOWNLOAD_SOURCE_MCIM, "MCIM 优先"),
]
SERVER_BOOT_TIMEOUT_STRICT = "strict"
SERVER_BOOT_TIMEOUT_SMART = "smart"
SERVER_BOOT_TIMEOUT_MODE_LABELS = {
    SERVER_BOOT_TIMEOUT_STRICT: "固定 90 秒",
    SERVER_BOOT_TIMEOUT_SMART: "智能等待",
}
SERVER_BOOT_TIMEOUT_MODE_OPTIONS = [
    (SERVER_BOOT_TIMEOUT_SMART, "智能等待"),
    (SERVER_BOOT_TIMEOUT_STRICT, "固定 90 秒"),
]
DEFAULT_SERVER_TIMEOUT_SECONDS = 90
DEFAULT_SERVER_INIT_MAX_TIMEOUT_SECONDS = 240
DEFAULT_SERVER_VERIFY_MAX_TIMEOUT_SECONDS = 480
DEFAULT_INSTALL_TIMEOUT_SECONDS = 900
DEFAULT_CLASSIFICATION_WORKERS = 10
DEFAULT_MCMOD_WORKERS = 3
DEFAULT_CF_WORKERS = 5
LOADER_SEARCH_TOKENS = {
    "fabric",
    "quilt",
    "forge",
    "neoforge",
}
GENERIC_QUERY_TOKENS = {
    *LOADER_SEARCH_TOKENS,
    "minecraft",
    "mod",
    "mods",
}
LIBRARY_SUFFIX_TERMS = {
    "api",
    "lib",
    "libs",
    "library",
    "libraries",
    "mod",
}
CLIENT_ENTRYPOINTS = {
    "client",
    "modmenu",
    "rei_client",
    "emi",
    "jei_mod_plugin",
    "jade",
    "journeymap",
    "waila",
}
CLIENT_ENTRYPOINT_TOKEN_HINTS = {
    "client",
    "emi",
    "jei",
    "plugin",
    "rei",
    "modmenu",
    "jade",
    "waila",
    "journeymap",
}
DEFAULT_SKIP_DIRS = {
    "mods",
    "logs",
    "resourcepacks",
    "schematics",
    "screenshots",
    "shaderpacks",
    "syncmatics",
    "save",
    "saves",
    "assets",
    "libraries",
    "versions",
    "runtime",
    "java",
    "downloads",
    "crash-reports",
}
CATEGORY_LABELS = {
    "server-keep": "服务端保留",
    "client-only": "纯客户端",
    "unknown": "待人工确认",
}
CATEGORY_SORT_ORDER = {
    "unknown": 0,
    "server-keep": 1,
    "client-only": 2,
}
CSV_COLUMN_LABELS = {
    "Path": "文件路径",
    "FileName": "文件名",
    "Loader": "加载器",
    "MetadataSource": "元数据来源",
    "ModId": "模组ID",
    "ModName": "模组名称",
    "Environment": "运行环境",
    "Entrypoints": "入口点",
    "Category": "分类结果",
    "DecisionSource": "判定来源",
    "Reason": "判定原因",
    "EvidenceUrl": "证据链接",
    "FinalPath": "最终路径",
    "SelectedForServer": "已选择复制到服务端",
    "JarStatus": "Jar状态",
    "JarIssue": "Jar异常",
}
DECISION_SOURCE_LABELS = {
    "local": "本地元数据",
    "offline-db": "本地离线库",
    "modrinth": "Modrinth",
    "mcmod": "MC百科",
    "curseforge": "CurseForge",
    "error": "处理异常",
}


def get_application_dir() -> Path:
    """返回源码运行目录或打包后 exe 所在目录。"""
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
    return base.resolve().parent


def get_optional_offline_db_path() -> Path:
    """离线库放在程序旁边，便于独立下载和替换更新。"""
    return get_application_dir() / OFFLINE_DB_FILE_NAME


def get_category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def get_decision_source_label(source: str) -> str:
    return DECISION_SOURCE_LABELS.get(source, source)


def write_csv_with_labels(file_path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else ["FileName"]
    with file_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow([CSV_COLUMN_LABELS.get(name, name) for name in fieldnames])
        for row in rows:
            values = []
            for name in fieldnames:
                value = row.get(name, "")
                if name == "DecisionSource":
                    value = get_decision_source_label(str(value))
                values.append(value)
            writer.writerow(values)


class LoaderType(str, Enum):
    FABRIC = "fabric"
    QUILT = "quilt"
    FORGE = "forge"
    NEOFORGE = "neoforge"
    UNKNOWN = "unknown"


class TaskStage(str, Enum):
    PRECHECK = "预检查"
    CLIENT_SCAN = "识别客户端"
    DOWNLOAD_INSTALLER = "下载安装器"
    INSTALL_SERVER = "安装服务端"
    CLASSIFY_MODS = "筛选模组"
    COPY_MODS = "复制模组"
    COPY_CONFIGS = "复制配置目录"
    PREPARE_LAUNCH = "生成启动脚本"
    FIRST_BOOT = "首次启动"
    PATCH_CONFIG = "修正服务器配置"
    VERIFY_BOOT = "验证启动"
    COMPLETE = "完成"


@dataclass
class ModMeta:
    file_name: str
    file_path: str
    mod_id: str
    mod_name: str
    description: str
    environment: str
    entrypoints: List[str]
    depends: List[str]
    loader: str
    metadata_source: str
    query_tokens: List[str]
    client_side_only: bool = False
    dependency_sides: List[str] = field(default_factory=list)
    jar_status: str = "normal"
    jar_issue: str = ""


@dataclass
class Classification:
    category: str
    source: str
    reason: str
    evidence_url: str = ""


@dataclass
class VersionCandidate:
    version_id: str
    minecraft_version: str
    loader: str
    loader_version: str
    java_major: int
    json_path: Path

    @property
    def display_name(self) -> str:
        return f"{self.minecraft_version} | {self.loader} | {self.loader_version}"


@dataclass
class InstallerSpec:
    loader: str
    minecraft_version: str
    loader_version: str
    installer_version: str
    download_url: str
    file_name: str


@dataclass
class JavaRuntime:
    path: Path
    major: int
    source: str
    version_text: str
    is_64bit: bool

    @property
    def summary(self) -> str:
        arch = "64-bit" if self.is_64bit else "32-bit"
        return f"Java {self.major} ({arch}) | {self.version_text} | {self.path}"


@dataclass
class ReviewItem:
    key: str
    label: str
    detail: str
    checked: bool = True
    enabled: bool = True


@dataclass
class LaunchScripts:
    user_script: Path
    internal_script: Path


@dataclass
class ModTaskOptions:
    """Mod 筛选任务的固定入参。"""

    mods_path: Path
    download_source: str
    dry_run: bool
    use_mcmod: bool
    use_curseforge_api: bool
    use_curseforge: bool
    use_offline_database: bool
    auto_update_offline_database: bool
    enable_second_pass: bool
    output_dir: Optional[Path] = None


@dataclass
class ServerTaskOptions:
    """一键开服任务的固定入参。"""

    client_dir: Path
    output_dir: Path
    download_source: str
    use_mcmod: bool
    use_curseforge_api: bool
    use_curseforge: bool
    use_offline_database: bool
    auto_update_offline_database: bool
    enable_second_pass: bool
    auto_download_java: bool = True
    boot_timeout_mode: str = SERVER_BOOT_TIMEOUT_SMART


def get_classification_worker_count(total: int) -> int:
    return min(DEFAULT_CLASSIFICATION_WORKERS, max(1, total))


def get_mcmod_worker_count(total: int) -> int:
    return min(DEFAULT_MCMOD_WORKERS, max(1, total))


def build_mod_result_row(jar_path: Path, meta: ModMeta, classification: Classification) -> Dict[str, Any]:
    return {
        "Path": jar_path,
        "FileName": meta.file_name,
        "Loader": meta.loader,
        "MetadataSource": meta.metadata_source,
        "ModId": meta.mod_id,
        "ModName": meta.mod_name,
        "Environment": meta.environment,
        "Entrypoints": ",".join(meta.entrypoints),
        "Category": classification.category,
        "DecisionSource": classification.source,
        "Reason": classification.reason,
        "EvidenceUrl": classification.evidence_url,
        "JarStatus": meta.jar_status,
        "JarIssue": meta.jar_issue,
    }


def build_mod_error_row(jar_path: Path, reason: str) -> Dict[str, Any]:
    return {
        "Path": jar_path,
        "FileName": jar_path.name,
        "Loader": LoaderType.UNKNOWN.value,
        "MetadataSource": "error",
        "ModId": "",
        "ModName": jar_path.stem,
        "Environment": "",
        "Entrypoints": "",
        "Category": "unknown",
        "DecisionSource": "error",
        "Reason": reason,
        "EvidenceUrl": "",
        "JarStatus": "error",
        "JarIssue": "",
    }


