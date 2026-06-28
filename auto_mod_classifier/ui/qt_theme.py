from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget
from qfluentwidgets import BodyLabel, PlainTextEdit, StrongBodyLabel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ICON_PATH = PROJECT_ROOT / "自动筛选模组分类器.ico"

# ═══════════════════════════════════════════════════
# 色彩系统 —— 低饱和深色基底，分层清晰
# ═══════════════════════════════════════════════════

# 背景分层（由深到浅）
BG_DEEP = "#090C12"              # 窗口最深底色
BG_SIDEBAR = "#0B0F16"           # 侧边导航背景
BG_CONTENT = "#0D1119"           # 内容区背景
SURFACE_CARD = "#131926"         # 一级卡片
SURFACE_ELEVATED = "#181F30"     # 悬浮/高亮卡片
SURFACE_INPUT = "#1B2338"        # 输入控件底色
SURFACE_DIMMED = "#101520"       # 减弱区域

# 兼容旧常量名（外部引用不变）
BG_COLOR = BG_CONTENT
SURFACE_COLOR = SURFACE_CARD
SURFACE_ALT_COLOR = SURFACE_INPUT
SURFACE_HERO_COLOR = SURFACE_ELEVATED

# 边框（极克制，几乎不可见）
BORDER_SUBTLE = "rgba(255, 255, 255, 0.04)"
BORDER_DEFAULT = "rgba(255, 255, 255, 0.06)"
BORDER_STRONG = "rgba(255, 255, 255, 0.09)"
BORDER_FOCUS = "rgba(61, 214, 140, 0.35)"

BORDER_COLOR = "#2A3350"         # 旧兼容
WEAK_BORDER_COLOR = "rgba(255, 255, 255, 0.05)"

# 文字层级
TEXT_PRIMARY = "#E3E6ED"
TEXT_SECONDARY = "#99A1B3"
TEXT_MUTED = "#656E80"
TEXT_DISABLED = "#3E4555"

TEXT_COLOR = TEXT_PRIMARY
SECONDARY_TEXT_COLOR = TEXT_SECONDARY
MUTED_TEXT_COLOR = TEXT_MUTED

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

# 语义色
IDLE_COLOR = "#5C6578"
RUNNING_COLOR = ACCENT_NORMAL
SUCCESS_COLOR = "#3DD68C"
WARNING_COLOR = "#F5A623"
ERROR_COLOR = "#F04770"
INFO_COLOR = "#5BA4FC"

WARNING_SOFT_COLOR = "rgba(245, 166, 35, 0.08)"
ERROR_SOFT_COLOR = "rgba(240, 71, 112, 0.08)"
INFO_SOFT_COLOR = "rgba(91, 164, 252, 0.08)"

# ═══════════════════════════════════════════════════
# 全局字体与尺寸
# ═══════════════════════════════════════════════════

FONT_SYSTEM = '"Segoe UI Variable Text", "Microsoft YaHei UI"'
FONT_CODE = 'Cascadia Code, Consolas, "Microsoft YaHei UI"'
FONT_SIZE_XS = 11
FONT_SIZE_SM = 12
FONT_SIZE_BASE = 13
FONT_SIZE_MD = 14
FONT_SIZE_LG = 16
FONT_SIZE_XL = 20
FONT_SIZE_XXL = 28

RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 10
RADIUS_XL = 12

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24


def build_window_stylesheet() -> str:
    """主窗口全局 QSS —— 克制、精致，所有控件统一 Fluent 质感。"""

    return f"""
    QWidget {{
        font-family: {FONT_SYSTEM};
        font-size: {FONT_SIZE_BASE}px;
        color: {TEXT_SECONDARY};
        background-color: {BG_CONTENT};
    }}

    QLabel {{
        color: {TEXT_SECONDARY};
        background: transparent;
    }}

    BodyLabel, StrongBodyLabel, TitleLabel, SubtitleLabel {{
        background: transparent;
    }}
    BodyLabel {{
        color: {TEXT_SECONDARY};
        font-size: {FONT_SIZE_BASE}px;
        font-weight: 400;
    }}
    StrongBodyLabel {{
        color: {TEXT_PRIMARY};
        font-size: {FONT_SIZE_MD}px;
        font-weight: 600;
    }}
    TitleLabel {{
        color: {TEXT_PRIMARY};
        font-size: {FONT_SIZE_XL}px;
        font-weight: 600;
    }}
    SubtitleLabel {{
        color: {TEXT_SECONDARY};
        font-size: {FONT_SIZE_MD}px;
        font-weight: 400;
    }}

    /* ── 按钮基类 ── */
    QPushButton {{
        min-height: 34px;
        max-height: 36px;
        padding: 0 18px;
        border-radius: {RADIUS_MD}px;
        border: 1px solid {BORDER_DEFAULT};
        background-color: {SURFACE_INPUT};
        color: {TEXT_SECONDARY};
        font-size: {FONT_SIZE_SM}px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: #232D42;
        border-color: {BORDER_STRONG};
        color: {TEXT_PRIMARY};
    }}
    QPushButton:pressed {{
        background-color: #1B2335;
        border-color: {BORDER_STRONG};
    }}
    QPushButton:disabled {{
        color: {TEXT_DISABLED};
        background-color: rgba(27, 35, 56, 0.5);
        border-color: {BORDER_SUBTLE};
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
        border: 1px solid rgba(245, 166, 35, 0.15);
    }}
    QPushButton#warningButton:hover {{
        background-color: rgba(245, 166, 35, 0.14);
        border-color: rgba(245, 166, 35, 0.25);
        color: #F5C04A;
    }}
    QPushButton#accentButton {{
        background-color: {ACCENT_BG_SOFT};
        color: {ACCENT_NORMAL};
        border: 1px solid rgba(61, 214, 140, 0.12);
        font-weight: 500;
    }}
    QPushButton#accentButton:hover {{
        background-color: {ACCENT_BG_MEDIUM};
        border-color: rgba(61, 214, 140, 0.22);
    }}

    /* ── 主按钮 ── */
    PrimaryPushButton {{
        background-color: {ACCENT_NORMAL};
        color: #0A1F16;
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
        color: rgba(10, 31, 22, 0.35);
    }}

    /* ── 输入框 ── */
    LineEdit, ComboBox, QLineEdit, QComboBox {{
        min-height: 34px;
        max-height: 34px;
        border-radius: {RADIUS_MD}px;
        border: 1px solid {BORDER_DEFAULT};
        background-color: {SURFACE_INPUT};
        color: {TEXT_PRIMARY};
        padding: 0 12px;
        font-size: {FONT_SIZE_SM}px;
        selection-background-color: {ACCENT_BG_MEDIUM};
    }}
    LineEdit:focus, ComboBox:focus, QLineEdit:focus, QComboBox:focus {{
        border-color: {BORDER_FOCUS};
        background-color: #1E283A;
    }}
    ComboBox::drop-down {{
        width: 24px;
        border: 0;
        background: transparent;
    }}
    ComboBox QAbstractItemView {{
        background-color: #161E2E;
        border: 1px solid {BORDER_STRONG};
        border-radius: {RADIUS_MD}px;
        selection-background-color: {ACCENT_BG_MEDIUM};
        padding: 4px;
    }}

    /* ── 复选框 ── */
    QCheckBox {{
        color: {TEXT_SECONDARY};
        background: transparent;
        spacing: 8px;
        font-size: {FONT_SIZE_SM}px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: {RADIUS_SM}px;
        border: 1px solid {BORDER_STRONG};
        background-color: {SURFACE_INPUT};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ACCENT_NORMAL};
        border-color: {ACCENT_NORMAL};
    }}

    /* ── 表格 ── */
    QHeaderView::section {{
        background-color: #151D2C;
        color: {TEXT_MUTED};
        border: 0;
        border-bottom: 1px solid {BORDER_DEFAULT};
        padding: 7px 10px;
        font-size: {FONT_SIZE_XS}px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background-color: #151D2C;
        border: 0;
        border-right: 1px solid {BORDER_DEFAULT};
        border-bottom: 1px solid {BORDER_DEFAULT};
    }}

    /* ── 进度条 ── */
    QProgressBar {{
        min-height: 6px;
        max-height: 6px;
        background-color: #151D2C;
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
        background-color: #0C131F;
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_DEFAULT};
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
        background: rgba(153, 161, 179, 0.15);
        border-radius: 3px;
        min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(153, 161, 179, 0.28);
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
        background: rgba(153, 161, 179, 0.15);
        border-radius: 3px;
        min-width: 40px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: rgba(153, 161, 179, 0.28);
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


def apply_card_style(widget: QWidget, variant: str = "panel") -> None:
    """
    统一卡片样式。
    panel    — 标准内容卡片
    hero     — 高亮/入口卡片（略深背景，更明显边框）
    subtle   — 弱化卡片（无边框，扁平）
    metric   — 指标卡片（紧凑，强调数字）
    soft     — 柔和强调（主色弱背景）
    warning  — 警告强调
    """
    if variant == "metric":
        bg = SURFACE_INPUT
        border = "transparent"
        shadow = False
        radius = RADIUS_LG
    elif variant == "hero":
        bg = SURFACE_ELEVATED
        border = BORDER_STRONG
        shadow = True
        radius = RADIUS_LG
    elif variant == "subtle":
        bg = "transparent"
        border = BORDER_SUBTLE
        shadow = False
        radius = RADIUS_MD
    elif variant == "soft":
        bg = ACCENT_BG_SOFT
        border = "rgba(61, 214, 140, 0.10)"
        shadow = False
        radius = RADIUS_LG
    elif variant == "warning":
        bg = WARNING_SOFT_COLOR
        border = "rgba(245, 166, 35, 0.10)"
        shadow = False
        radius = RADIUS_LG
    else:
        bg = SURFACE_CARD
        border = BORDER_DEFAULT
        shadow = True
        radius = RADIUS_LG

    object_name = f"{variant}Card"
    widget.setObjectName(object_name)
    widget.setStyleSheet(
        f"""
        QWidget#{object_name} {{
            background-color: {bg};
            border: 1px solid {border};
            border-radius: {radius}px;
        }}
        QWidget#{object_name}:hover {{
            border-color: rgba(255, 255, 255, 0.10);
        }}
        """
    )
    if shadow:
        install_shadow(widget)
    else:
        widget.setGraphicsEffect(None)


def apply_read_only_editor_style(editor: PlainTextEdit, *, console: bool = False) -> None:
    bg = "#090E16" if console else "#0E1522"
    font_family = f"{FONT_CODE}" if console else FONT_SYSTEM
    editor.setStyleSheet(
        f"""
        background-color: {bg};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER_DEFAULT};
        border-radius: {RADIUS_MD}px;
        padding: 8px;
        selection-background-color: {ACCENT_BG_MEDIUM};
        font-family: {font_family};
        font-size: {FONT_SIZE_XS}px;
        line-height: 1.6;
        """
    )


def apply_input_style(widget: QWidget) -> None:
    widget.setStyleSheet(
        f"""
        background-color: {SURFACE_INPUT};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER_DEFAULT};
        border-radius: {RADIUS_MD}px;
        padding: 0 12px;
        min-height: 34px;
        max-height: 34px;
        font-size: {FONT_SIZE_SM}px;
        """
    )


def apply_label_tone(
    label: QWidget | BodyLabel | StrongBodyLabel,
    *,
    muted: bool = False,
    level: int = 2,
    size: int | None = None,
    weight: int | None = None,
) -> None:
    if muted or level >= 3:
        color = TEXT_MUTED
    elif level == 1:
        color = TEXT_PRIMARY
    else:
        color = TEXT_SECONDARY
    extra = ""
    if size is not None:
        extra += f" font-size: {size}px;"
    if weight is not None:
        extra += f" font-weight: {weight};"
    style = f"color: {color}; background: transparent;"
    if extra:
        style += extra
    label.setStyleSheet(style)
