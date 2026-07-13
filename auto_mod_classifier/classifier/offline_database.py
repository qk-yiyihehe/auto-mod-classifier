from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..shared import CLIENT_ENTRYPOINTS, Classification, LoaderType, ModMeta, get_optional_offline_db_path


@dataclass(frozen=True)
class OfflineDatabaseMatch:
    modrinth_project: str = ""
    modrinth_version: str = ""
    curseforge_project: str = ""
    curseforge_file: str = ""
    mapped_modrinth_project: str = ""
    forge_mod_id: str = ""
    forge_version: str = ""
    fabric_mod_id: str = ""
    fabric_version: str = ""

    @property
    def has_identity(self) -> bool:
        return any(
            (
                self.modrinth_project,
                self.curseforge_project,
                self.forge_mod_id,
                self.fabric_mod_id,
            )
        )


class OfflineModDatabase:
    """Voxelum 离线库查询器。"""

    RELEASE_API_URL = "https://api.github.com/repos/Voxelum/minecraft-mods-database/releases/latest"
    USER_AGENT = "AutoModClassifier/3.01"

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_optional_offline_db_path()
        self._local = threading.local()
        self._update_lock = threading.Lock()
        self._last_update_summary = ""
        self._broken = False

    def is_available(self) -> bool:
        return not self._broken and self.db_path.exists() and self.db_path.is_file()

    def lookup(self, jar_path: Path, meta: ModMeta) -> Optional[Classification]:
        match = self.find_match(jar_path)
        if match is None:
            return None

        return self.lookup_match(meta, match)

    def lookup_match(self, meta: ModMeta, match: OfflineDatabaseMatch) -> Classification:
        source, reason, evidence_url = self._build_evidence(match)
        category = self.classify_meta_with_match(meta, match)
        if not category:
            return Classification("unknown", source, reason, evidence_url)
        return Classification(category, source, reason, evidence_url)

    def find_match(self, jar_path: Path) -> Optional[OfflineDatabaseMatch]:
        if not self.is_available():
            return None

        sha1 = self._compute_sha1(jar_path)
        if not sha1:
            return None

        return self.find_match_by_sha1(sha1)

    def find_match_by_sha1(self, sha1: str) -> Optional[OfflineDatabaseMatch]:
        if not self.is_available() or not sha1:
            return None

        try:
            connection = self._get_connection()
            row = connection.execute(
                """
                SELECT
                    mr.project AS modrinth_project,
                    mr.version AS modrinth_version,
                    cf.project AS curseforge_project,
                    cf.file AS curseforge_file,
                    pm.modrinth_project AS mapped_modrinth_project,
                    f.id AS forge_mod_id,
                    f.version AS forge_version,
                    fm.id AS fabric_mod_id,
                    fm.version AS fabric_version
                FROM file base
                LEFT JOIN modrinth_version mr ON mr.sha1 = base.sha1
                LEFT JOIN curseforge_file cf ON cf.sha1 = base.sha1
                LEFT JOIN project_mapping pm ON pm.curseforge_project = cf.project
                LEFT JOIN forge_mod f ON f.sha1 = base.sha1
                LEFT JOIN fabric_mod fm ON fm.sha1 = base.sha1
                WHERE base.sha1 = ?
                LIMIT 1
                """,
                (sha1,),
            ).fetchone()
        except sqlite3.DatabaseError:
            self._mark_broken()
            return None

        if row is None:
            return None

        match = OfflineDatabaseMatch(
            modrinth_project=str(row["modrinth_project"] or "").strip(),
            modrinth_version=str(row["modrinth_version"] or "").strip(),
            curseforge_project=str(row["curseforge_project"] or "").strip(),
            curseforge_file=str(row["curseforge_file"] or "").strip(),
            mapped_modrinth_project=str(row["mapped_modrinth_project"] or "").strip(),
            forge_mod_id=str(row["forge_mod_id"] or "").strip(),
            forge_version=str(row["forge_version"] or "").strip(),
            fabric_mod_id=str(row["fabric_mod_id"] or "").strip(),
            fabric_version=str(row["fabric_version"] or "").strip(),
        )
        return match if match.has_identity else None

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        try:
            connection.close()
        finally:
            self._local.connection = None

    def ensure_latest_database(
        self,
        *,
        auto_update: bool,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """按需检查并更新离线库；失败时静默回退，不中断主流程。"""
        if not auto_update:
            self._log(log_callback, "已关闭离线库自动检查更新，本次直接使用本地现有库。")
            return self.is_available()

        with self._update_lock:
            try:
                release = self._fetch_latest_release()
            except Exception as exc:
                self._log(log_callback, f"离线库检查已跳过：暂时无法连接更新源（{exc}）。")
                return self.is_available()

            db_asset = self._pick_asset(release, "db.sqlite")
            sha1_asset = self._pick_asset(release, "db.sqlite.sha1")
            if not db_asset or not sha1_asset:
                self._log(log_callback, "离线库检查已跳过：未找到可用的数据库发布文件。")
                return self.is_available()

            latest_tag = str(release.get("tag_name") or "").strip()
            try:
                latest_sha1 = self._download_text(str(sha1_asset.get("browser_download_url") or "")).strip().lower()
            except Exception as exc:
                self._log(log_callback, f"离线库检查已跳过：无法获取版本校验信息（{exc}）。")
                return self.is_available()

            local_sha1 = self._compute_sha1(self.db_path) if self.is_available() else ""
            if self.is_available() and local_sha1 and latest_sha1 and local_sha1 == latest_sha1:
                self._broken = False
                self._last_update_summary = f"离线库已是最新版本：{latest_tag or '当前版本'}"
                self._log(log_callback, self._last_update_summary)
                return True

            try:
                self._download_database_file(
                    str(db_asset.get("browser_download_url") or ""),
                    latest_sha1,
                )
            except Exception as exc:
                self._log(log_callback, f"离线库更新已跳过：下载失败（{exc}）。")
                return self.is_available()

            self._last_update_summary = f"离线库已更新：{latest_tag or '最新版本'}"
            self._broken = False
            self._log(log_callback, self._last_update_summary)
            return True

    def _get_connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
        return connection

    def _compute_sha1(self, jar_path: Path) -> str:
        try:
            digester = hashlib.sha1()
            with jar_path.open("rb") as fp:
                while True:
                    chunk = fp.read(1024 * 1024)
                    if not chunk:
                        break
                    digester.update(chunk)
            return digester.hexdigest()
        except Exception:
            return ""

    def classify_meta_with_match(self, meta: ModMeta, _match: OfflineDatabaseMatch) -> Optional[str]:
        environment = str(meta.environment or "").strip().lower()
        if environment == "client":
            return "client-only"
        if environment == "server":
            return "server-keep"
        if meta.client_side_only:
            return "client-only"

        dependency_sides = {item.upper() for item in meta.dependency_sides if item}
        if dependency_sides == {"CLIENT"}:
            return "client-only"
        if dependency_sides == {"SERVER"}:
            return "server-keep"

        if meta.loader in {LoaderType.FABRIC.value, LoaderType.QUILT.value}:
            normalized_entrypoints = {self._normalize_entrypoint_name(item) for item in meta.entrypoints if item}
            has_main = "main" in normalized_entrypoints
            has_server = "server" in normalized_entrypoints
            has_only_client_entrypoints = bool(normalized_entrypoints) and not has_main and not has_server
            if has_only_client_entrypoints and all(self._is_client_only_entrypoint(item) for item in normalized_entrypoints):
                return "client-only"
        return None

    def _normalize_entrypoint_name(self, value: str) -> str:
        return str(value or "").strip().lower().replace("-", "_")

    def _is_client_only_entrypoint(self, value: str) -> bool:
        normalized = self._normalize_entrypoint_name(value)
        if normalized in CLIENT_ENTRYPOINTS:
            return True
        return any(token in normalized for token in CLIENT_ENTRYPOINTS)

    def _build_evidence(self, match: OfflineDatabaseMatch) -> tuple[str, str, str]:
        reason_parts = ["本地离线库命中"]
        evidence_url = ""

        modrinth_project = match.modrinth_project
        modrinth_version = match.modrinth_version
        curseforge_project = match.curseforge_project
        curseforge_file = match.curseforge_file
        forge_mod_id = match.forge_mod_id
        forge_version = match.forge_version
        fabric_mod_id = match.fabric_mod_id
        fabric_version = match.fabric_version

        if modrinth_project:
            reason_parts.append(f"Modrinth 项目 {modrinth_project}")
            if modrinth_version:
                reason_parts[-1] += f" / 版本 {modrinth_version}"
            evidence_url = f"https://modrinth.com/project/{modrinth_project}"
        elif match.mapped_modrinth_project:
            reason_parts.append(f"CurseForge 映射到 Modrinth 项目 {match.mapped_modrinth_project}")
            evidence_url = f"https://modrinth.com/project/{match.mapped_modrinth_project}"

        if curseforge_project:
            cf_text = f"CurseForge 项目 {curseforge_project}"
            if curseforge_file:
                cf_text += f" / 文件 {curseforge_file}"
            reason_parts.append(cf_text)
            if not evidence_url:
                evidence_url = f"https://www.curseforge.com/minecraft/mc-mods/{curseforge_project}"

        if fabric_mod_id:
            fabric_text = f"Fabric 模组ID {fabric_mod_id}"
            if fabric_version:
                fabric_text += f" / 版本 {fabric_version}"
            reason_parts.append(fabric_text)

        if forge_mod_id:
            forge_text = f"Forge 模组ID {forge_mod_id}"
            if forge_version:
                forge_text += f" / 版本 {forge_version}"
            reason_parts.append(forge_text)

        return "offline-db", "；".join(reason_parts), evidence_url

    def _fetch_latest_release(self) -> dict:
        request = urllib.request.Request(
            self.RELEASE_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": self.USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))

    def _pick_asset(self, release: dict, asset_name: str) -> Optional[dict]:
        for asset in release.get("assets", []) or []:
            if str(asset.get("name") or "").strip() == asset_name:
                return asset
        return None

    def _download_text(self, url: str) -> str:
        if not url:
            raise RuntimeError("下载地址为空")
        request = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _download_database_file(self, url: str, expected_sha1: str) -> None:
        if not url:
            raise RuntimeError("数据库下载地址为空")

        self.close()
        temp_path = self.db_path.with_suffix(".sqlite.tmp")
        request = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response, temp_path.open("wb") as fp:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                fp.write(chunk)

        downloaded_sha1 = self._compute_sha1(temp_path).lower()
        if expected_sha1 and downloaded_sha1 != expected_sha1.lower():
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("下载完成，但校验失败")

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(self.db_path)

    def _log(self, log_callback: Optional[Callable[[str], None]], message: str) -> None:
        if log_callback is None:
            return
        try:
            log_callback(message)
        except Exception:
            pass

    def _mark_broken(self) -> None:
        self._broken = True
        self.close()
