from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

from PySide6.QtWidgets import QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

if TYPE_CHECKING:
    from .qt_widgets import MetricCard, StageBoard, StatusDot


@dataclass
class HomeWidgets:
    mod_status_dot: "StatusDot"
    mod_status_label: StrongBodyLabel
    mod_time_label: BodyLabel
    mod_output_label: BodyLabel
    server_status_dot: "StatusDot"
    server_status_label: StrongBodyLabel
    server_time_label: BodyLabel
    server_output_label: BodyLabel


@dataclass
class ReportSectionState:
    container_widget: Optional[QWidget]
    status_dot: "StatusDot"
    status_label: StrongBodyLabel
    time_label: BodyLabel
    summary_edit: PlainTextEdit
    result_button: PushButton
    extra_button: Optional[PushButton]
    log_edit: Optional[PlainTextEdit] = None
    empty_state_widget: Optional[QWidget] = None
    empty_state_title: Optional[StrongBodyLabel] = None
    empty_state_body: Optional[BodyLabel] = None
    preview_widget: Optional[QWidget] = None
    preview_table: Optional[TableWidget] = None
    preview_hint_label: Optional[BodyLabel] = None
    result_dir: Optional[Path] = None
    extra_dir: Optional[Path] = None


@dataclass
class TaskPanelState:
    status_dot: "StatusDot"
    stage_label: StrongBodyLabel
    status_label: BodyLabel
    progress_bar: ProgressBar
    progress_value_label: BodyLabel
    download_label: BodyLabel
    output_label: BodyLabel
    summary_edit: PlainTextEdit
    log_edit: PlainTextEdit
    start_button: PrimaryPushButton
    result_button: PushButton
    extra_button: Optional[PushButton]
    metric_cards: Dict[str, "MetricCard"] = field(default_factory=dict)
    stage_board: Optional["StageBoard"] = None
    result_table: Optional[TableWidget] = None
    result_hint_label: Optional[BodyLabel] = None
    result_dir: Optional[Path] = None
    extra_dir: Optional[Path] = None


@dataclass
class ModInputWidgets:
    path_edit: LineEdit
    output_path_edit: LineEdit


@dataclass
class ServerInputWidgets:
    client_path_edit: LineEdit
    output_path_edit: LineEdit


@dataclass
class SettingsWidgets:
    filter_dry_run_checkbox: CheckBox
    filter_use_offline_db_checkbox: CheckBox
    filter_auto_update_offline_db_checkbox: CheckBox
    filter_use_mcmod_checkbox: CheckBox
    filter_use_curseforge_api_checkbox: CheckBox
    filter_use_cf_checkbox: CheckBox
    filter_second_pass_checkbox: CheckBox
    server_output_path_edit: LineEdit
    server_download_source_combo: ComboBox
    java_rule_combo: ComboBox
    auto_download_java_checkbox: CheckBox
    server_boot_timeout_combo: ComboBox
    theme_combo: ComboBox
    save_button: PushButton
    reset_button: PushButton
