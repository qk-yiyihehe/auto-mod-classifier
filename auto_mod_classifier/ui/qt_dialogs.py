from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CheckBox, PlainTextEdit, PrimaryPushButton, PushButton, StrongBodyLabel

from ..shared import ReviewItem, VersionCandidate
from . import qt_theme
from .qt_theme import (
    ACCENT_HOVER,
    ACCENT_PRESSED,
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
)


_GROUP_HEADER_PATTERN = re.compile(r"^(?P<title>.+?)(?:\s*[（(](?P<count>\d+)[）)])?$")


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = _hex_to_rgb(color)
    return f"rgba({red}, {green}, {blue}, {alpha:.2f})"


DIALOG_BUTTON_HEIGHT = 42
DIALOG_BUTTON_MIN_WIDTH = 104


def _message_dialog_icon(kind: str) -> Tuple[str, str]:
    if kind == "error":
        return "✕", qt_theme.ERROR_COLOR
    if kind == "warning":
        return "!", qt_theme.WARNING_COLOR
    if kind == "question":
        return "?", qt_theme.INFO_COLOR
    return "i", qt_theme.INFO_COLOR


def _apply_dialog_button_size(button: PushButton | PrimaryPushButton) -> None:
    """统一弹窗按钮尺寸，避免不同弹窗各自漂移。"""
    button.setFixedHeight(DIALOG_BUTTON_HEIGHT)
    button.setMinimumWidth(DIALOG_BUTTON_MIN_WIDTH)


def _apply_dialog_primary_button_style(button: PrimaryPushButton) -> None:
    """弹窗主按钮强制使用项目主绿色，避免落回组件默认青色。"""
    apply_themed_style(
        button,
        lambda: f"""
        PrimaryPushButton {{
            background-color: {ACCENT_NORMAL};
            color: {qt_theme.PRIMARY_TEXT};
            border: 1px solid {ACCENT_NORMAL};
            border-radius: {RADIUS_MD}px;
            padding: 0 18px;
            font-size: {FONT_SIZE_SM}px;
            font-weight: 600;
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
            background-color: {qt_theme.ACCENT_DISABLED};
            border-color: transparent;
            color: {qt_theme.PRIMARY_TEXT_DISABLED};
        }}
        """,
    )


class ThemedMessageDialog(QDialog):
    """项目内统一消息弹窗，避免 QMessageBox 在深色主题下残留系统白底。"""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        kind: str = "info",
        confirm_text: str = "确定",
        cancel_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._accepted = False
        self.setWindowTitle(title)
        self.setModal(True)
        self.setObjectName("themedMessageDialog")
        self.resize(460, 188)
        self.setMinimumWidth(420)

        apply_themed_style(
            self,
            lambda: f"""
            QDialog#themedMessageDialog {{
                background-color: {qt_theme.BG_CONTENT};
            }}
            """,
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(SPACING_MD + 2, SPACING_MD + 2, SPACING_MD + 2, SPACING_MD + 2)
        root_layout.setSpacing(SPACING_SM)

        content_card = QFrame(self)
        apply_card_style(content_card, "panel")
        content_layout = QGridLayout(content_card)
        content_layout.setContentsMargins(SPACING_MD + 2, SPACING_MD + 2, SPACING_MD + 2, SPACING_MD + 2)
        content_layout.setHorizontalSpacing(SPACING_SM)
        content_layout.setVerticalSpacing(6)

        icon_text, icon_color = _message_dialog_icon(kind)
        icon_badge = QLabel(icon_text, content_card)
        icon_badge.setAlignment(Qt.AlignCenter)
        icon_badge.setFixedSize(40, 40)
        icon_badge.setObjectName("messageIconBadge")
        apply_themed_style(
            icon_badge,
            lambda: f"""
            QLabel#messageIconBadge {{
                color: {"#FFFFFF" if qt_theme.current_palette_name() == "light" else qt_theme.TEXT_PRIMARY};
                background-color: {icon_color};
                border: 0;
                border-radius: 20px;
                font-size: {FONT_SIZE_MD}px;
                font-weight: 700;
            }}
            """,
        )
        content_layout.addWidget(icon_badge, 0, 0, 2, 1, Qt.AlignTop)

        title_label = StrongBodyLabel(title, content_card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 700;",
        )
        content_layout.addWidget(title_label, 0, 1)

        message_label = BodyLabel(message, content_card)
        message_label.setWordWrap(True)
        apply_label_tone(message_label, level=2, size=FONT_SIZE_SM)
        content_layout.addWidget(message_label, 1, 1)

        root_layout.addWidget(content_card)

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING_SM)
        button_row.addStretch(1)

        if cancel_text:
            cancel_button = PushButton(cancel_text, self)
            _apply_dialog_button_size(cancel_button)
            cancel_button.clicked.connect(self.reject)
            button_row.addWidget(cancel_button)

        confirm_button = PrimaryPushButton(confirm_text, self)
        _apply_dialog_button_size(confirm_button)
        _apply_dialog_primary_button_style(confirm_button)
        confirm_button.clicked.connect(self._confirm)
        button_row.addWidget(confirm_button)

        root_layout.addLayout(button_row)

    def _confirm(self) -> None:
        self._accepted = True
        self.accept()

    @property
    def accepted_result(self) -> bool:
        return self._accepted


def themed_get_existing_directory(parent: Optional[QWidget], title: str, directory: str = "") -> str:
    """目录选择保持原生系统样式，不跟随应用主题。"""
    return QFileDialog.getExistingDirectory(parent, title, directory or "")


def themed_get_open_file_name(
    parent: Optional[QWidget],
    title: str,
    directory: str = "",
    file_filter: str = "",
) -> Tuple[str, str]:
    """文件选择保持原生系统样式，不跟随应用主题。"""
    return QFileDialog.getOpenFileName(parent, title, directory or "", file_filter)


def themed_question(parent: Optional[QWidget], title: str, message: str) -> bool:
    """主题感知的二次确认弹窗。"""
    dialog = ThemedMessageDialog(
        title,
        message,
        kind="question",
        confirm_text="继续等待",
        cancel_text="取消",
        parent=parent,
    )
    dialog.exec()
    return dialog.accepted_result


def themed_information(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = ThemedMessageDialog(title, message, kind="info", parent=parent)
    dialog.exec()


def themed_warning(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = ThemedMessageDialog(title, message, kind="warning", parent=parent)
    dialog.exec()


def themed_critical(parent: Optional[QWidget], title: str, message: str) -> None:
    dialog = ThemedMessageDialog(title, message, kind="error", parent=parent)
    dialog.exec()


class ServerFailureDiagnosticDialog(QDialog):
    """服务端最终启动失败时的诊断弹窗。"""

    def __init__(
        self,
        *,
        summary: str,
        findings: List[Dict[str, Any]],
        snippet: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._snippet = snippet or ""
        self.setWindowTitle("服务端启动失败")
        self.resize(760, 540)
        self.setMinimumSize(700, 480)
        self.setObjectName("serverFailureDiagnosticDialog")

        apply_themed_style(
            self,
            lambda: f"""
            QDialog#serverFailureDiagnosticDialog {{
                background-color: {qt_theme.BG_CONTENT};
            }}
            """,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_LG, SPACING_LG, SPACING_LG, SPACING_LG)
        layout.setSpacing(SPACING_MD)

        header_card = QFrame(self)
        apply_card_style(header_card, "soft")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        header_layout.setSpacing(6)

        title_label = StrongBodyLabel("服务端启动失败", header_card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 700;",
        )
        header_layout.addWidget(title_label)

        summary_label = BodyLabel(summary, header_card)
        summary_label.setWordWrap(True)
        apply_label_tone(summary_label, level=2, size=FONT_SIZE_MD)
        header_layout.addWidget(summary_label)
        layout.addWidget(header_card)

        findings_title = StrongBodyLabel("已识别到的问题", self)
        apply_themed_style(
            findings_title,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 700;",
        )
        layout.addWidget(findings_title)

        findings_area = QScrollArea(self)
        findings_area.setWidgetResizable(True)
        findings_area.setFrameShape(QFrame.NoFrame)
        findings_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        findings_area.setMinimumHeight(210)
        findings_area.setObjectName("serverFailureFindingsArea")
        apply_themed_style(
            findings_area,
            lambda: f"""
            QScrollArea#serverFailureFindingsArea {{
                background: transparent;
                border: none;
            }}
            QScrollArea#serverFailureFindingsArea > QWidget > QWidget {{
                background: transparent;
            }}
            QScrollArea#serverFailureFindingsArea QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 4px 0;
            }}
            QScrollArea#serverFailureFindingsArea QScrollBar::handle:vertical {{
                background: {qt_theme.SCROLL_HANDLE_BG};
                min-height: 40px;
                border-radius: 3px;
            }}
            QScrollArea#serverFailureFindingsArea QScrollBar::handle:vertical:hover {{
                background: {qt_theme.SCROLL_HANDLE_HOVER_BG};
            }}
            QScrollArea#serverFailureFindingsArea QScrollBar::add-line:vertical,
            QScrollArea#serverFailureFindingsArea QScrollBar::sub-line:vertical,
            QScrollArea#serverFailureFindingsArea QScrollBar::add-page:vertical,
            QScrollArea#serverFailureFindingsArea QScrollBar::sub-page:vertical {{
                background: transparent;
                border: 0;
                height: 0;
            }}
            """,
        )

        findings_container = QWidget(findings_area)
        findings_layout = QVBoxLayout(findings_container)
        findings_layout.setContentsMargins(0, 0, 0, 0)
        findings_layout.setSpacing(SPACING_SM)

        if findings:
            for item in findings:
                findings_layout.addWidget(self._build_failure_finding_card(item))
        else:
            findings_layout.addWidget(self._build_failure_empty_card())
        findings_layout.addStretch(1)
        findings_area.setWidget(findings_container)
        layout.addWidget(findings_area)

        snippet_title = StrongBodyLabel("关键报错片段", self)
        apply_themed_style(
            snippet_title,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 700;",
        )
        layout.addWidget(snippet_title)

        snippet_edit = PlainTextEdit(self)
        snippet_edit.setReadOnly(True)
        snippet_edit.setPlainText(self._snippet or "未提取到关键报错片段。")
        snippet_edit.setMinimumHeight(180)
        snippet_edit.setObjectName("serverFailureSnippetEdit")
        apply_themed_style(
            snippet_edit,
            lambda: f"""
            QPlainTextEdit#serverFailureSnippetEdit {{
                color: {qt_theme.TEXT_PRIMARY};
                background-color: {qt_theme.EDITOR_BG};
                border: 1px solid {qt_theme.BORDER_STRONG};
                border-radius: {RADIUS_MD}px;
                font-size: {FONT_SIZE_XS}px;
            }}
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 4px 0;
            }}
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::handle:vertical {{
                background: {qt_theme.SCROLL_HANDLE_BG};
                min-height: 40px;
                border-radius: 3px;
            }}
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::handle:vertical:hover {{
                background: {qt_theme.SCROLL_HANDLE_HOVER_BG};
            }}
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::add-line:vertical,
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::sub-line:vertical,
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::add-page:vertical,
            QPlainTextEdit#serverFailureSnippetEdit QScrollBar::sub-page:vertical {{
                background: transparent;
                border: 0;
                height: 0;
            }}
            """,
        )
        layout.addWidget(snippet_edit, 1)

        ai_hint = BodyLabel("可以先点“复制关键报错”，再把内容发给 DeepSeek、豆包等 AI 继续排查。", self)
        ai_hint.setWordWrap(True)
        apply_label_tone(ai_hint, muted=True, size=FONT_SIZE_SM)
        layout.addWidget(ai_hint)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        copy_button = PushButton("复制关键报错", self)
        _apply_dialog_button_size(copy_button)
        copy_button.clicked.connect(self.copy_snippet)
        button_row.addWidget(copy_button)

        confirm_button = PrimaryPushButton("确定", self)
        _apply_dialog_button_size(confirm_button)
        _apply_dialog_primary_button_style(confirm_button)
        confirm_button.clicked.connect(self.accept)
        button_row.addWidget(confirm_button)
        layout.addLayout(button_row)

    def copy_snippet(self) -> None:
        QApplication.clipboard().setText(self._snippet)

    def _build_failure_finding_card(self, item: Dict[str, Any]) -> QFrame:
        card = QFrame(self)
        apply_card_style(card, "panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        card_layout.setSpacing(6)

        title_label = StrongBodyLabel(str(item.get("title") or "已识别到异常"), card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 700;",
        )
        card_layout.addWidget(title_label)

        details = [str(detail) for detail in item.get("details") or [] if str(detail).strip()]
        details_html = "".join(f"<div>{self._format_finding_detail(detail)}</div>" for detail in details)

        details_label = QLabel(card)
        details_label.setWordWrap(True)
        details_label.setTextFormat(Qt.RichText)
        details_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details_label.setText(details_html or "<div>日志里提到了这个问题，但还没抽出更具体的对象。</div>")
        apply_themed_style(
            details_label,
            lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE_SM}px; line-height: 1.5;",
        )
        card_layout.addWidget(details_label)
        return card

    def _build_failure_empty_card(self) -> QFrame:
        card = QFrame(self)
        apply_card_style(card, "panel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        card_layout.setSpacing(6)

        title_label = StrongBodyLabel("暂未识别到明确冲突对象", card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 700;",
        )
        card_layout.addWidget(title_label)

        detail_label = BodyLabel("这次没有从日志里抽出确定的模组、版本或依赖关系，建议直接复制下面的关键报错片段继续排查。", card)
        detail_label.setWordWrap(True)
        apply_label_tone(detail_label, level=2, size=FONT_SIZE_SM)
        card_layout.addWidget(detail_label)
        return card

    def _format_finding_detail(self, detail: str) -> str:
        escaped = html.escape(str(detail))
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


class VersionSelectionDialog(QDialog):
    """服务端制作前的版本选择弹窗。"""

    def __init__(self, candidates: List[VersionCandidate], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.candidates = candidates
        self.selected_candidate: Optional[VersionCandidate] = None

        self.setWindowTitle("选择目标版本")
        self.resize(880, 420)
        self.setObjectName("versionSelectionDialog")

        apply_themed_style(
            self,
            lambda: f"""
            QDialog#versionSelectionDialog {{
                background-color: {qt_theme.BG_CONTENT};
            }}
            """,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header_card = QFrame(self)
        apply_card_style(header_card, "soft")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(SPACING_LG, SPACING_MD, SPACING_LG, SPACING_MD)
        header_layout.setSpacing(6)

        title_label = StrongBodyLabel("选择目标版本", header_card)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_XL}px; font-weight: 700;",
        )
        header_layout.addWidget(title_label)

        hint = BodyLabel("检测到多个可用版本，请选择本次要使用的客户端版本。")
        hint.setWordWrap(True)
        apply_label_tone(hint, level=2, size=FONT_SIZE_MD)
        header_layout.addWidget(hint)
        layout.addWidget(header_card)

        table = QTableWidget(len(candidates), 6, self)
        table.setObjectName("versionSelectionTable")
        table.setHorizontalHeaderLabels(["版本标识", "Minecraft", "加载器", "加载器版本", "Java", "版本来源"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        table.itemDoubleClicked.connect(lambda _item: self.confirm())
        apply_themed_style(
            table,
            lambda: f"""
            QTableWidget#versionSelectionTable {{
                background-color: {qt_theme.EDITOR_BG};
                alternate-background-color: {qt_theme.TABLE_ROW_BG};
                color: {qt_theme.TEXT_PRIMARY};
                border: 1px solid {qt_theme.BORDER_STRONG};
                border-radius: {RADIUS_MD}px;
                outline: 0;
                gridline-color: {qt_theme.SCROLL_HANDLE_BG};
                selection-background-color: {qt_theme.ACCENT_BG_MEDIUM};
                selection-color: {qt_theme.TEXT_PRIMARY};
                font-size: {FONT_SIZE_XS}px;
            }}
            QTableWidget#versionSelectionTable::item {{
                padding: 6px 8px;
            }}
            """,
        )
        apply_themed_style(
            table.horizontalHeader(),
            lambda: f"""
            QHeaderView {{
                background-color: {qt_theme.TABLE_HEADER_BG};
                border: 0;
            }}
            QHeaderView::section {{
                background-color: {qt_theme.TABLE_HEADER_BG};
                color: {qt_theme.TEXT_PRIMARY};
                border: 0;
                border-bottom: 1px solid {qt_theme.BORDER_STRONG};
                padding: 8px 10px;
                font-size: {FONT_SIZE_XS}px;
                font-weight: 600;
            }}
            """,
        )
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
        _apply_dialog_button_size(cancel_button)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        confirm_button = PrimaryPushButton("确定", self)
        _apply_dialog_button_size(confirm_button)
        _apply_dialog_primary_button_style(confirm_button)
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
        self._style_action_button(select_none_button, primary=False)
        select_none_button.clicked.connect(self.select_none)
        actions_layout.addWidget(select_none_button)

        self.copy_status_label = BodyLabel("点击具体条目的文件名即可复制，分类标题不提供复制操作。")
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
                copy_button = PushButton("复制文件名", row)
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
        _apply_dialog_button_size(button)
        if primary:
            if isinstance(button, PrimaryPushButton):
                _apply_dialog_primary_button_style(button)
            return

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
