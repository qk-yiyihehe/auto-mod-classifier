from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from . import qt_theme
from .qt_theme import (
    ACCENT_BG_SOFT,
    ACCENT_BG_MEDIUM,
    ACCENT_COLOR,
    ACCENT_NORMAL,
    ERROR_COLOR,
    IDLE_COLOR,
    RUNNING_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    apply_card_style,
    apply_themed_style,
    install_shadow,
    FONT_SIZE_XS,
    FONT_SIZE_SM,
    FONT_SIZE_BASE,
    FONT_SIZE_MD,
    FONT_SIZE_XL,
    FONT_SIZE_XXL,
    RADIUS_SM,
    RADIUS_MD,
    RADIUS_LG,
    SPACING_SM,
    SPACING_MD,
    SPACING_LG,
    SPACING_XL,
)


def _start_opacity_flash(widget: QWidget, owner: QWidget, store: List[QPropertyAnimation], *, start: float = 0.45) -> None:
    """数值或状态变更时的微闪动效，轻盈不抢眼。"""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(start)
    animation = QPropertyAnimation(effect, b"opacity", owner)
    animation.setDuration(200)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    store.append(animation)

    def _cleanup() -> None:
        if animation in store:
            store.remove(animation)

    animation.finished.connect(_cleanup)
    animation.start()


class ScrollablePage(ScrollArea):
    """设置页壳子 —— 卡片内部可滚动，不整页滚。"""

    def __init__(self, page_key: str, title: str, subtitle: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.page_key = page_key
        self.setObjectName(page_key)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.viewport().setStyleSheet("background: transparent; border: 0;")

        wrapper = QWidget(self)
        wrapper.setObjectName(f"{page_key}Wrapper")
        wrapper.setStyleSheet("background: transparent;")
        self.setWidget(wrapper)
        self.content = wrapper

        outer = QVBoxLayout(wrapper)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        inner = QWidget(wrapper)
        inner.setObjectName(f"{page_key}Content")
        apply_themed_style(inner, lambda: f"background-color: {qt_theme.BG_CONTENT};")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_LG)
        layout.setSpacing(SPACING_MD)
        self.container_layout = layout

        header = QWidget(inner)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)

        title_label = TitleLabel(title, header)
        subtitle_label = BodyLabel(subtitle, header)
        subtitle_label.setWordWrap(True)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 600;",
        )
        apply_themed_style(
            subtitle_label,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;",
        )
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        layout.addWidget(header)

        outer.addWidget(inner)

    def scroll_to_top(self) -> None:
        bar = self.verticalScrollBar()
        if isinstance(bar, QScrollBar):
            bar.setValue(0)


class TaskPage(QWidget):
    """固定首屏页面壳子 —— 不可整页纵向滚动，内容必须适配窗口。"""

    def __init__(self, page_key: str, title: str, subtitle: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.page_key = page_key
        self.setObjectName(page_key)
        apply_themed_style(self, lambda: f"background-color: {qt_theme.BG_CONTENT};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        layout.setSpacing(SPACING_SM)
        self.container_layout = layout

        header = QWidget(self)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(2)

        title_label = TitleLabel(title, header)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 600;",
        )
        header_layout.addWidget(title_label)
        if subtitle:
            sub = BodyLabel(subtitle, header)
            sub.setWordWrap(True)
            apply_themed_style(
                sub,
                lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;",
            )
            header_layout.addWidget(sub)
        layout.addWidget(header)

    def scroll_to_top(self) -> None:
        pass


class StatusDot(QFrame):
    """精致状态圆点 —— 外圈光环 + 内圈实心。"""

    STATE_COLORS = {
        "idle": IDLE_COLOR,
        "running": RUNNING_COLOR,
        "success": SUCCESS_COLOR,
        "warning": WARNING_COLOR,
        "error": ERROR_COLOR,
    }

    def __init__(self, parent: Optional[QWidget] = None, *, size: int = 10):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size + 6, size + 6)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        color = self.STATE_COLORS.get(state, IDLE_COLOR)
        glow = f"rgba({int(QColor(color).red())}, {int(QColor(color).green())}, {int(QColor(color).blue())}, 0.18)"
        # dot 颜色来自状态语义色 IDLE/RUNNING/...，本身与主题无关，每次状态切换重设即可
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {color};
                border: 1.5px solid {glow};
                border-radius: {(self._size + 6) // 2}px;
            }}
            """
        )


class MetricCard(QFrame):
    """精致指标卡片 —— 大数字、微弱标签、顶部色条。"""

    def __init__(
        self,
        title: str,
        value: str,
        note: str = "",
        parent: Optional[QWidget] = None,
        *,
        accent_color: str = ACCENT_COLOR,
    ):
        super().__init__(parent)
        self._animations: List[QPropertyAnimation] = []
        self._accent_color = accent_color
        apply_card_style(self, "metric")
        self.setMinimumHeight(76)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        layout.setSpacing(SPACING_SM)

        # 顶部细色条
        accent = QFrame(self)
        accent.setFixedHeight(2)
        accent.setStyleSheet(
            f"background-color: {accent_color}; border-radius: 1px; border: 0;"
        )
        layout.addWidget(accent)

        # 数字
        self.value_label = TitleLabel(value, self)
        self.value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        apply_themed_style(
            self.value_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XXL}px; font-weight: 700;",
        )
        layout.addWidget(self.value_label)

        # 标签
        caption = BodyLabel(title, self)
        caption.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        apply_themed_style(
            caption,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;",
        )
        layout.addWidget(caption)

        # 辅助说明
        self.note_label = BodyLabel(note, self)
        self.note_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.note_label.setWordWrap(True)
        apply_themed_style(
            self.note_label,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: 10px;",
        )
        layout.addWidget(self.note_label)
        layout.addStretch()

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_value(self, value: str) -> None:
        if self.value_label.text() != value:
            self.value_label.setText(value)
            _start_opacity_flash(self.value_label, self, self._animations)

    def set_note(self, note: str) -> None:
        self.note_label.setText(note)


class StageBoard(QFrame):
    """横向阶段进度 —— 圆点序号 + 连接线 + 状态动效。"""

    def __init__(self, title: str, stages: List[tuple[str, str]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._animations: List[QPropertyAnimation] = []
        self.setObjectName("stageBoard")
        apply_card_style(self, "panel")

        self.stage_order = [key for key, _ in stages]
        self.stage_rows: Dict[str, Dict[str, QLabel | QWidget]] = {}
        self.stage_details: Dict[str, str] = {}
        self.current_stage_key: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        layout.setSpacing(SPACING_SM)

        lbl = StrongBodyLabel(title, self)
        apply_themed_style(
            lbl,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px; text-transform: uppercase; letter-spacing: 0.5px;",
        )
        layout.addWidget(lbl)

        track = QWidget(self)
        track_layout = QHBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(0)
        layout.addWidget(track)

        for index, (stage_key, stage_title) in enumerate(stages, start=1):
            col = QWidget(track)
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(4)

            dot = QLabel(str(index), col)
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedSize(24, 24)
            col_layout.addWidget(dot, 0, Qt.AlignHCenter)

            title_w = QLabel(stage_title, col)
            title_w.setAlignment(Qt.AlignCenter)
            title_w.setWordWrap(True)
            col_layout.addWidget(title_w)

            self.stage_rows[stage_key] = {"container": col, "dot": dot, "title": title_w}
            track_layout.addWidget(col, 1)

            if index < len(stages):
                line = QFrame(track)
                line.setFixedHeight(1)
                line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                apply_themed_style(line, lambda: f"background-color: {qt_theme.BORDER_DEFAULT}; border: 0;")
                line_filler = QWidget(track)
                filler_layout = QVBoxLayout(line_filler)
                filler_layout.setContentsMargins(0, 11, 0, 0)
                filler_layout.addWidget(line)
                filler_layout.addStretch()
                self.stage_rows[stage_key]["line"] = line
                track_layout.addWidget(line_filler, 2)

        self.detail_label = BodyLabel("准备就绪", self)
        self.detail_label.setWordWrap(True)
        apply_themed_style(
            self.detail_label,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;",
        )
        layout.addWidget(self.detail_label)
        self.reset()

    def reset(self) -> None:
        self.stage_details.clear()
        self.current_stage_key = None
        for i, key in enumerate(self.stage_order):
            detail = "准备就绪" if i == 0 else ""
            self.stage_details[key] = detail
            self._apply_state(key, "pending", detail)
        self.detail_label.setText("准备就绪")

    def activate(self, stage_key: str, detail: str = "") -> None:
        if stage_key not in self.stage_rows:
            return
        self.current_stage_key = stage_key
        ci = self.stage_order.index(stage_key)
        if detail:
            self.stage_details[stage_key] = detail
        for i, key in enumerate(self.stage_order):
            if i < ci:
                self._apply_state(key, "done", self.stage_details.get(key, "已完成"))
            elif i == ci:
                self._apply_state(key, "running", self.stage_details.get(key, detail))
                self.detail_label.setText(self.stage_details.get(key, detail) or "运行中")
            else:
                self._apply_state(key, "pending", self.stage_details.get(key, ""))

    def finish(self, detail: str = "已完成") -> None:
        for key in self.stage_order[:-1]:
            self._apply_state(key, "done", self.stage_details.get(key, "已完成"))
        final = self.stage_order[-1]
        self.stage_details[final] = detail
        self._apply_state(final, "done", detail)
        self.detail_label.setText(detail)
        self.current_stage_key = final

    def fail(self, detail: str) -> None:
        stage_key = self.current_stage_key or self.stage_order[0]
        ci = self.stage_order.index(stage_key)
        for i, key in enumerate(self.stage_order):
            if i < ci:
                self._apply_state(key, "done", self.stage_details.get(key, "已完成"))
            elif i == ci:
                self._apply_state(key, "error", detail)
                self.detail_label.setText(detail)
            else:
                self._apply_state(key, "pending", self.stage_details.get(key, ""))

    def _apply_state(self, stage_key: str, state: str, detail: str) -> None:
        row = self.stage_rows[stage_key]
        dot = row["dot"]
        title = row["title"]
        if not isinstance(dot, QLabel) or not isinstance(title, QLabel):
            return

        # 状态色：dot/line 用语义色（不随主题），title 文字用主题色
        if state == "running":
            dot_color = RUNNING_COLOR
            dot_bg = ACCENT_BG_MEDIUM
            line_color = RUNNING_COLOR
        elif state == "done":
            dot_color = SUCCESS_COLOR
            dot_bg = "rgba(61, 214, 140, 0.08)"
            line_color = SUCCESS_COLOR
        elif state == "error":
            dot_color = ERROR_COLOR
            dot_bg = "rgba(240, 71, 112, 0.10)"
            line_color = ERROR_COLOR
        else:
            # pending
            dot_color = IDLE_COLOR
            dot_bg = "rgba(92, 101, 120, 0.08)"
            line_color = IDLE_COLOR  # pending 时 line 也用 IDLE_COLOR，不依赖主题

        apply_themed_style(
            dot,
            lambda: f"""
            color: {getattr(qt_theme, 'TEXT_MUTED' if state == 'pending' else 'TEXT_PRIMARY')};
            background-color: {dot_bg};
            border: 1px solid {dot_color};
            border-radius: 12px;
            font-size: {FONT_SIZE_XS}px;
            font-weight: 600;
            """,
        )
        apply_themed_style(
            title,
            lambda: f"color: {getattr(qt_theme, 'TEXT_MUTED' if state == 'pending' else 'TEXT_PRIMARY')}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 500;",
        )
        line = row.get("line")
        if isinstance(line, QFrame):
            _lc = line_color
            apply_themed_style(
                line,
                lambda: f"background-color: {_lc}; border: 0;",
            )
        if state in {"running", "done", "error"}:
            _start_opacity_flash(dot, self, self._animations, start=0.65)


class ActionCard(QFrame):
    """工作台入口卡片 —— 悬停浮起 + 仅左侧细色条点缀。"""

    def __init__(
        self,
        title: str,
        description: str,
        button_text: str,
        parent: Optional[QWidget] = None,
        *,
        icon=None,
        primary: bool = False,
    ):
        super().__init__(parent)
        variant = "hero" if primary else "panel"
        apply_card_style(self, variant)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
        layout.setSpacing(SPACING_SM)

        from qfluentwidgets import IconWidget, PushButton

        header_row = QHBoxLayout()
        header_row.setSpacing(SPACING_SM)
        if icon is not None:
            iw = IconWidget(icon, self)
            iw.setFixedSize(20, 20)
            header_row.addWidget(iw, 0, Qt.AlignTop)
        tl = StrongBodyLabel(title, self)
        apply_themed_style(
            tl,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        header_row.addWidget(tl, 1)
        layout.addLayout(header_row)

        dl = BodyLabel(description, self)
        dl.setWordWrap(True)
        apply_themed_style(
            dl,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;",
        )
        layout.addWidget(dl)
        layout.addStretch(1)

        self.button = PushButton(button_text, self)
        self.button.setObjectName("accentButton")
        layout.addWidget(self.button, 0, Qt.AlignLeft)


def build_tab_host(
    parent: QWidget, tabs: List[tuple[str, str, QWidget]]
) -> tuple[QWidget, SegmentedWidget, QStackedWidget]:
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACING_SM)

    segmented = SegmentedWidget(host)
    stacked = QStackedWidget(host)
    layout.addWidget(segmented, 0, Qt.AlignLeft)
    layout.addWidget(stacked, 1)

    for route_key, text, widget in tabs:
        stacked.addWidget(widget)
        segmented.addItem(
            route_key, text, onClick=lambda w=widget: stacked.setCurrentWidget(w)
        )

    if tabs:
        segmented.setCurrentItem(tabs[0][0])
        stacked.setCurrentWidget(tabs[0][2])

    return host, segmented, stacked


def build_result_table(parent: QWidget) -> TableWidget:
    table = TableWidget(parent)
    table.setColumnCount(5)
    table.setHorizontalHeaderLabels(["#", "文件名", "分类结果", "判定来源", "原因"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(TableWidget.NoEditTriggers)
    table.setSelectionBehavior(TableWidget.SelectRows)
    table.setAlternatingRowColors(True)
    table.setWordWrap(False)
    table.setMinimumHeight(120)
    table.verticalHeader().setDefaultSectionSize(30)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
    apply_themed_style(table, lambda: f"""
        background-color: {qt_theme.EDITOR_BG};
        alternate-background-color: {qt_theme.TABLE_ROW_BG};
        color: {qt_theme.TEXT_PRIMARY};
        border: 1px solid {qt_theme.BORDER_STRONG};
        border-radius: {RADIUS_MD}px;
        gridline-color: {qt_theme.SCROLL_HANDLE_BG};
        selection-background-color: {ACCENT_BG_MEDIUM};
        selection-color: {qt_theme.TEXT_PRIMARY};
        font-size: {FONT_SIZE_XS}px;
    """)
    table.horizontalHeader().setStyleSheet(
        f"""
        QHeaderView::section {{
            background-color: {qt_theme.TABLE_HEADER_BG};
            color: {qt_theme.TEXT_PRIMARY};
            border: 0;
            border-bottom: 1px solid {qt_theme.BORDER_STRONG};
            padding: 8px 10px;
            font-size: {FONT_SIZE_XS}px;
            font-weight: 600;
        }}
        """
    )
    return table


def populate_result_row(table: TableWidget, row_index: int, values: List[str]) -> None:
    numbered_values = [str(row_index + 1), *values]
    for ci, value in enumerate(numbered_values):
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        item.setForeground(QBrush(QColor(qt_theme.TEXT_PRIMARY)))
        table.setItem(row_index, ci, item)


def enable_filename_copy(table: TableWidget, status_label: Optional[BodyLabel] = None) -> None:
    def _copy_filename(row: int, column: int) -> None:
        if column != 1:
            return
        item = table.item(row, column)
        if item is None:
            return
        text = item.text().strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        if status_label is not None:
            status_label.setText(f"已复制文件名：{text}")

    table.cellClicked.connect(_copy_filename)
