from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from ..shared import CLIENT_ENTRYPOINTS, Classification, LoaderType, ModMeta, get_optional_offline_db_path


class OfflineModDatabase:
    """Voxelum 离线库查询器。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_optional_offline_db_path()
        self._local = threading.local()

    def is_available(self) -> bool:
        return self.db_path.exists() and self.db_path.is_file()

    def lookup(self, jar_path: Path, meta: ModMeta) -> Optional[Classification]:
        if not self.is_available():
            return None

        sha1 = self._compute_sha1(jar_path)
        if not sha1:
            return None

        connection = self._get_connection()
        row = connection.execute(
            """
            SELECT
                mr.project AS modrinth_project,
                mr.version AS modrinth_version,
                cf.project AS curseforge_project,
                cf.file AS curseforge_file,
                f.id AS forge_mod_id,
                f.version AS forge_version,
                fm.id AS fabric_mod_id,
                fm.version AS fabric_version
            FROM file base
            LEFT JOIN modrinth_version mr ON mr.sha1 = base.sha1
            LEFT JOIN curseforge_file cf ON cf.sha1 = base.sha1
            LEFT JOIN forge_mod f ON f.sha1 = base.sha1
            LEFT JOIN fabric_mod fm ON fm.sha1 = base.sha1
            WHERE base.sha1 = ?
            LIMIT 1
            """,
            (sha1,),
        ).fetchone()
        if row is None:
            return None

        source, reason, evidence_url = self._build_evidence(row)
        category = self._classify_from_meta(meta)
        if not category:
            return Classification("unknown", source, reason, evidence_url)
        return Classification(category, source, reason, evidence_url)

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        try:
            connection.close()
        finally:
            self._local.connection = None

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

    def _classify_from_meta(self, meta: ModMeta) -> Optional[str]:
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

    def _build_evidence(self, row: sqlite3.Row) -> tuple[str, str, str]:
        reason_parts = ["本地离线库命中"]
        evidence_url = ""

        modrinth_project = str(row["modrinth_project"] or "").strip()
        modrinth_version = str(row["modrinth_version"] or "").strip()
        curseforge_project = str(row["curseforge_project"] or "").strip()
        curseforge_file = str(row["curseforge_file"] or "").strip()
        forge_mod_id = str(row["forge_mod_id"] or "").strip()
        forge_version = str(row["forge_version"] or "").strip()
        fabric_mod_id = str(row["fabric_mod_id"] or "").strip()
        fabric_version = str(row["fabric_version"] or "").strip()

        if modrinth_project:
            reason_parts.append(f"Modrinth 项目 {modrinth_project}")
            if modrinth_version:
                reason_parts[-1] += f" / 版本 {modrinth_version}"
            evidence_url = f"https://modrinth.com/project/{modrinth_project}"

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
