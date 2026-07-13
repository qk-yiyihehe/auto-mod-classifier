from __future__ import annotations

import concurrent.futures
import hashlib
import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from ..download_support import choose_download_worker_count, http_get_json, http_post_json
from ..shared import Classification, DOWNLOAD_SOURCE_MCIM, DOWNLOAD_SOURCE_SMART, ModMeta
from .models import ClassificationOptions


_MODRINTH_BATCH_SIZE = 100
_CURSEFORGE_BATCH_SIZE = 500
_CURSEFORGE_WHITESPACE = {9, 10, 13, 32}


@dataclass
class ExactMatchOutcome:
    sha1: str = ""
    classification: Optional[Classification] = None
    fallback: Optional[Classification] = None
    matched_sources: set[str] = field(default_factory=set)


def calculate_sha1(path: Path) -> str:
    digester = hashlib.sha1()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digester.update(chunk)
    return digester.hexdigest()


def calculate_curseforge_fingerprint(path: Path) -> int:
    return calculate_file_hashes(path)[1]


def calculate_file_hashes(path: Path) -> tuple[str, int]:
    sha1 = hashlib.sha1()
    normalized = bytearray()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            sha1.update(chunk)
            normalized.extend(byte for byte in chunk if byte not in _CURSEFORGE_WHITESPACE)
    return sha1.hexdigest(), murmurhash2(bytes(normalized), seed=1)


def murmurhash2(data: bytes, seed: int = 1) -> int:
    multiplier = 0x5BD1E995
    value = (seed ^ len(data)) & 0xFFFFFFFF
    offset = 0
    remaining = len(data)

    while remaining >= 4:
        block = int.from_bytes(data[offset : offset + 4], "little")
        block = (block * multiplier) & 0xFFFFFFFF
        block ^= block >> 24
        block = (block * multiplier) & 0xFFFFFFFF
        value = (value * multiplier) & 0xFFFFFFFF
        value ^= block
        offset += 4
        remaining -= 4

    if remaining == 3:
        value ^= data[offset + 2] << 16
    if remaining >= 2:
        value ^= data[offset + 1] << 8
    if remaining >= 1:
        value ^= data[offset]
        value = (value * multiplier) & 0xFFFFFFFF

    value ^= value >> 13
    value = (value * multiplier) & 0xFFFFFFFF
    value ^= value >> 15
    return value & 0xFFFFFFFF


def _chunks(items: Sequence, size: int):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


class BatchExactMatchResolver:
    """按平台文件指纹批量解析项目，避免逐个名称搜索。"""

    def resolve(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        options: ClassificationOptions,
    ) -> Dict[str, ExactMatchOutcome]:
        if not pending:
            return {}

        outcomes = {str(path): ExactMatchOutcome() for path, _meta in pending}
        hashes_by_path = self._calculate_hashes([path for path, _meta in pending], calculate_file_hashes)
        sha1_by_path = {path: value[0] for path, value in hashes_by_path.items()}
        for path_key, sha1 in sha1_by_path.items():
            outcomes[path_key].sha1 = sha1
        self._resolve_modrinth(pending, sha1_by_path, outcomes, options)

        unresolved = [
            (path, meta)
            for path, meta in pending
            if outcomes[str(path)].classification is None
        ]
        if options.use_curseforge_api and unresolved:
            fingerprints = {
                str(path): hashes_by_path[str(path)][1]
                for path, _meta in unresolved
                if str(path) in hashes_by_path
            }
            self._resolve_curseforge(unresolved, fingerprints, outcomes, options)
        return outcomes

    def _calculate_hashes(self, paths: Sequence[Path], calculator) -> Dict[str, object]:
        results: Dict[str, object] = {}
        worker_count = choose_download_worker_count(len(paths))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(calculator, path): path for path in paths}
            for future in concurrent.futures.as_completed(future_map):
                path = future_map[future]
                try:
                    results[str(path)] = future.result()
                except Exception:
                    continue
        return results

    def _resolve_modrinth(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        sha1_by_path: Dict[str, object],
        outcomes: Dict[str, ExactMatchOutcome],
        options: ClassificationOptions,
    ) -> None:
        paths_by_sha1: Dict[str, list[str]] = {}
        for path, _meta in pending:
            sha1 = sha1_by_path.get(str(path))
            if sha1:
                paths_by_sha1.setdefault(str(sha1), []).append(str(path))
        versions: Dict[str, dict] = {}
        try:
            hashes = list(paths_by_sha1)
            for batch in _chunks(hashes, _MODRINTH_BATCH_SIZE):
                payload = http_post_json(
                    "https://api.modrinth.com/v2/version_files",
                    {"hashes": batch, "algorithm": "sha1"},
                    options.download_source,
                    timeout=20,
                    retry_rounds=2,
                )
                if isinstance(payload, dict):
                    versions.update({str(key): value for key, value in payload.items() if isinstance(value, dict)})
        except Exception:
            return

        project_ids = sorted({str(item.get("project_id") or "") for item in versions.values()} - {""})
        projects: Dict[str, dict] = {}
        try:
            for batch in _chunks(project_ids, _MODRINTH_BATCH_SIZE):
                query = urllib.parse.quote(json.dumps(batch, separators=(",", ":")))
                payload = http_get_json(
                    f"https://api.modrinth.com/v2/projects?ids={query}",
                    options.download_source,
                    timeout=20,
                    retry_rounds=2,
                )
                if isinstance(payload, list):
                    projects.update({str(item.get("id") or ""): item for item in payload if isinstance(item, dict)})
        except Exception:
            return

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

    def _resolve_curseforge(
        self,
        pending: Sequence[Tuple[Path, ModMeta]],
        fingerprints_by_path: Dict[str, object],
        outcomes: Dict[str, ExactMatchOutcome],
        options: ClassificationOptions,
    ) -> None:
        paths_by_fingerprint: Dict[int, list[str]] = {}
        for path, _meta in pending:
            fingerprint = fingerprints_by_path.get(str(path))
            if fingerprint is not None:
                paths_by_fingerprint.setdefault(int(fingerprint), []).append(str(path))
        try:
            matches = []
            fingerprints = list(paths_by_fingerprint)
            curseforge_source = DOWNLOAD_SOURCE_MCIM if options.download_source == DOWNLOAD_SOURCE_SMART else options.download_source
            for batch in _chunks(fingerprints, _CURSEFORGE_BATCH_SIZE):
                payload = http_post_json(
                    "https://api.curseforge.com/v1/fingerprints/432",
                    {"fingerprints": batch},
                    curseforge_source,
                    timeout=25,
                    retry_rounds=2,
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict) and isinstance(data.get("exactMatches"), list):
                    matches.extend(item for item in data["exactMatches"] if isinstance(item, dict))
        except Exception:
            return

        project_ids = sorted(
            {
                int(project_id)
                for item in matches
                if (project_id := str(item.get("id") or (item.get("file") or {}).get("modId") or "")).isdigit()
            }
        )
        projects: Dict[str, dict] = {}
        try:
            for batch in _chunks(project_ids, _CURSEFORGE_BATCH_SIZE):
                payload = http_post_json(
                    "https://api.curseforge.com/v1/mods",
                    {"modIds": batch},
                    curseforge_source,
                    timeout=25,
                    retry_rounds=2,
                )
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    projects.update({str(item.get("id") or ""): item for item in data if isinstance(item, dict)})
        except Exception:
            pass

        for match in matches:
            file_data = match.get("file") if isinstance(match.get("file"), dict) else {}
            fingerprint = file_data.get("fileFingerprint")
            try:
                path_keys = paths_by_fingerprint.get(int(fingerprint), [])
            except (TypeError, ValueError):
                path_keys = []
            if not path_keys:
                continue
            project_id = str(match.get("id") or file_data.get("modId") or "")
            project = projects.get(project_id, match)
            classification = self._classification_from_curseforge(project, file_data)
            for path_key in path_keys:
                outcome = outcomes[path_key]
                outcome.matched_sources.add("curseforge")
                if classification.category == "unknown":
                    if outcome.fallback is None:
                        outcome.fallback = classification
                else:
                    outcome.classification = classification

    def _classification_from_modrinth(self, project: dict, url: str) -> Classification:
        client_side = str(project.get("client_side") or "unknown")
        server_side = str(project.get("server_side") or "unknown")
        reason = f"Modrinth(SHA1 精确命中): client_side={client_side}, server_side={server_side}"
        if server_side == "unsupported":
            return Classification("client-only", "modrinth", reason, url)
        if server_side in {"required", "optional"}:
            return Classification("server-keep", "modrinth", reason, url)
        return Classification("unknown", "modrinth", reason, url)

    def _classification_from_curseforge(self, project: dict, file_data: dict) -> Classification:
        project_id = str(project.get("id") or file_data.get("modId") or "")
        project_name = str(project.get("name") or file_data.get("displayName") or project_id or "未知项目")
        website_url = str((project.get("links") or {}).get("websiteUrl") or "")
        versions = {str(item).strip().lower() for item in file_data.get("gameVersions") or []}
        if "client" in versions and "server" not in versions:
            return Classification("client-only", "curseforge", f"CurseForge(指纹精确命中): {project_name} 标记为 Client", website_url)
        if "server" in versions:
            return Classification("server-keep", "curseforge", f"CurseForge(指纹精确命中): {project_name} 标记为 Server", website_url)
        return Classification(
            "unknown",
            "curseforge",
            f"CurseForge(指纹精确命中): {project_name} 已确认文件，但接口未提供明确客户端/服务端标记",
            website_url,
        )
