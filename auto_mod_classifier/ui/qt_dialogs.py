from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CheckBox, PrimaryPushButton, PushButton, StrongBodyLabel

from ..shared import ReviewItem, VersionCandidate
from . import qt_theme
from .qt_theme import (
    ACCENT_NORMAL,
    FONT_SIZE_MD,
    FONT_SIZE_SM,
    FONT_SIZE_XS,
    FONT_SIZE_XL,
    INFO_COLOR,
    RADIUS_LG,
    RADIUS_MD,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SUCCESS_COLOR,
    WARNING_COLOR,
    apply_card_style,
    apply_themed_style,
    apply_label_tone,
    build_window_stylesheet,
)


_GROUP_HEADER_PATTERN = re.compile(r"^(?P<title>.+?)(?:\s*[（(](?P<count>\d+)[）)])?$")


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


def _transient_dialog_stylesheet() -> str:
    """给系统文件选择框/消息框使用的临时主题样式。"""
    return build_window_stylesheet() + f"""
    QFileDialog, QMessageBox {{
        background-color: {qt_theme.BG_CONTENT};
        color: {qt_theme.TEXT_PRIMARY};
    }}
    QFileDialog QLabel,
    QMessageBox QLabel {{
        color: {qt_theme.TEXT_PRIMARY};
        background: transparent;
    }}
    QFileDialog QListView,
    QFileDialog QTreeView,
    QMessageBox QTextEdit,
    QMessageBox QPlainTextEdit {{
        background-color: {qt_theme.EDITOR_BG};
        color: {qt_theme.TEXT_PRIMARY};
        border: 1px solid {qt_theme.BORDER_DEFAULT};
        border-radius: {RADIUS_MD}px;
    }}
    """


def themed_get_existing_directory(parent: Optional[QWidget], title: str, directory: str = "") -> str:
    """使用非原生目录选择框，确保能跟随应用主题。"""
    dialog = QFileDialog(parent, title, directory or "")
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True, on=True)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True, on=True)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    if dialog.exec():
        selected_files = dialog.selectedFiles()
        if selected_files:
            return selected_files[0]
    return ""


def themed_get_open_file_name(
    parent: Optional[QWidget],
    title: str,
    directory: str = "",
    file_filter: str = "",
) -> Tuple[str, str]:
    """使用非原生文件选择框，确保能跟随应用主题。"""
    dialog = QFileDialog(parent, title, directory or "", file_filter)
    dialog.setFileMode(QFileDialog.ExistingFile)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True, on=True)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    if dialog.exec():
        selected_files = dialog.selectedFiles()
        if selected_files:
            return selected_files[0], dialog.selectedNameFilter()
    return "", ""


def themed_question(parent: Optional[QWidget], title: str, message: str) -> bool:
    """主题感知的二次确认弹窗。"""
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Question)
    dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    dialog.setDefaultButton(QMessageBox.Yes)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    return dialog.exec() == QMessageBox.Yes


def themed_information(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Information)
    dialog.setStandardButtons(QMessageBox.Ok)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    dialog.exec()


def themed_warning(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Warning)
    dialog.setStandardButtons(QMessageBox.Ok)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    dialog.exec()


def themed_critical(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Critical)
    dialog.setStandardButtons(QMessageBox.Ok)
    dialog.setStyleSheet(_transient_dialog_stylesheet())
    dialog.exec()


class VersionSelectionDialog(QDialog):
    """服务端制作前的版本选择弹窗。"""

    def __init__(self, candidates: List[VersionCandidate], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.candidates = candidates
        self.selected_candidate: Optional[VersionCandidate] = None

        self.setWindowTitle("选择目标版本")
        self.resize(880, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        hint = BodyLabel("检测到多个可用版本，请选择要制作服务端的客户端版本。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(len(candidates), 6, self)
        table.setHorizontalHeaderLabels(["版本ID", "Minecraft", "加载器", "加载器版本", "Java", "版本文件"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.itemDoubleClicked.connect(lambda _item: self.confirm())
        self.table = table

        for row_index, candidate in enumerate(candidates):
            values = [
                candidate.version_id,
                candidate.minecraft_version,
                candidate.loader,
                candidate.loader_version,
                str(candidate.java_major),
                str(candidate.json_path),
            ]
            for column_index, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row_index, column_index, item)

        if candidates:
            table.selectRow(0)

        layout.addWidget(table)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel_button = PushButton("取消", self)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        confirm_button = PushButton("确定", self)
        confirm_button.clicked.connect(self.confirm)
        button_row.addWidget(confirm_button)

        layout.addLayout(button_row)

    def confirm(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.candidates):
            return
        self.selected_candidate = self.candidates[row]
        self.accept()


class ChecklistDialog(QDialog):
    """人工复核项勾选弹窗。"""

    def __init__(self, title: str, message: str, items: List[ReviewItem], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.items = items
        self.selected_keys: Optional[List[str]] = None
        self.checkboxes: Dict[str, CheckBox] = {}

        self.setWindowTitle(title)
        self.resize(1040, 700)
        self.setMinimumSize(920, 620)
        self.setObjectName("checklistDialog")
        self.setAutoFillBackground(True)
        self.setStyleSheet("")
        self._apply_dialog_style()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(SPACING_LG + 4, SPACING_LG + 2, SPACING_LG + 4, SPACING_LG + 4)
        root_layout.setSpacing(SPACING_MD)

        header_card = QFrame(self)
        apply_card_style(header_card, "soft")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        header_layout.setSpacing(6)

        title_label = StrongBodyLabel(title, header_card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 700;",
        )
        header_layout.addWidget(title_label)

        message_label = BodyLabel(message, header_card)
        message_label.setWordWrap(True)
        apply_label_tone(message_label, level=2, size=FONT_SIZE_MD)
        header_layout.addWidget(message_label)
        root_layout.addWidget(header_card)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(SPACING_SM)

        select_all_button = PrimaryPushButton("全选", self)
        self._style_action_button(select_all_button, primary=True)
        select_all_button.clicked.connect(self.select_all)
        actions_layout.addWidget(select_all_button)

        select_none_button = PushButton("全不选", self)
        select_none_button.setObjectName("accentButton")
        self._style_action_button(select_none_button, primary=False)
        select_none_button.clicked.connect(self.select_none)
        actions_layout.addWidget(select_none_button)

        self.copy_status_label = BodyLabel("提示：左键点击具体条目的文件名可直接复制；大分类标题不再提供复制。")
        self.copy_status_label.setWordWrap(True)
        apply_label_tone(self.copy_status_label, muted=True, size=FONT_SIZE_SM)
        actions_layout.addWidget(self.copy_status_label, 1)
        root_layout.addLayout(actions_layout)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setObjectName("checklistScrollArea")
        apply_themed_style(
            scroll_area,
            lambda: f"""
            QScrollArea#checklistScrollArea {{
                background-color: {qt_theme.SURFACE_DIMMED};
                border: 1px solid {qt_theme.BORDER_SUBTLE};
                border-radius: {RADIUS_LG}px;
            }}
            QScrollArea#checklistScrollArea > QWidget {{
                background: transparent;
                border: 0;
            }}
            """,
        )

        container = QWidget(scroll_area)
        container.setObjectName("checklistContainer")
        apply_themed_style(
            container,
            lambda: f"background: transparent; border: 0;",
        )
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(SPACING_SM, SPACING_SM, SPACING_SM, SPACING_SM)
        container_layout.setSpacing(SPACING_MD)

        for item in items:
            row = QFrame(container)
            row.setFrameShape(QFrame.NoFrame)
            is_group_header = not item.enabled
            if is_group_header:
                self._build_group_header(row, item)
            else:
                apply_card_style(row, "panel")
                row_layout = QVBoxLayout(row)
                row_layout.setContentsMargins(SPACING_LG, SPACING_MD + 2, SPACING_LG, SPACING_MD + 2)
                row_layout.setSpacing(SPACING_SM)

                top_layout = QHBoxLayout()
                top_layout.setSpacing(SPACING_SM)

                checkbox = CheckBox(item.label, row)
                checkbox.setChecked(item.checked)
                self.checkboxes[item.key] = checkbox
                checkbox.setMinimumHeight(34)
                apply_themed_style(
                    checkbox,
                    lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
                )
                top_layout.addWidget(checkbox, 1)
                copy_button = PushButton("复制名称", row)
                copy_button.setObjectName("smallButton")
                copy_button.clicked.connect(lambda _checked=False, text=item.label: self.copy_item_text(text))
                top_layout.addWidget(copy_button, 0, Qt.AlignRight)
                row_layout.addLayout(top_layout)

                if item.detail:
                    detail_label = QLabel(item.detail, row)
                    detail_label.setWordWrap(True)
                    detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    apply_themed_style(
                        detail_label,
                        lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; border: 0; font-size: {FONT_SIZE_SM}px; line-height: 1.55;",
                    )
                    row_layout.addWidget(detail_label)

            container_layout.addWidget(row)

        container_layout.addStretch(1)
        scroll_area.setWidget(container)
        root_layout.addWidget(scroll_area, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING_SM)
        button_row.addStretch(1)

        cancel_button = PushButton("取消", self)
        cancel_button.setObjectName("accentButton")
        self._style_action_button(cancel_button, primary=False)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        confirm_button = PrimaryPushButton("确认继续", self)
        self._style_action_button(confirm_button, primary=True)
        confirm_button.clicked.connect(self.confirm)
        button_row.addWidget(confirm_button)

        root_layout.addLayout(button_row)

    def _apply_dialog_style(self) -> None:
        apply_themed_style(
            self,
            lambda: f"""
            QDialog#checklistDialog {{
                background-color: {qt_theme.BG_CONTENT};
            }}
            """,
        )

    def _build_group_header(self, row: QFrame, item: ReviewItem) -> None:
        title_text, count_text = self._parse_group_header(item.label)
        accent_color = self._resolve_group_accent(title_text)
        row.setObjectName("checklistGroupHeader")
        apply_themed_style(
            row,
            lambda: f"""
            QFrame#checklistGroupHeader {{
                background-color: {self._group_header_background(accent_color)};
                border: 1px solid {self._group_header_border(accent_color)};
                border-radius: {RADIUS_LG}px;
            }}
            QFrame#checklistGroupHeader:hover {{
                border-color: {self._group_header_border_hover(accent_color)};
            }}
            """,
        )

        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(SPACING_LG, SPACING_MD + 2, SPACING_LG, SPACING_MD + 2)
        row_layout.setSpacing(SPACING_SM)

        accent_bar = QFrame(row)
        accent_bar.setFixedHeight(3)
        apply_themed_style(
            accent_bar,
            lambda: f"background-color: {accent_color}; border: 0; border-radius: 1px;",
        )
        row_layout.addWidget(accent_bar)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING_SM)

        eyebrow = QLabel(self._group_header_caption(title_text), row)
        apply_themed_style(
            eyebrow,
            lambda: f"color: {self._group_header_eyebrow(accent_color)}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 700; letter-spacing: 0.5px;",
        )
        header_layout.addWidget(eyebrow, 0, Qt.AlignVCenter)

        title_label = StrongBodyLabel(title_text, row)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 700;",
        )
        header_layout.addWidget(title_label, 1)

        if count_text:
            badge = QLabel(f"{count_text} 项", row)
            badge.setAlignment(Qt.AlignCenter)
            badge.setMinimumHeight(28)
            badge.setMinimumWidth(64)
            badge.setObjectName("groupCountBadge")
            apply_themed_style(
                badge,
                lambda: f"""
                QLabel#groupCountBadge {{
                    color: {self._group_header_badge_text(accent_color)};
                    background-color: {self._group_header_badge_background(accent_color)};
                    border: 1px solid {self._group_header_badge_border(accent_color)};
                    border-radius: {RADIUS_MD}px;
                    padding: 0 10px;
                    font-size: {FONT_SIZE_SM}px;
                    font-weight: 700;
                }}
                """,
            )
            header_layout.addWidget(badge, 0, Qt.AlignRight | Qt.AlignVCenter)

        row_layout.addLayout(header_layout)

        if item.detail:
            detail_label = QLabel(item.detail, row)
            detail_label.setWordWrap(True)
            detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            apply_themed_style(
                detail_label,
                lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; border: 0; font-size: {FONT_SIZE_MD}px; line-height: 1.55;",
            )
            row_layout.addWidget(detail_label)

    def _parse_group_header(self, label: str) -> Tuple[str, str]:
        cleaned = label.replace("====", "").strip()
        match = _GROUP_HEADER_PATTERN.match(cleaned)
        if not match:
            return cleaned, ""
        title = (match.group("title") or cleaned).strip()
        count = (match.group("count") or "").strip()
        return title, count

    def _resolve_group_accent(self, title: str) -> str:
        if "待人工确认" in title or "待确认" in title:
            return WARNING_COLOR
        if "服务端保留" in title:
            return INFO_COLOR
        if "纯客户端" in title:
            return SUCCESS_COLOR
        return ACCENT_NORMAL

    def _group_header_background(self, accent_color: str) -> str:
        return _rgba(accent_color, 0.10 if qt_theme.current_palette_name() == "light" else 0.16)

    def _group_header_border(self, accent_color: str) -> str:
        return _rgba(accent_color, 0.28 if qt_theme.current_palette_name() == "light" else 0.38)

    def _group_header_border_hover(self, accent_color: str) -> str:
        return _rgba(accent_color, 0.40 if qt_theme.current_palette_name() == "light" else 0.52)

    def _group_header_eyebrow(self, accent_color: str) -> str:
        return accent_color

    def _group_header_badge_background(self, accent_color: str) -> str:
        return _rgba(accent_color, 0.14 if qt_theme.current_palette_name() == "light" else 0.18)

    def _group_header_badge_border(self, accent_color: str) -> str:
        return _rgba(accent_color, 0.34 if qt_theme.current_palette_name() == "light" else 0.40)

    def _group_header_badge_text(self, accent_color: str) -> str:
        if qt_theme.current_palette_name() == "light":
            return accent_color
        return "#F5F7FA"

    def _group_header_caption(self, title: str) -> str:
        if "待人工确认" in title or "待确认" in title:
            return "建议复核"
        if "服务端保留" in title:
            return "推荐保留"
        if "纯客户端" in title:
            return "默认不复制"
        return "分类结果"

    def _style_action_button(self, button: PushButton, *, primary: bool) -> None:
        button.setMinimumHeight(42)
        button.setMinimumWidth(116)
        if primary:
            return
        apply_themed_style(
            button,
            lambda: f"""
            QPushButton#accentButton {{
                background-color: {qt_theme.ACCENT_BG_SOFT};
                color: {qt_theme.ACCENT_NORMAL};
                border: 1px solid {qt_theme.ACCENT_BORDER};
                border-radius: {RADIUS_MD}px;
                padding: 0 20px;
                font-size: {FONT_SIZE_SM}px;
                font-weight: 600;
            }}
            QPushButton#accentButton:hover {{
                background-color: {qt_theme.ACCENT_BG_MEDIUM};
                border-color: {qt_theme.ACCENT_BORDER_HOVER};
            }}
            QPushButton#accentButton:pressed {{
                background-color: {qt_theme.ACCENT_BG_MEDIUM};
                border-color: {qt_theme.ACCENT_BORDER_HOVER};
            }}
            """,
        )

    def select_all(self) -> None:
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(True)

    def select_none(self) -> None:
        for checkbox in self.checkboxes.values():
            checkbox.setChecked(False)

    def copy_item_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.copy_status_label.setText(f"已复制：{text}")

    def confirm(self) -> None:
        self.selected_keys = [key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()]
        self.accept()
