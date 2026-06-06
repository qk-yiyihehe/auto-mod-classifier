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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_TITLE = "自动筛选模组分类器 2.08"
USER_AGENT = "AutoModClassifier/2.08 (+Codex)"
SYSTEM_ENCODING = locale.getpreferredencoding(False) or "utf-8"
SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
TOOL_DIR_NAME = "_自动筛选模组分类器"
MOD_REPORT_BASENAME = "模组筛选报告"
CONFIG_COPY_SUMMARY_NAME = "目录复制摘要.json"
BUILD_LOG_NAME = "制作日志.txt"
INSTALL_LOG_NAME = "安装阶段日志.txt"
DEFAULT_SERVER_TIMEOUT_SECONDS = 90
DEFAULT_INSTALL_TIMEOUT_SECONDS = 900
DEFAULT_CLASSIFICATION_WORKERS = 10
DEFAULT_MCMOD_WORKERS = 3
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


def get_category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def write_csv_with_labels(file_path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = list(rows[0].keys()) if rows else ["FileName"]
    with file_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow([CSV_COLUMN_LABELS.get(name, name) for name in fieldnames])
        for row in rows:
            writer.writerow([row.get(name, "") for name in fieldnames])


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
class PanelState:
    status_var: tk.StringVar
    progress_var: tk.DoubleVar
    output_var: tk.StringVar
    log_widget: ScrolledText
    result_dir: Optional[Path] = None
    extra_dir: Optional[Path] = None


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


def classify_jars_parallel(
    classifier: "ClassifierCore",
    jar_files: Sequence[Path],
    use_mcmod: bool,
    progress_callback: Optional[Callable[[int, int, Path], None]] = None,
    result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    total = len(jar_files)
    if total <= 0:
        return []

    worker_count = get_classification_worker_count(total)
    mcmod_worker_count = get_mcmod_worker_count(total)
    results: List[Optional[Dict[str, Any]]] = [None] * total
    completed = 0
    done_event = threading.Event()
    results_lock = threading.Lock()
    remote_lock = threading.Condition()
    active_modrinth = 0
    active_mcmod = 0
    pending_mcmod = 0

    def finish_row(index: int, jar: Path, row: Dict[str, Any]) -> None:
        nonlocal completed
        with results_lock:
            results[index] = row
            completed += 1
            done_count = completed
        if progress_callback:
            progress_callback(done_count, total, jar)
        if result_callback:
            result_callback(done_count, total, jar, row)
        if done_count >= total:
            done_event.set()

    def finish_classification(index: int, jar: Path, meta: ModMeta, classification: Classification) -> None:
        finish_row(index, jar, build_mod_result_row(jar, meta, classification))

    def reserve_mcmod_capacity() -> None:
        nonlocal pending_mcmod
        with remote_lock:
            pending_mcmod += 1
            remote_lock.notify_all()

    def acquire_modrinth_slot() -> None:
        nonlocal active_modrinth
        with remote_lock:
            while True:
                reserved_mcmod = min(DEFAULT_MCMOD_WORKERS, pending_mcmod)
                modrinth_limit = max(1, worker_count - reserved_mcmod)
                if active_modrinth < modrinth_limit and active_modrinth + active_mcmod < worker_count:
                    active_modrinth += 1
                    return
                remote_lock.wait()

    def release_modrinth_slot() -> None:
        nonlocal active_modrinth
        with remote_lock:
            active_modrinth -= 1
            remote_lock.notify_all()

    def acquire_mcmod_slot() -> None:
        nonlocal active_mcmod, pending_mcmod
        with remote_lock:
            while active_mcmod >= DEFAULT_MCMOD_WORKERS or active_modrinth + active_mcmod >= worker_count:
                remote_lock.wait()
            pending_mcmod -= 1
            active_mcmod += 1

    def release_mcmod_slot() -> None:
        nonlocal active_mcmod
        with remote_lock:
            active_mcmod -= 1
            remote_lock.notify_all()

    def run_mcmod(
        index: int,
        jar: Path,
        meta: ModMeta,
        local: Classification,
        remote: Optional[Classification],
    ) -> None:
        try:
            acquire_mcmod_slot()
            try:
                fallback = classifier.mcmod_search(meta)
            finally:
                release_mcmod_slot()

            if fallback and fallback.category != "unknown":
                finish_classification(index, jar, meta, fallback)
            elif remote:
                finish_classification(index, jar, meta, remote)
            else:
                finish_classification(index, jar, meta, local)
        except Exception as exc:
            finish_row(index, jar, build_mod_error_row(jar, str(exc)))

    def run_modrinth(index: int, jar: Path, meta: ModMeta, local: Classification) -> None:
        try:
            acquire_modrinth_slot()
            try:
                remote = classifier.modrinth_search(meta)
            finally:
                release_modrinth_slot()

            if remote and remote.category != "unknown":
                finish_classification(index, jar, meta, remote)
            elif use_mcmod:
                assert mcmod_executor is not None
                reserve_mcmod_capacity()
                mcmod_executor.submit(run_mcmod, index, jar, meta, local, remote)
            elif remote:
                finish_classification(index, jar, meta, remote)
            else:
                finish_classification(index, jar, meta, local)
        except Exception as exc:
            finish_row(index, jar, build_mod_error_row(jar, str(exc)))

    def run_local(index: int, jar: Path) -> None:
        try:
            meta = classifier.get_jar_metadata(jar)
            local = classifier.local_classification(meta)
            if local.category in {"client-only", "server-keep"}:
                finish_classification(index, jar, meta, local)
                return
            modrinth_executor.submit(run_modrinth, index, jar, meta, local)
        except Exception as exc:
            finish_row(index, jar, build_mod_error_row(jar, str(exc)))

    local_executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
    modrinth_executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
    mcmod_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
    if use_mcmod:
        mcmod_executor = concurrent.futures.ThreadPoolExecutor(max_workers=mcmod_worker_count)

    try:
        for index, jar in enumerate(jar_files):
            local_executor.submit(run_local, index, jar)
        done_event.wait()
    finally:
        local_executor.shutdown(wait=True, cancel_futures=False)
        modrinth_executor.shutdown(wait=True, cancel_futures=False)
        if mcmod_executor is not None:
            mcmod_executor.shutdown(wait=True, cancel_futures=False)

    return [row for row in results if row is not None]


def rerun_unknown_classifications(
    rows: List[Dict[str, Any]],
    use_mcmod: bool,
    progress_callback: Optional[Callable[[int, int, Path], None]] = None,
    result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
) -> int:
    unknown_rows = [
        row
        for row in rows
        if row.get("Category") == "unknown" and isinstance(row.get("Path"), Path)
    ]
    if not unknown_rows:
        return 0

    retry_classifier = ClassifierCore()
    retry_results = classify_jars_parallel(
        retry_classifier,
        [row["Path"] for row in unknown_rows],
        use_mcmod,
        progress_callback=progress_callback,
        result_callback=result_callback,
    )
    retry_map = {str(row["Path"]): row for row in retry_results}
    recovered = 0
    for row in unknown_rows:
        retry_row = retry_map.get(str(row["Path"]))
        if retry_row and retry_row["Category"] != "unknown":
            preserved_final_path = row.get("FinalPath")
            preserved_selection = row.get("SelectedForServer")
            row.update(retry_row)
            if preserved_final_path is not None:
                row["FinalPath"] = preserved_final_path
            if preserved_selection is not None:
                row["SelectedForServer"] = preserved_selection
            recovered += 1
    return recovered


class ClassifierCore:
    def __init__(self, throttle_ms: int = 80):
        self.throttle_ms = throttle_ms
        self.cache: Dict[str, object] = {}
        self.cache_lock = threading.Lock()
        self.inflight_requests: Dict[str, threading.Event] = {}
        self.request_lock = threading.Lock()
        self.next_request_at = 0.0
        self.mcmod_request_lock = threading.Lock()
        self.next_mcmod_request_at = 0.0
        self.modrinth_request_lock = threading.Lock()
        self.next_modrinth_request_at = 0.0

    def normalize_text(self, text: str, strip_brackets: bool = True) -> str:
        if not text:
            return ""
        text = text.lower()
        if strip_brackets:
            text = re.sub(r"\[[^\]]+\]", "", text)
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    def clean_filename_token(self, file_name: str) -> str:
        stem = Path(file_name).stem
        stem = re.sub(r"\[[^\]]+\]", " ", stem)
        stem = re.sub(r"【[^】]+】", " ", stem)
        stem = re.sub(r"\b(mc)?1\.\d+(\.\d+)?\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(fabric|forge|quilt|neoforge)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(v?\d+([._+-]\d+)*([a-z]+\d*)?)\b", " ", stem, flags=re.I)
        stem = re.sub(r"[_\-+.]+", " ", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        return stem

    def normalize_match_text(self, text: str, strip_brackets: bool = True, keep_cjk: bool = False) -> str:
        if not text:
            return ""
        text = text.lower()
        if strip_brackets:
            text = re.sub(r"\[[^\]]+\]", "", text)
            text = re.sub(r"【[^】]+】", "", text)
        pattern = r"[^a-z0-9\u4e00-\u9fff]+" if keep_cjk else r"[^a-z0-9]+"
        return re.sub(pattern, "", text)

    def expand_query_token(self, value: str) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        variants: List[str] = []

        def add_variant(item: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(item or "").strip())
            if cleaned and cleaned not in variants:
                variants.append(cleaned)

        add_variant(text)
        separator_split = re.sub(r"[_\-.+/]+", " ", text)
        add_variant(separator_split)
        camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", separator_split)
        camel_split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", camel_split)
        camel_split = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", camel_split)
        camel_split = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", camel_split)
        add_variant(camel_split)
        if camel_split:
            add_variant(camel_split.replace(" ", ""))
        return variants

    def extract_bracket_tokens(self, file_name: str) -> List[str]:
        stem = Path(file_name).stem
        tokens: List[str] = []
        for pattern in (r"\[([^\]]{1,60})\]", r"【([^】]{1,60})】"):
            for match in re.findall(pattern, stem):
                token = re.sub(r"\s+", " ", str(match or "").strip())
                if token and token not in tokens:
                    tokens.append(token)
        return tokens

    def split_words(self, text: str, keep_cjk: bool = False) -> List[str]:
        value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text or "").strip())
        value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
        pattern = r"[a-z0-9\u4e00-\u9fff]+" if keep_cjk else r"[a-z0-9]+"
        return re.findall(pattern, value.lower())

    def normalize_match_word(self, word: str) -> str:
        value = str(word or "").strip().lower()
        if not value:
            return ""
        roman_map = {
            "ii": "2",
            "iii": "3",
            "iv": "4",
            "v": "5",
            "vi": "6",
            "vii": "7",
            "viii": "8",
            "ix": "9",
            "x": "10",
        }
        return roman_map.get(value, value)

    def is_placeholder_value(self, value: str) -> bool:
        text = str(value or "").strip()
        return bool(text and re.fullmatch(r"\$\{[^}]+\}", text))

    def is_generic_query_token(self, value: str) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and text in GENERIC_QUERY_TOKENS)

    def is_meaningful_query_token(self, value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text or self.is_placeholder_value(text) or self.is_generic_query_token(text):
            return False
        return any(char.isalnum() for char in text)

    def collect_search_values(self, meta: ModMeta, query: str = "") -> List[str]:
        values = [
            meta.mod_id,
            meta.mod_name,
            query,
            *self.extract_bracket_tokens(meta.file_name),
            self.clean_filename_token(meta.file_name),
        ]
        tokens: List[str] = []
        for value in values:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip())
            if not self.is_meaningful_query_token(cleaned):
                continue
            if cleaned not in tokens:
                tokens.append(cleaned)
        return tokens

    def extract_name_variants(self, text: str) -> List[str]:
        raw = re.sub(r"\s+", " ", str(text or "").strip())
        if not raw:
            return []

        variants: List[str] = []

        def add_variant(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip(" -|/,:;")).strip()
            if cleaned and cleaned not in variants:
                variants.append(cleaned)

        add_variant(raw)

        bracket_patterns = (
            r"\(([^()]{1,120})\)",
            r"（([^（）]{1,120})）",
            r"\[([^\[\]]{1,120})\]",
            r"【([^【】]{1,120})】",
        )
        for pattern in bracket_patterns:
            for match in re.findall(pattern, raw):
                add_variant(match)

        stripped = raw
        for pattern in bracket_patterns:
            stripped = re.sub(pattern, " ", stripped)
        add_variant(stripped)

        for part in re.split(r"\s*(?:/|\||｜)\s*", raw):
            add_variant(part)

        return variants

    def build_library_base_keys(self, text: str, keep_cjk: bool = False) -> Dict[str, bool]:
        keys: Dict[str, bool] = {}
        for variant in self.extract_name_variants(text):
            words = [
                self.normalize_match_word(word)
                for word in self.split_words(variant, keep_cjk=keep_cjk)
                if self.normalize_match_word(word)
            ]
            if not words:
                continue

            joined = "".join(words)
            if joined and joined not in keys:
                keys[joined] = False

            trimmed = list(words)
            removed_suffix = False
            while len(trimmed) > 1 and trimmed[-1] in LIBRARY_SUFFIX_TERMS:
                trimmed = trimmed[:-1]
                removed_suffix = True
                joined = "".join(trimmed)
                if joined and len(joined) >= 6:
                    keys[joined] = True

            if removed_suffix and len(trimmed) >= 2:
                joined = "".join(trimmed)
                if joined:
                    keys[joined] = True
        return keys

    def score_library_suffix_match(self, left: str, right: str, keep_cjk: bool = False) -> int:
        left_keys = self.build_library_base_keys(left, keep_cjk=keep_cjk)
        right_keys = self.build_library_base_keys(right, keep_cjk=keep_cjk)
        best = 0
        for key, left_relaxed in left_keys.items():
            right_relaxed = right_keys.get(key)
            if right_relaxed is None or len(key) < 6 or not (left_relaxed or right_relaxed):
                continue
            best = max(best, 126 if len(key) >= 10 else 104)
        return best

    def count_word_matches(self, expected: Sequence[str], actual: Sequence[str], threshold: float = 0.82) -> Tuple[int, int, int]:
        filtered_expected = [item for item in expected if self.normalize_match_word(item)]
        filtered_actual = [item for item in actual if self.normalize_match_word(item)]
        if not filtered_expected or not filtered_actual:
            return 0, 0, 0

        matches = 0
        for expected_word in filtered_expected:
            best = max(self.word_similarity(expected_word, actual_word) for actual_word in filtered_actual)
            if best >= threshold:
                matches += 1
        return matches, len(filtered_expected), max(0, len(filtered_actual) - matches)

    def allows_mcmod_extension(self, expected_words: Sequence[str], actual_words: Sequence[str]) -> bool:
        matches, expected_count, extra_words = self.count_word_matches(expected_words, actual_words)
        if not expected_count or matches < expected_count:
            return False
        allowed_extra = 2 if expected_count >= 4 else 1
        return extra_words <= allowed_extra

    def has_mcmod_subtitle_prefix(self, value: str, title_variant: str) -> bool:
        raw_value = re.sub(r"\s+", " ", str(value or "").strip())
        raw_title = re.sub(r"\s+", " ", str(title_variant or "").strip())
        if not raw_value or not raw_title or self.looks_like_compact_alias(raw_value):
            return False

        lower_value = raw_value.lower()
        lower_title = raw_title.lower()
        if not lower_title.startswith(lower_value):
            return False

        remainder = raw_title[len(raw_value):].lstrip()
        return bool(remainder and remainder[0] in ":：-|/｜")

    def score_word_alignment(self, expected: Sequence[str], actual: Sequence[str], allow_partial: bool = True) -> int:
        matches, expected_count, extra_words = self.count_word_matches(expected, actual)
        if not expected_count:
            return 0

        coverage = matches / expected_count
        if coverage >= 0.999:
            if expected_count <= 2:
                if extra_words == 0:
                    return 110
                if extra_words == 1:
                    return 54
                return 0
            if expected_count == 3:
                return 132 if extra_words <= 1 else 98
            return 136 if extra_words <= 1 else 114

        if not allow_partial:
            return 0

        if expected_count >= 4 and coverage >= 0.75 and extra_words <= 2:
            return 84
        if expected_count == 3 and coverage >= 0.67 and extra_words <= 1:
            return 58
        return 0

    def score_directional_containment(self, key: str, candidate: str) -> int:
        if not key or not candidate or key == candidate or key not in candidate:
            return 0
        if len(key) <= 4:
            return 0

        if candidate.startswith(key):
            remainder = candidate[len(key):]
            return 78 if len(remainder) <= 12 else 52
        if candidate.endswith(key):
            remainder = candidate[:-len(key)]
            return 26 if len(remainder) <= 8 else 12
        return 12

    def get_modrinth_loader_tags(self, hit: dict) -> set:
        values = set()
        for key in ("categories", "display_categories", "loaders"):
            for item in hit.get(key) or []:
                normalized = str(item or "").strip().lower()
                if normalized in LOADER_SEARCH_TOKENS:
                    values.add(normalized)
        return values

    def score_modrinth_loader_alignment(self, meta: ModMeta, hit: dict) -> int:
        if meta.loader == LoaderType.UNKNOWN.value:
            return 0
        loader_tags = self.get_modrinth_loader_tags(hit)
        if not loader_tags:
            return 0
        if meta.loader in loader_tags:
            return 36
        return -120

    def is_confident_modrinth_candidate(self, meta: ModMeta, hit: dict) -> bool:
        search_values = self.collect_search_values(meta)
        search_keys = [self.normalize_text(value) for value in search_values if self.normalize_text(value)]
        norm_slug = self.normalize_text(str(hit.get("slug", "")))
        norm_title = self.normalize_text(str(hit.get("title", "")))
        if any(key in {norm_slug, norm_title} for key in search_keys):
            return True

        remote_variants = self.extract_name_variants(str(hit.get("title", ""))) + self.extract_name_variants(str(hit.get("slug", "")))
        title_words = self.split_words(str(hit.get("title", "")))
        slug_words = self.split_words(str(hit.get("slug", "")))
        title_acronyms = {
            self.normalize_text(item, strip_brackets=False)
            for item in self.extract_acronym_candidates(str(hit.get("title", "")))
        }

        for value in search_values:
            if any(self.score_library_suffix_match(value, variant) for variant in remote_variants):
                return True
            value_words = self.split_words(value)
            title_alignment = self.score_word_alignment(value_words, title_words)
            slug_alignment = self.score_word_alignment(value_words, slug_words)
            if title_alignment >= 110 or slug_alignment >= 96:
                return True
            if title_alignment >= 58 and any(key and key in title_acronyms for key in search_keys):
                return True

            norm_value = self.normalize_text(value)
            if norm_value and norm_title.startswith(norm_value) and len(value_words) >= 2:
                return True
        return False

    def is_confident_mcmod_candidate(self, meta: ModMeta, title: str) -> bool:
        search_values = self.collect_search_values(meta)
        title_variants = self.extract_name_variants(title) or [title]
        title_acronyms = {
            self.normalize_match_text(item, strip_brackets=False, keep_cjk=False)
            for item in self.extract_acronym_candidates(title)
        }
        compact_keys = {
            self.normalize_match_text(value, strip_brackets=False, keep_cjk=False)
            for value in search_values
            if self.looks_like_compact_alias(value)
        }

        descriptive_values = [
            value
            for value in search_values
            if len(self.split_words(value, keep_cjk=True)) >= 3
        ]
        descriptive_match = False
        strong_descriptive_match = False
        for title_variant in title_variants:
            title_words = self.split_words(title_variant, keep_cjk=True)
            for value in descriptive_values:
                value_words = self.split_words(value, keep_cjk=True)
                alignment_score = self.score_word_alignment(value_words, title_words)
                if alignment_score >= 58:
                    descriptive_match = True
                if alignment_score >= 110:
                    strong_descriptive_match = True

        for value in search_values:
            if any(self.score_library_suffix_match(value, variant, keep_cjk=True) for variant in title_variants):
                return True
            if (
                descriptive_match
                and self.normalize_match_text(value, strip_brackets=False, keep_cjk=False) in title_acronyms
            ):
                return True

        for title_variant in title_variants:
            norm_title = self.normalize_match_text(title_variant, strip_brackets=False, keep_cjk=True)
            title_words = self.split_words(title_variant, keep_cjk=True)
            compact_title_words = self.split_words(title_variant, keep_cjk=False)

            for value in search_values:
                norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=True)
                if not norm_value:
                    continue

                value_words = self.split_words(value, keep_cjk=True)
                if norm_title == norm_value:
                    if (
                        len(value_words) >= 2
                        or len(compact_title_words) >= 2
                        or len(norm_value) >= 8
                        or not self.looks_like_compact_alias(value)
                    ):
                        return True

                if (
                    norm_title.startswith(norm_value)
                    and len(value_words) >= 2
                    and (
                        self.allows_mcmod_extension(value_words, title_words)
                        or self.has_mcmod_subtitle_prefix(value, title_variant)
                    )
                    and (not self.looks_like_compact_alias(value) or descriptive_match)
                ):
                    return True

                if self.score_word_alignment(value_words, title_words) >= 110:
                    return True

        full_title_words = self.split_words(title, keep_cjk=True)
        if compact_keys & title_acronyms and (descriptive_match or len(full_title_words) >= 3):
            return True
        return strong_descriptive_match

    def expand_match_word(self, word: str) -> List[str]:
        base = self.normalize_match_word(word)
        if not base or base == "s":
            return []

        variants = {base}
        if base.endswith("ies") and len(base) > 4:
            variants.add(base[:-3] + "y")
        if base.endswith("es") and len(base) > 4:
            variants.add(base[:-2])
        if base.endswith("s") and len(base) > 4:
            variants.add(base[:-1])
        if base.endswith("ing") and len(base) > 5:
            variants.add(base[:-3])
        return [item for item in variants if item]

    def word_similarity(self, left: str, right: str) -> float:
        left_variants = self.expand_match_word(left)
        right_variants = self.expand_match_word(right)
        if not left_variants or not right_variants:
            return 0.0
        best = 0.0
        for left_item in left_variants:
            for right_item in right_variants:
                if left_item == right_item:
                    return 1.0
                best = max(best, difflib.SequenceMatcher(None, left_item, right_item).ratio())
        return best

    def extract_acronym_candidates(self, text: str) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []

        acronyms: List[str] = []
        for token in re.findall(r"\(([A-Z0-9]{2,12})\)", raw):
            cleaned = token.strip()
            if cleaned and cleaned not in acronyms:
                acronyms.append(cleaned)
        for token in re.findall(r"[\[【]([A-Za-z0-9]{2,16})[\]】]", raw):
            cleaned = token.strip()
            if cleaned and cleaned not in acronyms:
                acronyms.append(cleaned)

        words = [item for item in self.split_words(raw) if len(item) > 1 and not item.isdigit()]
        if len(words) >= 2:
            acronym = "".join(item[0] for item in words[:8]).upper()
            if len(acronym) >= 2 and acronym not in acronyms:
                acronyms.append(acronym)
        return acronyms

    def read_zip_entry_text(self, zf: zipfile.ZipFile, entry_name: str) -> Optional[str]:
        try:
            with zf.open(entry_name) as fp:
                return fp.read().decode("utf-8", errors="ignore")
        except KeyError:
            return None
        except Exception:
            return None

    def read_zip_entry_json(self, zf: zipfile.ZipFile, entry_name: str) -> Optional[dict]:
        text = self.read_zip_entry_text(zf, entry_name)
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def build_query_tokens(self, file_name: str, *values: str) -> List[str]:
        query_tokens: List[str] = []
        for value in (*values, self.clean_filename_token(file_name)):
            if self.is_placeholder_value(value):
                continue
            for variant in self.expand_query_token(value):
                if self.is_meaningful_query_token(variant) and variant not in query_tokens:
                    query_tokens.append(variant)
        return query_tokens

    def build_mcmod_query_tokens(self, meta: ModMeta) -> List[str]:
        query_tokens: List[str] = []
        values = [meta.mod_id, meta.mod_name, *self.extract_bracket_tokens(meta.file_name), self.clean_filename_token(meta.file_name)]
        for value in values:
            if self.is_placeholder_value(value):
                continue
            for variant in self.expand_query_token(value):
                if self.is_meaningful_query_token(variant) and variant not in query_tokens:
                    query_tokens.append(variant)
        return query_tokens

    def collect_unique_queries(self, tokens: Sequence[str], limit: int = 8) -> List[str]:
        queries: List[str] = []
        seen_queries = set()
        for token in tokens:
            query = token.strip()
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)
            queries.append(query)
            if len(queries) >= limit:
                break
        return queries

    def looks_like_compact_alias(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw or " " in raw:
            return False
        return bool(
            re.search(r"\d", raw)
            or re.search(r"[A-Z]{2,}", raw)
            or re.search(r"[_-]", raw)
            or (raw.islower() and len(raw) <= 8)
        )

    def extract_page_title(self, html: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
        if not match:
            return ""
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        title = re.sub(r"\s*\|\s*最大的Minecraft中文MOD百科.*$", "", title)
        title = re.sub(r"\s*[-|｜]\s*MC百科.*$", "", title)
        return title.strip()

    def extract_mcmod_search_results(self, html: str) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        seen_links = set()
        for href, raw_title in re.findall(r'<a[^>]+href="([^"]*class/\d+\.html[^"]*)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
            link = urllib.parse.urljoin("https://www.mcmod.cn", href.strip())
            if not re.match(r"^https?://www\.mcmod\.cn/class/\d+\.html$", link):
                continue
            title = re.sub(r"<.*?>", "", raw_title).strip()
            title = re.sub(r"\s+", " ", title)
            if not title or title.startswith("www.mcmod.cn/class/") or link in seen_links:
                continue
            seen_links.add(link)
            candidates.append((title, link))
        return candidates

    def extract_mcmod_environment(self, html: str) -> str:
        if not html:
            return ""

        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&#160;", " ")
        text = re.sub(r"\s+", " ", text).strip()

        for pattern in (
            r"运行环境\s*[:：]\s*(.{1,120}?)(?=\s*(收录时间|编辑次数|最后编辑|最后推荐|模组标签|支持的MC版本|相关链接|Mod作者|总浏览|$))",
            r"运行环境\s*(客户端[^ ]{0,20}\s*,\s*服务端[^ ]{0,20})",
        ):
            match = re.search(pattern, text, flags=re.I)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()
        return ""

    def throttle_request(self) -> None:
        interval = self.throttle_ms / 1000
        if interval <= 0:
            return
        with self.request_lock:
            now = time.monotonic()
            if now < self.next_request_at:
                time.sleep(self.next_request_at - now)
            self.next_request_at = time.monotonic() + interval

    def throttle_mcmod_request(self, interval_ms: int = 350) -> None:
        interval = interval_ms / 1000
        if interval <= 0:
            return
        with self.mcmod_request_lock:
            now = time.monotonic()
            if now < self.next_mcmod_request_at:
                time.sleep(self.next_mcmod_request_at - now)
            self.next_mcmod_request_at = time.monotonic() + interval

    def throttle_modrinth_request(self) -> None:
        with self.modrinth_request_lock:
            now = time.monotonic()
            if now < self.next_modrinth_request_at:
                time.sleep(self.next_modrinth_request_at - now)

    def update_modrinth_rate_limit(self, headers: Any, status_code: int = 200) -> None:
        limit = None
        remaining = None
        reset_seconds = None
        try:
            limit = int(headers.get("X-Ratelimit-Limit", "0") or 0)
        except Exception:
            limit = 0
        try:
            remaining = int(headers.get("X-Ratelimit-Remaining", "0") or 0)
        except Exception:
            remaining = 0
        try:
            reset_seconds = float(headers.get("X-Ratelimit-Reset", "0") or 0)
        except Exception:
            reset_seconds = 0.0

        fallback_interval = 0.22
        with self.modrinth_request_lock:
            now = time.monotonic()
            next_at = now + fallback_interval
            if status_code == 429:
                next_at = now + max(reset_seconds, 1.0)
            elif limit and remaining and reset_seconds > 0:
                next_at = now + max(reset_seconds / max(remaining, 1), fallback_interval)
            elif limit and remaining <= 0 and reset_seconds > 0:
                next_at = now + reset_seconds
            self.next_modrinth_request_at = max(self.next_modrinth_request_at, next_at)

    def is_mcmod_rate_limited(self, html: str) -> bool:
        if not html:
            return False
        return "搜索太频繁，请稍后再试" in html or "鎼滅储澶绻侊紝璇风◢鍚庡啀璇" in html

    def mcmod_text_request(self, cache_key: str, url: str, max_attempts: int = 4) -> str:
        with self.cache_lock:
            cached = self.cache.get(cache_key)
            if isinstance(cached, str) and cached and not self.is_mcmod_rate_limited(cached):
                return cached
            wait_event = self.inflight_requests.get(cache_key)
            owner = wait_event is None
            if owner:
                wait_event = threading.Event()
                self.inflight_requests[cache_key] = wait_event

        if not owner:
            assert wait_event is not None
            wait_event.wait()
            with self.cache_lock:
                cached = self.cache.get(cache_key)
            if isinstance(cached, str) and cached and not self.is_mcmod_rate_limited(cached):
                return cached
            return ""

        last_html = ""
        try:
            for attempt in range(max_attempts):
                try:
                    self.throttle_request()
                    self.throttle_mcmod_request()
                    html = self.http_get_text(url) or ""
                except Exception:
                    html = ""
                last_html = html
                if html and not self.is_mcmod_rate_limited(html):
                    with self.cache_lock:
                        self.cache[cache_key] = html
                    return html
                time.sleep(0.35 * (attempt + 1))
            return last_html if last_html and not self.is_mcmod_rate_limited(last_html) else ""
        finally:
            with self.cache_lock:
                event = self.inflight_requests.pop(cache_key, None)
                if event:
                    event.set()

    def get_cached_value(self, cache_key: str, loader: Callable[[], object]) -> object:
        owner = False
        wait_event: Optional[threading.Event] = None
        with self.cache_lock:
            if cache_key in self.cache:
                return self.cache[cache_key]
            wait_event = self.inflight_requests.get(cache_key)
            if wait_event is None:
                wait_event = threading.Event()
                self.inflight_requests[cache_key] = wait_event
                owner = True

        if not owner:
            assert wait_event is not None
            wait_event.wait()
            with self.cache_lock:
                return self.cache.get(cache_key)

        value: object = None
        try:
            value = loader()
        except Exception:
            value = None
        finally:
            with self.cache_lock:
                self.cache[cache_key] = value
                event = self.inflight_requests.pop(cache_key, None)
                if event:
                    event.set()
        return value

    def cached_json_request(self, cache_key: str, url: str, use_throttle: bool = True) -> Optional[dict]:
        value = self.get_cached_value(
            cache_key,
            lambda: (self.throttle_request(), self.http_get_json(url))[1] if use_throttle else self.http_get_json(url),
        )
        return value if isinstance(value, dict) else None

    def modrinth_json_request(self, cache_key: str, url: str, max_attempts: int = 3) -> Optional[dict]:
        def loader() -> Optional[dict]:
            last_payload: Optional[dict] = None
            for attempt in range(max_attempts):
                self.throttle_modrinth_request()
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        raw = resp.read()
                        charset = resp.headers.get_content_charset() or "utf-8"
                        payload = json.loads(raw.decode(charset, errors="ignore"))
                        self.update_modrinth_rate_limit(resp.headers, getattr(resp, "status", 200))
                        last_payload = payload if isinstance(payload, dict) else None
                        return last_payload
                except urllib.error.HTTPError as exc:
                    self.update_modrinth_rate_limit(exc.headers or {}, exc.code)
                    if exc.code == 429:
                        time.sleep(min(2.0 * (attempt + 1), 8.0))
                        continue
                    return None
                except Exception:
                    time.sleep(0.3 * (attempt + 1))
            return last_payload

        value = self.get_cached_value(cache_key, loader)
        return value if isinstance(value, dict) else None

    def cached_text_request(self, cache_key: str, url: str) -> str:
        value = self.get_cached_value(
            cache_key,
            lambda: (self.throttle_request(), self.http_get_text(url))[1],
        )
        return value if isinstance(value, str) else ""

    def parse_quilt_metadata(self, file_path: Path, quilt_json: dict) -> ModMeta:
        loader_block = quilt_json.get("quilt_loader") or {}
        metadata_block = loader_block.get("metadata") or {}
        mod_id = str(loader_block.get("id") or "").strip()
        mod_name = str(metadata_block.get("name") or mod_id or file_path.stem).strip()
        description = str(metadata_block.get("description") or "").strip()
        entrypoints = list((loader_block.get("entrypoints") or {}).keys())
        depends = []
        for dep in loader_block.get("depends") or []:
            if isinstance(dep, dict) and dep.get("id"):
                depends.append(str(dep["id"]))
        return ModMeta(
            file_name=file_path.name,
            file_path=str(file_path),
            mod_id=mod_id,
            mod_name=mod_name,
            description=description,
            environment=str(quilt_json.get("environment") or metadata_block.get("environment") or "*").strip(),
            entrypoints=entrypoints,
            depends=depends,
            loader=LoaderType.QUILT.value,
            metadata_source="quilt.mod.json",
            query_tokens=self.build_query_tokens(file_path.name, mod_id, mod_name),
        )

    def extract_forge_dependency_sides(self, toml_text: str) -> List[str]:
        sides: List[str] = []
        blocks = re.split(r"(?m)^\s*\[\[dependencies\.[^\]]+\]\]\s*", toml_text)
        for block in blocks[1:]:
            match = re.search(r'(?m)^\s*side\s*=\s*"([A-Z_]+)"', block)
            if not match:
                continue
            side = match.group(1).strip().upper()
            if side:
                sides.append(side)
        return sides

    def parse_forge_toml_metadata(self, file_path: Path, toml_text: str, source_name: str) -> ModMeta:
        mod_ids = re.findall(r'(?m)^\s*modId\s*=\s*"([^"]+)"', toml_text)
        display_names = re.findall(r'(?m)^\s*displayName\s*=\s*"([^"]+)"', toml_text)
        description_match = re.search(
            r'(?ms)^\s*description\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"]*)")',
            toml_text,
        )
        client_side_only = bool(re.search(r"(?m)^\s*clientSideOnly\s*=\s*true\b", toml_text))
        dependency_sides = self.extract_forge_dependency_sides(toml_text)

        mod_id = mod_ids[0].strip() if mod_ids else ""
        mod_name = display_names[0].strip() if display_names else (mod_id or file_path.stem)
        description = ""
        if description_match:
            description = next((group.strip() for group in description_match.groups() if group), "")

        path_hint = str(file_path).lower()
        loader = LoaderType.NEOFORGE.value if "neoforge" in source_name.lower() or "neoforge" in path_hint else LoaderType.FORGE.value
        if loader == LoaderType.FORGE.value and re.search(r'(?im)^\s*license\s*=\s*".*neoforge', toml_text):
            loader = LoaderType.NEOFORGE.value

        return ModMeta(
            file_name=file_path.name,
            file_path=str(file_path),
            mod_id=mod_id,
            mod_name=mod_name,
            description=description,
            environment="*",
            entrypoints=[],
            depends=[],
            loader=loader,
            metadata_source=source_name,
            query_tokens=self.build_query_tokens(file_path.name, mod_id, mod_name),
            client_side_only=client_side_only,
            dependency_sides=dependency_sides,
        )

    def build_damaged_mod_meta(self, file_path: Path, reason: str, metadata_source: str = "damaged-jar") -> ModMeta:
        return ModMeta(
            file_name=file_path.name,
            file_path=str(file_path),
            mod_id="",
            mod_name=file_path.stem,
            description="",
            environment="",
            entrypoints=[],
            depends=[],
            loader=LoaderType.UNKNOWN.value,
            metadata_source=metadata_source,
            query_tokens=self.build_query_tokens(file_path.name, file_path.stem),
            jar_status="damaged",
            jar_issue=reason,
        )

    def get_jar_metadata(self, file_path: Path) -> ModMeta:
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                entry_names = set(zf.namelist())

                if "fabric.mod.json" in entry_names:
                    try:
                        fabric_text = self.read_zip_entry_text(zf, "fabric.mod.json")
                        if fabric_text is None:
                            raise ValueError("无法读取 fabric.mod.json")
                        fabric_json = json.loads(fabric_text)
                    except Exception as exc:
                        return self.build_damaged_mod_meta(
                            file_path,
                            f"fabric.mod.json 解析失败: {type(exc).__name__}: {exc}",
                        )
                    entrypoints = list((fabric_json.get("entrypoints") or {}).keys())
                    depends = list((fabric_json.get("depends") or {}).keys())
                    return ModMeta(
                        file_name=file_path.name,
                        file_path=str(file_path),
                        mod_id=str(fabric_json.get("id") or "").strip(),
                        mod_name=str(fabric_json.get("name") or file_path.stem).strip(),
                        description=str(fabric_json.get("description") or "").strip(),
                        environment=str(fabric_json.get("environment") or "*").strip(),
                        entrypoints=entrypoints,
                        depends=depends,
                        loader=LoaderType.FABRIC.value,
                        metadata_source="fabric.mod.json",
                        query_tokens=self.build_query_tokens(
                            file_path.name,
                            str(fabric_json.get("id") or "").strip(),
                            str(fabric_json.get("name") or "").strip(),
                        ),
                    )

                if "quilt.mod.json" in entry_names:
                    try:
                        quilt_text = self.read_zip_entry_text(zf, "quilt.mod.json")
                        if quilt_text is None:
                            raise ValueError("无法读取 quilt.mod.json")
                        quilt_json = json.loads(quilt_text)
                        return self.parse_quilt_metadata(file_path, quilt_json)
                    except Exception as exc:
                        return self.build_damaged_mod_meta(
                            file_path,
                            f"quilt.mod.json 解析失败: {type(exc).__name__}: {exc}",
                        )

                for source_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                    if source_name not in entry_names:
                        continue
                    try:
                        toml_text = self.read_zip_entry_text(zf, source_name)
                        if toml_text is None:
                            raise ValueError(f"无法读取 {source_name}")
                        if not toml_text.strip():
                            raise ValueError(f"{source_name} 为空")
                        return self.parse_forge_toml_metadata(file_path, toml_text, source_name)
                    except Exception as exc:
                        return self.build_damaged_mod_meta(
                            file_path,
                            f"{source_name} 解析失败: {type(exc).__name__}: {exc}",
                        )
        except zipfile.BadZipFile as exc:
            return self.build_damaged_mod_meta(file_path, f"Zip 结构损坏: {type(exc).__name__}: {exc}")
        except Exception as exc:
            return self.build_damaged_mod_meta(file_path, f"Jar 读取失败: {type(exc).__name__}: {exc}")

        return ModMeta(
            file_name=file_path.name,
            file_path=str(file_path),
            mod_id="",
            mod_name=file_path.stem,
            description="",
            environment="",
            entrypoints=[],
            depends=[],
            loader=LoaderType.UNKNOWN.value,
            metadata_source="filename-only",
            query_tokens=self.build_query_tokens(file_path.name, file_path.stem),
        )

    def normalize_entrypoint_name(self, entrypoint_name: str) -> str:
        normalized = str(entrypoint_name or "").strip().lower()
        normalized = re.sub(r"[\s.\-:/]+", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized

    def is_client_only_entrypoint(self, entrypoint_name: str) -> bool:
        normalized = self.normalize_entrypoint_name(entrypoint_name)
        if not normalized:
            return False
        if normalized in CLIENT_ENTRYPOINTS:
            return True

        parts = [part for part in normalized.split("_") if part]
        if not parts:
            return False
        if "main" in parts or "server" in parts:
            return False
        if "client" in parts:
            return True

        part_set = set(parts)
        if {"jei", "plugin"} <= part_set:
            return True
        if "rei" in part_set and "client" in normalized:
            return True
        if "journeymap" in part_set:
            return True
        if any(token in part_set for token in {"modmenu", "emi", "jade", "waila"}):
            return True

        matched_hints = sum(1 for token in CLIENT_ENTRYPOINT_TOKEN_HINTS if token in part_set)
        return matched_hints >= 2

    def local_classification(self, meta: ModMeta) -> Classification:
        if meta.loader in {LoaderType.FORGE.value, LoaderType.NEOFORGE.value}:
            if meta.client_side_only:
                return Classification("client-only", "local", f"{meta.loader} mods.toml: clientSideOnly=true")

            dependency_sides = {item.upper() for item in meta.dependency_sides if item}
            if dependency_sides == {"CLIENT"}:
                return Classification("client-only", "local", f"{meta.loader} dependencies side=CLIENT")
            if dependency_sides == {"SERVER"}:
                return Classification("server-keep", "local", f"{meta.loader} dependencies side=SERVER")

            return Classification("unknown", "local", f"{meta.loader} 本地元数据缺少可靠环境结论")

        if meta.loader == LoaderType.UNKNOWN.value:
            return Classification("unknown", "local", "未知加载器，本地元数据不足")

        entrypoints = set(meta.entrypoints)
        normalized_entrypoints = {self.normalize_entrypoint_name(item) for item in entrypoints if item}
        has_main = "main" in normalized_entrypoints
        has_server = "server" in normalized_entrypoints
        non_client_only = [item for item in entrypoints if not self.is_client_only_entrypoint(item)]

        if meta.environment == "client":
            return Classification("client-only", "local", "fabric/quilt 元数据 environment=client")

        if meta.environment == "server":
            return Classification("server-keep", "local", "fabric/quilt 元数据 environment=server")

        if entrypoints and not has_main and not has_server and not non_client_only:
            return Classification("unknown", "local", "仅声明客户端入口点，继续联网核对")

        if has_main or has_server:
            return Classification("unknown", "local", "含 main/server 入口，本地无法直接确认")

        return Classification("unknown", "local", "本地元数据不足")

    def http_get_json(self, url: str) -> Optional[dict]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(charset, errors="ignore"))

    def classification_from_modrinth_payload(self, payload: dict, reason_prefix: str, url: str) -> Classification:
        client_side = str(payload.get("client_side", "unknown"))
        server_side = str(payload.get("server_side", "unknown"))
        reason = f"{reason_prefix}: client_side={client_side}, server_side={server_side}"
        if server_side == "unsupported":
            return Classification("client-only", "modrinth", reason, url)
        if server_side in {"required", "optional"}:
            return Classification("server-keep", "modrinth", reason, url)
        return Classification("unknown", "modrinth", reason, url)

    def modrinth_direct_lookup(self, meta: ModMeta) -> Optional[Classification]:
        if not meta.mod_id or self.is_placeholder_value(meta.mod_id):
            return None

        slug = meta.mod_id.strip()
        cache_key = f"modrinth-project::{slug}"
        url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(slug)}"
        payload = self.modrinth_json_request(cache_key, url)
        if not payload:
            return None

        payload_slug = self.normalize_text(str(payload.get("slug", "")))
        payload_title = self.normalize_text(str(payload.get("title", "")))
        search_keys = [
            self.normalize_text(value)
            for value in self.collect_search_values(meta, meta.mod_id)
            if self.normalize_text(value)
        ]

        if payload_slug not in search_keys and payload_title not in search_keys:
            return None

        hit_score = self.score_modrinth_hit(meta, meta.mod_id, payload)
        if hit_score < 190 or not self.is_confident_modrinth_candidate(meta, payload):
            return None

        return self.classification_from_modrinth_payload(
            payload,
            "Modrinth(直连)",
            f"https://modrinth.com/mod/{payload.get('slug', '')}",
        )

    def http_get_text(self, url: str) -> Optional[str]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="ignore")

    def score_contained_alias(self, key: str, candidate: str) -> int:
        return self.score_directional_containment(key, candidate)

    def score_modrinth_hit(self, meta: ModMeta, query: str, hit: dict) -> int:
        score = 0
        norm_slug = self.normalize_text(str(hit.get("slug", "")))
        norm_title = self.normalize_text(str(hit.get("title", "")))
        norm_desc = self.normalize_text(str(hit.get("description", "")))
        search_values = self.collect_search_values(meta, query)
        alias_mode = any(self.looks_like_compact_alias(item) for item in search_values)
        search_keys = [self.normalize_text(item) for item in search_values if self.normalize_text(item)]
        remote_variants = self.extract_name_variants(str(hit.get("title", ""))) + self.extract_name_variants(str(hit.get("slug", "")))

        if any(key == norm_slug for key in search_keys):
            score += 180
        if any(key == norm_title for key in search_keys):
            score += 165
        score += max(self.score_contained_alias(key, norm_slug) for key in search_keys) if search_keys else 0
        score += max(self.score_contained_alias(key, norm_title) for key in search_keys) if search_keys else 0
        if any(key in norm_desc for key in search_keys):
            score += 95 if alias_mode else 35
        if any(key in norm_slug and key != norm_slug for key in search_keys):
            score += 16
        if any(key in norm_title and key != norm_title for key in search_keys):
            score += 12

        title_words = self.split_words(str(hit.get("title", "")))
        slug_words = self.split_words(str(hit.get("slug", "")))
        for value in dict.fromkeys(search_values):
            if not value:
                continue
            value_words = self.split_words(value)
            score += self.score_word_alignment(value_words, title_words)
            score += max(0, self.score_word_alignment(value_words, slug_words) - 16)

        acronym_candidates = [
            self.normalize_text(item, strip_brackets=False)
            for item in self.extract_acronym_candidates(str(hit.get("title", "")))
        ]
        if any(key and key in acronym_candidates for key in search_keys):
            score += 155

        library_bonus = 0
        for value in search_values:
            for remote_value in remote_variants:
                library_bonus = max(library_bonus, self.score_library_suffix_match(value, remote_value))
        score += library_bonus

        score += self.score_modrinth_loader_alignment(meta, hit)
        if hit.get("slug"):
            score += 5
        return score

    def modrinth_search(self, meta: ModMeta) -> Optional[Classification]:
        direct = self.modrinth_direct_lookup(meta)
        if direct and direct.category != "unknown":
            return direct

        candidate_map: Dict[str, Tuple[int, dict, str]] = {}
        queries = self.collect_unique_queries(meta.query_tokens)
        if not queries:
            return None

        for query in queries:
            cache_key = f"modrinth::{query}"
            url = (
                "https://api.modrinth.com/v2/search?"
                f"query={urllib.parse.quote(query)}&limit=8&facets=%5B%5B%22project_type%3Amod%22%5D%5D"
            )
            response = self.modrinth_json_request(cache_key, url) or {}
            for hit in response.get("hits", []):
                score = self.score_modrinth_hit(meta, query, hit)
                slug = str(hit.get("slug") or hit.get("project_id") or "")
                if not slug:
                    slug = f"{query}::{len(candidate_map)}"
                previous = candidate_map.get(slug)
                if previous is None or score > previous[0]:
                    candidate_map[slug] = (score, hit, query)

        if not candidate_map:
            return None

        candidates = sorted(candidate_map.values(), key=lambda item: item[0], reverse=True)
        score, hit, _query = candidates[0]
        runner_up = candidates[1][0] if len(candidates) > 1 else 0
        if score < 180 or score - runner_up < 35 or not self.is_confident_modrinth_candidate(meta, hit):
            return None

        return self.classification_from_modrinth_payload(
            hit,
            "Modrinth",
            f"https://modrinth.com/mod/{hit.get('slug', '')}",
        )

    def score_mcmod_page(self, meta: ModMeta, title: str) -> int:
        score = 0
        title_variants = self.extract_name_variants(title)
        search_values = self.collect_search_values(meta)

        for title_variant in title_variants or [title]:
            norm_title = self.normalize_match_text(title_variant, strip_brackets=False, keep_cjk=True)
            title_words = self.split_words(title_variant, keep_cjk=True)

            for value in dict.fromkeys(item for item in search_values if item):
                norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=True)
                if not norm_value:
                    continue
                if norm_title == norm_value:
                    score = max(score, 180)
                    continue

                value_words = self.split_words(value, keep_cjk=True)
                if (
                    norm_title.startswith(norm_value)
                    and len(value_words) >= 2
                    and (
                        self.allows_mcmod_extension(value_words, title_words)
                        or self.has_mcmod_subtitle_prefix(value, title_variant)
                    )
                ):
                    score = max(score, 112 if len(value_words) <= 2 else 150)
                elif norm_value in norm_title and self.allows_mcmod_extension(value_words, title_words):
                    score = max(score, self.score_directional_containment(norm_value, norm_title))

                alignment_score = self.score_word_alignment(value_words, title_words)
                if alignment_score:
                    score = max(score, alignment_score + (18 if len(value_words) >= 3 else 0))
                library_score = self.score_library_suffix_match(value, title_variant, keep_cjk=True)
                if library_score:
                    score = max(score, library_score + 28)

        acronym_candidates = [
            self.normalize_match_text(item, strip_brackets=False, keep_cjk=False)
            for item in self.extract_acronym_candidates(title)
        ]
        for value in (meta.mod_id, meta.mod_name):
            norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=False)
            if norm_value and norm_value in acronym_candidates:
                score = max(score, 150)
        return score

    def mcmod_search(self, meta: ModMeta) -> Optional[Classification]:
        candidates: List[Tuple[int, Classification]] = []
        for query in self.collect_unique_queries(self.build_mcmod_query_tokens(meta)):
            search_key = f"mcmod-search::{query}"
            url = f"https://search.mcmod.cn/s?key={urllib.parse.quote(query)}"
            html = self.mcmod_text_request(search_key, url)
            search_results = self.extract_mcmod_search_results(html)
            if not search_results:
                continue

            ranked_results: List[Tuple[int, str, str]] = []
            for title, link in search_results:
                score = self.score_mcmod_page(meta, title)
                if score > 0 or len(search_results) == 1:
                    ranked_results.append((score, title, link))
            ranked_results.sort(key=lambda item: item[0], reverse=True)

            for search_score, search_title, link in ranked_results[:5]:
                page_key = f"mcmod-page::{link}"
                page_html = self.mcmod_text_request(page_key, link, max_attempts=3)
                if not page_html:
                    continue

                title = self.extract_page_title(page_html) or search_title
                env_text = self.extract_mcmod_environment(page_html)
                if not env_text:
                    continue

                score = max(
                    search_score,
                    self.score_mcmod_page(meta, search_title),
                    self.score_mcmod_page(meta, title),
                    self.score_mcmod_page(meta, f"{search_title} {title}"),
                )
                if score < 100 or not (
                    self.is_confident_mcmod_candidate(meta, search_title)
                    or self.is_confident_mcmod_candidate(meta, title)
                    or self.is_confident_mcmod_candidate(meta, f"{search_title} {title}")
                ):
                    continue
                if "服务端无效" in env_text:
                    candidates.append((score, Classification("client-only", "mcmod", f"MC百科: {env_text}", link)))
                elif "服务端需装" in env_text or "服务端可选" in env_text or "服务端支持" in env_text:
                    candidates.append((score, Classification("server-keep", "mcmod", f"MC百科: {env_text}", link)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def resolve_classification(self, meta: ModMeta, use_mcmod: bool = True) -> Classification:
        if meta.jar_status == "damaged":
            reason = f"Jar 读取异常: {meta.jar_issue}" if meta.jar_issue else "Jar 读取异常"
            return Classification("unknown", "damaged-jar", reason)

        local = self.local_classification(meta)
        if local.category in {"client-only", "server-keep"}:
            return local

        remote = self.modrinth_search(meta)
        if remote and remote.category != "unknown":
            return remote

        if use_mcmod:
            fallback = self.mcmod_search(meta)
            if fallback and fallback.category != "unknown":
                return fallback

        if local.category in {"client-only", "server-keep"}:
            return local
        if remote:
            return remote
        return local

    def analyze_mod_file(self, jar_path: Path, use_mcmod: bool = True) -> Tuple[ModMeta, Classification]:
        meta = self.get_jar_metadata(jar_path)
        classification = self.resolve_classification(meta, use_mcmod=use_mcmod)
        return meta, classification


class ServerBuilderCore:
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
        self.classifier = classifier
        self.log = log
        self.set_status = set_status
        self.set_progress = set_progress
        self.request_version_choice = request_version_choice
        self.request_checklist = request_checklist
        self.use_mcmod = use_mcmod
        self.enable_second_pass = enable_second_pass
        self.network_cache: Dict[str, Any] = {}
        self.build_log_lines: List[str] = []
        self.install_log_lines: List[str] = []

    def log_line(self, message: str) -> None:
        self.build_log_lines.append(message)
        self.log(message)

    def set_stage(self, stage: TaskStage, progress: float, detail: str) -> None:
        self.set_progress(progress)
        self.set_status(f"{stage.value}：{detail}")
        self.log_line(f"[{stage.value}] {detail}")

    def http_get_text(self, url: str) -> str:
        cache_key = f"text::{url}"
        if cache_key in self.network_cache:
            return self.network_cache[cache_key]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        self.network_cache[cache_key] = text
        return text

    def http_get_json(self, url: str) -> Any:
        cache_key = f"json::{url}"
        if cache_key in self.network_cache:
            return self.network_cache[cache_key]
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.network_cache[cache_key] = data
        return data

    def http_download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with destination.open("wb") as fp:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)

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
        key: List[Tuple[int, object]] = []
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

    def get_required_java_major(self, candidate: VersionCandidate) -> int:
        release = self.parse_release_version(candidate.minecraft_version)
        if release:
            if release >= (26, 1, 0):
                return 25
            if release[0] == 1:
                if release >= (1, 20, 5):
                    return 21
                if release >= (1, 18, 0):
                    return 17
                if release >= (1, 17, 0):
                    return 16
                return 8
        return candidate.java_major if candidate.java_major >= 8 else 8

    def java_requires_64bit(self, required_major: int) -> bool:
        return required_major >= 21

    def collect_candidate_java_paths(self, client_dir: Path, game_root: Path) -> List[Tuple[Path, str]]:
        candidates: List[Tuple[Path, str]] = []
        seen: set[str] = set()
        # Java path collection is intentionally broad because many launchers
        # keep runtimes in download or cache folders instead of system paths.
        java_path_pattern = re.compile(r'([A-Za-z]:\\[^\r\n"<>|?*]*?java\.exe)', flags=re.I)

        def add_java_path(path: Path, source: str) -> None:
            try:
                resolved = path.resolve()
            except OSError:
                return
            normalized = str(resolved).lower()
            if normalized in seen or not resolved.exists() or not resolved.is_file():
                return
            seen.add(normalized)
            candidates.append((resolved, source))

        def add_java_root(root: Path, source: str) -> None:
            if not root.exists() or not root.is_dir():
                return
            add_java_path(root / "bin" / "java.exe", source)
            for pattern in ("*/bin/java.exe", "*/*/bin/java.exe", "*/*/*/bin/java.exe"):
                for java_path in root.glob(pattern):
                    add_java_path(java_path, source)

        def add_java_paths_from_records(base_dir: Path, source: str) -> None:
            if not base_dir.exists() or not base_dir.is_dir():
                return
            scan_dirs = [base_dir]
            for name in ("Cache", "Config", "Logs", "Log"):
                child = base_dir / name
                if child.exists() and child.is_dir():
                    scan_dirs.append(child)

            scanned_files = 0
            for folder in scan_dirs:
                for pattern in ("*.ini", "*.cfg", "*.json", "*.log", "*.txt"):
                    iterator = folder.rglob(pattern) if folder != base_dir else folder.glob(pattern)
                    for text_file in iterator:
                        scanned_files += 1
                        if scanned_files > 160:
                            return
                        try:
                            if text_file.stat().st_size > 2 * 1024 * 1024:
                                continue
                            content = text_file.read_text(encoding="utf-8", errors="ignore")
                        except OSError:
                            continue
                        for raw_path in java_path_pattern.findall(content):
                            add_java_path(Path(raw_path), f"{source}记录/{text_file.name}")

        def add_drive_hint_roots(*paths: Path) -> None:
            drive_roots: List[Path] = []
            for path in paths:
                anchor = path.anchor.strip() if path.anchor else ""
                if not anchor:
                    continue
                root = Path(anchor)
                if root not in drive_roots:
                    drive_roots.append(root)

            name_tokens = (
                "download",
                "java",
                "jdk",
                "jre",
                "jvm",
                "graal",
                "openjdk",
                "temurin",
                "adoptium",
                "adoptopenjdk",
                "corretto",
                "zulu",
                "bellsoft",
                "liberica",
                "semeru",
            )
            exact_names = {
                "downloads",
                "download",
                "edgedownload",
                "java",
                "jdk",
                "jre",
                "runtime",
                "tools",
                "software",
                "softwares",
                "downloads2",
                "下载",
            }

            for drive_root in drive_roots:
                try:
                    children = list(drive_root.iterdir())
                except OSError:
                    continue
                for child in children:
                    if not child.is_dir():
                        continue
                    lowered = child.name.lower()
                    if lowered in exact_names or any(token in lowered for token in name_tokens):
                        add_java_root(child, f"盘符常见目录/{child.name}")

        app_dir = self.get_application_dir()
        for name in ("runtime", "jdk", "jre"):
            add_java_root(app_dir / name, f"程序目录/{name}")

        minecraft_home = Path(os.environ.get("APPDATA", "")) / ".minecraft"
        add_java_root(minecraft_home / "runtime", "默认 .minecraft/runtime")

        pcl_temp_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "PCL"
        add_java_root(pcl_temp_root, "PCL 临时目录")
        add_java_paths_from_records(Path(os.environ.get("APPDATA", "")) / "PCL", "PCL 配置")
        add_java_paths_from_records(pcl_temp_root, "PCL 临时目录")

        related_bases: List[Path] = []
        for base in (client_dir.resolve(), game_root.resolve()):
            if base not in related_bases:
                related_bases.append(base)
            for parent in list(base.parents)[:3]:
                if parent not in related_bases:
                    related_bases.append(parent)
        for base in related_bases:
            for name in ("runtime", "jdk", "jre"):
                add_java_root(base / name, f"实例相关目录/{name}")

        for base in related_bases:
            add_java_root(base / "PCL", "实例相关目录/PCL")
            add_java_paths_from_records(base / "PCL", "实例相关目录/PCL")

        java_home = os.environ.get("JAVA_HOME", "").strip()
        if java_home:
            add_java_path(Path(java_home) / "bin" / "java.exe", "JAVA_HOME")

        which_java = shutil.which("java.exe") or shutil.which("java")
        if which_java:
            add_java_path(Path(which_java), "PATH")

        env_roots = [
            Path(os.environ.get("ProgramFiles", "")) / "Java",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Java",
            Path(os.environ.get("ProgramFiles", "")) / "Eclipse Adoptium",
            Path(os.environ.get("ProgramFiles", "")) / "AdoptOpenJDK",
            Path(os.environ.get("ProgramFiles", "")) / "Microsoft",
            Path(os.environ.get("ProgramFiles", "")) / "BellSoft",
            Path(os.environ.get("ProgramFiles", "")) / "Zulu",
            Path(os.environ.get("ProgramFiles", "")) / "Amazon Corretto",
            Path(os.environ.get("ProgramFiles", "")) / "Semeru",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        ]
        for root in env_roots:
            add_java_root(root, f"常见安装目录/{root.name}")

        user_home = Path.home()
        for root in (
            user_home / "Desktop",
            user_home / "Downloads",
            user_home / "Documents",
            user_home / "AppData" / "Roaming" / ".minecraft" / "PCL",
        ):
            add_java_root(root, f"用户常见目录/{root.name}")
            add_java_paths_from_records(root, f"用户常见目录/{root.name}")

        add_drive_hint_roots(app_dir.resolve(), client_dir.resolve(), game_root.resolve(), user_home)
        return candidates

    def inspect_java_runtime(self, java_path: Path, source: str) -> Optional[JavaRuntime]:
        try:
            proc = subprocess.run(
                [str(java_path), "-XshowSettings:properties", "-version"],
                capture_output=True,
                text=True,
                encoding=SYSTEM_ENCODING,
                errors="replace",
                timeout=15,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        except (FileNotFoundError, OSError):
            return None

        output_parts = [part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip()]
        version_text = "\n".join(output_parts).strip()
        match = re.search(r'version "(\d+)(?:\.(\d+))?', version_text)
        if not match:
            return None
        lines = [line.strip() for line in version_text.splitlines() if line.strip()]
        first_line = next((line for line in lines if 'version "' in line.lower()), lines[0] if lines else "")
        is_64bit = bool(
            re.search(
                r"(?im)(64-Bit|sun\.arch\.data\.model\s*=\s*64|os\.arch\s*=\s*(amd64|x86_64))",
                version_text,
            )
        )
        return JavaRuntime(
            path=java_path.resolve(),
            major=int(match.group(1)),
            source=source,
            version_text=first_line or version_text,
            is_64bit=is_64bit,
        )

    def format_java_runtime_list(self, runtimes: List[JavaRuntime]) -> str:
        if not runtimes:
            return "未检测到任何可用 Java。"
        lines = [f"- Java {item.major} | {item.source} | {item.path}" for item in runtimes[:8]]
        if len(runtimes) > 8:
            lines.append(f"- 其余 {len(runtimes) - 8} 个结果已省略")
        return "\n".join(lines)

    def ensure_java(self, client_dir: Path, game_root: Path, candidate: VersionCandidate) -> JavaRuntime:
        required_major = self.get_required_java_major(candidate)
        require_64bit = self.java_requires_64bit(required_major)

        runtimes: List[JavaRuntime] = []
        for java_path, source in self.collect_candidate_java_paths(client_dir, game_root):
            runtime = self.inspect_java_runtime(java_path, source)
            if runtime:
                runtimes.append(runtime)

        matched = [
            item
            for item in runtimes
            if item.major == required_major and (item.is_64bit or not require_64bit)
        ]
        if matched:
            return matched[0]

        if require_64bit and any(item.major == required_major for item in runtimes):
            raise RuntimeError(
                f"Minecraft {candidate.minecraft_version} 需要 64 位 Java {required_major}，"
                f"但当前扫描到的同版本 Java 不满足 64 位要求。\n{self.format_java_runtime_list(runtimes)}"
            )

        raise RuntimeError(
            f"Minecraft {candidate.minecraft_version} 需要 Java {required_major}，"
            f"但当前未找到完全匹配的 java.exe。\n{self.format_java_runtime_list(runtimes)}"
        )

    def normalize_client_root(self, client_dir: Path) -> Path:
        minecraft_dir = client_dir / ".minecraft"
        if minecraft_dir.exists() and minecraft_dir.is_dir():
            return minecraft_dir

        has_mods = (client_dir / "mods").is_dir()
        has_versions = (client_dir / "versions").is_dir()
        has_config = (client_dir / "config").is_dir()
        has_root_version_manifest = any((client_dir / f"{item.stem}.jar").exists() for item in client_dir.glob("*.json"))
        if has_mods and (has_versions or has_config or has_root_version_manifest):
            return client_dir

        raise RuntimeError("所选目录不是可识别的客户端实例目录。")

    def extract_minecraft_version_hint(self, text: str) -> str:
        if not text:
            return ""
        match = re.search(r"\b1\.\d+(?:\.\d+)?\b", text)
        return match.group(0) if match else ""

    def parse_library_coordinate(self, coordinate: str) -> Tuple[str, str, str, str]:
        parts = [part.strip() for part in coordinate.split(":")]
        if len(parts) < 3:
            return "", "", "", ""
        group = parts[0]
        artifact = parts[1]
        version = parts[2]
        classifier = ":".join(parts[3:]) if len(parts) > 3 else ""
        if "@" in version:
            version = version.split("@", 1)[0].strip()
        if classifier and "@" in classifier:
            classifier = classifier.split("@", 1)[0].strip()
        return group, artifact, version, classifier

    def split_forge_library_version(self, library_version: str) -> Tuple[str, str]:
        if "-" not in library_version:
            return "", ""
        minecraft_version, loader_version = library_version.split("-", 1)
        return minecraft_version.strip(), loader_version.strip()

    def parse_minecraft_version_from_neoforge(self, loader_version: str, fallback: str = "") -> str:
        fallback_version = self.extract_minecraft_version_hint(fallback)
        if fallback_version:
            return fallback_version
        match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", loader_version)
        if not match:
            return ""
        major = match.group(1)
        minor = match.group(2)
        if minor == "0":
            return f"1.{major}"
        return f"1.{major}.{minor}"

    def parse_version_candidate(self, json_path: Path, data: dict) -> Optional[VersionCandidate]:
        libraries = [str(item.get("name", "")) for item in data.get("libraries", []) if isinstance(item, dict)]
        inherits_from = str(data.get("inheritsFrom") or "").strip()
        version_id = str(data.get("id") or json_path.stem).strip()
        java_major = int((data.get("javaVersion") or {}).get("majorVersion") or 21)
        inherited_minecraft_version = self.extract_minecraft_version_hint(inherits_from)
        version_hint = self.extract_minecraft_version_hint(version_id)

        for name in libraries:
            group, artifact, library_version, _classifier = self.parse_library_coordinate(name)

            if group == "net.fabricmc" and artifact == "fabric-loader":
                loader_version = library_version
                minecraft_version = inherited_minecraft_version
                if not minecraft_version:
                    for library_name in libraries:
                        lib_group, lib_artifact, lib_version, _ = self.parse_library_coordinate(library_name)
                        if lib_group == "net.fabricmc" and lib_artifact == "intermediary":
                            minecraft_version = lib_version
                            break
                if not minecraft_version:
                    minecraft_version = version_hint
                return VersionCandidate(version_id, minecraft_version, LoaderType.FABRIC.value, loader_version, java_major, json_path)

            if group == "org.quiltmc" and artifact == "quilt-loader":
                loader_version = library_version
                minecraft_version = inherited_minecraft_version
                if not minecraft_version:
                    minecraft_version = version_hint
                return VersionCandidate(version_id, minecraft_version, LoaderType.QUILT.value, loader_version, java_major, json_path)

            if group == "net.minecraftforge" and artifact in {"forge", "fmlloader"}:
                minecraft_version, loader_version = self.split_forge_library_version(library_version)
                if not minecraft_version or not loader_version:
                    continue
                return VersionCandidate(version_id, minecraft_version, LoaderType.FORGE.value, loader_version, java_major, json_path)

            if group == "net.neoforged" and artifact == "neoforge":
                loader_version = library_version
                minecraft_version = self.parse_minecraft_version_from_neoforge(loader_version, inherits_from or version_id)
                return VersionCandidate(version_id, minecraft_version, LoaderType.NEOFORGE.value, loader_version, java_major, json_path)
        return None

    def is_probable_version_manifest(self, json_path: Path, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        if not isinstance(data.get("libraries"), list):
            return False
        if not data.get("mainClass") and not data.get("downloads"):
            return False
        sibling_jar = json_path.with_suffix(".jar")
        if sibling_jar.exists():
            return True
        return any(
            str(item.get("name", "")).startswith(
                (
                    "net.fabricmc:fabric-loader:",
                    "org.quiltmc:quilt-loader:",
                    "net.minecraftforge:forge:",
                    "net.minecraftforge:fmlloader:",
                    "net.neoforged:neoforge:",
                    "net.neoforged:fancymodloader:",
                )
            )
            for item in data.get("libraries", [])
            if isinstance(item, dict)
        )

    def find_root_level_version_candidates(self, game_root: Path) -> List[VersionCandidate]:
        candidates: List[VersionCandidate] = []
        for json_path in sorted(game_root.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not self.is_probable_version_manifest(json_path, data):
                continue
            candidate = self.parse_version_candidate(json_path, data)
            if candidate:
                candidates.append(candidate)
        return candidates

    def find_version_candidates(self, game_root: Path) -> List[VersionCandidate]:
        candidates = self.find_root_level_version_candidates(game_root)

        versions_dir = game_root / "versions"
        if versions_dir.is_dir():
            for json_path in versions_dir.rglob("*.json"):
                if json_path.name in {"usercache.json", "debug-profile.json", "log.json"}:
                    continue
                if json_path.stem != json_path.parent.name:
                    continue
                try:
                    data = json.loads(json_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                candidate = self.parse_version_candidate(json_path, data)
                if candidate:
                    candidates.append(candidate)

        unique: Dict[Tuple[str, str, str, str], VersionCandidate] = {}
        for candidate in candidates:
            key = (candidate.version_id, candidate.minecraft_version, candidate.loader, candidate.loader_version)
            unique[key] = candidate

        final_candidates = sorted(unique.values(), key=self.version_candidate_sort_key)
        if not final_candidates:
            raise RuntimeError("未能从当前根目录或 versions 目录中的版本清单识别出 Fabric / Forge / NeoForge 版本。")
        return final_candidates

    def choose_version_candidate(self, candidates: List[VersionCandidate]) -> VersionCandidate:
        if len(candidates) == 1:
            return candidates[0]
        selected = self.request_version_choice(candidates)
        if not selected:
            raise RuntimeError("已取消版本选择。")
        return selected

    def resolve_fabric_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        versions = self.http_get_json("https://meta.fabricmc.net/v2/versions/installer")
        stable_versions = [item for item in versions if item.get("stable")]
        if not stable_versions:
            raise RuntimeError("Fabric 官方未返回可用的 installer 版本。")
        installer_version = str(stable_versions[0]["version"])
        return InstallerSpec(
            loader=LoaderType.FABRIC.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=installer_version,
            download_url=f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{installer_version}/fabric-installer-{installer_version}.jar",
            file_name=f"fabric-installer-{installer_version}.jar",
        )

    def resolve_forge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        metadata = ET.fromstring(self.http_get_text("https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"))
        wanted = f"{candidate.minecraft_version}-{candidate.loader_version}"
        versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
        if wanted not in versions:
            raise RuntimeError(f"Forge 官方源未找到完全同版本服务端安装器：{wanted}")
        return InstallerSpec(
            loader=LoaderType.FORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=wanted,
            download_url=f"https://maven.minecraftforge.net/net/minecraftforge/forge/{wanted}/forge-{wanted}-installer.jar",
            file_name=f"forge-{wanted}-installer.jar",
        )

    def resolve_neoforge_installer(self, candidate: VersionCandidate) -> InstallerSpec:
        metadata = ET.fromstring(self.http_get_text("https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"))
        versions = [item.text for item in metadata.findall("./versioning/versions/version") if item.text]
        if candidate.loader_version not in versions:
            raise RuntimeError(f"NeoForge 官方源未找到完全同版本服务端安装器：{candidate.loader_version}")
        return InstallerSpec(
            loader=LoaderType.NEOFORGE.value,
            minecraft_version=candidate.minecraft_version,
            loader_version=candidate.loader_version,
            installer_version=candidate.loader_version,
            download_url=f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{candidate.loader_version}/neoforge-{candidate.loader_version}-installer.jar",
            file_name=f"neoforge-{candidate.loader_version}-installer.jar",
        )

    def resolve_installer_spec(self, candidate: VersionCandidate) -> InstallerSpec:
        if candidate.loader == LoaderType.FABRIC.value:
            return self.resolve_fabric_installer(candidate)
        if candidate.loader == LoaderType.FORGE.value:
            return self.resolve_forge_installer(candidate)
        if candidate.loader == LoaderType.NEOFORGE.value:
            return self.resolve_neoforge_installer(candidate)
        if candidate.loader == LoaderType.QUILT.value:
            raise RuntimeError("2.08 的一键制作服务端模式暂不支持 Quilt。")
        raise RuntimeError(f"暂不支持自动制作 {candidate.loader} 服务端。")

    def run_process_capture(
        self,
        args: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
        install_log_only: bool = False,
    ) -> Tuple[int, List[str]]:
        lines: List[str] = []
        display = " ".join(str(item) for item in args)
        self.log_line(f"执行命令：{display}")
        process = subprocess.Popen(
            list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            encoding=SYSTEM_ENCODING,
            errors="replace",
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )

        stream_queue: "queue.Queue[Optional[str]]" = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                stream_queue.put(line.rstrip("\r\n"))
            stream_queue.put(None)

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()

        deadline = time.time() + timeout_seconds
        stream_closed = False
        while True:
            try:
                item = stream_queue.get(timeout=0.2)
            except queue.Empty:
                if time.time() > deadline:
                    break
                item = ""
            if item == "":
                pass
            elif item is None:
                if stream_closed:
                    pass
                else:
                    stream_closed = True
            else:
                lines.append(item)
                self.install_log_lines.append(item)
                if not install_log_only:
                    self.log_line(item)

            if process.poll() is not None and stream_closed and stream_queue.empty():
                break
            if time.time() > deadline:
                break

        if process.poll() is None:
            process.kill()
            raise RuntimeError(f"命令执行超时：{display}")

        return process.returncode, lines

    def download_installer(self, spec: InstallerSpec, temp_dir: Path) -> Path:
        destination = temp_dir / spec.file_name
        self.log_line(f"下载官方安装器：{spec.download_url}")
        self.http_download(spec.download_url, destination)
        return destination

    def install_server(
        self,
        output_root: Path,
        candidate: VersionCandidate,
        installer_path: Path,
        java_runtime: JavaRuntime,
    ) -> None:
        if candidate.loader == LoaderType.FABRIC.value:
            args = [
                str(java_runtime.path),
                "-jar",
                str(installer_path),
                "server",
                "-dir",
                str(output_root),
                "-mcversion",
                candidate.minecraft_version,
                "-loader",
                candidate.loader_version,
                "-downloadMinecraft",
            ]
        else:
            args = [
                str(java_runtime.path),
                "-jar",
                str(installer_path),
                "--installServer",
                str(output_root),
            ]
        code, _ = self.run_process_capture(args, output_root, DEFAULT_INSTALL_TIMEOUT_SECONDS)
        if code != 0:
            raise RuntimeError("服务端安装器执行失败。")

    def classify_mod_directory(self, mods_dir: Path) -> List[Dict[str, Any]]:
        if not mods_dir.is_dir():
            return []

        jar_files = sorted(mods_dir.glob("*.jar"), key=lambda item: item.name.lower())
        total = len(jar_files)
        self.log_line(f"开始分析客户端 mods：共 {total} 个 jar 模组。")
        worker_count = get_classification_worker_count(total)
        if total > 1:
            self.log_line(f"联网分类使用 {worker_count} 个并发线程。")

        first_span = 6 if self.enable_second_pass else 9

        def first_pass_progress(completed: int, inner_total: int, jar: Path) -> None:
            percent = completed / max(inner_total, 1)
            self.set_progress(52 + percent * first_span)
            self.set_status(f"{TaskStage.CLASSIFY_MODS.value}：正在汇总 [{completed}/{inner_total}] {jar.name}")

        def first_pass_result(completed: int, inner_total: int, jar: Path, row: Dict[str, Any]) -> None:
            self.log_line(
                f"[模组 {completed}/{inner_total}] {jar.name} -> {get_category_label(row['Category'])} | {row['Reason']}"
            )

        results = classify_jars_parallel(
            self.classifier,
            jar_files,
            self.use_mcmod,
            progress_callback=first_pass_progress,
            result_callback=first_pass_result,
        )

        unknown_rows = [row for row in results if row["Category"] == "unknown"]
        if self.enable_second_pass:
            if unknown_rows:
                retry_total = len(unknown_rows)
                retry_worker_count = get_classification_worker_count(retry_total)
                self.log_line(f"开始进行 2次筛选：仅重试首轮未确定的 {retry_total} 个模组。")
                if retry_total > 1:
                    self.log_line(f"2次筛选使用 {retry_worker_count} 个并发线程。")

                def second_pass_progress(completed: int, inner_total: int, jar: Path) -> None:
                    percent = completed / max(inner_total, 1)
                    self.set_progress(58 + percent * 3)
                    self.set_status(f"{TaskStage.CLASSIFY_MODS.value}：正在进行 2次筛选 [{completed}/{inner_total}] {jar.name}")

                def second_pass_result(completed: int, inner_total: int, jar: Path, row: Dict[str, Any]) -> None:
                    self.log_line(
                        f"[2次筛选 {completed}/{inner_total}] {jar.name} -> {get_category_label(row['Category'])} | {row['Reason']}"
                    )

                recovered = rerun_unknown_classifications(
                    results,
                    self.use_mcmod,
                    progress_callback=second_pass_progress,
                    result_callback=second_pass_result,
                )
                remaining_unknown = sum(1 for row in results if row["Category"] == "unknown")
                self.log_line(f"2次筛选完成：回补 {recovered} 个，仍待人工确认 {remaining_unknown} 个。")
            else:
                self.log_line("已开启 2次筛选，但首轮没有 unknown 模组，跳过重试。")

        unknown_rows = [row for row in results if row["Category"] == "unknown"]
        server_keep_rows = [row for row in results if row["Category"] == "server-keep"]
        client_only_rows = [row for row in results if row["Category"] == "client-only"]
        damaged_rows = [row for row in results if row.get("JarStatus") == "damaged"]
        self.log_line(
            "模组自动筛选完成："
            f"服务端保留 {len(server_keep_rows)} 个，"
            f"待人工确认 {len(unknown_rows)} 个，"
            f"纯客户端 {len(client_only_rows)} 个。"
        )
        if damaged_rows:
            self.log_line(f"检测到 {len(damaged_rows)} 个损坏或元数据损坏的 Jar，已在报告中单独标记：")
            for row in damaged_rows:
                self.log_line(f" - {row['FileName']} | {row.get('JarIssue') or row['Reason']}")
        if unknown_rows:
            self.log_line("以下模组未能自动确认，人工核查时会优先展示：")
            for row in unknown_rows:
                self.log_line(f" - {row['FileName']} | {row['Reason']}")
        return results

    def build_mod_review_items(self, mod_results: List[Dict[str, Any]]) -> List[ReviewItem]:
        items: List[ReviewItem] = []
        grouped_rows: Dict[str, List[Dict[str, Any]]] = {}
        for row in mod_results:
            grouped_rows.setdefault(row["Category"], []).append(row)

        for category in sorted(grouped_rows, key=lambda item: (CATEGORY_SORT_ORDER.get(item, 99), item)):
            group = sorted(
                grouped_rows[category],
                key=lambda item: (0 if item.get("JarStatus") == "damaged" else 1, item["FileName"].lower()),
            )
            items.append(
                ReviewItem(
                    key=f"__header__{category}",
                    label=f"==== {get_category_label(category)}（{len(group)}） ====",
                    detail="待人工确认项默认排在最前；左键点击文件名可直接复制；纯客户端默认不勾选，但仍会展示给你复核。",
                    checked=False,
                    enabled=False,
                )
            )
            for row in group:
                detail_parts = [
                    f"来源：{row['DecisionSource']}",
                    f"原因：{row['Reason']}",
                ]
                if row.get("JarStatus") == "damaged":
                    detail_parts.insert(0, f"Jar：损坏（{row.get('JarIssue') or '读取异常'}）")
                if row.get("ModName") and row["ModName"] != row["FileName"]:
                    detail_parts.insert(0, f"名称：{row['ModName']}")
                if row.get("EvidenceUrl"):
                    detail_parts.append(f"链接：{row['EvidenceUrl']}")
                items.append(
                    ReviewItem(
                        key=row["FileName"],
                        label=row["FileName"],
                        detail=" | ".join(detail_parts),
                        checked=category != "client-only",
                    )
                )
        return items

    def copy_selected_mods(self, mod_results: List[Dict[str, Any]], selected_keys: List[str], mods_target_dir: Path) -> int:
        mods_target_dir.mkdir(parents=True, exist_ok=True)
        selected_set = set(selected_keys)
        copied = 0
        for row in mod_results:
            source_path = row["Path"]
            is_selected = row["FileName"] in selected_set
            row["SelectedForServer"] = is_selected
            if is_selected:
                shutil.copy2(source_path, mods_target_dir / source_path.name)
                copied += 1
        return copied

    def enumerate_copyable_directories(self, game_root: Path) -> List[ReviewItem]:
        items: List[ReviewItem] = []
        for child in sorted(game_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            lowered = child.name.lower()
            if lowered.startswith("."):
                continue
            if lowered in DEFAULT_SKIP_DIRS:
                continue
            if "xaero" in lowered:
                continue
            items.append(
                ReviewItem(
                    key=child.name,
                    label=child.name,
                    detail=str(child),
                    checked=True,
                )
            )
        return items

    def copy_selected_directories(self, game_root: Path, output_root: Path, selected_keys: List[str]) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        selected_set = set(selected_keys)
        for child in sorted(game_root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            if child.name in selected_set:
                shutil.copytree(child, output_root / child.name, dirs_exist_ok=True)
                summary.append({"Directory": child.name, "Copied": True})
            elif child.name.lower() not in DEFAULT_SKIP_DIRS and "xaero" not in child.name.lower() and not child.name.startswith(".") and child.name != "mods":
                summary.append({"Directory": child.name, "Copied": False})
        return summary

    def estimate_memory_settings(self, mod_count: int) -> Tuple[str, str]:
        if mod_count <= 50:
            xmx = "4G"
        elif mod_count <= 100:
            xmx = "6G"
        elif mod_count <= 180:
            xmx = "8G"
        elif mod_count <= 260:
            xmx = "10G"
        else:
            xmx = "12G"
        return "2G", xmx

    def write_user_jvm_args(self, output_root: Path, xms: str, xmx: str) -> None:
        path = output_root / "user_jvm_args.txt"
        path.write_text(
            "\n".join(
                [
                    "# 自动筛选模组分类器 2.08 生成",
                    f"-Xms{xms}",
                    f"-Xmx{xmx}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def format_java_executable(self, java_runtime: JavaRuntime) -> str:
        return f'"{java_runtime.path}"'

    def get_batch_root_cd_line(self, script_depth: int = 0) -> str:
        if script_depth <= 0:
            return "cd /d %~dp0"
        return 'cd /d "%~dp0\\' + ("..\\" * (script_depth - 1)) + '.."'

    def build_fabric_launch_lines(
        self,
        output_root: Path,
        xms: str,
        xmx: str,
        java_runtime: JavaRuntime,
        script_depth: int = 0,
    ) -> List[str]:
        launch_jar = output_root / "fabric-server-launch.jar"
        if not launch_jar.exists():
            raise RuntimeError("Fabric 服务端安装后未找到 fabric-server-launch.jar。")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(script_depth),
            f"{self.format_java_executable(java_runtime)} -Xms{xms} -Xmx{xmx} -jar fabric-server-launch.jar nogui",
        ]

    def write_batch_script(self, script_path: Path, lines: Sequence[str]) -> Path:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return script_path

    def get_run_bat_path(self, output_root: Path) -> Path:
        run_bat = output_root / "run.bat"
        if not run_bat.exists():
            raise RuntimeError("服务端安装后未找到官方 run.bat。")
        return run_bat

    def build_run_bat_wrapper_lines(self, output_root: Path, java_runtime: JavaRuntime) -> List[str]:
        run_bat = self.get_run_bat_path(output_root)
        java_bin_dir = java_runtime.path.parent
        java_home = java_bin_dir.parent
        run_target = run_bat.relative_to(output_root).as_posix().replace("/", "\\")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(),
            f'set "JAVA_HOME={java_home}"',
            f'set "PATH={java_bin_dir};%PATH%"',
            f'call "{run_target}" nogui %*',
        ]

    def get_expected_win_args_relative_path(self, candidate: VersionCandidate) -> Path:
        if candidate.loader == LoaderType.FORGE.value:
            return (
                Path("libraries")
                / "net"
                / "minecraftforge"
                / "forge"
                / f"{candidate.minecraft_version}-{candidate.loader_version}"
                / "win_args.txt"
            )
        if candidate.loader == LoaderType.NEOFORGE.value:
            return (
                Path("libraries")
                / "net"
                / "neoforged"
                / "neoforge"
                / candidate.loader_version
                / "win_args.txt"
            )
        raise RuntimeError(f"{candidate.loader} 不使用 win_args.txt 启动链路。")

    def parse_win_args_path_from_run_bat(self, output_root: Path) -> Optional[Path]:
        run_bat = output_root / "run.bat"
        if not run_bat.exists():
            return None
        pattern = re.compile(r'@"?(?P<path>[^"\s]+win_args\.txt)"?', re.IGNORECASE)
        for line in run_bat.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = pattern.search(line)
            if not match:
                continue
            relative_text = match.group("path").replace("/", "\\")
            candidate_path = output_root / Path(relative_text)
            try:
                resolved_path = candidate_path.resolve()
            except Exception:
                continue
            if self.is_same_or_nested_path(output_root.resolve(), resolved_path) and resolved_path.exists():
                return resolved_path
        return None

    def resolve_win_args_file(self, output_root: Path, candidate: VersionCandidate) -> Path:
        exact_path = output_root / self.get_expected_win_args_relative_path(candidate)
        if exact_path.exists():
            return exact_path
        fallback_path = self.parse_win_args_path_from_run_bat(output_root)
        if fallback_path:
            self.log_line(f"未在标准位置找到 win_args.txt，已改为按官方 run.bat 解析：{fallback_path}")
            return fallback_path
        raise RuntimeError("未找到与当前 Forge / NeoForge 版本匹配的 win_args.txt。")

    def build_win_args_launch_lines(
        self,
        output_root: Path,
        candidate: VersionCandidate,
        java_runtime: JavaRuntime,
    ) -> List[str]:
        win_args = self.resolve_win_args_file(output_root, candidate)
        relative = win_args.relative_to(output_root).as_posix().replace("/", "\\")
        return [
            "@echo off",
            "setlocal",
            self.get_batch_root_cd_line(1),
            f"{self.format_java_executable(java_runtime)} @user_jvm_args.txt @{relative} nogui",
        ]

    def write_launch_scripts(
        self,
        output_root: Path,
        candidate: VersionCandidate,
        xms: str,
        xmx: str,
        java_runtime: JavaRuntime,
    ) -> LaunchScripts:
        report_dir = output_root / TOOL_DIR_NAME
        if candidate.loader == LoaderType.FABRIC.value:
            user_lines = self.build_fabric_launch_lines(output_root, xms, xmx, java_runtime)
            internal_lines = self.build_fabric_launch_lines(output_root, xms, xmx, java_runtime, script_depth=1)
        else:
            self.write_user_jvm_args(output_root, xms, xmx)
            user_lines = self.build_run_bat_wrapper_lines(output_root, java_runtime)
            internal_lines = self.build_win_args_launch_lines(output_root, candidate, java_runtime)
        return LaunchScripts(
            user_script=self.write_batch_script(output_root / "启动服务器.bat", user_lines),
            internal_script=self.write_batch_script(report_dir / "内部验证启动.bat", internal_lines),
        )

    def set_eula_true(self, output_root: Path) -> None:
        path = output_root / "eula.txt"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            content = re.sub(r"(?im)^eula\s*=\s*false\s*$", "eula=true", content)
            if "eula=true" not in content:
                content += "\neula=true\n"
        else:
            content = "eula=true\n"
        path.write_text(content, encoding="utf-8")

    def set_online_mode_false(self, output_root: Path) -> None:
        path = output_root / "server.properties"
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"(?im)^online-mode=", content):
                content = re.sub(r"(?im)^online-mode=.*$", "online-mode=false", content)
            else:
                content += "\nonline-mode=false\n"
        else:
            content = "online-mode=false\n"
        path.write_text(content, encoding="utf-8")

    def stop_process(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdin:
                process.stdin.write("stop\n")
                process.stdin.flush()
        except Exception:
            pass
        try:
            process.wait(timeout=15)
        except Exception:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )

    def run_server_script(self, output_root: Path, launch_script: Path, mode: str) -> None:
        try:
            launch_target = launch_script.relative_to(output_root).as_posix().replace("/", "\\")
        except ValueError:
            launch_target = str(launch_script)
        process = subprocess.Popen(
            ["cmd.exe", "/c", launch_target],
            cwd=str(output_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            encoding=SYSTEM_ENCODING,
            errors="replace",
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )

        line_queue: "queue.Queue[Optional[str]]" = queue.Queue()

        def reader() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                line_queue.put(line.rstrip("\r\n"))
            line_queue.put(None)

        threading.Thread(target=reader, daemon=True).start()

        deadline = time.time() + DEFAULT_SERVER_TIMEOUT_SECONDS
        eula_path = output_root / "eula.txt"
        properties_path = output_root / "server.properties"
        saw_success = False
        stream_closed = False
        generated_at: Optional[float] = None

        while True:
            try:
                item = line_queue.get(timeout=0.2)
            except queue.Empty:
                item = ""

            if item == "":
                pass
            elif item is None:
                if not stream_closed and process.poll() is not None:
                    stream_closed = True
            else:
                self.install_log_lines.append(item)
                self.log_line(item)
                if "Done (" in item or "For help, type" in item:
                    saw_success = True
                    if mode == "verify":
                        self.stop_process(process)

            if mode == "init":
                if process.poll() is not None:
                    break
                if eula_path.exists() or properties_path.exists():
                    if generated_at is None:
                        generated_at = time.time()
                        self.log_line("检测到服务端已生成初始化配置，准备优雅结束首次启动。")
                    if time.time() - generated_at >= 5:
                        self.stop_process(process)
                        break

            if mode == "verify":
                if saw_success and process.poll() is not None:
                    break

            if process.poll() is not None and stream_closed and line_queue.empty():
                break

            if time.time() > deadline:
                if mode == "verify" and saw_success:
                    self.stop_process(process)
                    break
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                raise RuntimeError(f"服务端{('首次启动' if mode == 'init' else '验证启动')}超过 {DEFAULT_SERVER_TIMEOUT_SECONDS} 秒仍未完成。")

        if mode == "init":
            if not eula_path.exists() and not properties_path.exists():
                raise RuntimeError("首次启动后未生成 eula.txt 或 server.properties。")
            return

        if not saw_success:
            raise RuntimeError("第二次启动未检测到服务端启动完成标志。")

    def is_same_or_nested_path(self, base_path: Path, candidate_path: Path) -> bool:
        try:
            candidate_path.relative_to(base_path)
            return True
        except ValueError:
            return False

    def write_mod_reports(self, report_dir: Path, mod_results: List[Dict[str, Any]]) -> None:
        json_path = report_dir / f"{MOD_REPORT_BASENAME}.json"
        csv_path = report_dir / f"{MOD_REPORT_BASENAME}.csv"
        txt_path = report_dir / f"{MOD_REPORT_BASENAME}.txt"

        serializable = []
        for row in mod_results:
            copy_row = dict(row)
            path_value = copy_row.get("Path")
            if isinstance(path_value, Path):
                copy_row["Path"] = str(path_value)
            serializable.append(copy_row)

        json_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        write_csv_with_labels(csv_path, serializable)

        summary_lines = [
            f"服务端保留: {sum(1 for row in mod_results if row['Category'] == 'server-keep')}",
            f"纯客户端: {sum(1 for row in mod_results if row['Category'] == 'client-only')}",
            f"无法分类: {sum(1 for row in mod_results if row['Category'] == 'unknown')}",
            f"损坏Jar: {sum(1 for row in mod_results if row.get('JarStatus') == 'damaged')}",
            f"最终复制: {sum(1 for row in mod_results if row.get('SelectedForServer'))}",
        ]
        txt_path.write_text("\n".join(summary_lines), encoding="utf-8")

    def write_directory_summary(self, report_dir: Path, summary_rows: List[Dict[str, Any]]) -> None:
        (report_dir / CONFIG_COPY_SUMMARY_NAME).write_text(
            json.dumps(summary_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_logs(self, report_dir: Path) -> Tuple[Path, Path]:
        build_log_path = report_dir / BUILD_LOG_NAME
        install_log_path = report_dir / INSTALL_LOG_NAME
        build_log_path.write_text("\n".join(self.build_log_lines) + "\n", encoding="utf-8")
        install_log_path.write_text("\n".join(self.install_log_lines) + "\n", encoding="utf-8")
        return build_log_path, install_log_path

    def build_server(self, client_dir: Path, output_root: Path) -> Dict[str, Path]:
        temp_workspace = Path(tempfile.mkdtemp(prefix="auto-mod-classifier-"))
        report_dir = output_root / TOOL_DIR_NAME
        mod_results: Optional[List[Dict[str, Any]]] = None
        directory_summary: Optional[List[Dict[str, Any]]] = None
        try:
            self.set_stage(TaskStage.PRECHECK, 2, "校验目录")
            if not client_dir.exists() or not client_dir.is_dir():
                raise RuntimeError("客户端目录不存在。")
            if output_root.exists():
                if not output_root.is_dir():
                    raise RuntimeError("服务端输出路径不是目录。")
                if any(output_root.iterdir()):
                    raise RuntimeError("服务端输出目录必须是新的空目录。")
            else:
                output_root.mkdir(parents=True, exist_ok=True)

            client_resolved = client_dir.resolve()
            output_resolved = output_root.resolve()
            if client_resolved == output_resolved or self.is_same_or_nested_path(client_resolved, output_resolved):
                raise RuntimeError("服务端输出目录不能与客户端目录相同，也不能位于客户端目录内部。")

            self.set_stage(TaskStage.CLIENT_SCAN, 10, "识别客户端实例根目录")
            game_root = self.normalize_client_root(client_dir)
            self.log_line(f"客户端实例根目录：{game_root}")

            self.set_stage(TaskStage.CLIENT_SCAN, 18, "扫描版本清单")
            candidates = self.find_version_candidates(game_root)
            chosen = self.choose_version_candidate(candidates)
            self.log_line(f"目标版本：{chosen.display_name} | 版本清单 Java {chosen.java_major}")
            if chosen.loader == LoaderType.QUILT.value:
                raise RuntimeError("检测到 Quilt 客户端，2.08 的一键制作服务端模式暂不支持 Quilt。")

            required_java_major = self.get_required_java_major(chosen)
            self.set_stage(TaskStage.PRECHECK, 24, f"匹配 Java {required_java_major}")
            java_runtime = self.ensure_java(client_dir, game_root, chosen)
            self.log_line(f"Minecraft {chosen.minecraft_version} 需要 Java {required_java_major}")
            self.log_line(f"已选 Java：{java_runtime.summary} | 来源：{java_runtime.source}")

            self.set_stage(TaskStage.DOWNLOAD_INSTALLER, 26, "解析官方安装器地址")
            installer_spec = self.resolve_installer_spec(chosen)
            installer_path = self.download_installer(installer_spec, temp_workspace)

            self.set_stage(TaskStage.INSTALL_SERVER, 40, "安装服务端")
            self.install_server(output_root, chosen, installer_path, java_runtime)

            self.set_stage(TaskStage.CLASSIFY_MODS, 52, "分析客户端 mods")
            mod_results = self.classify_mod_directory(game_root / "mods")
            mod_review_items = self.build_mod_review_items(mod_results)
            if mod_review_items:
                selected_mod_keys = self.request_checklist(
                    "Mod复制核查",
                    "已按分类展示全部模组。待人工确认项排在最前并默认勾选；纯客户端默认不勾选，但会展示给你复核。",
                    mod_review_items,
                )
                if selected_mod_keys is None:
                    raise RuntimeError("已取消模组复制核查。")
            else:
                selected_mod_keys = []
                self.log_line("客户端 mods 目录中没有需要复制的模组。")

            self.set_stage(TaskStage.COPY_MODS, 63, "复制服务端模组")
            copied_mods = self.copy_selected_mods(mod_results, selected_mod_keys, output_root / "mods")
            self.log_line(f"已复制 {copied_mods} 个模组到服务端。")

            self.set_stage(TaskStage.COPY_CONFIGS, 72, "收集配置目录候选")
            config_review_items = self.enumerate_copyable_directories(game_root)
            if config_review_items:
                selected_directories = self.request_checklist(
                    "配置目录复制核查",
                    "默认勾选所有配置类目录；取消勾选的目录不会复制到服务端。",
                    config_review_items,
                )
                if selected_directories is None:
                    raise RuntimeError("已取消配置目录复制核查。")
            else:
                selected_directories = []
                self.log_line("没有可复制的顶层配置目录。")

            self.set_stage(TaskStage.COPY_CONFIGS, 78, "复制配置目录")
            directory_summary = self.copy_selected_directories(game_root, output_root, selected_directories)
            self.log_line(f"已复制 {sum(1 for row in directory_summary if row['Copied'])} 个目录到服务端。")

            self.set_stage(TaskStage.PREPARE_LAUNCH, 84, "生成统一启动脚本")
            copied_mod_count = sum(1 for row in mod_results if row.get("SelectedForServer"))
            xms, xmx = self.estimate_memory_settings(copied_mod_count)
            self.log_line(f"按 {copied_mod_count} 个模组分配内存：Xms={xms}, Xmx={xmx}")
            launch_scripts = self.write_launch_scripts(output_root, chosen, xms, xmx, java_runtime)

            self.set_stage(TaskStage.FIRST_BOOT, 89, "首次启动生成服务器配置")
            self.run_server_script(output_root, launch_scripts.internal_script, "init")

            self.set_stage(TaskStage.PATCH_CONFIG, 93, "写入 eula 与 server.properties")
            self.set_eula_true(output_root)
            self.set_online_mode_false(output_root)

            self.set_stage(TaskStage.VERIFY_BOOT, 97, "第二次启动验证")
            self.run_server_script(output_root, launch_scripts.internal_script, "verify")

            report_dir.mkdir(parents=True, exist_ok=True)
            self.write_mod_reports(report_dir, mod_results)
            self.write_directory_summary(report_dir, directory_summary)
            _, install_log_path = self.write_logs(report_dir)

            self.set_stage(TaskStage.COMPLETE, 100, "服务端制作完成")
            return {
                "server_root": output_root,
                "report_dir": report_dir,
                "launch_script": launch_scripts.user_script,
                "install_log_path": install_log_path,
            }
        except Exception:
            if output_root.exists():
                report_dir.mkdir(parents=True, exist_ok=True)
                if mod_results is not None:
                    self.write_mod_reports(report_dir, mod_results)
                if directory_summary is not None:
                    self.write_directory_summary(report_dir, directory_summary)
                self.write_logs(report_dir)
            raise
        finally:
            shutil.rmtree(temp_workspace, ignore_errors=True)


class VersionSelectionDialog:
    def __init__(self, parent: tk.Tk, candidates: List[VersionCandidate]):
        self.result: Optional[VersionCandidate] = None
        self.candidates = candidates
        self.window = tk.Toplevel(parent)
        self.window.title("选择目标版本")
        self.window.geometry("780x360")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        ttk.Label(
            self.window,
            text="检测到多个可用版本，请选择要制作服务端的客户端版本：",
            padding=12,
        ).pack(anchor="w")

        columns = ("version", "mc", "loader", "loader_version", "java", "path")
        tree = ttk.Treeview(self.window, columns=columns, show="headings", height=10)
        self.tree = tree
        tree.heading("version", text="版本ID")
        tree.heading("mc", text="Minecraft")
        tree.heading("loader", text="加载器")
        tree.heading("loader_version", text="加载器版本")
        tree.heading("java", text="Java")
        tree.heading("path", text="版本文件")
        tree.column("version", width=150)
        tree.column("mc", width=100)
        tree.column("loader", width=90)
        tree.column("loader_version", width=120)
        tree.column("java", width=60)
        tree.column("path", width=230)
        for index, item in enumerate(candidates):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item.version_id,
                    item.minecraft_version,
                    item.loader,
                    item.loader_version,
                    item.java_major,
                    str(item.json_path),
                ),
            )
        tree.pack(fill="both", expand=True, padx=12)
        tree.selection_set("0")
        tree.bind("<Double-1>", lambda _event: self.confirm())

        buttons = ttk.Frame(self.window, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="确定", command=self.confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.cancel).pack(side="right", padx=(0, 8))

        self.window.wait_window()

    def confirm(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        self.result = self.candidates[int(selection[0])]
        self.window.destroy()

    def cancel(self) -> None:
        self.result = None
        self.window.destroy()


class ChecklistDialog:
    def __init__(self, parent: tk.Tk, title: str, message: str, items: List[ReviewItem]):
        self.result: Optional[List[str]] = None
        self.items = items
        self.variables: Dict[str, tk.BooleanVar] = {}
        self.copy_status_var = tk.StringVar(value="提示：左键点击文件名或目录名，可一键复制到剪贴板，方便你去实例目录里筛查。")

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("860x560")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        ttk.Label(self.window, text=message, padding=12, wraplength=800).pack(anchor="w")

        actions = ttk.Frame(self.window, padding=(12, 0, 12, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="全选", command=self.select_all).pack(side="left")
        ttk.Button(actions, text="全不选", command=self.select_none).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.copy_status_var, foreground="#2c5aa0").pack(side="left", padx=(16, 0))

        canvas = tk.Canvas(self.window, borderwidth=0, highlightthickness=0)
        self.canvas = canvas
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=canvas.yview)
        container = ttk.Frame(canvas)

        container.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 12))
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=(0, 12))

        for item in items:
            row = ttk.Frame(container, padding=(8, 6))
            row.pack(fill="x", expand=True)
            if item.enabled:
                variable = tk.BooleanVar(value=item.checked)
                self.variables[item.key] = variable
                header = ttk.Frame(row)
                header.pack(fill="x", anchor="w")
                ttk.Checkbutton(header, variable=variable).pack(side="left")
                name_label = ttk.Label(header, text=item.label, cursor="hand2")
                name_label.pack(side="left", anchor="w")
                name_label.bind("<Button-1>", lambda _event, text=item.label: self.copy_item_text(text))
            else:
                ttk.Label(row, text=item.label, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            if item.detail:
                ttk.Label(row, text=item.detail, foreground="#666").pack(anchor="w", padx=(24, 0))

        buttons = ttk.Frame(self.window, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.cancel).pack(side="right")
        ttk.Button(buttons, text="确认继续", command=self.confirm).pack(side="right", padx=(0, 8))

        self.bind_mousewheel()
        self.window.wait_window()

    def bind_mousewheel(self) -> None:
        self.window.bind_all("<MouseWheel>", self.on_mousewheel)
        self.window.bind_all("<Button-4>", self.on_mousewheel_up)
        self.window.bind_all("<Button-5>", self.on_mousewheel_down)

    def unbind_mousewheel(self) -> None:
        self.window.unbind_all("<MouseWheel>")
        self.window.unbind_all("<Button-4>")
        self.window.unbind_all("<Button-5>")

    def on_mousewheel(self, event: tk.Event) -> None:
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def on_mousewheel_up(self, _event: tk.Event) -> None:
        self.canvas.yview_scroll(-1, "units")

    def on_mousewheel_down(self, _event: tk.Event) -> None:
        self.canvas.yview_scroll(1, "units")

    def select_all(self) -> None:
        for variable in self.variables.values():
            variable.set(True)

    def select_none(self) -> None:
        for variable in self.variables.values():
            variable.set(False)

    def copy_item_text(self, text: str) -> None:
        self.window.clipboard_clear()
        self.window.clipboard_append(text)
        self.copy_status_var.set(f"已复制：{text}")

    def confirm(self) -> None:
        self.result = [key for key, variable in self.variables.items() if variable.get()]
        self.unbind_mousewheel()
        self.window.destroy()

    def cancel(self) -> None:
        self.result = None
        self.unbind_mousewheel()
        self.window.destroy()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x760")
        self.root.minsize(920, 680)

        self.mod_path_var = tk.StringVar()
        self.mod_dry_run_var = tk.BooleanVar(value=False)
        self.mod_use_mcmod_var = tk.BooleanVar(value=True)
        self.mod_second_pass_var = tk.BooleanVar(value=False)

        self.server_client_path_var = tk.StringVar()
        self.server_output_path_var = tk.StringVar()
        self.server_use_mcmod_var = tk.BooleanVar(value=True)
        self.server_second_pass_var = tk.BooleanVar(value=False)

        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[dict]" = queue.Queue()

        self.mod_panel: Optional[PanelState] = None
        self.server_panel: Optional[PanelState] = None

        self.build_ui()
        self.root.after(150, self.poll_queue)

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)

        mod_frame = ttk.Frame(notebook, padding=12)
        server_frame = ttk.Frame(notebook, padding=12)
        notebook.add(mod_frame, text="Mod筛选模式")
        notebook.add(server_frame, text="一键制作服务端模式")

        self.mod_panel = self.build_mod_tab(mod_frame)
        self.server_panel = self.build_server_tab(server_frame)

    def build_mod_tab(self, parent: ttk.Frame) -> PanelState:
        top = ttk.LabelFrame(parent, text="选择 mods 目录", padding=12)
        top.pack(fill="x")
        ttk.Entry(top, textvariable=self.mod_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="浏览…", command=self.choose_mod_folder).pack(side="left", padx=(10, 0))

        options = ttk.Frame(parent, padding=(0, 12, 0, 0))
        options.pack(fill="x")
        ttk.Checkbutton(options, text="仅试运行，不移动文件", variable=self.mod_dry_run_var).pack(side="left")
        ttk.Checkbutton(options, text="启用 MC百科 兜底查询", variable=self.mod_use_mcmod_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="启用 2次筛选（仅重试 unknown）", variable=self.mod_second_pass_var).pack(side="left", padx=(18, 0))
        ttk.Button(options, text="开始分类", command=self.start_mod_task).pack(side="right")

        status_var = tk.StringVar(value="请选择 mods 目录。")
        progress_var = tk.DoubleVar(value=0)
        output_var = tk.StringVar(value="尚未运行")

        middle = ttk.LabelFrame(parent, text="进度", padding=12)
        middle.pack(fill="x", pady=(12, 0))
        ttk.Label(middle, textvariable=status_var).pack(anchor="w")
        ttk.Progressbar(middle, variable=progress_var, maximum=100).pack(fill="x", pady=(8, 0))
        ttk.Label(middle, textvariable=output_var, foreground="#555").pack(anchor="w", pady=(8, 0))

        log_box = ttk.LabelFrame(parent, text="日志", padding=12)
        log_box.pack(fill="both", expand=True, pady=(12, 0))
        log_widget = ScrolledText(log_box, wrap="word", font=("Consolas", 10))
        log_widget.pack(fill="both", expand=True)

        bottom = ttk.Frame(parent, padding=(0, 12, 0, 0))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="打开结果目录", command=lambda: self.open_panel_path("mod", "result")).pack(side="left")
        ttk.Button(bottom, text="退出", command=self.root.destroy).pack(side="right")

        return PanelState(status_var=status_var, progress_var=progress_var, output_var=output_var, log_widget=log_widget)

    def build_server_tab(self, parent: ttk.Frame) -> PanelState:
        client_box = ttk.LabelFrame(parent, text="客户端实例目录", padding=12)
        client_box.pack(fill="x")
        ttk.Entry(client_box, textvariable=self.server_client_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(client_box, text="浏览…", command=self.choose_client_folder).pack(side="left", padx=(10, 0))

        output_box = ttk.LabelFrame(parent, text="服务端输出目录（必须为空目录）", padding=12)
        output_box.pack(fill="x", pady=(12, 0))
        ttk.Entry(output_box, textvariable=self.server_output_path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(output_box, text="浏览…", command=self.choose_output_folder).pack(side="left", padx=(10, 0))

        options = ttk.Frame(parent, padding=(0, 12, 0, 0))
        options.pack(fill="x")
        ttk.Checkbutton(options, text="模组筛选时启用 MC百科 兜底查询", variable=self.server_use_mcmod_var).pack(side="left")
        ttk.Checkbutton(options, text="模组筛选启用 2次筛选", variable=self.server_second_pass_var).pack(side="left", padx=(18, 0))
        ttk.Button(options, text="开始制作服务端", command=self.start_server_task).pack(side="right")

        status_var = tk.StringVar(value="请选择客户端目录和新的空服务端目录。")
        progress_var = tk.DoubleVar(value=0)
        output_var = tk.StringVar(value="尚未运行")

        middle = ttk.LabelFrame(parent, text="进度", padding=12)
        middle.pack(fill="x", pady=(12, 0))
        ttk.Label(middle, textvariable=status_var).pack(anchor="w")
        ttk.Progressbar(middle, variable=progress_var, maximum=100).pack(fill="x", pady=(8, 0))
        ttk.Label(middle, textvariable=output_var, foreground="#555").pack(anchor="w", pady=(8, 0))

        log_box = ttk.LabelFrame(parent, text="日志", padding=12)
        log_box.pack(fill="both", expand=True, pady=(12, 0))
        log_widget = ScrolledText(log_box, wrap="word", font=("Consolas", 10))
        log_widget.pack(fill="both", expand=True)

        bottom = ttk.Frame(parent, padding=(0, 12, 0, 0))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="打开服务端目录", command=lambda: self.open_panel_path("server", "result")).pack(side="left")
        ttk.Button(bottom, text="打开日志目录", command=lambda: self.open_panel_path("server", "extra")).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="退出", command=self.root.destroy).pack(side="right")

        return PanelState(status_var=status_var, progress_var=progress_var, output_var=output_var, log_widget=log_widget)

    def choose_mod_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择 mods 目录")
        if selected:
            self.mod_path_var.set(selected)

    def choose_client_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择客户端实例目录")
        if selected:
            self.server_client_path_var.set(selected)

    def choose_output_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择新的空服务端输出目录")
        if selected:
            self.server_output_path_var.set(selected)

    def clear_panel(self, panel_key: str) -> None:
        panel = self.get_panel(panel_key)
        panel.log_widget.delete("1.0", "end")
        panel.progress_var.set(0)
        panel.output_var.set("运行中")
        panel.result_dir = None
        panel.extra_dir = None

    def start_mod_task(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "任务正在运行，请先等待当前任务结束。")
            return

        mods_path = self.mod_path_var.get().strip()
        if not mods_path:
            self.choose_mod_folder()
            mods_path = self.mod_path_var.get().strip()
            if not mods_path:
                return

        path = Path(mods_path)
        if not path.exists() or not path.is_dir():
            messagebox.showerror(APP_TITLE, "mods 目录不存在。")
            return

        self.clear_panel("mod")
        self.get_panel("mod").status_var.set("准备开始…")
        self.worker_thread = threading.Thread(
            target=self.run_mod_task,
            args=(path, self.mod_dry_run_var.get(), self.mod_use_mcmod_var.get(), self.mod_second_pass_var.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def start_server_task(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "任务正在运行，请先等待当前任务结束。")
            return

        client_dir = self.server_client_path_var.get().strip()
        output_dir = self.server_output_path_var.get().strip()
        if not client_dir:
            self.choose_client_folder()
            client_dir = self.server_client_path_var.get().strip()
        if not output_dir:
            self.choose_output_folder()
            output_dir = self.server_output_path_var.get().strip()
        if not client_dir or not output_dir:
            return

        self.clear_panel("server")
        self.get_panel("server").status_var.set("准备开始…")
        self.worker_thread = threading.Thread(
            target=self.run_server_task,
            args=(Path(client_dir), Path(output_dir), self.server_use_mcmod_var.get(), self.server_second_pass_var.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def get_panel(self, panel_key: str) -> PanelState:
        if panel_key == "mod":
            assert self.mod_panel is not None
            return self.mod_panel
        assert self.server_panel is not None
        return self.server_panel

    def emit(self, panel: str, kind: str, payload: Any) -> None:
        self.ui_queue.put({"panel": panel, "kind": kind, "payload": payload})

    def append_log(self, panel_key: str, message: str) -> None:
        panel = self.get_panel(panel_key)
        panel.log_widget.insert("end", message.rstrip() + "\n")
        panel.log_widget.see("end")

    def request_version_choice(self, candidates: List[VersionCandidate]) -> Optional[VersionCandidate]:
        event = threading.Event()
        request = {"kind": "version", "candidates": candidates, "event": event, "response": None}
        self.ui_queue.put({"panel": "server", "kind": "ui-request", "payload": request})
        event.wait()
        return request["response"]

    def request_checklist(self, title: str, message: str, items: List[ReviewItem]) -> Optional[List[str]]:
        event = threading.Event()
        request = {"kind": "checklist", "title": title, "message": message, "items": items, "event": event, "response": None}
        self.ui_queue.put({"panel": "server", "kind": "ui-request", "payload": request})
        event.wait()
        return request["response"]

    def poll_queue(self) -> None:
        while True:
            try:
                event = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            panel_key = event["panel"]
            kind = event["kind"]
            payload = event["payload"]
            panel = self.get_panel(panel_key)

            if kind == "log":
                self.append_log(panel_key, payload)
            elif kind == "status":
                panel.status_var.set(payload)
            elif kind == "progress":
                panel.progress_var.set(payload)
            elif kind == "output":
                panel.output_var.set(payload)
            elif kind == "done":
                panel.result_dir = payload.get("result_dir")
                panel.extra_dir = payload.get("extra_dir")
                panel.status_var.set(payload["status"])
                panel.progress_var.set(100)
                panel.output_var.set(payload["output"])
                if payload.get("summary"):
                    self.append_log(panel_key, "")
                    self.append_log(panel_key, payload["summary"])
                messagebox.showinfo(APP_TITLE, payload["status"])
            elif kind == "error":
                panel.status_var.set("运行失败")
                panel.output_var.set("失败")
                self.append_log(panel_key, payload)
                messagebox.showerror(APP_TITLE, payload)
            elif kind == "ui-request":
                if payload["kind"] == "version":
                    dialog = VersionSelectionDialog(self.root, payload["candidates"])
                    payload["response"] = dialog.result
                elif payload["kind"] == "checklist":
                    dialog = ChecklistDialog(self.root, payload["title"], payload["message"], payload["items"])
                    payload["response"] = dialog.result
                payload["event"].set()

        self.root.after(150, self.poll_queue)

    def run_mod_task(self, mods_path: Path, dry_run: bool, use_mcmod: bool, enable_second_pass: bool) -> None:
        try:
            classifier = ClassifierCore()
            jar_files = sorted(mods_path.glob("*.jar"), key=lambda item: item.name.lower())
            if not jar_files:
                raise RuntimeError("所选目录中没有找到 jar 模组。")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            result_root = mods_path / f"_分类结果_{timestamp}"
            client_dir = result_root / "纯客户端_已移出"
            unknown_dir = result_root / "无法分类_待人工确认"
            client_dir.mkdir(parents=True, exist_ok=True)
            unknown_dir.mkdir(parents=True, exist_ok=True)

            self.emit("mod", "log", f"开始扫描目录：{mods_path}")
            self.emit("mod", "log", f"共发现 {len(jar_files)} 个 jar 模组")
            worker_count = get_classification_worker_count(len(jar_files))
            if len(jar_files) > 1:
                self.emit("mod", "log", f"联网分类使用 {worker_count} 个并发线程")

            first_span = 72 if enable_second_pass else 88

            def first_pass_progress(completed: int, total: int, jar: Path) -> None:
                percent = completed / max(total, 1)
                self.emit("mod", "progress", percent * first_span)
                self.emit("mod", "status", f"正在汇总：{jar.name}")

            def first_pass_result(completed: int, total: int, jar: Path, row: Dict[str, Any]) -> None:
                self.emit("mod", "log", f"[{completed}/{total}] {jar.name} -> {row['Category']} | {row['Reason']}")

            results = classify_jars_parallel(
                classifier,
                jar_files,
                use_mcmod,
                progress_callback=first_pass_progress,
                result_callback=first_pass_result,
            )

            unknown_rows = [row for row in results if row["Category"] == "unknown"]
            if enable_second_pass:
                if unknown_rows:
                    retry_total = len(unknown_rows)
                    retry_worker_count = get_classification_worker_count(retry_total)
                    self.emit("mod", "log", f"开始进行 2次筛选：仅重试首轮未确定的 {retry_total} 个模组")
                    if retry_total > 1:
                        self.emit("mod", "log", f"2次筛选使用 {retry_worker_count} 个并发线程")

                    def second_pass_progress(completed: int, total: int, jar: Path) -> None:
                        percent = completed / max(total, 1)
                        self.emit("mod", "progress", 72 + percent * 16)
                        self.emit("mod", "status", f"正在进行 2次筛选：{jar.name}")

                    def second_pass_result(completed: int, total: int, jar: Path, row: Dict[str, Any]) -> None:
                        self.emit("mod", "log", f"[2次筛选 {completed}/{total}] {jar.name} -> {row['Category']} | {row['Reason']}")

                    recovered = rerun_unknown_classifications(
                        results,
                        use_mcmod,
                        progress_callback=second_pass_progress,
                        result_callback=second_pass_result,
                    )
                    remaining_unknown = sum(1 for row in results if row["Category"] == "unknown")
                    self.emit("mod", "log", f"2次筛选完成：回补 {recovered} 个，仍待确认 {remaining_unknown} 个")
                else:
                    self.emit("mod", "log", "已开启 2次筛选，但首轮没有 unknown 模组，跳过重试")

            self.emit("mod", "progress", 90)
            self.emit("mod", "status", "正在整理分类结果目录…")
            for row in results:
                source_path = row["Path"]
                final_path = str(source_path)
                if row["Category"] == "client-only":
                    target = client_dir / source_path.name
                    final_path = str(target)
                    if not dry_run and source_path.exists():
                        shutil.move(str(source_path), str(target))
                elif row["Category"] == "unknown":
                    target = unknown_dir / source_path.name
                    final_path = str(target)
                    if not dry_run and source_path.exists():
                        shutil.move(str(source_path), str(target))
                row["FinalPath"] = final_path

            final_unknown_rows = [row for row in results if row["Category"] == "unknown"]
            if final_unknown_rows:
                self.emit("mod", "log", "以下模组在最终结果中仍未自动确认：")
                for row in final_unknown_rows:
                    self.emit("mod", "log", f" - {row['FileName']} | {row['Reason']}")

            json_path = result_root / "分类报告.json"
            csv_path = result_root / "分类报告.csv"
            txt_path = result_root / "分类摘要.txt"

            self.emit("mod", "progress", 96)
            self.emit("mod", "status", "正在写出报告…")
            output_rows = [{key: value for key, value in row.items() if key != "Path"} for row in results]

            json_path.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            write_csv_with_labels(csv_path, output_rows)

            server_keep = sum(1 for item in output_rows if item["Category"] == "server-keep")
            client_only = sum(1 for item in output_rows if item["Category"] == "client-only")
            unknown = sum(1 for item in output_rows if item["Category"] == "unknown")
            summary = "\n".join(
                [
                    f"扫描目录: {mods_path}",
                    f"执行模式: {'DryRun(不移动文件)' if dry_run else '实际移动文件'}",
                    f"服务端保留: {server_keep}",
                    f"纯客户端移出: {client_only}",
                    f"无法分类: {unknown}",
                    f"结果目录: {result_root}",
                    f"JSON 报告: {json_path}",
                    f"CSV 报告: {csv_path}",
                ]
            )
            txt_path.write_text(summary, encoding="utf-8")
            self.emit(
                "mod",
                "done",
                {
                    "status": f"分类完成：保留 {server_keep}，移出 {client_only}，待确认 {unknown}",
                    "output": str(result_root),
                    "result_dir": result_root,
                    "extra_dir": result_root,
                    "summary": summary,
                },
            )
        except Exception:
            self.emit("mod", "error", traceback.format_exc())

    def run_server_task(self, client_dir: Path, output_dir: Path, use_mcmod: bool, enable_second_pass: bool) -> None:
        try:
            classifier = ClassifierCore()
            builder = ServerBuilderCore(
                classifier=classifier,
                log=lambda message: self.emit("server", "log", message),
                set_status=lambda message: self.emit("server", "status", message),
                set_progress=lambda value: self.emit("server", "progress", value),
                request_version_choice=self.request_version_choice,
                request_checklist=self.request_checklist,
                use_mcmod=use_mcmod,
                enable_second_pass=enable_second_pass,
            )
            result = builder.build_server(client_dir, output_dir)
            summary = "\n".join(
                [
                    f"客户端目录: {client_dir}",
                    f"服务端目录: {result['server_root']}",
                    f"日志目录: {result['report_dir']}",
                    f"启动脚本: {result['launch_script']}",
                ]
            )
            self.emit(
                "server",
                "done",
                {
                    "status": "服务端制作完成，已通过两次启动验证。",
                    "output": str(result["server_root"]),
                    "result_dir": result["server_root"],
                    "extra_dir": result["report_dir"],
                    "summary": summary,
                },
            )
        except Exception:
            self.emit("server", "error", traceback.format_exc())

    def open_panel_path(self, panel_key: str, target: str) -> None:
        panel = self.get_panel(panel_key)
        path = panel.result_dir if target == "result" else panel.extra_dir
        if not path or not path.exists():
            messagebox.showinfo(APP_TITLE, "当前还没有可打开的目录。")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"打开目录失败：{exc}")
def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
