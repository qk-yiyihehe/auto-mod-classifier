from .common import *
from ..tasks import run_mod_task, run_server_task
from .dialogs import ChecklistDialog, VersionSelectionDialog


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        # 适配屏幕：取屏幕高度的 85% 或默认尺寸中较小的
        screen_h = root.winfo_screenheight()
        target_h = min(int(screen_h * 0.85), 860)
        self.root.geometry(f"980x{max(target_h, 600)}")
        self.root.minsize(900, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.mod_path_var = tk.StringVar()
        self.mod_dry_run_var = tk.BooleanVar(value=False)
        self.mod_use_mcmod_var = tk.BooleanVar(value=True)
        self.mod_use_cf_var = tk.BooleanVar(value=False)
        self.mod_second_pass_var = tk.BooleanVar(value=False)

        self.server_client_path_var = tk.StringVar()
        self.server_output_path_var = tk.StringVar()
        self.server_use_mcmod_var = tk.BooleanVar(value=True)
        self.server_use_cf_var = tk.BooleanVar(value=False)
        self.server_second_pass_var = tk.BooleanVar(value=False)

        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[dict]" = queue.Queue()
        self._runtime_ref: Any = None

        self.mod_panel: Optional[PanelState] = None
        self.server_panel: Optional[PanelState] = None

        self.build_ui()
        self.root.after(150, self.poll_queue)

    def _on_close(self) -> None:
        """退出时清理当前任务持有的运行时对象。"""
        self._close_runtime()
        try:
            browser_dir = Path(tempfile.gettempdir()) / "_mcmod_browser_data"
            if browser_dir.exists():
                shutil.rmtree(browser_dir, ignore_errors=True)
        except Exception:
            pass
        self.root.destroy()

    def _close_runtime(self) -> None:
        runtime = self._runtime_ref
        self._runtime_ref = None
        if runtime is None:
            return
        try:
            close_browser = getattr(runtime, "close_browser", None)
            if callable(close_browser):
                close_browser()
                return
            close = getattr(runtime, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def set_runtime_ref(self, runtime: Any) -> None:
        self._runtime_ref = runtime

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
        ttk.Checkbutton(options, text="仅试运行", variable=self.mod_dry_run_var).pack(side="left")
        ttk.Checkbutton(options, text="MC百科(需手动填验证码)", variable=self.mod_use_mcmod_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="CurseForge(较慢/需梯子/测试版)", variable=self.mod_use_cf_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="2次筛选", variable=self.mod_second_pass_var).pack(side="left", padx=(18, 0))
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
        bottom.pack(fill="x", side="bottom")
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
        ttk.Checkbutton(options, text="MC百科(需手动填验证码)", variable=self.server_use_mcmod_var).pack(side="left")
        ttk.Checkbutton(options, text="CurseForge(较慢/需梯子/测试版)", variable=self.server_use_cf_var).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(options, text="2次筛选", variable=self.server_second_pass_var).pack(side="left", padx=(18, 0))
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
        bottom.pack(fill="x", side="bottom")
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
        options = ModTaskOptions(
            mods_path=path,
            dry_run=self.mod_dry_run_var.get(),
            use_mcmod=self.mod_use_mcmod_var.get(),
            use_curseforge=self.mod_use_cf_var.get(),
            enable_second_pass=self.mod_second_pass_var.get(),
        )
        self.worker_thread = threading.Thread(
            target=run_mod_task,
            args=(
                options,
                lambda kind, payload: self.emit("mod", kind, payload),
                self.set_runtime_ref,
            ),
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
        options = ServerTaskOptions(
            client_dir=Path(client_dir),
            output_dir=Path(output_dir),
            use_mcmod=self.server_use_mcmod_var.get(),
            use_curseforge=self.server_use_cf_var.get(),
            enable_second_pass=self.server_second_pass_var.get(),
        )
        self.worker_thread = threading.Thread(
            target=run_server_task,
            args=(
                options,
                lambda kind, payload: self.emit("server", kind, payload),
                self.set_runtime_ref,
                self.request_version_choice,
                self.request_checklist,
            ),
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

            if kind == "warning":
                messagebox.showwarning(APP_TITLE, payload)
                continue

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
