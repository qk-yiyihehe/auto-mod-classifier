import csv
import json
import queue
import re
import shutil
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_TITLE = "自动筛选模组分类器"
USER_AGENT = "AutoModClassifier/1.0 (+Codex)"
CLIENT_ENTRYPOINTS = {
    "client",
    "modmenu",
    "rei_client",
    "emi",
    "jei_mod_plugin",
    "jade",
    "journeymap",
    "controlify",
}


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


@dataclass
class Classification:
    category: str
    source: str
    reason: str
    evidence_url: str = ""


class ClassifierCore:
    def __init__(self, throttle_ms: int = 80):
        self.throttle_ms = throttle_ms
        self.cache: Dict[str, object] = {}

    def normalize_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = re.sub(r"\[[^\]]+\]", "", text)
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    def clean_filename_token(self, file_name: str) -> str:
        stem = Path(file_name).stem
        stem = re.sub(r"\[[^\]]+\]", " ", stem)
        stem = re.sub(r"\b(mc)?1\.\d+(\.\d+)?\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(fabric|forge|quilt|neoforge)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(v?\d+([._+-]\d+)*([a-z]+\d*)?)\b", " ", stem, flags=re.I)
        stem = re.sub(r"[_\-+.]+", " ", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        return stem

    def read_zip_json(self, jar_path: Path, entry_name: str) -> Optional[dict]:
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                try:
                    with zf.open(entry_name) as fp:
                        return json.loads(fp.read().decode("utf-8"))
                except KeyError:
                    return None
        except Exception:
            return None

    def read_zip_text(self, jar_path: Path, entry_name: str) -> Optional[str]:
        try:
            with zipfile.ZipFile(jar_path, "r") as zf:
                try:
                    with zf.open(entry_name) as fp:
                        return fp.read().decode("utf-8", errors="ignore")
                except KeyError:
                    return None
        except Exception:
            return None

    def build_query_tokens(self, file_name: str, *values: str) -> List[str]:
        query_tokens: List[str] = []
        for value in (*values, self.clean_filename_token(file_name)):
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in query_tokens:
                query_tokens.append(cleaned)
        return query_tokens

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
            loader="quilt",
            metadata_source="quilt.mod.json",
            query_tokens=self.build_query_tokens(file_path.name, mod_id, mod_name),
        )

    def parse_forge_toml_metadata(self, file_path: Path, toml_text: str, source_name: str) -> ModMeta:
        mod_ids = re.findall(r'(?m)^\s*modId\s*=\s*"([^"]+)"', toml_text)
        display_names = re.findall(r'(?m)^\s*displayName\s*=\s*"([^"]+)"', toml_text)
        description_match = re.search(
            r'(?ms)^\s*description\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"]*)")',
            toml_text,
        )

        mod_id = mod_ids[0].strip() if mod_ids else ""
        mod_name = display_names[0].strip() if display_names else (mod_id or file_path.stem)
        description = ""
        if description_match:
            description = next((group.strip() for group in description_match.groups() if group), "")

        path_hint = str(file_path).lower()
        loader = "neoforge" if "neoforge" in source_name.lower() or "neoforge" in path_hint else "forge"
        if loader == "forge" and re.search(r'(?im)^\s*license\s*=\s*".*neoforge', toml_text):
            loader = "neoforge"

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
        )

    def get_jar_metadata(self, file_path: Path) -> ModMeta:
        fabric_json = self.read_zip_json(file_path, "fabric.mod.json")
        if fabric_json:
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
                loader="fabric",
                metadata_source="fabric.mod.json",
                query_tokens=self.build_query_tokens(
                    file_path.name,
                    str(fabric_json.get("id") or "").strip(),
                    str(fabric_json.get("name") or "").strip(),
                ),
            )

        quilt_json = self.read_zip_json(file_path, "quilt.mod.json")
        if quilt_json:
            return self.parse_quilt_metadata(file_path, quilt_json)

        for source_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
            toml_text = self.read_zip_text(file_path, source_name)
            if toml_text:
                return self.parse_forge_toml_metadata(file_path, toml_text, source_name)

        return ModMeta(
            file_name=file_path.name,
            file_path=str(file_path),
            mod_id="",
            mod_name=file_path.stem,
            description="",
            environment="",
            entrypoints=[],
            depends=[],
            loader="unknown",
            metadata_source="filename-only",
            query_tokens=self.build_query_tokens(file_path.name, file_path.stem),
        )

    def local_classification(self, meta: ModMeta) -> Classification:
        if meta.loader in {"forge", "neoforge", "unknown"}:
            return Classification("unknown", "local", f"{meta.loader} 本地元数据不提供可靠环境信息")

        entrypoints = set(meta.entrypoints)
        has_main = "main" in entrypoints
        has_server = "server" in entrypoints
        non_client_only = [item for item in entrypoints if item not in CLIENT_ENTRYPOINTS]

        if meta.environment == "client":
            return Classification("client-only", "local", "fabric.mod.json environment=client")

        if meta.environment == "server":
            return Classification("server-keep", "local", "fabric.mod.json environment=server")

        # 仅有 client/modmenu 之类入口时，不能直接认定“服务端无效”。
        # 像 YACL 这类库模组常见 client 入口，但仍可能是服务端可选。
        if entrypoints and not has_main and not has_server and not non_client_only:
            return Classification("unknown", "local", "仅声明客户端入口点，继续联网核对")

        if has_main or has_server:
            return Classification("needs-remote-check", "local", "含 main/server 入口，继续联网核对")

        return Classification("unknown", "local", "本地元数据不足")

    def http_get_json(self, url: str) -> Optional[dict]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

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
        if not meta.mod_id:
            return None

        slug = meta.mod_id.strip()
        cache_key = f"modrinth-project::{slug}"
        if cache_key not in self.cache:
            url = f"https://api.modrinth.com/v2/project/{urllib.parse.quote(slug)}"
            time.sleep(self.throttle_ms / 1000)
            try:
                self.cache[cache_key] = self.http_get_json(url)
            except Exception:
                self.cache[cache_key] = None

        payload = self.cache.get(cache_key)
        if not payload:
            return None

        payload_slug = self.normalize_text(str(payload.get("slug", "")))
        payload_title = self.normalize_text(str(payload.get("title", "")))
        mod_id = self.normalize_text(meta.mod_id)
        mod_name = self.normalize_text(meta.mod_name)

        if payload_slug != mod_id and payload_title not in {mod_id, mod_name}:
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
            return resp.read().decode("utf-8", errors="ignore")

    def modrinth_search(self, meta: ModMeta) -> Optional[Classification]:
        direct = self.modrinth_direct_lookup(meta)
        if direct and direct.category != "unknown":
            return direct

        candidates: List[Tuple[int, dict, str]] = []
        seen_queries = set()
        for token in meta.query_tokens[:3]:
            query = token.strip()
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)

            cache_key = f"modrinth::{query}"
            if cache_key not in self.cache:
                url = (
                    "https://api.modrinth.com/v2/search?"
                    f"query={urllib.parse.quote(query)}&limit=8&facets=%5B%5B%22project_type%3Amod%22%5D%5D"
                )
                time.sleep(self.throttle_ms / 1000)
                try:
                    self.cache[cache_key] = self.http_get_json(url)
                except Exception:
                    self.cache[cache_key] = None

            response = self.cache.get(cache_key) or {}
            local_candidates: List[Tuple[int, dict]] = []
            for hit in response.get("hits", []):
                score = 0
                norm_id = self.normalize_text(meta.mod_id)
                norm_name = self.normalize_text(meta.mod_name)
                norm_slug = self.normalize_text(str(hit.get("slug", "")))
                norm_title = self.normalize_text(str(hit.get("title", "")))
                norm_query = self.normalize_text(query)

                if norm_id and norm_slug == norm_id:
                    score += 120
                if norm_id and norm_title == norm_id:
                    score += 110
                if norm_name and norm_title == norm_name:
                    score += 100
                if norm_name and norm_slug == norm_name:
                    score += 95
                if norm_query and norm_slug == norm_query:
                    score += 90
                if norm_query and norm_title == norm_query:
                    score += 85
                if norm_query and norm_query in norm_title:
                    score += 70
                if norm_query and norm_query in norm_slug:
                    score += 65
                if norm_id and norm_id in norm_slug:
                    score += 35
                if norm_name and norm_name in norm_title:
                    score += 75
                if norm_name and norm_name in norm_slug:
                    score += 60
                if hit.get("slug"):
                    score += 5

                local_candidates.append((score, hit))
                candidates.append((score, hit, query))

            local_candidates.sort(key=lambda item: item[0], reverse=True)
            if local_candidates:
                best_score, best_hit = local_candidates[0]
                classification = self.classification_from_modrinth_payload(
                    best_hit,
                    "Modrinth",
                    f"https://modrinth.com/mod/{best_hit.get('slug', '')}",
                )
                if best_score >= 95 and classification.category != "unknown":
                    return classification

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, hit, _query = candidates[0]
        if score < 60:
            return None

        return self.classification_from_modrinth_payload(
            hit,
            "Modrinth",
            f"https://modrinth.com/mod/{hit.get('slug', '')}",
        )

    def mcmod_search(self, meta: ModMeta) -> Optional[Classification]:
        seen_queries = set()
        for token in meta.query_tokens[:3]:
            query = token.strip()
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)

            search_key = f"mcmod-search::{query}"
            if search_key not in self.cache:
                url = f"https://search.mcmod.cn/s?key={urllib.parse.quote(query)}"
                time.sleep(self.throttle_ms / 1000)
                try:
                    self.cache[search_key] = self.http_get_text(url)
                except Exception:
                    self.cache[search_key] = None

            html = self.cache.get(search_key) or ""
            links = list(dict.fromkeys(re.findall(r"https://www\.mcmod\.cn/class/\d+\.html", html)))
            for link in links[:3]:
                page_key = f"mcmod-page::{link}"
                if page_key not in self.cache:
                    time.sleep(self.throttle_ms / 1000)
                    try:
                        self.cache[page_key] = self.http_get_text(link)
                    except Exception:
                        self.cache[page_key] = None

                page_html = self.cache.get(page_key) or ""
                match = re.search(r"运行环境:\s*([^<]{1,80})", page_html)
                if not match:
                    continue

                env_text = match.group(1).strip()
                if "服务端无效" in env_text:
                    return Classification("client-only", "mcmod", f"MC百科: {env_text}", link)
                if "服务端需装" in env_text or "服务端可选" in env_text:
                    return Classification("server-keep", "mcmod", f"MC百科: {env_text}", link)

        return None

    def resolve_classification(self, meta: ModMeta, use_mcmod: bool = True) -> Classification:
        local = self.local_classification(meta)
        if local.category in {"client-only", "server-keep"} and "environment=" in local.reason:
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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("900x680")
        self.root.minsize(820, 600)

        self.path_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.use_mcmod_var = tk.BooleanVar(value=True)
        self.output_var = tk.StringVar(value="尚未运行")
        self.status_var = tk.StringVar(value="请选择 mods 文件夹。")
        self.progress_var = tk.DoubleVar(value=0)

        self.result_dir: Optional[Path] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()

        self.build_ui()
        self.root.after(150, self.poll_queue)

    def build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill="both", expand=True)

        top = ttk.LabelFrame(main, text="选择目录", padding=12)
        top.pack(fill="x")

        entry = ttk.Entry(top, textvariable=self.path_var)
        entry.pack(side="left", fill="x", expand=True)

        ttk.Button(top, text="浏览…", command=self.choose_folder).pack(side="left", padx=(10, 0))

        options = ttk.Frame(main, padding=(0, 12, 0, 0))
        options.pack(fill="x")
        ttk.Checkbutton(options, text="仅试运行，不移动文件", variable=self.dry_run_var).pack(side="left")
        ttk.Checkbutton(options, text="启用 MC百科 兜底查询", variable=self.use_mcmod_var).pack(side="left", padx=(18, 0))
        ttk.Button(options, text="开始分类", command=self.start).pack(side="right")

        middle = ttk.LabelFrame(main, text="进度", padding=12)
        middle.pack(fill="x", pady=(12, 0))
        ttk.Label(middle, textvariable=self.status_var).pack(anchor="w")
        ttk.Progressbar(middle, variable=self.progress_var, maximum=100).pack(fill="x", pady=(8, 0))
        ttk.Label(middle, textvariable=self.output_var, foreground="#555").pack(anchor="w", pady=(8, 0))

        log_box = ttk.LabelFrame(main, text="日志", padding=12)
        log_box.pack(fill="both", expand=True, pady=(12, 0))
        self.log = ScrolledText(log_box, wrap="word", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)

        bottom = ttk.Frame(main, padding=(0, 12, 0, 0))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="打开结果目录", command=self.open_result_dir).pack(side="left")
        ttk.Button(bottom, text="退出", command=self.root.destroy).pack(side="right")

    def append_log(self, message: str) -> None:
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="选择 mods 文件夹")
        if selected:
            self.path_var.set(selected)

    def start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "任务正在运行，请先等待当前任务结束。")
            return

        mods_path = self.path_var.get().strip()
        if not mods_path:
            self.choose_folder()
            mods_path = self.path_var.get().strip()
            if not mods_path:
                return

        path = Path(mods_path)
        if not path.exists() or not path.is_dir():
            messagebox.showerror(APP_TITLE, "选择的目录不存在。")
            return

        self.log.delete("1.0", "end")
        self.result_dir = None
        self.progress_var.set(0)
        self.status_var.set("准备开始…")
        self.output_var.set("运行中")

        self.worker_thread = threading.Thread(
            target=self.run_task,
            args=(path, self.dry_run_var.get(), self.use_mcmod_var.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def poll_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self.append_log(payload)
            elif kind == "status":
                self.status_var.set(payload)
            elif kind == "progress":
                self.progress_var.set(payload)
            elif kind == "output":
                self.output_var.set(payload)
            elif kind == "done":
                self.result_dir = Path(payload["result_dir"])
                self.progress_var.set(100)
                self.status_var.set(payload["status"])
                self.output_var.set(payload["output"])
                self.append_log("")
                self.append_log(payload["summary"])
                messagebox.showinfo(APP_TITLE, payload["status"])
            elif kind == "error":
                self.status_var.set("运行失败")
                self.output_var.set("失败")
                self.append_log(payload)
                messagebox.showerror(APP_TITLE, payload)

        self.root.after(150, self.poll_queue)

    def run_task(self, mods_path: Path, dry_run: bool, use_mcmod: bool) -> None:
        try:
            core = ClassifierCore()
            jar_files = sorted(mods_path.glob("*.jar"), key=lambda item: item.name.lower())
            if not jar_files:
                raise RuntimeError("所选目录中没有找到 jar 模组。")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            result_root = mods_path / f"_分类结果_{timestamp}"
            client_dir = result_root / "纯客户端_已移出"
            unknown_dir = result_root / "无法分类_待人工确认"
            client_dir.mkdir(parents=True, exist_ok=True)
            unknown_dir.mkdir(parents=True, exist_ok=True)

            self.ui_queue.put(("log", f"开始扫描目录：{mods_path}"))
            self.ui_queue.put(("log", f"共发现 {len(jar_files)} 个 jar 模组"))

            results = []
            for index, jar in enumerate(jar_files, start=1):
                percent = index * 100 / max(len(jar_files), 1)
                self.ui_queue.put(("progress", percent))
                self.ui_queue.put(("status", f"正在分析：{jar.name}"))

                try:
                    meta = core.get_jar_metadata(jar)
                    classification = core.resolve_classification(meta, use_mcmod=use_mcmod)

                    final_path = str(jar)
                    if classification.category == "client-only":
                        target = client_dir / jar.name
                        final_path = str(target)
                        if not dry_run:
                            shutil.move(str(jar), str(target))
                    elif classification.category == "unknown":
                        target = unknown_dir / jar.name
                        final_path = str(target)
                        if not dry_run:
                            shutil.move(str(jar), str(target))

                    results.append(
                        {
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
                            "FinalPath": final_path,
                        }
                    )
                    self.ui_queue.put(
                        (
                            "log",
                            f"[{index}/{len(jar_files)}] {jar.name} -> {classification.category} | {classification.reason}",
                        )
                    )
                except Exception as exc:
                    target = unknown_dir / jar.name
                    final_path = str(target)
                    if not dry_run and jar.exists():
                        shutil.move(str(jar), str(target))

                    results.append(
                        {
                            "FileName": jar.name,
                            "Loader": "unknown",
                            "MetadataSource": "error",
                            "ModId": "",
                            "ModName": jar.stem,
                            "Environment": "",
                            "Entrypoints": "",
                            "Category": "unknown",
                            "DecisionSource": "error",
                            "Reason": str(exc),
                            "EvidenceUrl": "",
                            "FinalPath": final_path,
                        }
                    )
                    self.ui_queue.put(("log", f"[错误] {jar.name}: {exc}"))

            json_path = result_root / "分类报告.json"
            csv_path = result_root / "分类报告.csv"
            txt_path = result_root / "分类摘要.txt"

            json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=list(results[0].keys()))
                writer.writeheader()
                writer.writerows(results)

            server_keep = sum(1 for item in results if item["Category"] == "server-keep")
            client_only = sum(1 for item in results if item["Category"] == "client-only")
            unknown = sum(1 for item in results if item["Category"] == "unknown")

            summary_lines = [
                f"扫描目录: {mods_path}",
                f"执行模式: {'DryRun(不移动文件)' if dry_run else '实际移动文件'}",
                f"服务端保留: {server_keep}",
                f"纯客户端移出: {client_only}",
                f"无法分类: {unknown}",
                f"结果目录: {result_root}",
                f"JSON 报告: {json_path}",
                f"CSV 报告: {csv_path}",
            ]
            txt_path.write_text("\n".join(summary_lines), encoding="utf-8")

            status = f"分类完成：保留 {server_keep}，移出 {client_only}，待确认 {unknown}"
            self.ui_queue.put(
                (
                    "done",
                    {
                        "result_dir": str(result_root),
                        "status": status,
                        "output": str(result_root),
                        "summary": "\n".join(summary_lines),
                    },
                )
            )
        except Exception:
            self.ui_queue.put(("error", traceback.format_exc()))

    def open_result_dir(self) -> None:
        if not self.result_dir or not self.result_dir.exists():
            messagebox.showinfo(APP_TITLE, "当前还没有可打开的结果目录。")
            return
        try:
            import os

            os.startfile(str(self.result_dir))
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
