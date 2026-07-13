from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

from ..download_support import choose_download_worker_count, http_get_json, http_post_json
from ..shared import Classification, DOWNLOAD_SOURCE_MCIM, DOWNLOAD_SOURCE_SMART, ModMeta
from .models import ClassificationOptions


_MODRINTH_BATCH_SIZE = 50
_CURSEFORGE_BATCH_SIZE = 25
_MODRINTH_API_WORKERS = 10
_CURSEFORGE_API_WORKERS = 4
_CURSEFORGE_WHITESPACE = {9, 10, 13, 32}
_CURSEFORGE_WHITESPACE_BYTES = b"\t\n\r "
_CURSEFORGE_HASH_WORKERS = 4

ExactMatchProgressCallback = Callable[[str, int, int, Optional[Path]], None]


@dataclass
class ExactMatchOutcome:
    sha1: str = ""
    classification: Optional[Classification] = None
    fallback: Optional[Classification] = None
    matched_sources: set[str] = field(default_factory=set)


ExactMatchResultCallback = Callable[[Path, ExactMatchOutcome], None]


class _ApiRateLimitGate:
    def __init__(self, minimum_interval: float, dynamic_headers: bool):
        self.minimum_interval = minimum_interval
        self.dynamic_headers = dynamic_headers
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_request_at:
                time.sleep(self._next_request_at - now)
            self._next_request_at = time.monotonic() + self.minimum_interval

    def update(self, headers, status_code: int = 200) -> None:
        retry_after = self._header_float(headers, "Retry-After")
        reset_seconds = self._header_float(headers, "X-Ratelimit-Reset") if self.dynamic_headers else 0.0
        remaining = self._header_int(headers, "X-Ratelimit-Remaining") if self.dynamic_headers else None

        delay = 0.0
        if status_code == 429:
            delay = max(retry_after, reset_seconds, 1.0)
        elif self.dynamic_headers and remaining is not None and remaining <= 0:
            delay = max(reset_seconds, self.minimum_interval)
        elif self.dynamic_headers and remaining and reset_seconds > 0:
            delay = max(reset_seconds / remaining, self.minimum_interval)

        if delay > 0:
            with self._lock:
                self._next_request_at = max(self._next_request_at, time.monotonic() + delay)

    @staticmethod
    def _header_float(headers, name: str) -> float:
        try:
            return max(0.0, float(headers.get(name, "0") or 0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _header_int(headers, name: str) -> Optional[int]:
        try:
            value = headers.get(name)
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


def calculate_sha1(path: Path) -> str:
    return _calculate_sha1_details(path)[0]


def _calculate_sha1_details(path: Path) -> tuple[str, int]:
    digester = hashlib.sha1()
    normalized_size = 0
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digester.update(chunk)
            normalized_size += len(chunk) - sum(chunk.count(bytes((value,))) for value in _CURSEFORGE_WHITESPACE)
    return digester.hexdigest(), normalized_size


def calculate_curseforge_fingerprint(path: Path, normalized_size: Optional[int] = None) -> int:
    if normalized_size is None:
        normalized_size = _calculate_sha1_details(path)[1]

    multiplier = 0x5BD1E995
    value = (1 ^ normalized_size) & 0xFFFFFFFF
    carry = b""
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            normalized = carry + chunk.translate(None, _CURSEFORGE_WHITESPACE_BYTES)
            block_end = len(normalized) - (len(normalized) % 4)
            blocks = memoryview(normalized)[:block_end].cast("I")
            for block in blocks:
                value = _mix_murmurhash2_block(value, block, multiplier)
            carry = normalized[block_end:]
            time.sleep(0)

    return _finish_murmurhash2(value, carry, multiplier)


def _calculate_curseforge_fingerprint_task(task: tuple[str, int]) -> int:
    path, normalized_size = task
    return calculate_curseforge_fingerprint(Path(path), normalized_size)


def calculate_file_hashes(path: Path) -> tuple[str, int]:
    sha1, normalized_size = _calculate_sha1_details(path)
    return sha1, calculate_curseforge_fingerprint(path, normalized_size)


def _mix_murmurhash2_block(value: int, block: int, multiplier: int) -> int:
    block = (block * multiplier) & 0xFFFFFFFF
    block ^= block >> 24
    block = (block * multiplier) & 0xFFFFFFFF
    value = (value * multiplier) & 0xFFFFFFFF
    return value ^ block


def _finish_murmurhash2(value: int, tail: bytes, multiplier: int) -> int:
    if len(tail) == 3:
        value ^= tail[2] << 16
    if len(tail) >= 2:
        value ^= tail[1] << 8
    if tail:
        value ^= tail[0]
        value = (value * multiplier) & 0xFFFFFFFF

    value ^= value >> 13
    value = (value * multiplier) & 0xFFFFFFFF
    value ^= value >> 15
    return value & 0xFFFFFFFF


def murmurhash2(data: bytes, seed: int = 1) -> int:
    multiplier = 0x5BD1E995
    value = (seed ^ len(data)) & 0xFFFFFFFF
    offset = 0
    remaining = len(data)

    while remaining >= 4:
        block = int.from_bytes(data[offset : offset + 4], "little")
        value = _mix_murmurhash2_block(value, block, multiplier)
        offset += 4
        remaining -= 4
    return _finish_murmurhash2(value, data[offset:], multiplier)


def _chunks(items: Sequence, size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


class BatchExactMatchResolver:
    """按平台文件指纹批量解析项目，避免逐个名称搜索。"""

    def __init__(self):
        self.modrinth_request_gate = _ApiRateLimitGate(minimum_interval=0.08, dynamic_headers=True)
        self.curseforge_request_gate = _ApiRateLimitGate(minimum_interval=0.08, dynamic_headers=False)

    def resolve(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        options: ClassificationOptions,
        progress_callback: Optional[ExactMatchProgressCallback] = None,
        result_callback: Optional[ExactMatchResultCallback] = None,
    ) -> Dict[str, ExactMatchOutcome]:
        if not pending:
            return {}

        outcomes = {str(path): ExactMatchOutcome() for path, _meta in pending}
        paths = [path for path, _meta in pending]
        sha1_details = self._calculate_values(paths, _calculate_sha1_details, "sha1", progress_callback)
        sha1_by_path = {path: value[0] for path, value in sha1_details.items()}
        for path_key, sha1 in sha1_by_path.items():
            outcomes[path_key].sha1 = sha1
        self._resolve_modrinth(
            pending,
            sha1_by_path,
            outcomes,
            options,
            progress_callback,
            result_callback,
        )

        unresolved = [
            (path, meta)
            for path, meta in pending
            if outcomes[str(path)].classification is None
        ]
        if options.use_curseforge_api and unresolved:
            unresolved_paths = [path for path, _meta in unresolved]
            fingerprints = self._calculate_fingerprints(
                unresolved_paths,
                {path: value[1] for path, value in sha1_details.items()},
                progress_callback,
            )
            self._resolve_curseforge(
                unresolved,
                fingerprints,
                outcomes,
                options,
                progress_callback,
                result_callback,
            )
        return outcomes

    def _calculate_values(
        self,
        paths: Sequence[Path],
        calculator,
        stage: str,
        progress_callback: Optional[ExactMatchProgressCallback],
        max_workers: Optional[int] = None,
    ) -> Dict[str, object]:
        results: Dict[str, object] = {}
        worker_count = max_workers or choose_download_worker_count(len(paths))
        completed = 0
        self._notify_progress(progress_callback, stage, completed, len(paths), None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(calculator, path): path for path in paths}
            for future in concurrent.futures.as_completed(future_map):
                path = future_map[future]
                try:
                    results[str(path)] = future.result()
                except Exception:
                    pass
                completed += 1
                self._notify_progress(progress_callback, stage, completed, len(paths), path)
        return results

    def _calculate_fingerprints(
        self,
        paths: Sequence[Path],
        normalized_sizes: Dict[str, int],
        progress_callback: Optional[ExactMatchProgressCallback],
    ) -> Dict[str, object]:
        results: Dict[str, object] = {}
        completed = 0
        self._notify_progress(progress_callback, "curseforge_hash", completed, len(paths), None)
        worker_count = min(_CURSEFORGE_HASH_WORKERS, os.cpu_count() or 1, max(1, len(paths)))
        with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(
                    _calculate_curseforge_fingerprint_task,
                    (str(path), normalized_sizes[str(path)]),
                ): path
                for path in paths
                if str(path) in normalized_sizes
            }
            for future in concurrent.futures.as_completed(future_map):
                path = future_map[future]
                try:
                    results[str(path)] = future.result()
                except Exception:
                    pass
                completed += 1
                self._notify_progress(progress_callback, "curseforge_hash", completed, len(paths), path)
        return results

    @staticmethod
    def _notify_progress(
        callback: Optional[ExactMatchProgressCallback],
        stage: str,
        completed: int,
        total: int,
        path: Optional[Path],
    ) -> None:
        if callback is not None:
            callback(stage, completed, total, path)

    def _resolve_modrinth(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        sha1_by_path: Dict[str, object],
        outcomes: Dict[str, ExactMatchOutcome],
        options: ClassificationOptions,
        progress_callback: Optional[ExactMatchProgressCallback],
        result_callback: Optional[ExactMatchResultCallback],
    ) -> None:
        paths_by_sha1: Dict[str, list[str]] = {}
        for path, _meta in pending:
            sha1 = sha1_by_path.get(str(path))
            if sha1:
                paths_by_sha1.setdefault(str(sha1), []).append(str(path))
        hash_batches = list(_chunks(list(paths_by_sha1), _MODRINTH_BATCH_SIZE))
        total = len(pending)
        completed = total - sum(len(path_keys) for path_keys in paths_by_sha1.values())
        self._notify_progress(progress_callback, "modrinth", completed, total, None)

        def fetch_batch(batch):
            try:
                version_payload = http_post_json(
                    "https://api.modrinth.com/v2/version_files",
                    {"hashes": batch, "algorithm": "sha1"},
                    options.download_source,
                    timeout=20,
                    retry_rounds=2,
                    request_gate=self.modrinth_request_gate,
                )
                versions = (
                    {str(key): value for key, value in version_payload.items() if isinstance(value, dict)}
                    if isinstance(version_payload, dict)
                    else {}
                )
                project_ids = sorted({str(item.get("project_id") or "") for item in versions.values()} - {""})
                projects: Dict[str, dict] = {}
                if project_ids:
                    query = urllib.parse.quote(json.dumps(project_ids, separators=(",", ":")))
                    project_payload = http_get_json(
                        f"https://api.modrinth.com/v2/projects?ids={query}",
                        options.download_source,
                        timeout=20,
                        retry_rounds=2,
                        request_gate=self.modrinth_request_gate,
                    )
                    if isinstance(project_payload, list):
                        projects = {
                            str(item.get("id") or ""): item
                            for item in project_payload
                            if isinstance(item, dict)
                        }
                return batch, versions, projects
            except Exception:
                return batch, {}, {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(_MODRINTH_API_WORKERS, max(1, len(hash_batches)))) as executor:
            future_map = {executor.submit(fetch_batch, batch): batch for batch in hash_batches}
            for future in concurrent.futures.as_completed(future_map):
                batch, versions, projects = future.result()
                batch_path_keys = [path_key for sha1 in batch for path_key in paths_by_sha1.get(sha1, [])]
                for sha1, version in versions.items():
                    project_id = str(version.get("project_id") or "")
                    project = projects.get(project_id)
                    path_keys = paths_by_sha1.get(sha1, [])
                    if not path_keys or not project:
                        continue
                    slug = str(project.get("slug") or project_id)
                    classification = self._classification_from_modrinth(project, f"https://modrinth.com/mod/{slug}")
                    for path_key in path_keys:
                        outcome = outcomes[path_key]
                        outcome.matched_sources.add("modrinth")
                        if classification.category == "unknown":
                            outcome.fallback = classification
                        else:
                            outcome.classification = classification
                            if result_callback is not None:
                                result_callback(Path(path_key), outcome)
                completed += len(batch_path_keys)
                last_path = Path(batch_path_keys[-1]) if batch_path_keys else None
                self._notify_progress(progress_callback, "modrinth", completed, total, last_path)

    def _resolve_curseforge(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        fingerprints_by_path: Dict[str, object],
        outcomes: Dict[str, ExactMatchOutcome],
        options: ClassificationOptions,
        progress_callback: Optional[ExactMatchProgressCallback],
        result_callback: Optional[ExactMatchResultCallback],
    ) -> None:
        paths_by_fingerprint: Dict[int, list[str]] = {}
        for path, _meta in pending:
            fingerprint = fingerprints_by_path.get(str(path))
            if fingerprint is not None:
                paths_by_fingerprint.setdefault(int(fingerprint), []).append(str(path))
        fingerprints = list(paths_by_fingerprint)
        batches = list(_chunks(fingerprints, _CURSEFORGE_BATCH_SIZE))
        total = len(pending)
        completed = total - sum(len(path_keys) for path_keys in paths_by_fingerprint.values())
        curseforge_source = DOWNLOAD_SOURCE_MCIM if options.download_source == DOWNLOAD_SOURCE_SMART else options.download_source
        self._notify_progress(progress_callback, "curseforge", completed, total, None)

        def fetch_batch(batch):
            try:
                payload = http_post_json(
                    "https://api.curseforge.com/v1/fingerprints/432",
                    {"fingerprints": batch},
                    curseforge_source,
                    timeout=25,
                    retry_rounds=2,
                    request_gate=self.curseforge_request_gate,
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict) and isinstance(data.get("exactMatches"), list):
                    return batch, [item for item in data["exactMatches"] if isinstance(item, dict)]
            except Exception:
                pass
            return batch, []

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(_CURSEFORGE_API_WORKERS, max(1, len(batches)))) as executor:
            future_map = {executor.submit(fetch_batch, batch): batch for batch in batches}
            for future in concurrent.futures.as_completed(future_map):
                batch, matches = future.result()
                for match in matches:
                    file_data = match.get("file") if isinstance(match.get("file"), dict) else {}
                    fingerprint = file_data.get("fileFingerprint")
                    try:
                        path_keys = paths_by_fingerprint.get(int(fingerprint), [])
                    except (TypeError, ValueError):
                        path_keys = []
                    if not path_keys:
                        continue
                    classification = self._classification_from_curseforge(file_data)
                    for path_key in path_keys:
                        outcome = outcomes[path_key]
                        outcome.matched_sources.add("curseforge")
                        if classification.category == "unknown":
                            if outcome.fallback is None:
                                outcome.fallback = classification
                        else:
                            outcome.classification = classification
                            if result_callback is not None:
                                result_callback(Path(path_key), outcome)
                batch_path_keys = [
                    path_key
                    for fingerprint in batch
                    for path_key in paths_by_fingerprint.get(fingerprint, [])
                ]
                completed += len(batch_path_keys)
                last_path = Path(batch_path_keys[-1]) if batch_path_keys else None
                self._notify_progress(progress_callback, "curseforge", completed, total, last_path)

    def _classification_from_modrinth(self, project: dict, url: str) -> Classification:
        client_side = str(project.get("client_side") or "unknown")
        server_side = str(project.get("server_side") or "unknown")
        reason = f"Modrinth(SHA1 精确命中): client_side={client_side}, server_side={server_side}"
        if server_side == "unsupported":
            return Classification("client-only", "modrinth", reason, url)
        if server_side in {"required", "optional"}:
            return Classification("server-keep", "modrinth", reason, url)
        return Classification("unknown", "modrinth", reason, url)

    def _classification_from_curseforge(self, file_data: dict) -> Classification:
        project_id = str(file_data.get("modId") or "")
        project_name = str(file_data.get("displayName") or project_id or "未知项目")
        evidence_url = str(file_data.get("downloadUrl") or "")
        versions = {str(item).strip().lower() for item in file_data.get("gameVersions") or []}
        if "client" in versions and "server" not in versions:
            return Classification("client-only", "curseforge", f"CurseForge(指纹精确命中): {project_name} 标记为 Client", evidence_url)
        if "server" in versions:
            return Classification("server-keep", "curseforge", f"CurseForge(指纹精确命中): {project_name} 标记为 Server", evidence_url)
        return Classification(
            "unknown",
            "curseforge",
            f"CurseForge(指纹精确命中): {project_name} 已确认文件，但接口未提供明确客户端/服务端标记",
            evidence_url,
        )
