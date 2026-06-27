from dataclasses import dataclass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..shared import *


@dataclass
class PanelState:
    """Tk 面板状态，仅供桌面前端使用。"""

    status_var: tk.StringVar
    progress_var: tk.DoubleVar
    output_var: tk.StringVar
    log_widget: ScrolledText
    result_dir: Optional[Path] = None
    extra_dir: Optional[Path] = None
