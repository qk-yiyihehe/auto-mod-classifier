from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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
from qfluentwidgets import BodyLabel, CheckBox, PushButton, StrongBodyLabel

from ..shared import ReviewItem, VersionCandidate


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
        self.resize(920, 620)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        message_label = BodyLabel(message)
        message_label.setWordWrap(True)
        root_layout.addWidget(message_label)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        select_all_button = PushButton("全选", self)
        select_all_button.clicked.connect(self.select_all)
        actions_layout.addWidget(select_all_button)

        select_none_button = PushButton("全不选", self)
        select_none_button.clicked.connect(self.select_none)
        actions_layout.addWidget(select_none_button)

        self.copy_status_label = BodyLabel("提示：可以复制名称，方便去实例目录里复核。")
        self.copy_status_label.setWordWrap(True)
        actions_layout.addWidget(self.copy_status_label, 1)
        root_layout.addLayout(actions_layout)

        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)

        container = QWidget(scroll_area)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(10)

        for item in items:
            row = QFrame(container)
            row.setFrameShape(QFrame.StyledPanel)
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 12, 12, 12)
            row_layout.setSpacing(8)

            top_layout = QHBoxLayout()
            top_layout.setSpacing(8)

            if item.enabled:
                checkbox = CheckBox(item.label, row)
                checkbox.setChecked(item.checked)
                self.checkboxes[item.key] = checkbox
                top_layout.addWidget(checkbox, 1)
            else:
                label = StrongBodyLabel(item.label, row)
                top_layout.addWidget(label, 1)

            copy_button = PushButton("复制名称", row)
            copy_button.clicked.connect(lambda _checked=False, text=item.label: self.copy_item_text(text))
            top_layout.addWidget(copy_button, 0, Qt.AlignRight)
            row_layout.addLayout(top_layout)

            if item.detail:
                detail_label = QLabel(item.detail, row)
                detail_label.setWordWrap(True)
                detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                row_layout.addWidget(detail_label)

            container_layout.addWidget(row)

        container_layout.addStretch(1)
        scroll_area.setWidget(container)
        root_layout.addWidget(scroll_area, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel_button = PushButton("取消", self)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        confirm_button = PushButton("确认继续", self)
        confirm_button.clicked.connect(self.confirm)
        button_row.addWidget(confirm_button)

        root_layout.addLayout(button_row)

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
