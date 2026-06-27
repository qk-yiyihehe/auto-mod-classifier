from .common import *


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
