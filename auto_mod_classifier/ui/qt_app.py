from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget
from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
    Theme,
    setTheme,
    setThemeColor,
)

from ..download_support import build_idle_download_status_text
from ..infrastructure.importers import cleanup_stale_import_workspaces
from ..shared import (
    APP_TITLE,
    DOWNLOAD_SOURCE_DOMESTIC,
    DOWNLOAD_SOURCE_LABELS,
    DOWNLOAD_SOURCE_OPTIONS,
    DOWNLOAD_SOURCE_SMART,
    ModTaskOptions,
    ReviewItem,
    ServerTaskOptions,
    TaskStage,
    VersionCandidate,
    get_category_label,
)
from ..tasks import run_mod_task, run_server_task
from .qt_dialogs import ChecklistDialog, VersionSelectionDialog
from .qt_pages import QtPageFactory
from .qt_state import HomeWidgets, ModInputWidgets, ReportSectionState, ServerInputWidgets, SettingsWidgets, TaskPanelState
from .qt_theme import ACCENT_COLOR, APP_ICON_PATH, build_window_stylesheet
from .qt_widgets import populate_result_row


class App(FluentWindow):
    """主窗口控制器，只负责任务编排、事件回写和页面切换。"""

    def __init__(self):
        super().__init__()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._pending_logs: Dict[str, List[str]] = {"mod": [], "server": []}
        self._runtime_ref: Any = None

        self.home_widgets: Optional[HomeWidgets] = None
        self.report_sections: Dict[str, ReportSectionState] = {}
        self.mod_panel: Optional[TaskPanelState] = None
        self.server_panel: Optional[TaskPanelState] = None
        self.mod_inputs: Optional[ModInputWidgets] = None
        self.server_inputs: Optional[ServerInputWidgets] = None
        self.settings_widgets: Optional[SettingsWidgets] = None

        cleanup_stale_import_workspaces()

        self._build_window()
        self._build_pages()
        self._refresh_home_overview()
        self._refresh_report_sections()

        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.poll_queue)
        self.queue_timer.start(120)

    def _build_window(self) -> None:
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1060, 520)
        self._resize_to_available_screen()
        self.setAcceptDrops(True)
        self.setMicaEffectEnabled(False)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setStyleSheet(build_window_stylesheet())

    def _resize_to_available_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1120, 720)
            return
        available = screen.availableGeometry()
        width = min(1280, max(1060, int(available.width() * 0.84)))
        height = min(620, max(520, int(available.height() * 0.58)))
        self.resize(width, height)

    def _build_pages(self) -> None:
        page_factory = QtPageFactory(self)

        home_build = page_factory.build_home_page()
        mod_build = page_factory.build_mod_page()
        server_build = page_factory.build_server_page()
        report_build = page_factory.build_report_page()
        settings_build = page_factory.build_settings_page()

        self.home_page = home_build.page
        self.home_widgets = home_build.widgets

        self.mod_page = mod_build.page
        self.mod_panel = mod_build.panel
        self.mod_inputs = mod_build.inputs

        self.server_page = server_build.page
        self.server_panel = server_build.panel
        self.server_inputs = server_build.inputs

        self.report_page = report_build.page
        self.report_sections = report_build.sections

        self.settings_page = settings_build.page
        self.settings_widgets = settings_build.widgets

        self.addSubInterface(self.home_page, FIF.HOME, "工作台")
        self.addSubInterface(self.mod_page, FIF.ZIP_FOLDER, "模组筛选")
        self.addSubInterface(self.server_page, FIF.COMMAND_PROMPT, "一键开服")
        self.addSubInterface(self.report_page, FIF.DOCUMENT, "结果报告")
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", position=NavigationItemPosition.BOTTOM)
        self.open_page(self.home_page)

    def _require_mod_inputs(self) -> ModInputWidgets:
        assert self.mod_inputs is not None
        return self.mod_inputs

    def _require_server_inputs(self) -> ServerInputWidgets:
        assert self.server_inputs is not None
        return self.server_inputs

    def _require_settings_widgets(self) -> SettingsWidgets:
        assert self.settings_widgets is not None
        return self.settings_widgets

    def open_page(self, page: QWidget) -> None:
        self.switchTo(page)
        scroll_to_top = getattr(page, "scroll_to_top", None)
        if callable(scroll_to_top):
            scroll_to_top()

    def choose_mod_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择 mods 目录")
        if selected:
            self._require_mod_inputs().path_edit.setText(selected)

    def choose_mod_archive(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择整合包文件",
            "",
            "整合包 (*.mrpack *.zip);;MRPACK (*.mrpack);;ZIP (*.zip);;所有文件 (*.*)",
        )
        if selected:
            self._require_mod_inputs().path_edit.setText(selected)

    def choose_client_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择客户端实例目录")
        if selected:
            self._require_server_inputs().client_path_edit.setText(selected)

    def choose_server_archive(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择整合包文件",
            "",
            "整合包 (*.mrpack *.zip);;MRPACK (*.mrpack);;ZIP (*.zip);;所有文件 (*.*)",
        )
        if selected:
            self._require_server_inputs().client_path_edit.setText(selected)

    def choose_output_folder(self) -> None:
        default_dir = self._require_settings_widgets().server_output_path_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(self, "选择新的空服务端输出目录", default_dir)
        if selected:
            self._require_server_inputs().output_path_edit.setText(selected)

    def cleanup_import_cache(self) -> None:
        cleanup_stale_import_workspaces()
        InfoBar.success(
            "缓存已清理",
            "整合包残留缓存和临时工作区已经尝试清理。",
            duration=2500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )

    def validate_source_path(self, path: Path, target_name: str) -> bool:
        if path.is_dir():
            return True
        if path.is_file() and path.suffix.lower() in {".zip", ".mrpack"}:
            return True
        if path.is_file():
            self.show_error(f"{target_name}当前只支持目录、.zip 和 .mrpack 文件。")
            return False
        self.show_error(f"{target_name}不存在。")
        return False

    def resolve_download_source(self, combo: ComboBox) -> str:
        current = combo.currentData()
        if isinstance(current, str) and current:
            return current
        current_text = combo.currentText().strip()
        if current_text in {value for value, _ in DOWNLOAD_SOURCE_OPTIONS}:
            return current_text
        for value, label in DOWNLOAD_SOURCE_OPTIONS:
            if current_text == label:
                return value
        if current_text == DOWNLOAD_SOURCE_LABELS.get(DOWNLOAD_SOURCE_DOMESTIC):
            return DOWNLOAD_SOURCE_SMART
        return DOWNLOAD_SOURCE_SMART

    def start_mod_task(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self.show_info("任务正在运行，请先等待当前任务结束。")
            return

        mod_inputs = self._require_mod_inputs()
        source_text = mod_inputs.path_edit.text().strip()
        if not source_text:
            self.show_warning("请先选择一个 mods 目录、客户端目录或整合包。")
            return

        source_path = Path(source_text)
        if not source_path.exists():
            self.show_error("所选目录或整合包不存在。")
            return
        if not self.validate_source_path(source_path, "模组筛选输入源"):
            return

        assert self.mod_panel is not None
        self.clear_panel("mod")
        self.mod_panel.status_label.setText("准备开始…")
        self.mod_panel.status_dot.set_state("running")
        self._set_busy_state(True)
        self._refresh_home_overview(panel_key="mod", status="运行中", output=None)
        settings_widgets = self._require_settings_widgets()

        options = ModTaskOptions(
            mods_path=source_path,
            download_source=self.resolve_download_source(settings_widgets.filter_download_source_combo),
            dry_run=mod_inputs.dry_run_checkbox.isChecked(),
            use_mcmod=settings_widgets.filter_use_mcmod_checkbox.isChecked(),
            use_curseforge=settings_widgets.filter_use_cf_checkbox.isChecked(),
            enable_second_pass=settings_widgets.filter_second_pass_checkbox.isChecked(),
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
            self.show_info("任务正在运行，请先等待当前任务结束。")
            return

        server_inputs = self._require_server_inputs()
        source_text = server_inputs.client_path_edit.text().strip()
        output_text = server_inputs.output_path_edit.text().strip()
        settings_widgets = self._require_settings_widgets()
        if not output_text:
            output_text = settings_widgets.server_output_path_edit.text().strip()
            if output_text:
                server_inputs.output_path_edit.setText(output_text)
        if not source_text:
            self.show_warning("请先选择客户端目录或整合包。")
            return
        if not output_text:
            self.show_warning("请先选择服务端输出目录。")
            return

        source_path = Path(source_text)
        if not source_path.exists():
            self.show_error("所选客户端目录或整合包不存在。")
            return
        if not self.validate_source_path(source_path, "一键开服输入源"):
            return

        assert self.server_panel is not None
        self.clear_panel("server")
        self.server_panel.status_label.setText("准备开始…")
        self.server_panel.status_dot.set_state("running")
        self._set_busy_state(True)
        self._refresh_home_overview(panel_key="server", status="运行中", output=None)

        options = ServerTaskOptions(
            client_dir=source_path,
            output_dir=Path(output_text),
            download_source=self.resolve_download_source(settings_widgets.server_download_source_combo),
            use_mcmod=settings_widgets.filter_use_mcmod_checkbox.isChecked(),
            use_curseforge=settings_widgets.filter_use_cf_checkbox.isChecked(),
            enable_second_pass=settings_widgets.filter_second_pass_checkbox.isChecked(),
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

    def _set_busy_state(self, running: bool) -> None:
        for panel in (self.mod_panel, self.server_panel):
            if panel is not None:
                panel.start_button.setEnabled(not running)

    def clear_panel(self, panel_key: str) -> None:
        panel = self.get_panel(panel_key)
        self._pending_logs[panel_key] = []
        panel.log_edit.clear()
        panel.summary_edit.setPlainText("任务进行中，完成后这里会刷新摘要。")
        panel.progress_bar.setValue(0)
        panel.output_label.setText("输出位置：运行中")
        panel.download_label.setText(build_idle_download_status_text())
        panel.stage_label.setText("当前阶段：准备开始")
        panel.status_dot.set_state("running")
        if panel.stage_board is not None:
            panel.stage_board.reset()
        panel.result_dir = None
        panel.extra_dir = None
        panel.result_button.setEnabled(False)
        if panel.extra_button is not None:
            panel.extra_button.setEnabled(False)
        for metric_card in panel.metric_cards.values():
            metric_card.set_value("--")
        if panel.result_table is not None:
            panel.result_table.clearContents()
            panel.result_table.setRowCount(0)
        if panel.result_hint_label is not None:
            panel.result_hint_label.setText("任务进行中，完成后会优先展示待确认和关键条目。")

    def get_panel(self, panel_key: str) -> TaskPanelState:
        if panel_key == "mod":
            assert self.mod_panel is not None
            return self.mod_panel
        assert self.server_panel is not None
        return self.server_panel

    def emit(self, panel: str, kind: str, payload: Any) -> None:
        self.ui_queue.put({"panel": panel, "kind": kind, "payload": payload})

    def append_log(self, panel_key: str, message: str) -> None:
        if not message:
            return
        self._pending_logs.setdefault(panel_key, []).append(message.rstrip())

    def _flush_pending_logs(self) -> None:
        for panel_key, messages in self._pending_logs.items():
            if not messages:
                continue
            panel = self.get_panel(panel_key)
            current_text = panel.log_edit.toPlainText().strip()
            if current_text == "等待任务开始。":
                panel.log_edit.clear()
            panel.log_edit.appendPlainText("\n".join(messages))
            messages.clear()

    def request_version_choice(self, candidates: List[VersionCandidate]) -> Optional[VersionCandidate]:
        event = threading.Event()
        request = {"kind": "version", "candidates": candidates, "event": event, "response": None}
        self.ui_queue.put({"panel": "server", "kind": "ui-request", "payload": request})
        event.wait()
        return request["response"]

    def request_checklist(self, title: str, message: str, items: List[ReviewItem]) -> Optional[List[str]]:
        event = threading.Event()
        request = {
            "kind": "checklist",
            "title": title,
            "message": message,
            "items": items,
            "event": event,
            "response": None,
        }
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
            panel = self.get_panel(panel_key)

            if kind == "warning":
                self.show_warning(str(payload))
                continue

            if kind == "log":
                self.append_log(panel_key, str(payload))
                self._update_stage_by_message(panel_key, str(payload))
            elif kind == "status":
                panel.status_label.setText(str(payload))
                panel.status_dot.set_state(self._status_to_dot_state(str(payload)))
                self._update_stage_by_message(panel_key, str(payload))
            elif kind == "progress":
                panel.progress_bar.setValue(max(0, min(100, int(float(payload)))))
            elif kind == "output":
                panel.output_label.setText(f"输出位置：{payload}")
            elif kind == "download-stats":
                panel.download_label.setText(str(payload))
            elif kind == "done":
                panel.result_dir = payload.get("result_dir")
                panel.extra_dir = payload.get("extra_dir")
                panel.status_label.setText(payload["status"])
                panel.status_dot.set_state("success")
                panel.progress_bar.setValue(100)
                panel.output_label.setText(f"输出位置：{payload['output']}")
                panel.download_label.setText(build_idle_download_status_text())
                panel.summary_edit.setPlainText(payload.get("summary", payload["status"]))
                panel.stage_label.setText("当前阶段：已完成")
                if panel.stage_board is not None:
                    panel.stage_board.finish(payload["status"])
                panel.result_button.setEnabled(bool(panel.result_dir))
                if panel.extra_button is not None:
                    panel.extra_button.setEnabled(bool(panel.extra_dir))
                self._update_panel_metrics(panel_key, payload)
                if panel_key == "mod":
                    self._load_mod_result_preview(panel.result_dir)
                self._update_report_section(panel_key, payload["status"], payload.get("summary", ""), panel.result_dir, panel.extra_dir)
                self._refresh_home_overview(panel_key=panel_key, status="已完成", output=payload.get("output"))
                self._set_busy_state(False)
                self.show_success(payload["status"])
            elif kind == "error":
                panel.status_label.setText("运行失败")
                panel.status_dot.set_state("error")
                panel.output_label.setText("输出位置：失败")
                panel.download_label.setText(build_idle_download_status_text())
                panel.summary_edit.setPlainText(str(payload))
                panel.stage_label.setText("当前阶段：运行失败")
                if panel.stage_board is not None:
                    panel.stage_board.fail(self._summarize_error_text(str(payload)))
                self.append_log(panel_key, str(payload))
                if panel.metric_cards:
                    for metric_card in panel.metric_cards.values():
                        metric_card.set_value("失败")
                self._update_report_section(panel_key, "运行失败", str(payload), panel.result_dir, panel.extra_dir)
                self._refresh_home_overview(panel_key=panel_key, status="失败", output=None)
                self._set_busy_state(False)
                self.show_error(self._summarize_error_text(str(payload)))
            elif kind == "ui-request":
                if payload["kind"] == "version":
                    dialog = VersionSelectionDialog(payload["candidates"], self)
                    dialog.exec()
                    payload["response"] = dialog.selected_candidate
                elif payload["kind"] == "checklist":
                    dialog = ChecklistDialog(payload["title"], payload["message"], payload["items"], self)
                    dialog.exec()
                    payload["response"] = dialog.selected_keys
                payload["event"].set()

        self._flush_pending_logs()

    def _update_panel_metrics(self, panel_key: str, payload: Dict[str, Any]) -> None:
        if panel_key != "mod" or not self.mod_panel:
            return

        summary = payload.get("summary", "")
        mapping = {
            "server-keep": r"服务端保留:\s*(\d+)",
            "client-only": r"纯客户端移出:\s*(\d+)",
            "unknown": r"无法分类:\s*(\d+)",
        }
        for key, pattern in mapping.items():
            metric_card = self.mod_panel.metric_cards.get(key)
            if metric_card is None:
                continue
            match = re.search(pattern, summary)
            metric_card.set_value(match.group(1) if match else "--")

    def _detect_stage_key(self, panel_key: str, message: str) -> Optional[str]:
        text = str(message or "")
        if not text:
            return None

        if panel_key == "mod":
            mod_rules = [
                ("complete", ["分类完成", "写出报告", "正在写出报告", "json 报告", "csv 报告", "正在整理分类结果目录", "最终结果"]),
                ("second-pass", ["2次筛选", "二次筛选"]),
                ("classify", ["正在汇总", "联网分类", "->", "开始扫描目录", "共发现"]),
                ("scan", ["开始扫描目录", "共发现", "扫描目录"]),
            ]
            for stage_key, markers in mod_rules:
                if any(marker in text for marker in markers):
                    return stage_key
            return None

        server_rules = [
            ("verify", [TaskStage.COMPLETE.value, "服务端制作完成", TaskStage.VERIFY_BOOT.value, "第二次启动验证"]),
            ("verify", [TaskStage.PATCH_CONFIG.value, "写入 eula", "server.properties", TaskStage.FIRST_BOOT.value, "首次启动"]),
            ("classify", [TaskStage.COPY_CONFIGS.value, "复制配置目录", "收集配置目录候选", TaskStage.COPY_MODS.value, "复制服务端模组"]),
            ("classify", [TaskStage.CLASSIFY_MODS.value, "分析客户端 mods", "mod复制核查"]),
            ("install", [TaskStage.INSTALL_SERVER.value, "安装服务端"]),
            ("installer", [TaskStage.DOWNLOAD_INSTALLER.value, "解析官方安装器地址", "下载安装器"]),
            ("precheck", [TaskStage.PRECHECK.value, "匹配 Java", "需要 Java"]),
            ("scan", [TaskStage.CLIENT_SCAN.value, "识别客户端实例根目录", "扫描版本清单", "目标版本"]),
        ]
        for stage_key, markers in server_rules:
            if any(marker in text for marker in markers):
                return stage_key
        return None

    def _update_stage_by_message(self, panel_key: str, message: str) -> None:
        panel = self.get_panel(panel_key)
        if panel.stage_board is None:
            return
        stage_key = self._detect_stage_key(panel_key, message)
        if stage_key is None:
            return
        if stage_key not in panel.stage_board.stage_rows:
            return
        panel.stage_board.activate(stage_key, str(message))
        stage_title = panel.stage_board.stage_rows[stage_key]["title"].text()
        panel.stage_label.setText(f"当前阶段：{stage_title}")

    def _load_mod_result_preview(self, result_dir: Optional[Path]) -> None:
        if not self.mod_panel or self.mod_panel.result_table is None:
            return

        table = self.mod_panel.result_table
        hint_label = self.mod_panel.result_hint_label
        table.clearContents()
        table.setRowCount(0)

        if not result_dir:
            if hint_label is not None:
                hint_label.setText("当前还没有可读取的结果目录。")
            return

        report_path = result_dir / "分类报告.json"
        if not report_path.exists():
            if hint_label is not None:
                hint_label.setText("筛选已经完成，但还没找到分类报告。")
            return

        try:
            rows = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            if hint_label is not None:
                hint_label.setText(f"读取分类报告失败：{exc}")
            return

        priority = {"unknown": 0, "server-keep": 1, "client-only": 2}
        ordered_rows = sorted(rows, key=lambda row: priority.get(row.get("Category", ""), 9))
        preview_rows = ordered_rows[:40]
        table.setRowCount(len(preview_rows))

        for row_index, row in enumerate(preview_rows):
            populate_result_row(
                table,
                row_index,
                [
                    str(row.get("FileName", "")),
                    get_category_label(str(row.get("Category", ""))),
                    str(row.get("DecisionSource", "")),
                    str(row.get("Reason", "")),
                ],
            )

        if hint_label is not None:
            unknown_count = sum(1 for row in rows if row.get("Category") == "unknown")
            hint_label.setText(
                f"已读取 {len(rows)} 条分类结果，当前预览前 {len(preview_rows)} 条，其中待确认 {unknown_count} 条。"
            )

    def _update_report_section(
        self,
        panel_key: str,
        status: str,
        summary: str,
        result_dir: Optional[Path],
        extra_dir: Optional[Path],
    ) -> None:
        section = self.report_sections.get(panel_key)
        if section is None:
            return
        section.status_label.setText(status)
        section.status_dot.set_state(self._status_to_dot_state(status))
        section.time_label.setText(f"最近时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        section.summary_edit.setPlainText(summary or status)
        section.result_dir = result_dir
        section.extra_dir = extra_dir
        section.result_button.setEnabled(bool(result_dir))
        if section.extra_button is not None:
            section.extra_button.setEnabled(bool(extra_dir))

    def _refresh_home_overview(
        self,
        panel_key: Optional[str] = None,
        status: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        if self.home_widgets is None:
            return

        if panel_key == "mod":
            if status:
                self.home_widgets.mod_status_label.setText(status)
                self.home_widgets.mod_status_dot.set_state(self._status_to_dot_state(status))
                self.home_widgets.mod_time_label.setText(f"最近时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            if output:
                self.home_widgets.mod_output_label.setText(f"输出位置：{output}")
        elif panel_key == "server":
            if status:
                self.home_widgets.server_status_label.setText(status)
                self.home_widgets.server_status_dot.set_state(self._status_to_dot_state(status))
                self.home_widgets.server_time_label.setText(f"最近时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            if output:
                self.home_widgets.server_output_label.setText(f"输出位置：{output}")

    def _refresh_report_sections(self) -> None:
        for section in self.report_sections.values():
            section.status_dot.set_state("idle")
            section.result_button.setEnabled(False)
            if section.extra_button is not None:
                section.extra_button.setEnabled(False)

    def _status_to_dot_state(self, status: str) -> str:
        if any(word in status for word in ("失败", "错误", "异常")):
            return "error"
        if any(word in status for word in ("完成", "成功")):
            return "success"
        if any(word in status for word in ("运行", "准备", "处理中")):
            return "running"
        return "idle"

    def _summarize_error_text(self, payload: str) -> str:
        lines = [line.strip() for line in payload.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith(("RuntimeError:", "ValueError:", "FileNotFoundError:", "Exception:")):
                return line
        return lines[-1] if lines else "运行失败，详细信息已经写入日志。"

    def show_info(self, message: str) -> None:
        QMessageBox.information(self, APP_TITLE, message)

    def show_warning(self, message: str) -> None:
        QMessageBox.warning(self, APP_TITLE, message)

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, APP_TITLE, message)

    def show_success(self, message: str) -> None:
        InfoBar.success(
            "任务完成",
            message,
            duration=3500,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )

    def _open_path(self, path: Optional[Path]) -> None:
        if not path or not path.exists():
            self.show_info("当前还没有可打开的目录。")
            return
        if os.name == "nt":
            os.startfile(str(path))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_panel_path(self, panel_key: str, target: str) -> None:
        panel = self.get_panel(panel_key)
        path = panel.result_dir if target == "result" else panel.extra_dir
        self._open_path(path)

    def open_report_path(self, panel_key: str, target: str) -> None:
        section = self.report_sections.get(panel_key)
        if section is None:
            self.show_info("当前还没有可打开的目录。")
            return
        path = section.result_dir if target == "result" else section.extra_dir
        self._open_path(path)

    def set_runtime_ref(self, runtime: Any) -> None:
        self._runtime_ref = runtime

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

    def closeEvent(self, event: QCloseEvent) -> None:
        self.queue_timer.stop()
        self._close_runtime()
        try:
            browser_dir = Path(tempfile.gettempdir()) / "_mcmod_browser_data"
            if browser_dir.exists():
                shutil.rmtree(browser_dir, ignore_errors=True)
        except Exception:
            pass
        cleanup_stale_import_workspaces()
        super().closeEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            local_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if local_paths:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            event.ignore()
            return

        current_widget = self.stackedWidget.currentWidget()
        panel_key = "server" if current_widget is self.server_page else "mod"
        self._apply_dropped_paths(panel_key, paths)
        event.acceptProposedAction()

    def _apply_dropped_paths(self, panel_key: str, paths: List[str]) -> None:
        if not paths:
            return
        chosen = paths[0]
        if panel_key == "server":
            self._require_server_inputs().client_path_edit.setText(chosen)
            self.append_log("server", f"已拖入输入源：{chosen}")
        else:
            self._require_mod_inputs().path_edit.setText(chosen)
            self.append_log("mod", f"已拖入输入源：{chosen}")

        if len(paths) > 1:
            self.append_log(panel_key, f"检测到拖入了多个项目，本次先使用第一个：{chosen}")

        InfoBar.info(
            "已接收拖入文件",
            f"当前使用：{Path(chosen).name}",
            duration=1800,
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )


def main() -> None:
    app = QApplication.instance()
    created_app = False
    if app is None:
        app = QApplication(sys.argv)
        created_app = True

    app.setApplicationName(APP_TITLE)
    setTheme(Theme.DARK)
    setThemeColor(QColor(ACCENT_COLOR))

    window = App()
    window.show()

    if created_app:
        sys.exit(app.exec())
