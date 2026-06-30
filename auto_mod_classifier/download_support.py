import ipaddress
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union

from .shared import (
    DOWNLOAD_SOURCE_BMCLAPI,
    DOWNLOAD_SOURCE_DOMESTIC,
    DOWNLOAD_SOURCE_LABELS,
    DOWNLOAD_SOURCE_MCIM,
    DOWNLOAD_SOURCE_OFFICIAL,
    DOWNLOAD_SOURCE_SMART,
    USER_AGENT,
)

DEFAULT_DOWNLOAD_WORKERS = 6
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 15
DEFAULT_METADATA_TIMEOUT_SECONDS = 12
_DOWNLOAD_CHUNK_SIZE = 64 * 1024
_PROGRESS_UPDATE_INTERVAL_SECONDS = 0.15
_PROGRESS_SAMPLE_WINDOW_SECONDS = 2.0
_ATTEMPT_SUCCESS_CACHE_TTL_SECONDS = 300.0
_ATTEMPT_FAILURE_CACHE_TTL_SECONDS = 45.0
_SMART_RESOURCE_MOD_PLATFORM = "mod-platform"
_SMART_RESOURCE_SERVER_DEPENDENCY = "server-dependency"
_SMART_RESOURCE_GENERIC = "generic"
_PROXY_MODE_NONE = "none"
_PROXY_MODE_LOCAL = "local"
_PROXY_MODE_REMOTE = "remote"
_MOD_PLATFORM_HOSTS = {
    "api.modrinth.com",
    "cdn.modrinth.com",
    "cdn-raw.modrinth.com",
    "api.curseforge.com",
    "edge.forgecdn.net",
    "mediafilez.forgecdn.net",
}
_SERVER_DEPENDENCY_HOSTS = {
    "libraries.minecraft.net",
    "launcher.mojang.com",
    "launchermeta.mojang.com",
    "maven.fabricmc.net",
    "maven.minecraftforge.net",
    "maven.neoforged.net",
    "meta.fabricmc.net",
    "piston-data.mojang.com",
    "piston-meta.mojang.com",
}

_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ATTEMPT_SCORE_CACHE: dict[tuple[str, str, str], tuple[float, float]] = {}
_ATTEMPT_SCORE_LOCK = threading.Lock()


@dataclass(frozen=True)
class DownloadAttempt:
    url: str
    source_code: str
    source_label: str
    route_code: str
    route_label: str
    source_rank: int = 99

    @property
    def display_label(self) -> str:
        return f"{self.source_label} / {self.route_label}"


def format_bytes(size: float) -> str:
    """把字节数格式化成更容易读的文本。"""
    value = float(max(size, 0.0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return "0 B"


def build_download_status_text(speed_bytes: float, active_count: int, thread_limit: int, completed_count: int, total_count: int) -> str:
    """统一下载状态文案，供界面直接显示。"""
    return (
        f"当前网速：{format_bytes(speed_bytes)}/s | "
        f"下载线程：{max(active_count, 0)}/{max(thread_limit, 0)} | "
        f"已完成：{max(completed_count, 0)}/{max(total_count, 0)}"
    )


def build_idle_download_status_text() -> str:
    return build_download_status_text(0.0, 0, 0, 0, 0)


def choose_download_worker_count(total_files: int) -> int:
    return min(DEFAULT_DOWNLOAD_WORKERS, max(1, total_files))


def normalize_download_source(download_source: str) -> str:
    """兼容旧值，统一收敛成当前支持的下载策略。"""
    if download_source == DOWNLOAD_SOURCE_DOMESTIC:
        return DOWNLOAD_SOURCE_SMART
    if download_source in {DOWNLOAD_SOURCE_SMART, DOWNLOAD_SOURCE_OFFICIAL, DOWNLOAD_SOURCE_BMCLAPI, DOWNLOAD_SOURCE_MCIM}:
        return download_source
    return DOWNLOAD_SOURCE_SMART


def _rewrite_to_mcim(url: str) -> str:
    replacements = (
        ("https://api.modrinth.com/", "https://mod.mcimirror.top/modrinth/"),
        ("https://cdn.modrinth.com/", "https://mod.mcimirror.top/"),
        ("https://api.curseforge.com/", "https://mod.mcimirror.top/curseforge/"),
        ("https://edge.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("https://mediafilez.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("http://edge.forgecdn.net/", "https://mod.mcimirror.top/"),
        ("http://mediafilez.forgecdn.net/", "https://mod.mcimirror.top/"),
    )
    for before, after in replacements:
        if url.startswith(before):
            return after + url[len(before) :]
    return url


def _rewrite_to_bmclapi(url: str) -> str:
    if "/net/fabricmc/fabric-installer/" in url:
        # BMCLAPI 当前没有同步这条安装器路径，直接跳过可以避免稳定 404。
        return url
    replacements = (
        ("https://libraries.minecraft.net/", "https://bmclapi2.bangbang93.com/maven/"),
        ("https://meta.fabricmc.net/", "https://bmclapi2.bangbang93.com/fabric-meta/"),
        ("https://maven.fabricmc.net/", "https://bmclapi2.bangbang93.com/maven/"),
        ("https://maven.minecraftforge.net/", "https://bmclapi2.bangbang93.com/maven/"),
        ("https://maven.neoforged.net/releases/", "https://bmclapi2.bangbang93.com/maven/"),
        ("https://piston-meta.mojang.com/", "https://bmclapi2.bangbang93.com/"),
        ("https://piston-data.mojang.com/", "https://bmclapi2.bangbang93.com/"),
        ("https://launcher.mojang.com/", "https://bmclapi2.bangbang93.com/"),
        ("https://launchermeta.mojang.com/", "https://bmclapi2.bangbang93.com/"),
    )
    for before, after in replacements:
        if url.startswith(before):
            return after + url[len(before) :]
    return url


def _build_source_variants(url: str) -> dict[str, str]:
    variants = {DOWNLOAD_SOURCE_OFFICIAL: url}
    mcim_url = _rewrite_to_mcim(url)
    if mcim_url != url:
        variants[DOWNLOAD_SOURCE_MCIM] = mcim_url
    bmclapi_url = _rewrite_to_bmclapi(url)
    if bmclapi_url != url:
        variants[DOWNLOAD_SOURCE_BMCLAPI] = bmclapi_url
    return variants


def _detect_smart_resource_family(url: str) -> str:
    """智能优选按资源类型拆分：Mod 平台文件和开服依赖各走不同优先级。"""
    lowered_url = str(url or "").strip().lower()
    if not lowered_url:
        return _SMART_RESOURCE_GENERIC

    host = urllib.parse.urlsplit(lowered_url).netloc
    if host in _MOD_PLATFORM_HOSTS:
        return _SMART_RESOURCE_MOD_PLATFORM
    if host in _SERVER_DEPENDENCY_HOSTS:
        return _SMART_RESOURCE_SERVER_DEPENDENCY
    return _SMART_RESOURCE_GENERIC


def _extract_proxy_host(proxy_value: str) -> str:
    raw_value = str(proxy_value or "").strip()
    if not raw_value:
        return ""
    if "://" not in raw_value:
        raw_value = f"http://{raw_value}"
    parsed = urllib.parse.urlsplit(raw_value)
    return (parsed.hostname or "").strip().lower()


def _is_local_proxy_host(host: str) -> bool:
    lowered = str(host or "").strip().lower()
    if not lowered:
        return False
    if lowered in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return lowered.endswith(".local")
    return address.is_loopback or address.is_private or address.is_link_local


def _detect_proxy_mode() -> str:
    proxies = urllib.request.getproxies()
    if not proxies:
        return _PROXY_MODE_NONE

    for key in ("https", "http", "all"):
        value = proxies.get(key)
        if not value:
            continue
        return _PROXY_MODE_LOCAL if _is_local_proxy_host(_extract_proxy_host(value)) else _PROXY_MODE_REMOTE
    return _PROXY_MODE_REMOTE


def _get_source_priority_for_url(url: str, profile: str) -> list[str]:
    if profile == DOWNLOAD_SOURCE_SMART:
        resource_family = _detect_smart_resource_family(url)
        if resource_family == _SMART_RESOURCE_MOD_PLATFORM:
            # Mod 文件默认先试官方，再把 MCIM 作为回退。
            return [DOWNLOAD_SOURCE_OFFICIAL, DOWNLOAD_SOURCE_MCIM]
        if resource_family == _SMART_RESOURCE_SERVER_DEPENDENCY:
            if _detect_proxy_mode() != _PROXY_MODE_NONE:
                # 用户开了系统代理时，官方依赖更容易直接吃到代理收益。
                return [DOWNLOAD_SOURCE_OFFICIAL, DOWNLOAD_SOURCE_BMCLAPI]
            # Mojang / Fabric / Forge / NeoForge 依赖默认更适合先走 BMCLAPI。
            return [DOWNLOAD_SOURCE_BMCLAPI, DOWNLOAD_SOURCE_OFFICIAL]
        return [DOWNLOAD_SOURCE_OFFICIAL]
    if profile == DOWNLOAD_SOURCE_BMCLAPI:
        return [DOWNLOAD_SOURCE_BMCLAPI, DOWNLOAD_SOURCE_OFFICIAL]
    if profile == DOWNLOAD_SOURCE_MCIM:
        return [DOWNLOAD_SOURCE_MCIM, DOWNLOAD_SOURCE_OFFICIAL]
    return [DOWNLOAD_SOURCE_OFFICIAL]


def build_download_candidates(urls: Union[str, Sequence[str]], download_source: str) -> list[str]:
    """只返回 URL 级别候选，主要给调试和最小验证用。"""
    raw_urls = [urls] if isinstance(urls, str) else list(urls)
    profile = normalize_download_source(download_source)

    candidates: list[str] = []
    seen: set[str] = set()
    for url in raw_urls:
        if not url:
            continue
        source_priority = _get_source_priority_for_url(url, profile)
        variants = _build_source_variants(url)
        for source_code in source_priority:
            candidate = variants.get(source_code)
            if candidate and candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates


def _available_routes() -> list[tuple[str, str]]:
    if urllib.request.getproxies():
        return [("system", "系统代理"), ("direct", "直连")]
    return [("direct", "直连")]


def _route_rank_for_source(source_code: str) -> dict[str, int]:
    # 官方源更可能从代理受益，国内镜像默认更适合先走直连。
    if source_code == DOWNLOAD_SOURCE_OFFICIAL:
        return {"system": 0, "direct": 1}
    return {"direct": 0, "system": 1}


def build_download_attempts(urls: Union[str, Sequence[str]], download_source: str) -> list[DownloadAttempt]:
    raw_urls = [urls] if isinstance(urls, str) else list(urls)
    profile = normalize_download_source(download_source)
    routes = _available_routes()

    attempts: list[DownloadAttempt] = []
    seen: set[tuple[str, str]] = set()
    for url in raw_urls:
        if not url:
            continue
        source_priority = _get_source_priority_for_url(url, profile)
        variants = _build_source_variants(url)
        for source_rank, source_code in enumerate(source_priority):
            candidate_url = variants.get(source_code)
            if not candidate_url:
                continue
            source_label = DOWNLOAD_SOURCE_LABELS.get(source_code, source_code)
            ordered_routes = sorted(routes, key=lambda item: _route_rank_for_source(source_code).get(item[0], 99))
            for route_code, route_label in ordered_routes:
                key = (candidate_url, route_code)
                if key in seen:
                    continue
                seen.add(key)
                attempts.append(
                    DownloadAttempt(
                        url=candidate_url,
                        source_code=source_code,
                        source_label=source_label,
                        route_code=route_code,
                        route_label=route_label,
                        source_rank=source_rank,
                    )
                )
    return _sort_attempts(attempts)


def _sort_attempts(attempts: list[DownloadAttempt]) -> list[DownloadAttempt]:
    if len(attempts) <= 1:
        return attempts

    return sorted(attempts, key=_build_attempt_sort_key)


def _build_attempt_sort_key(attempt: DownloadAttempt) -> tuple[float, float, float, int]:
    cached_score = _get_cached_attempt_score(attempt)
    route_rank = _route_rank_for_source(attempt.source_code).get(attempt.route_code, 99)
    if cached_score is None:
        return (1.0, float(attempt.source_rank), route_rank, 0.0)
    if math.isinf(cached_score):
        return (2.0, float(attempt.source_rank), route_rank, 0.0)
    return (0.0, cached_score, float(attempt.source_rank), float(route_rank))


def _attempt_cache_key(attempt: DownloadAttempt) -> tuple[str, str, str]:
    parsed = urllib.parse.urlsplit(attempt.url)
    return (parsed.netloc.lower(), attempt.source_code, attempt.route_code)


def _get_cached_attempt_score(attempt: DownloadAttempt) -> Optional[float]:
    cache_key = _attempt_cache_key(attempt)
    now = time.monotonic()
    with _ATTEMPT_SCORE_LOCK:
        cached = _ATTEMPT_SCORE_CACHE.get(cache_key)
        if not cached:
            return None
        ttl = _ATTEMPT_FAILURE_CACHE_TTL_SECONDS if math.isinf(cached[1]) else _ATTEMPT_SUCCESS_CACHE_TTL_SECONDS
        if now - cached[0] < ttl:
            return cached[1]
        _ATTEMPT_SCORE_CACHE.pop(cache_key, None)
    return None


def _record_attempt_score(attempt: DownloadAttempt, score: float) -> None:
    cache_key = _attempt_cache_key(attempt)
    with _ATTEMPT_SCORE_LOCK:
        _ATTEMPT_SCORE_CACHE[cache_key] = (time.monotonic(), score)


def _record_attempt_failure(attempt: DownloadAttempt) -> None:
    _record_attempt_score(attempt, float("inf"))


def _open_request(req: urllib.request.Request, route_code: str, timeout: int):
    if route_code == "direct":
        return _DIRECT_OPENER.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _format_exception_short(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            return "长时间无进展或连接超时"
        return str(reason or exc).strip() or exc.__class__.__name__
    if isinstance(exc, socket.timeout):
        return "长时间无进展或连接超时"
    text = str(exc).strip()
    if "timed out" in text.lower():
        return "长时间无进展或连接超时"
    return text or exc.__class__.__name__


class DownloadStatsReporter:
    """聚合多个下载任务的网速和线程状态，给界面做实时展示。"""

    def __init__(self, emit_status: Optional[Callable[[str], None]], total_files: int, thread_limit: int):
        self.emit_status = emit_status
        self.total_files = max(0, total_files)
        self.thread_limit = max(0, thread_limit)
        self.active_files = 0
        self.completed_files = 0
        self.downloaded_bytes = 0
        self._lock = threading.Lock()
        self._last_emit_at = 0.0
        self._samples: deque[tuple[float, int]] = deque()

    def start_file(self) -> None:
        with self._lock:
            self.active_files += 1
            self._emit_locked(force=True)

    def add_bytes(self, size: int) -> None:
        if size <= 0:
            return
        with self._lock:
            self.downloaded_bytes += size
            self._emit_locked(force=False)

    def finish_file(self) -> None:
        with self._lock:
            self.active_files = max(0, self.active_files - 1)
            self.completed_files += 1
            self._emit_locked(force=True)

    def fail_file(self) -> None:
        with self._lock:
            self.active_files = max(0, self.active_files - 1)
            self._emit_locked(force=True)

    def close(self) -> None:
        with self._lock:
            self.active_files = 0
            self._samples.clear()
            self._last_emit_at = 0.0
            completed_files = self.completed_files
            total_files = self.total_files
            thread_limit = self.thread_limit
        self._emit_text(build_download_status_text(0.0, 0, thread_limit, completed_files, total_files))

    def _emit_text(self, text: str) -> None:
        if callable(self.emit_status):
            self.emit_status(text)

    def _emit_locked(self, force: bool) -> None:
        if not callable(self.emit_status):
            return

        now = time.monotonic()
        if not force and now - self._last_emit_at < _PROGRESS_UPDATE_INTERVAL_SECONDS:
            return

        self._samples.append((now, self.downloaded_bytes))
        while len(self._samples) > 1 and now - self._samples[0][0] > _PROGRESS_SAMPLE_WINDOW_SECONDS:
            self._samples.popleft()

        speed_bytes = 0.0
        if self.active_files > 0 and len(self._samples) >= 2:
            start_time, start_bytes = self._samples[0]
            duration = now - start_time
            if duration > 0:
                speed_bytes = (self.downloaded_bytes - start_bytes) / duration

        self._last_emit_at = now
        text = build_download_status_text(
            speed_bytes=speed_bytes,
            active_count=self.active_files,
            thread_limit=self.thread_limit,
            completed_count=self.completed_files,
            total_count=self.total_files,
        )
        self._emit_text(text)


def _fetch_with_attempts(url: str, download_source: str, timeout: int, parser: Callable[[bytes], Any]) -> Any:
    last_error: Optional[Exception] = None
    attempts = build_download_attempts(url, download_source)
    if not attempts:
        raise RuntimeError("未提供可用下载地址。")

    for attempt in attempts:
        try:
            req = urllib.request.Request(attempt.url, headers={"User-Agent": USER_AGENT})
            started_at = time.monotonic()
            with _open_request(req, attempt.route_code, timeout=timeout) as resp:
                raw = resp.read()
            _record_attempt_score(attempt, time.monotonic() - started_at)
            return parser(raw)
        except Exception as exc:
            last_error = exc
            _record_attempt_failure(attempt)
    raise RuntimeError(f"获取内容失败：{url}\n{last_error}")


def http_get_text(url: str, download_source: str, timeout: int = DEFAULT_METADATA_TIMEOUT_SECONDS) -> str:
    return str(_fetch_with_attempts(url, download_source, timeout, lambda raw: raw.decode("utf-8", errors="ignore")))


def http_get_json(url: str, download_source: str, timeout: int = DEFAULT_METADATA_TIMEOUT_SECONDS) -> Any:
    return _fetch_with_attempts(url, download_source, timeout, lambda raw: json.loads(raw.decode("utf-8")))


def http_download(
    urls: Union[str, Sequence[str]],
    destination: Path,
    download_source: str,
    reporter: Optional[DownloadStatsReporter] = None,
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    display_name: Optional[str] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    log_success: bool = True,
) -> None:
    """下载单个文件，支持镜像回退、直连/代理双路线、进度统计和原子替换。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    attempts = build_download_attempts(urls, download_source)
    if not attempts:
        raise RuntimeError("未提供可用下载地址。")

    file_label = display_name or destination.name
    temp_path = destination.with_name(destination.name + ".part")
    if temp_path.exists():
        temp_path.unlink()

    file_started = False
    try:
        if reporter is not None:
            reporter.start_file()
            file_started = True

        last_error: Optional[Exception] = None
        for index, attempt in enumerate(attempts, start=1):
            try:
                req = urllib.request.Request(attempt.url, headers={"User-Agent": USER_AGENT})
                started_at = time.monotonic()
                first_chunk_at: Optional[float] = None
                with _open_request(req, attempt.route_code, timeout=timeout) as resp:
                    with temp_path.open("wb") as fp:
                        while True:
                            chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                            if not chunk:
                                break
                            if first_chunk_at is None:
                                first_chunk_at = time.monotonic()
                            fp.write(chunk)
                            if reporter is not None:
                                reporter.add_bytes(len(chunk))
                first_response_seconds = (first_chunk_at or time.monotonic()) - started_at
                _record_attempt_score(attempt, first_response_seconds)
                temp_path.replace(destination)
                if log_callback is not None and log_success:
                    log_callback(f"[下载成功] {file_label} | {attempt.display_label}")
                if reporter is not None:
                    reporter.finish_file()
                    file_started = False
                return
            except Exception as exc:
                last_error = exc
                _record_attempt_failure(attempt)
                if temp_path.exists():
                    temp_path.unlink()
                if log_callback is not None and index < len(attempts):
                    log_callback(
                        f"[切换下载路线] {file_label} | {attempt.display_label} 失败：{_format_exception_short(exc)}，改试下一条。"
                    )

        raise RuntimeError(f"下载失败：{file_label}\n{_format_exception_short(last_error or RuntimeError('未知错误'))}")
    except Exception:
        if file_started and reporter is not None:
            reporter.fail_file()
        if temp_path.exists():
            temp_path.unlink()
        raise
