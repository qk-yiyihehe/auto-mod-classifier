from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Tuple

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget
from qfluentwidgets import BodyLabel, PlainTextEdit, StrongBodyLabel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ICON_PATH = PROJECT_ROOT / "自动筛选模组分类器.ico"

# ═══════════════════════════════════════════
# 调色板：背景 / 卡片 / 边框 / 文字随主题变
# ═══════════════════════════════════════════

# 这些名字会在 __getattr__ 拦截下按当前主题返回。
# 调用方必须用 `qt_theme.BG_CONTENT` 而不是 `from qt_theme import BG_CONTENT`，
# 这样每次访问才会拿到当前主题值。
_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BG_DEEP": "#090C12",
        "BG_SIDEBAR": "#0B0F16",
        "BG_CONTENT": "#0D1119",
        "SURFACE_CARD": "#131926",
        "SURFACE_ELEVATED": "#181F30",
        "SURFACE_INPUT": "#1B2338",
        "SURFACE_DIMMED": "#101520",
        "BORDER_SUBTLE": "rgba(255, 255, 255, 0.04)",
        "BORDER_DEFAULT": "rgba(255, 255, 255, 0.06)",
        "BORDER_STRONG": "rgba(255, 255, 255, 0.09)",
        "BORDER_FOCUS": "rgba(61, 214, 140, 0.35)",
        "BORDER_COLOR": "#2A3350",
        "WEAK_BORDER_COLOR": "rgba(255, 255, 255, 0.05)",
        "TEXT_PRIMARY": "#E3E6ED",
        "TEXT_SECONDARY": "#99A1B3",
        "TEXT_MUTED": "#656E80",
        "TEXT_DISABLED": "#3E4555",
        # 旧名兼容
        "BG_COLOR": "#0D1119",
        "SURFACE_COLOR": "#131926",
        "SURFACE_ALT_COLOR": "#1B2338",
        "SURFACE_HERO_COLOR": "#181F30",
        "TEXT_COLOR": "#E3E6ED",
        "SECONDARY_TEXT_COLOR": "#99A1B3",
        "MUTED_TEXT_COLOR": "#656E80",
    },
    "light": {
        "BG_DEEP": "#E6EAF0",
        "BG_SIDEBAR": "#FFFFFF",
        "BG_CONTENT": "#F4F6FA",
        "SURFACE_CARD": "#FFFFFF",
        "SURFACE_ELEVATED": "#FFFFFF",
        "SURFACE_INPUT": "#FFFFFF",
        "SURFACE_DIMMED": "#EDF0F5",
        "BORDER_SUBTLE": "rgba(0, 0, 0, 0.05)",
        "BORDER_DEFAULT": "rgba(0, 0, 0, 0.09)",
        "BORDER_STRONG": "rgba(0, 0, 0, 0.14)",
        "BORDER_FOCUS": "rgba(38, 175, 110, 0.45)",
        "BORDER_COLOR": "#D5DAE3",
        "WEAK_BORDER_COLOR": "rgba(0, 0, 0, 0.05)",
        "TEXT_PRIMARY": "#1A1F2C",
        "TEXT_SECONDARY": "#4C5466",
        "TEXT_MUTED": "#7A8194",
        "TEXT_DISABLED": "#A8AEBB",
        "BG_COLOR": "#F4F6FA",
        "SURFACE_COLOR": "#FFFFFF",
        "SURFACE_ALT_COLOR": "#FFFFFF",
        "SURFACE_HERO_COLOR": "#FFFFFF",
        "TEXT_COLOR": "#1A1F2C",
        "SECONDARY_TEXT_COLOR": "#4C5466",
        "MUTED_TEXT_COLOR": "#7A8194",
    },
}

# 主题切换辅助色：浅色主题下按钮/输入框的 hover/pressed 背景需要更浅的灰阶
# dark 主题用了 hard-coded `#232D42` / `#1B2335` / `#1E283A` 等。这里也按主题区分。
_PALETTES["dark"]["BUTTON_HOVER_BG"] = "#232D42"
_PALETTES["dark"]["BUTTON_PRESSED_BG"] = "#1B2335"
_PALETTES["dark"]["INPUT_FOCUS_BG"] = "#1E283A"
_PALETTES["dark"]["BUTTON_DISABLED_BG"] = "rgba(27, 35, 56, 0.5)"
_PALETTES["dark"]["EDITOR_BG"] = "#0C131F"
_PALETTES["dark"]["TABLE_HEADER_BG"] = "#151D2C"
_PALETTES["dark"]["TABLE_ROW_BG"] = "#101826"
_PALETTES["dark"]["SCROLL_HANDLE_BG"] = "rgba(153, 161, 179, 0.15)"
_PALETTES["dark"]["SCROLL_HANDLE_HOVER_BG"] = "rgba(153, 161, 179, 0.28)"
_PALETTES["dark"]["MENU_BG"] = "#161E2E"
_PALETTES["dark"]["COMBO_POPUP_BG"] = "#161E2E"
_PALETTES["dark"]["HOVER_BORDER"] = "rgba(255, 255, 255, 0.10)"
_PALETTES["dark"]["WARNING_BORDER"] = "rgba(245, 166, 35, 0.15)"
_PALETTES["dark"]["WARNING_BG_HOVER"] = "rgba(245, 166, 35, 0.14)"
_PALETTES["dark"]["WARNING_BORDER_HOVER"] = "rgba(245, 166, 35, 0.25)"
_PALETTES["dark"]["PRIMARY_TEXT"] = "#0A1F16"
_PALETTES["dark"]["PRIMARY_TEXT_DISABLED"] = "rgba(10, 31, 22, 0.35)"
_PALETTES["dark"]["ACCENT_BORDER"] = "rgba(61, 214, 140, 0.12)"
_PALETTES["dark"]["ACCENT_BORDER_HOVER"] = "rgba(61, 214, 140, 0.22)"
_PALETTES["dark"]["SOFT_BORDER"] = "rgba(61, 214, 140, 0.10)"

_PALETTES["light"]["BUTTON_HOVER_BG"] = "#E9EDF2"
_PALETTES["light"]["BUTTON_PRESSED_BG"] = "#DDE2EA"
_PALETTES["light"]["INPUT_FOCUS_BG"] = "#FFFFFF"
_PALETTES["light"]["BUTTON_DISABLED_BG"] = "rgba(0, 0, 0, 0.04)"
_PALETTES["light"]["EDITOR_BG"] = "#FFFFFF"
_PALETTES["light"]["TABLE_HEADER_BG"] = "#EEF1F6"
_PALETTES["light"]["TABLE_ROW_BG"] = "#F8FAFC"
_PALETTES["light"]["SCROLL_HANDLE_BG"] = "rgba(76, 84, 102, 0.18)"
_PALETTES["light"]["SCROLL_HANDLE_HOVER_BG"] = "rgba(76, 84, 102, 0.32)"
_PALETTES["light"]["MENU_BG"] = "#FFFFFF"
_PALETTES["light"]["COMBO_POPUP_BG"] = "#FFFFFF"
_PALETTES["light"]["HOVER_BORDER"] = "rgba(0, 0, 0, 0.20)"
_PALETTES["light"]["WARNING_BORDER"] = "rgba(245, 166, 35, 0.35)"
_PALETTES["light"]["WARNING_BG_HOVER"] = "rgba(245, 166, 35, 0.18)"
_PALETTES["light"]["WARNING_BORDER_HOVER"] = "rgba(245, 166, 35, 0.45)"
_PALETTES["light"]["PRIMARY_TEXT"] = "#FFFFFF"
_PALETTES["light"]["PRIMARY_TEXT_DISABLED"] = "rgba(255, 255, 255, 0.55)"
_PALETTES["light"]["ACCENT_BORDER"] = "rgba(38, 175, 110, 0.30)"
_PALETTES["light"]["ACCENT_BORDER_HOVER"] = "rgba(38, 175, 110, 0.45)"
_PALETTES["light"]["SOFT_BORDER"] = "rgba(38, 175, 110, 0.20)"

_current_palette = "dark"


def current_palette_name() -> str:
    return _current_palette


def set_palette(name: str) -> None:
    """切换当前调色板。"""
    global _current_palette
    if name not in _PALETTES:
        raise ValueError(f"unknown palette: {name}")
    _current_palette = name


def __getattr__(name: str) -> str:
    """模块级属性动态查找：根据当前主题返回色值。"""
    if name in _PALETTES["dark"]:
        return _PALETTES[_current_palette][name]
    raise AttributeError(f"module 'qt_theme' has no attribute {name!r}")


# ═══════════════════════════════════════════
# 不随主题变的常量：主色调 / 语义色 / 字体 / 半径 / 间距
# ═══════════════════════════════════════════

# 主色调 —— 柔和翠绿，无荧光感
ACCENT_NORMAL = "#3DD68C"
ACCENT_HOVER = "#4FE09C"
ACCENT_PRESSED = "#30C87C"
ACCENT_DISABLED = "rgba(61, 214, 140, 0.25)"
ACCENT_BG_SOFT = "rgba(61, 214, 140, 0.08)"
ACCENT_BG_MEDIUM = "rgba(61, 214, 140, 0.14)"

ACCENT_COLOR = ACCENT_NORMAL
ACCENT_HOVER_COLOR = ACCENT_HOVER
ACCENT_PRESSED_COLOR = ACCENT_PRESSED
ACCENT_DISABLED_COLOR = ACCENT_DISABLED
ACCENT_SOFT_COLOR = ACCENT_BG_SOFT

# 语义色 —— 状态色，与主题无关
IDLE_COLOR = "#5C6578"
RUNNING_COLOR = ACCENT_NORMAL
SUCCESS_COLOR = "#3DD68C"
WARNING_COLOR = "#F5A623"
ERROR_COLOR = "#F04770"
INFO_COLOR = "#5BA4FC"
WARNING_SOFT_COLOR = "rgba(245, 166, 35, 0.08)"
ERROR_SOFT_COLOR = "rgba(240, 71, 112, 0.08)"
INFO_SOFT_COLOR = "rgba(91, 164, 252, 0.08)"

# 字体
FONT_SYSTEM = '"Segoe UI Variable Text", "Microsoft YaHei UI"'
FONT_CODE = 'Cascadia Code, Consolas, "Microsoft YaHei UI"'
FONT_SIZE_XS = 11
FONT_SIZE_SM = 12
FONT_SIZE_BASE = 13
FONT_SIZE_MD = 14
FONT_SIZE_LG = 16
FONT_SIZE_XL = 20
FONT_SIZE_XXL = 28

# 圆角 / 间距
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 10
RADIUS_XL = 12
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24

# ═══════════════════════════════════════════
# 主题感知 setStyleSheet：记录 widget，主题切换时自动重设
# ═══════════════════════════════════════════

_themed_widgets: List[Tuple[QWidget, Callable[[], str]]] = []


def apply_themed_style(widget: QWidget, css_fn: Callable[[], str]) -> None:
    """主题感知的 setStyleSheet。css_fn 是无参函数，每次调用返回新主题下的 QSS。"""
    widget.setStyleSheet(css_fn())
    _themed_widgets.append((widget, css_fn))


def refresh_themed_styles() -> None:
    """切换调色板后调用，重设所有已注册 widget 的 QSS。"""
    stale: List[Tuple[QWidget, Callable[[], str]]] = []
    for widget, css_fn in _themed_widgets:
        try:
            if not widget.isVisible() and not widget.isWindow():
                # widget 已被销毁或不可见，跳过
                pass
            widget.setStyleSheet(css_fn())
        except RuntimeError:
            stale.append((widget, css_fn))
    for entry in stale:
        _themed_widgets.remove(entry)


def build_window_stylesheet() -> str:
    """主窗口全局 QSS —— 克制、精致，所有控件统一 Fluent 质感。

    颜色通过 __getattr__ 按当前调色板动态返回，
    set_palette() 切换后下次调用会拿到新主题色。

    注意点：模块内 `BG_CONTENT` 这种名字引用不会触发 __getattr__，
    所以这里全部走 self.BG_CONTENT（属性访问）显式触发。
    """
    # 通过 self.__getattr__ / self.X 显式触发模块级 __getattr__
    self_obj = sys.modules[__name__]
    g = lambda key: getattr(self_obj, key)

    bg_content = g("BG_CONTENT")
    surface_card = g("SURFACE_CARD")
    surface_input = g("SURFACE_INPUT")
    surface_elevated = g("SURFACE_ELEVATED")
    border_subtle = g("BORDER_SUBTLE")
    border_default = g("BORDER_DEFAULT")
    border_strong = g("BORDER_STRONG")
    border_focus = g("BORDER_FOCUS")
    text_primary = g("TEXT_PRIMARY")
    text_secondary = g("TEXT_SECONDARY")
    text_muted = g("TEXT_MUTED")
    text_disabled = g("TEXT_DISABLED")
    button_hover_bg = g("BUTTON_HOVER_BG")
    button_pressed_bg = g("BUTTON_PRESSED_BG")
    input_focus_bg = g("INPUT_FOCUS_BG")
    button_disabled_bg = g("BUTTON_DISABLED_BG")
    editor_bg = g("EDITOR_BG")
    table_header_bg = g("TABLE_HEADER_BG")
    scroll_handle_bg = g("SCROLL_HANDLE_BG")
    scroll_handle_hover_bg = g("SCROLL_HANDLE_HOVER_BG")
    menu_bg = g("MENU_BG")
    combo_popup_bg = g("COMBO_POPUP_BG")
    hover_border = g("HOVER_BORDER")
    warning_border = g("WARNING_BORDER")
    warning_bg_hover = g("WARNING_BG_HOVER")
    warning_border_hover = g("WARNING_BORDER_HOVER")
    primary_text = g("PRIMARY_TEXT")
    primary_text_disabled = g("PRIMARY_TEXT_DISABLED")
    accent_border = g("ACCENT_BORDER")
    accent_border_hover = g("ACCENT_BORDER_HOVER")
    soft_border = g("SOFT_BORDER")

    return f"""
    * {{
        font-family: {FONT_SYSTEM};
        font-size: {FONT_SIZE_BASE}px;
        color: {text_secondary};
    }}

    QMenu {{
        background-color: {menu_bg};
        color: {text_secondary};
        border: 1px solid {border_strong};
        border-radius: {RADIUS_MD}px;
        padding: 4px;
    }}
    QMenu::item {{
        min-height: 28px;
        padding: 5px 24px 5px 12px;
    }}
    QMenu::item:selected {{
        background-color: {ACCENT_BG_MEDIUM};
        color: {text_primary};
    }}

    QLabel {{
        color: {text_secondary};
        background: transparent;
    }}

    BodyLabel, StrongBodyLabel, TitleLabel, SubtitleLabel {{
        background: transparent;
    }}
    BodyLabel {{
        color: {text_secondary};
        font-size: {FONT_SIZE_BASE}px;
        font-weight: 400;
    }}
    StrongBodyLabel {{
        color: {text_primary};
        font-size: {FONT_SIZE_MD}px;
        font-weight: 600;
    }}
    TitleLabel {{
        color: {text_primary};
        font-size: {FONT_SIZE_XL}px;
        font-weight: 600;
    }}
    SubtitleLabel {{
        color: {text_secondary};
        font-size: {FONT_SIZE_MD}px;
        font-weight: 400;
    }}

    /* ── 按钮基类 ── */
    QPushButton {{
        min-height: 34px;
        max-height: 36px;
        padding: 0 18px;
        border-radius: {RADIUS_MD}px;
        border: 1px solid {border_default};
        background-color: {surface_input};
        color: {text_secondary};
        font-size: {FONT_SIZE_SM}px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {button_hover_bg};
        border-color: {border_strong};
        color: {text_primary};
    }}
    QPushButton:pressed {{
        background-color: {button_pressed_bg};
        border-color: {border_strong};
    }}
    QPushButton:disabled {{
        color: {text_disabled};
        background-color: {button_disabled_bg};
        border-color: {border_subtle};
    }}
    QPushButton#smallButton {{
        min-height: 28px;
        max-height: 30px;
        padding: 0 14px;
        font-size: {FONT_SIZE_XS}px;
        border-radius: {RADIUS_SM}px;
    }}
    QPushButton#warningButton {{
        background-color: {WARNING_SOFT_COLOR};
        color: #F5C04A;
        border: 1px solid {warning_border};
    }}
    QPushButton#warningButton:hover {{
        background-color: {warning_bg_hover};
        border-color: {warning_border_hover};
        color: #F5C04A;
    }}
    QPushButton#accentButton {{
        background-color: {ACCENT_BG_SOFT};
        color: {ACCENT_NORMAL};
        border: 1px solid {accent_border};
        font-weight: 500;
    }}
    QPushButton#accentButton:hover {{
        background-color: {ACCENT_BG_MEDIUM};
        border-color: {accent_border_hover};
    }}

    /* ── 主按钮 ── */
    PrimaryPushButton {{
        background-color: {ACCENT_NORMAL};
        color: {primary_text};
        border: 1px solid {ACCENT_NORMAL};
        border-radius: {RADIUS_MD}px;
        padding: 0 22px;
        font-weight: 600;
        font-size: {FONT_SIZE_SM}px;
    }}
    PrimaryPushButton:hover {{
        background-color: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER};
    }}
    PrimaryPushButton:pressed {{
        background-color: {ACCENT_PRESSED};
        border-color: {ACCENT_PRESSED};
    }}
    PrimaryPushButton:disabled {{
        background-color: {ACCENT_DISABLED};
        border-color: transparent;
        color: {primary_text_disabled};
    }}

    /* ── 输入框 ── */
    LineEdit, ComboBox, QLineEdit, QComboBox {{
        min-height: 34px;
        max-height: 34px;
        border-radius: {RADIUS_MD}px;
        border: 1px solid {border_default};
        background-color: {surface_input};
        color: {text_primary};
        padding: 0 12px;
        font-size: {FONT_SIZE_SM}px;
        selection-background-color: {ACCENT_BG_MEDIUM};
    }}
    LineEdit:focus, ComboBox:focus, QLineEdit:focus, QComboBox:focus {{
        border-color: {border_focus};
        background-color: {input_focus_bg};
    }}
    ComboBox::drop-down {{
        width: 24px;
        border: 0;
        background: transparent;
    }}
    ComboBox QAbstractItemView {{
        background-color: {combo_popup_bg};
        border: 1px solid {border_strong};
        border-radius: {RADIUS_MD}px;
        selection-background-color: {ACCENT_BG_MEDIUM};
        selection-color: {text_primary};
        padding: 4px;
        outline: none;
    }}
    ComboBox QAbstractItemView::item {{
        min-height: 30px;
        padding: 4px 8px;
        color: {text_secondary};
    }}
    ComboBox QAbstractItemView::item:selected {{
        color: {text_primary};
    }}

    /* ── 复选框 ── */
    QCheckBox {{
        color: {text_secondary};
        background: transparent;
        spacing: 8px;
        font-size: {FONT_SIZE_SM}px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: {RADIUS_SM}px;
        border: 1px solid {border_strong};
        background-color: {surface_input};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ACCENT_NORMAL};
        border-color: {ACCENT_NORMAL};
    }}

    /* ── 表格 ── */
    QHeaderView::section {{
        background-color: {table_header_bg};
        color: {text_muted};
        border: 0;
        border-bottom: 1px solid {border_default};
        padding: 7px 10px;
        font-size: {FONT_SIZE_XS}px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background-color: {table_header_bg};
        border: 0;
        border-right: 1px solid {border_default};
        border-bottom: 1px solid {border_default};
    }}

    /* ── 进度条 ── */
    QProgressBar {{
        min-height: 6px;
        max-height: 6px;
        background-color: {table_header_bg};
        color: transparent;
        border: 0;
        border-radius: 3px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {ACCENT_NORMAL};
        border-radius: 3px;
    }}

    /* ── 文本编辑 ── */
    QPlainTextEdit, QTextEdit {{
        background-color: {editor_bg};
        color: {text_secondary};
        border: 1px solid {border_default};
        border-radius: {RADIUS_MD}px;
        font-size: {FONT_SIZE_SM}px;
        selection-background-color: {ACCENT_BG_MEDIUM};
        padding: 8px;
    }}

    /* ── 滚动条 ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {scroll_handle_bg};
        border-radius: 3px;
        min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {scroll_handle_hover_bg};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        border: 0;
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 6px;
        margin: 0 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {scroll_handle_bg};
        border-radius: 3px;
        min-width: 40px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {scroll_handle_hover_bg};
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {{
        background: transparent;
        border: 0;
        width: 0;
    }}
    """


def install_shadow(widget: QWidget, *, blur_radius: int = 20, y_offset: int = 2, alpha: int = 45) -> None:
    """给卡片加极轻阴影，制造悬浮层次感。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur_radius)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


def _palette_pick(key: str) -> str:
    """从当前 palette 中取色（模块内不走 __getattr__，所以走字典）。"""
    return _PALETTES[_current_palette][key]


def apply_card_style(widget: QWidget, variant: str = "panel") -> None:
    """
    统一卡片样式（主题感知）。
    panel    — 标准内容卡片
    hero     — 高亮/入口卡片（略深背景，更明显边框）
    subtle   — 弱化卡片（无边框，扁平）
    metric   — 指标卡片（紧凑，强调数字）
    soft     — 柔和强调（主色弱背景）
    warning  — 警告强调
    """
    if variant == "metric":
        bg_key = "SURFACE_INPUT"
        border_key = None
        shadow = False
        radius = RADIUS_LG
    elif variant == "hero":
        bg_key = "SURFACE_ELEVATED"
        border_key = "BORDER_STRONG"
        shadow = False
        radius = RADIUS_LG
    elif variant == "subtle":
        bg_key = None  # transparent
        border_key = "BORDER_SUBTLE"
        shadow = False
        radius = RADIUS_MD
    elif variant == "soft":
        bg_key = "_ACCENT_BG_SOFT"  # 标记是常量 ACCENT_BG_SOFT
        border_key = "SOFT_BORDER"
        shadow = False
        radius = RADIUS_LG
    elif variant == "warning":
        bg_key = "_WARNING_SOFT_COLOR"
        border_key = "WARNING_BORDER"
        shadow = False
        radius = RADIUS_LG
    else:
        bg_key = "SURFACE_CARD"
        border_key = "BORDER_DEFAULT"
        shadow = False
        radius = RADIUS_LG

    def _bg():
        if bg_key is None:
            return "transparent"
        if bg_key == "_ACCENT_BG_SOFT":
            return ACCENT_BG_SOFT
        if bg_key == "_WARNING_SOFT_COLOR":
            return WARNING_SOFT_COLOR
        return _palette_pick(bg_key)

    def _border():
        if border_key is None:
            return "transparent"
        if border_key == "SOFT_BORDER":
            return _palette_pick("SOFT_BORDER")
        if border_key == "WARNING_BORDER":
            return _palette_pick("WARNING_BORDER")
        return _palette_pick(border_key)

    def _hover():
        return _palette_pick("HOVER_BORDER")

    object_name = f"{variant}Card"
    widget.setObjectName(object_name)
    widget.setStyleSheet(
        f"""
        QWidget#{object_name} {{
            background-color: {_bg()};
            border: 1px solid {_border()};
            border-radius: {radius}px;
        }}
        QWidget#{object_name}:hover {{
            border-color: {_hover()};
        }}
        """
    )

    def _refresh() -> str:
        return f"""
        QWidget#{object_name} {{
            background-color: {_bg()};
            border: 1px solid {_border()};
            border-radius: {radius}px;
        }}
        QWidget#{object_name}:hover {{
            border-color: {_hover()};
        }}
        """
    _themed_widgets.append((widget, _refresh))
    if shadow:
        install_shadow(widget)
    else:
        widget.setGraphicsEffect(None)


def apply_read_only_editor_style(editor: PlainTextEdit, *, console: bool = False) -> None:
    """主题感知的只读编辑器样式。"""
    font_family = f"{FONT_CODE}" if console else FONT_SYSTEM
    def _css() -> str:
        bg = "#090E16" if console else _palette_pick("EDITOR_BG")
        return f"""
        background-color: {bg};
        color: {_palette_pick("TEXT_SECONDARY")};
        border: 1px solid {_palette_pick("BORDER_DEFAULT")};
        border-radius: {RADIUS_MD}px;
        padding: 8px;
        selection-background-color: {ACCENT_BG_MEDIUM};
        font-family: {font_family};
        font-size: {FONT_SIZE_XS}px;
        line-height: 1.6;
        """
    apply_themed_style(editor, _css)


def apply_input_style(widget: QWidget) -> None:
    """主题感知的输入框样式（LineEdit/ComboBox）。

    必须用具体选择器包裹，否则 Qt 会把这条没选择器的样式
    当作"匹配所有 widget"，并通过 setStyleSheet 传到子 widget。
    ComboBox 弹出的 RoundMenu 会继承到 min-height/max-height: 34px，
    把 popup 高度锁到 34px 只能显示一个 item。
    QLineEdit/QPushButton 已经覆盖了 LineEdit/ComboBox（含 qfluentwidgets 子类）。
    """
    def _css() -> str:
        return f"""
        QLineEdit, QPushButton {{
            background-color: {_palette_pick("SURFACE_INPUT")};
            color: {_palette_pick("TEXT_PRIMARY")};
            border: 1px solid {_palette_pick("BORDER_DEFAULT")};
            border-radius: {RADIUS_MD}px;
            padding: 0 12px;
            min-height: 34px;
            max-height: 34px;
            font-size: {FONT_SIZE_SM}px;
        }}
        """
    apply_themed_style(widget, _css)


def apply_label_tone(
    label: QWidget | BodyLabel | StrongBodyLabel,
    *,
    muted: bool = False,
    level: int = 2,
    size: int | None = None,
    weight: int | None = None,
) -> None:
    """主题感知的标签样式。"""
    extra = ""
    if size is not None:
        extra += f" font-size: {size}px;"
    if weight is not None:
        extra += f" font-weight: {weight};"

    def _css() -> str:
        if muted or level >= 3:
            color = _palette_pick("TEXT_MUTED")
        elif level == 1:
            color = _palette_pick("TEXT_PRIMARY")
        else:
            color = _palette_pick("TEXT_SECONDARY")
        return f"color: {color}; background: transparent;{extra}"

    apply_themed_style(label, _css)
