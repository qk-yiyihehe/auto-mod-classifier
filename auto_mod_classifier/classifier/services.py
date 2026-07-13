import json
import re
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional, Sequence

from ..shared import Classification, LoaderType, ModMeta
from .offline_database import OfflineDatabaseMatch
from .contracts import (
    ClassificationStrategy,
    LocalClassifier,
    MetadataReader,
    RemoteClassificationSource,
    SupplementalClassificationSource,
)
from .exact_match import BatchExactMatchResolver
from .models import ClassificationOptions, RemoteResolutionResult
from .text_utils import ClassifierTextTools


class JarMetadataReader(MetadataReader):
    """独立的 jar 元数据读取器，后续可单独替换或扩展。"""

    def __init__(self, text_tools: Optional[ClassifierTextTools] = None):
        self.text_tools = text_tools or ClassifierTextTools()

    def read(self, jar_path: Path) -> ModMeta:
        # 这里专门负责“从 jar 里读出线索”，不参与最终分类决定。
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                entry_names = set(zf.namelist())

                if "fabric.mod.json" in entry_names:
                    try:
                        fabric_text = self._read_zip_entry_text(zf, "fabric.mod.json")
                        if fabric_text is None:
                            raise ValueError("无法读取 fabric.mod.json")
                        fabric_json = json.loads(fabric_text)
                    except Exception as exc:
                        return self._build_damaged_mod_meta(
                            jar_path,
                            f"fabric.mod.json 解析失败: {type(exc).__name__}: {exc}",
                        )
                    entrypoints = list((fabric_json.get("entrypoints") or {}).keys())
                    depends = list((fabric_json.get("depends") or {}).keys())
                    return ModMeta(
                        file_name=jar_path.name,
                        file_path=str(jar_path),
                        mod_id=str(fabric_json.get("id") or "").strip(),
                        mod_name=str(fabric_json.get("name") or jar_path.stem).strip(),
                        description=str(fabric_json.get("description") or "").strip(),
                        environment=str(fabric_json.get("environment") or "*").strip(),
                        entrypoints=entrypoints,
                        depends=depends,
                        loader=LoaderType.FABRIC.value,
                        metadata_source="fabric.mod.json",
                        query_tokens=self.text_tools.build_query_tokens(
                            jar_path.name,
                            str(fabric_json.get("id") or "").strip(),
                            str(fabric_json.get("name") or "").strip(),
                        ),
                    )

                if "quilt.mod.json" in entry_names:
                    try:
                        quilt_text = self._read_zip_entry_text(zf, "quilt.mod.json")
                        if quilt_text is None:
                            raise ValueError("无法读取 quilt.mod.json")
                        return self._parse_quilt_metadata(jar_path, json.loads(quilt_text))
                    except Exception as exc:
                        return self._build_damaged_mod_meta(
                            jar_path,
                            f"quilt.mod.json 解析失败: {type(exc).__name__}: {exc}",
                        )

                for source_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                    if source_name not in entry_names:
                        continue
                    try:
                        toml_text = self._read_zip_entry_text(zf, source_name)
                        if toml_text is None:
                            raise ValueError(f"无法读取 {source_name}")
                        if not toml_text.strip():
                            raise ValueError(f"{source_name} 为空")
                        return self._parse_forge_toml_metadata(jar_path, toml_text, source_name)
                    except Exception as exc:
                        return self._build_damaged_mod_meta(
                            jar_path,
                            f"{source_name} 解析失败: {type(exc).__name__}: {exc}",
                        )
        except zipfile.BadZipFile as exc:
            return self._build_damaged_mod_meta(jar_path, f"Zip 结构损坏: {type(exc).__name__}: {exc}")
        except Exception as exc:
            return self._build_damaged_mod_meta(jar_path, f"Jar 读取失败: {type(exc).__name__}: {exc}")

        return ModMeta(
            file_name=jar_path.name,
            file_path=str(jar_path),
            mod_id="",
            mod_name=jar_path.stem,
            description="",
            environment="",
            entrypoints=[],
            depends=[],
            loader=LoaderType.UNKNOWN.value,
            metadata_source="filename-only",
            query_tokens=self.text_tools.build_query_tokens(jar_path.name, jar_path.stem),
        )

    def _read_zip_entry_text(self, zf: zipfile.ZipFile, entry_name: str) -> Optional[str]:
        try:
            with zf.open(entry_name) as fp:
                return fp.read().decode("utf-8", errors="ignore")
        except KeyError:
            return None
        except Exception:
            return None

    def _parse_quilt_metadata(self, file_path: Path, quilt_json: dict) -> ModMeta:
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
            query_tokens=self.text_tools.build_query_tokens(file_path.name, mod_id, mod_name),
        )

    def _extract_forge_dependency_sides(self, toml_text: str) -> list[str]:
        sides: list[str] = []
        blocks = re.split(r"(?m)^\s*\[\[dependencies\.[^\]]+\]\]\s*", toml_text)
        for block in blocks[1:]:
            match = re.search(r'(?m)^\s*side\s*=\s*"([A-Z_]+)"', block)
            if not match:
                continue
            side = match.group(1).strip().upper()
            if side:
                sides.append(side)
        return sides

    def _parse_forge_toml_metadata(self, file_path: Path, toml_text: str, source_name: str) -> ModMeta:
        mod_ids = re.findall(r'(?m)^\s*modId\s*=\s*"([^"]+)"', toml_text)
        display_names = re.findall(r'(?m)^\s*displayName\s*=\s*"([^"]+)"', toml_text)
        description_match = re.search(
            r'(?ms)^\s*description\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"]*)")',
            toml_text,
        )
        client_side_only = bool(re.search(r"(?m)^\s*clientSideOnly\s*=\s*true\b", toml_text))
        dependency_sides = self._extract_forge_dependency_sides(toml_text)

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
            query_tokens=self.text_tools.build_query_tokens(file_path.name, mod_id, mod_name),
            client_side_only=client_side_only,
            dependency_sides=dependency_sides,
        )

    def _build_damaged_mod_meta(self, file_path: Path, reason: str, metadata_source: str = "damaged-jar") -> ModMeta:
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
            query_tokens=self.text_tools.build_query_tokens(file_path.name, file_path.stem),
            jar_status="damaged",
            jar_issue=reason,
        )


class DefaultLocalClassifier(LocalClassifier):
    """当前本地元数据判定规则。"""

    def __init__(self, text_tools: ClassifierTextTools):
        self.text_tools = text_tools

    def classify(self, meta: ModMeta) -> Classification:
        # 本地规则只做“有把握的判断”，拿不准就交给远程来源继续查。
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
        normalized_entrypoints = {self.text_tools.normalize_entrypoint_name(item) for item in entrypoints if item}
        has_main = "main" in normalized_entrypoints
        has_server = "server" in normalized_entrypoints
        non_client_only = [item for item in entrypoints if not self.text_tools.is_client_only_entrypoint(item)]

        if meta.environment == "client":
            return Classification("client-only", "local", "fabric/quilt 元数据 environment=client")

        if meta.environment == "server":
            return Classification("server-keep", "local", "fabric/quilt 元数据 environment=server")

        if entrypoints and not has_main and not has_server and not non_client_only:
            return Classification("unknown", "local", "仅声明客户端入口点，继续联网核对")

        if has_main or has_server:
            return Classification("unknown", "local", "含 main/server 入口，本地无法直接确认")

        return Classification("unknown", "local", "本地元数据不足")


class ModrinthRemoteSource:
    name = "modrinth"
    concurrency_group = "modrinth"
    preserve_unknown_result = True

    def __init__(self, classifier):
        self.classifier = classifier

    def is_enabled(self, options: ClassificationOptions) -> bool:
        return True

    def lookup(self, meta: ModMeta) -> Optional[Classification]:
        # 先尝试按 mod_id 直连，再退回搜索。
        direct = self._direct_lookup(meta)
        if direct and direct.category != "unknown":
            return direct

        candidate_map: dict[str, tuple[int, dict, str]] = {}
        queries = self.classifier.collect_unique_queries(meta.query_tokens)
        if not queries:
            return None

        for query in queries:
            cache_key = f"modrinth::{query}"
            url = (
                "https://api.modrinth.com/v2/search?"
                f"query={urllib.parse.quote(query)}&limit=8&facets=%5B%5B%22project_type%3Amod%22%5D%5D"
            )
            response = self.classifier.modrinth_json_request(cache_key, url) or {}
            for hit in response.get("hits", []):
                score = self.classifier.score_modrinth_hit(meta, query, hit)
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
        if score < 180 or score - runner_up < 35 or not self.classifier.is_confident_modrinth_candidate(meta, hit):
            return None

        return self._classification_from_payload(
            hit,
            "Modrinth",
            f"https://modrinth.com/mod/{hit.get('slug', '')}",
        )

    def lookup_by_project_id(self, meta: ModMeta, project_id: str) -> Optional[Classification]:
        project_id = str(project_id or "").strip()
        if not project_id:
            return None

        cache_key = f"modrinth-project-id::{project_id}"
        url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(project_id)}"
        payload = self.classifier.modrinth_json_request(cache_key, url)
        if not payload:
            return None
        return self._classification_from_payload(
            payload,
            "Modrinth(离线库直连)",
            f"https://modrinth.com/mod/{payload.get('slug', '')}",
        )

    def _direct_lookup(self, meta: ModMeta) -> Optional[Classification]:
        if not meta.mod_id or self.classifier.is_placeholder_value(meta.mod_id):
            return None

        slug = meta.mod_id.strip()
        cache_key = f"modrinth-project::{slug}"
        url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(slug)}"
        payload = self.classifier.modrinth_json_request(cache_key, url)
        if not payload:
            return None

        payload_slug = self.classifier.normalize_text(str(payload.get("slug", "")))
        payload_title = self.classifier.normalize_text(str(payload.get("title", "")))
        search_keys = [
            self.classifier.normalize_text(value)
            for value in self.classifier.collect_search_values(meta, meta.mod_id)
            if self.classifier.normalize_text(value)
        ]
        if payload_slug not in search_keys and payload_title not in search_keys:
            return None

        hit_score = self.classifier.score_modrinth_hit(meta, meta.mod_id, payload)
        if hit_score < 190 or not self.classifier.is_confident_modrinth_candidate(meta, payload):
            return None

        return self._classification_from_payload(
            payload,
            "Modrinth(直连)",
            f"https://modrinth.com/mod/{payload.get('slug', '')}",
        )

    def _classification_from_payload(self, payload: dict, reason_prefix: str, url: str) -> Classification:
        client_side = str(payload.get("client_side", "unknown"))
        server_side = str(payload.get("server_side", "unknown"))
        reason = f"{reason_prefix}: client_side={client_side}, server_side={server_side}"
        if server_side == "unsupported":
            return Classification("client-only", "modrinth", reason, url)
        if server_side in {"required", "optional"}:
            return Classification("server-keep", "modrinth", reason, url)
        return Classification("unknown", "modrinth", reason, url)


class OfflineDatabaseSource:
    name = "offline-db"
    preserve_unknown_result = True

    def __init__(self, classifier):
        self.classifier = classifier
        self.modrinth_source = ModrinthRemoteSource(classifier)
        self.curseforge_source = CurseforgeRemoteSource(classifier)

    def is_enabled(self, options: ClassificationOptions) -> bool:
        return options.use_offline_database and self.classifier.offline_database.is_available()

    def lookup(self, jar_path: Path, meta: ModMeta, sha1: str = "") -> Optional[Classification]:
        match = (
            self.classifier.offline_database.find_match_by_sha1(sha1)
            if sha1
            else self.classifier.offline_database.find_match(jar_path)
        )
        if match is None:
            return None

        local_classification = self.classifier.offline_database.lookup_match(meta, match)
        if local_classification and local_classification.category != "unknown":
            return local_classification

        direct = self._lookup_precise_remote(meta, match)
        if direct is not None:
            return direct
        return local_classification

    def _lookup_precise_remote(self, meta: ModMeta, match: OfflineDatabaseMatch) -> Optional[Classification]:
        if match.modrinth_project:
            classification = self.modrinth_source.lookup_by_project_id(meta, match.modrinth_project)
            if classification is not None:
                return classification
        if match.mapped_modrinth_project:
            classification = self.modrinth_source.lookup_by_project_id(meta, match.mapped_modrinth_project)
            if classification is not None:
                return classification
        if match.curseforge_project and getattr(self.classifier, "use_curseforge_api", True):
            classification = self.curseforge_source.lookup_by_file_identity(
                meta,
                project_id=match.curseforge_project,
                file_id=match.curseforge_file,
            )
            if classification is not None:
                return classification
        return None


class McmodRemoteSource:
    name = "mcmod"
    concurrency_group = "mcmod"
    preserve_unknown_result = False

    def __init__(self, classifier):
        self.classifier = classifier

    def is_enabled(self, options: ClassificationOptions) -> bool:
        return options.use_mcmod

    def lookup(self, meta: ModMeta) -> Optional[Classification]:
        # MC百科更像“人工词条库”，所以这里会多做几轮标题比对。
        candidates: list[tuple[int, Classification]] = []
        for query in self.classifier.collect_unique_queries(self.classifier.build_mcmod_query_tokens(meta)):
            search_key = f"mcmod-search::{query}"
            url = f"https://search.mcmod.cn/s?key={urllib.parse.quote(query)}"
            html = self.classifier.mcmod_text_request(search_key, url)
            search_results = self.classifier.extract_mcmod_search_results(html)
            if not search_results:
                continue

            ranked_results: list[tuple[int, str, str]] = []
            for title, link in search_results:
                score = self.classifier.score_mcmod_page(meta, title)
                if score > 0 or len(search_results) == 1:
                    ranked_results.append((score, title, link))
            ranked_results.sort(key=lambda item: item[0], reverse=True)

            for search_score, search_title, link in ranked_results[:5]:
                page_key = f"mcmod-page::{link}"
                page_html = self.classifier.mcmod_text_request(page_key, link, max_attempts=3)
                if not page_html:
                    continue

                title = self.classifier.extract_page_title(page_html) or search_title
                env_text = self.classifier.extract_mcmod_environment(page_html)
                if not env_text:
                    continue

                score = max(
                    search_score,
                    self.classifier.score_mcmod_page(meta, search_title),
                    self.classifier.score_mcmod_page(meta, title),
                    self.classifier.score_mcmod_page(meta, f"{search_title} {title}"),
                )
                if score < 100 or not (
                    self.classifier.is_confident_mcmod_candidate(meta, search_title)
                    or self.classifier.is_confident_mcmod_candidate(meta, title)
                    or self.classifier.is_confident_mcmod_candidate(meta, f"{search_title} {title}")
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


class CurseforgeRemoteSource:
    name = "curseforge"
    concurrency_group = "curseforge"
    preserve_unknown_result = False

    def __init__(self, classifier):
        self.classifier = classifier

    def is_enabled(self, options: ClassificationOptions) -> bool:
        return options.use_curseforge

    def lookup(self, meta: ModMeta) -> Optional[Classification]:
        # CurseForge 目前是兜底来源，所以放在策略链后面。
        for query in self.classifier.collect_unique_queries(self.classifier.build_mcmod_query_tokens(meta)):
            search_key = f"cf-search::{query}"
            url = (
                "https://www.curseforge.com/minecraft/search?"
                f"class=mc-mods&search={urllib.parse.quote(query)}"
            )
            html = self.classifier.mcmod_text_request(search_key, url)
            if not html:
                continue

            seen = set()
            links: list[tuple[int, str, str]] = []
            for pattern in [
                r'<a[^>]+href="(/minecraft/mc-mods/[^"?]+)"[^>]*>(.*?)</a>',
                r'href="(/minecraft/mc-mods/[^"?]+)"[^>]*>([^<]+)<',
            ]:
                for href, raw in re.findall(pattern, html, re.I | re.S):
                    if "/download" in href or "/relations" in href or "/comments" in href or "/files" in href:
                        continue
                    title = re.sub(r"<.*?>", "", raw).strip()
                    title = re.sub(r"\s+", " ", title)
                    if href not in seen and title and title != "Download" and title != "Relations" and title != "Comments":
                        seen.add(href)
                        score = self.classifier.score_mcmod_page(meta, title)
                        if score <= 0:
                            continue
                        links.append((score, title, "https://www.curseforge.com" + href))

            if not links:
                continue
            links.sort(key=lambda item: item[0], reverse=True)

            for search_score, title, link in links[:5]:
                page_key = f"cf-page::{link}"
                page_html = self.classifier.mcmod_text_request(page_key, link, max_attempts=3)
                if not page_html:
                    continue
                score = max(
                    search_score,
                    self.classifier.score_mcmod_page(meta, title),
                    self.classifier.score_mcmod_page(meta, self.classifier.extract_page_title(page_html) or title),
                )
                if score < 100 or not self.classifier.is_confident_mcmod_candidate(meta, title):
                    continue

                matched = re.search(r'Environment\s*:\s*(Client(?:&amp;)?\s*(?:&amp;)?\s*Server|Client)\b', page_html, re.I)
                if matched:
                    server_side = matched.group(1).strip()
                else:
                    fallback_match = re.search(
                        r'<span[^>]*>\s*(Client\s*(?:&amp;)?\s*(?:&amp;)?\s*Server|Client)\s*</span>',
                        page_html,
                        re.I,
                    )
                    if not fallback_match:
                        continue
                    server_side = fallback_match.group(1).strip()

                server_side = server_side.replace("&amp;", "&")
                if "Server" in server_side:
                    return Classification("server-keep", "curseforge", f"CurseForge: {server_side}", link)
                return Classification("client-only", "curseforge", f"CurseForge: {server_side}", link)
            break
        return None

    def lookup_by_file_identity(self, meta: ModMeta, project_id: str, file_id: str) -> Optional[Classification]:
        project_id = str(project_id or "").strip()
        file_id = str(file_id or "").strip()
        if not project_id:
            return None

        file_payload = None
        if file_id:
            cache_key = f"cf-file-id::{project_id}::{file_id}"
            file_url = f"https://api.curseforge.com/v1/mods/{urllib.parse.quote(project_id)}/files/{urllib.parse.quote(file_id)}"
            file_payload = self.classifier.curseforge_json_request(cache_key, file_url)

        project_payload = self.classifier.curseforge_json_request(
            f"cf-project-id::{project_id}",
            f"https://api.curseforge.com/v1/mods/{urllib.parse.quote(project_id)}",
        )
        project_data = (project_payload or {}).get("data") or {}
        file_data = (file_payload or {}).get("data") or {}
        if not project_data and not file_data:
            return None

        return self._classification_from_api_payload(meta, project_data, file_data)

    def _classification_from_api_payload(self, meta: ModMeta, project_data: dict, file_data: dict) -> Optional[Classification]:
        game_versions = [str(item).strip() for item in file_data.get("gameVersions") or [] if str(item).strip()]
        normalized_versions = {item.lower() for item in game_versions}
        website_url = str((project_data.get("links") or {}).get("websiteUrl") or "").strip()
        project_name = str(project_data.get("name") or file_data.get("displayName") or meta.mod_name or meta.file_name).strip()

        if "client" in normalized_versions and "server" not in normalized_versions:
            return Classification(
                "client-only",
                "curseforge",
                f"CurseForge(离线库直连): {project_name} 标记为 Client",
                website_url,
            )
        if "server" in normalized_versions:
            return Classification(
                "server-keep",
                "curseforge",
                f"CurseForge(离线库直连): {project_name} 标记为 Server",
                website_url,
            )

        # CurseForge API 能精确命中项目/文件，但大多数文件并不直接提供端侧结论。
        # 这种场景保留 unknown，让后面的网页兜底继续尝试。
        if file_data:
            return Classification(
                "unknown",
                "curseforge",
                f"CurseForge(离线库直连): {project_name} 已命中精确文件，但接口未提供明确客户端/服务端标记",
                website_url,
            )
        return None


class DefaultClassificationStrategy(ClassificationStrategy):
    """默认筛选策略：本地判定 -> 离线库 -> Modrinth -> MC百科 -> CurseForge。"""

    def __init__(self, classifier):
        # strategy 的意义是把“怎么判、先查谁、查不到怎么办”集中放在一起。
        self.text_tools = ClassifierTextTools()
        self.metadata_reader = JarMetadataReader(self.text_tools)
        self.local_classifier = DefaultLocalClassifier(self.text_tools)
        self.exact_match_resolver = BatchExactMatchResolver()
        self.supplemental_sources: list[SupplementalClassificationSource] = [
            OfflineDatabaseSource(classifier),
        ]
        self.remote_sources: list[RemoteClassificationSource] = [
            ModrinthRemoteSource(classifier),
            McmodRemoteSource(classifier),
            CurseforgeRemoteSource(classifier),
        ]

    def is_local_final(self, classification: Classification) -> bool:
        return classification.category in {"client-only", "server-keep"}

    def get_remote_sources(self, options: ClassificationOptions) -> Sequence[RemoteClassificationSource]:
        return [source for source in self.remote_sources if source.is_enabled(options)]

    def get_supplemental_sources(self, options: ClassificationOptions) -> Sequence[SupplementalClassificationSource]:
        return [source for source in self.supplemental_sources if source.is_enabled(options)]

    def choose_fallback(
        self,
        local: Classification,
        remote_results: Sequence[RemoteResolutionResult],
    ) -> Classification:
        # 远程全都没给出确定答案时，最后再决定保留哪个 unknown 结果。
        for result in remote_results:
            if result.classification and result.classification.category != "unknown":
                return result.classification

        for result in remote_results:
            if result.preserve_unknown and result.classification:
                return result.classification

        return local
