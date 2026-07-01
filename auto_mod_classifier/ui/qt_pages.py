from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
)

from ..download_support import build_idle_download_status_text
from ..shared import DOWNLOAD_SOURCE_OPTIONS, DOWNLOAD_SOURCE_SMART, SERVER_BOOT_TIMEOUT_MODE_OPTIONS
from .qt_state import (
    HomeWidgets,
    ModInputWidgets,
    ReportSectionState,
    ServerInputWidgets,
    SettingsWidgets,
    TaskPanelState,
)
from . import qt_theme
from .qt_theme import (
    FONT_SIZE_XS,
    FONT_SIZE_BASE,
    FONT_SIZE_MD,
    INFO_COLOR,
    RADIUS_MD,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
    SUCCESS_COLOR,
    WARNING_COLOR,
    apply_card_style,
    apply_input_style,
    apply_label_tone,
    apply_read_only_editor_style,
    apply_themed_style,
)
from .qt_widgets import (
    ActionCard,
    LiveLogEdit,
    MetricCard,
    ScrollablePage,
    StageBoard,
    StatusDot,
    TaskPage,
    build_result_table,
    enable_filename_copy,
)


@dataclass
class HomePageBuild:
    page: TaskPage
    widgets: HomeWidgets


@dataclass
class ModPageBuild:
    page: TaskPage
    panel: TaskPanelState
    inputs: ModInputWidgets


@dataclass
class ServerPageBuild:
    page: TaskPage
    panel: TaskPanelState
    inputs: ServerInputWidgets


@dataclass
class ReportPageBuild:
    page: TaskPage
    sections: Dict[str, ReportSectionState]


@dataclass
class SettingsPageBuild:
    page: ScrollablePage
    widgets: SettingsWidgets


class QtPageFactory:

    def __init__(self, app: QWidget):
        self.app = app

    def _create_card(
        self,
        title: str,
        description: str = "",
        *,
        variant: str = "panel",
    ) -> tuple[QFrame, QVBoxLayout]:
        # 外层透明容器，不设 border-radius 防止裁剪子控件弹出层
        outer = QFrame(self.app)
        outer.setStyleSheet("background: transparent; border: 0;")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 内层做圆角背景
        inner = QFrame(outer)
        apply_card_style(inner, variant)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        layout.setSpacing(SPACING_SM)
        outer_layout.addWidget(inner)

        title_label = StrongBodyLabel(title, inner)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_BASE}px; font-weight: 600;",
        )
        layout.addWidget(title_label)

        if description:
            desc = BodyLabel(description, inner)
            desc.setWordWrap(True)
            apply_label_tone(desc, muted=True, size=FONT_SIZE_XS)
            layout.addWidget(desc)

        return outer, layout

    def _build_download_source_combo(self, current: str = DOWNLOAD_SOURCE_SMART) -> ComboBox:
        combo = ComboBox(self.app)
        for code, label in DOWNLOAD_SOURCE_OPTIONS:
            combo.addItem(label, userData=code)
            if code == current:
                combo.setCurrentIndex(combo.count() - 1)
        apply_input_style(combo)
        combo.setMaxVisibleItems(8)
        return combo

    def _add_control_row(
        self,
        layout: QVBoxLayout,
        title: str,
        control: QWidget,
        hint: str = "",
    ) -> None:
        row = QHBoxLayout()
        row.setSpacing(SPACING_SM)
        title_label = BodyLabel(title, self.app)
        title_label.setFixedWidth(90)
        apply_themed_style(
            title_label,
            lambda: f"color: {qt_theme.SECONDARY_TEXT_COLOR}; background: transparent; font-size: {FONT_SIZE_XS}px;",
        )
        row.addWidget(title_label, 0, Qt.AlignVCenter)
        row.addWidget(control, 1)
        layout.addLayout(row)
        if hint:
            h = BodyLabel(hint, self.app)
            h.setWordWrap(True)
            apply_label_tone(h, muted=True, size=10)
            layout.addWidget(h)

    def _build_path_buttons(
        self,
        parent: QWidget,
        left_text: str,
        left_slot,
        right_text: str = "",
        right_slot=None,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(SPACING_SM)
        lb = PushButton(left_text, parent)
        lb.setObjectName("smallButton")
        lb.clicked.connect(left_slot)
        row.addWidget(lb)
        if right_text and right_slot:
            rb = PushButton(right_text, parent)
            rb.setObjectName("smallButton")
            rb.clicked.connect(right_slot)
            row.addWidget(rb)
        row.addStretch(1)
        return row

    def _build_task_workspace(
        self, page: TaskPage
    ) -> tuple[QWidget, QVBoxLayout, QWidget, QVBoxLayout]:
        workspace = QWidget(page)
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(SPACING_MD)

        left_column = QWidget(workspace)
        left_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left_column)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(SPACING_MD)

        right_column = QWidget(workspace)
        right_column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING_MD)

        workspace_layout.addWidget(left_column, 4)
        workspace_layout.addWidget(right_column, 6)
        page.container_layout.addWidget(workspace, 1)
        return left_column, left_layout, right_column, right_layout

    def _build_status_card(
        self,
        title: str,
        ready_text: str,
        result_button_text: str,
        result_slot,
        report_slot,
        *,
        parent: QWidget,
    ) -> tuple[
        QFrame, StatusDot, StrongBodyLabel, BodyLabel, ProgressBar,
        BodyLabel, BodyLabel, BodyLabel, PushButton, PushButton,
    ]:
        card, layout = self._create_card(title)
        card.setParent(parent)

        # 上行：状态点 + 阶段名 + 进度条
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING_SM)

        status_dot = StatusDot(card)
        top_row.addWidget(status_dot, 0, Qt.AlignVCenter)

        stage_label = StrongBodyLabel("准备开始", card)
        apply_themed_style(
            stage_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        top_row.addWidget(stage_label, 1)

        progress_bar = ProgressBar(card)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setFixedWidth(120)
        top_row.addWidget(progress_bar, 0, Qt.AlignVCenter)

        progress_value_label = BodyLabel("0%", card)
        apply_themed_style(
            progress_value_label,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 600;",
        )
        top_row.addWidget(progress_value_label, 0, Qt.AlignVCenter)
        layout.addLayout(top_row)

        # 中行：状态文字
        status_label = BodyLabel(ready_text, card)
        status_label.setWordWrap(True)
        apply_label_tone(status_label, muted=True, size=FONT_SIZE_XS)
        layout.addWidget(status_label)

        # 下行：下载状态 + 输出位置
        info_row = QHBoxLayout()
        info_row.setSpacing(SPACING_MD)
        dl = BodyLabel(build_idle_download_status_text(), card)
        dl.setWordWrap(True)
        apply_label_tone(dl, muted=True, size=FONT_SIZE_XS)
        info_row.addWidget(dl, 1)
        ol = BodyLabel("输出位置：暂未生成", card)
        ol.setWordWrap(True)
        ol.setTextInteractionFlags(Qt.TextSelectableByMouse)
        apply_label_tone(ol, muted=True, size=FONT_SIZE_XS)
        info_row.addWidget(ol, 1)
        layout.addLayout(info_row)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACING_SM)
        result_button = PushButton(result_button_text, card)
        result_button.setObjectName("smallButton")
        result_button.setEnabled(False)
        result_button.clicked.connect(result_slot)
        btn_row.addWidget(result_button)
        report_button = PushButton("查看报告", card)
        report_button.setObjectName("smallButton")
        report_button.clicked.connect(report_slot)
        btn_row.addWidget(report_button)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return (
            card, status_dot, stage_label, status_label, progress_bar, progress_value_label,
            dl, ol, result_button, report_button,
        )

    def _build_log_pages(
        self,
        parent: QWidget,
        *,
        with_result_table: bool,
    ) -> tuple[QWidget, PlainTextEdit, PlainTextEdit, QWidget | None, BodyLabel | None]:
        summary_page = QWidget(parent)
        summary_layout = QVBoxLayout(summary_page)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_edit = PlainTextEdit(summary_page)
        summary_edit.setReadOnly(True)
        summary_edit.setMaximumBlockCount(500)
        summary_edit.setPlainText("任务完成后，这里会显示本次处理摘要。")
        apply_read_only_editor_style(summary_edit)
        summary_layout.addWidget(summary_edit)

        log_page = QWidget(parent)
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_edit = LiveLogEdit(log_page)
        log_edit.setReadOnly(True)
        log_edit.setMaximumBlockCount(1500)
        log_edit.setPlainText("等待开始处理。")
        apply_read_only_editor_style(log_edit, console=True)
        log_layout.addWidget(log_edit)

        if not with_result_table:
            return log_page, summary_edit, log_edit, None, None

        result_page = QWidget(parent)
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(SPACING_SM)

        hint_label = BodyLabel("处理完成后，这里会显示结果明细。", result_page)
        hint_label.setWordWrap(True)
        apply_label_tone(hint_label, muted=True, size=FONT_SIZE_XS)
        result_layout.addWidget(hint_label)

        result_table = build_result_table(result_page)
        result_layout.addWidget(result_table, 1)

        return result_page, summary_edit, log_edit, result_table, hint_label

    # ═══════════════════════════════════════════
    # 工作台
    # ═══════════════════════════════════════════
    def build_home_page(self) -> HomePageBuild:
        page = TaskPage(
            "homePage", "首页",
            "快速开始模组整理或服务端制作，并查看最近一次处理结果。",
            self.app,
        )

        # 三大入口卡片
        section_title = StrongBodyLabel("快速开始", page)
        apply_themed_style(
            section_title,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;"
            f"text-transform: uppercase; letter-spacing: 0.5px;",
        )
        page.container_layout.addWidget(section_title)

        quick_host = QWidget(page)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(SPACING_MD)
        quick_host.setLayout(action_row)

        mod_action = ActionCard(
            "模组筛选", "自动区分可保留、可移除和需要确认的模组",
            "开始筛选", quick_host,
            icon=FIF.ZIP_FOLDER, primary=True,
        )
        mod_action.button.clicked.connect(
            lambda: self.app.open_page(self.app.mod_page)
        )
        action_row.addWidget(mod_action, 1)

        server_action = ActionCard(
            "一键开服", "从客户端或整合包快速生成可用的服务端",
            "开始制作", quick_host,
            icon=FIF.COMMAND_PROMPT,
        )
        server_action.button.clicked.connect(
            lambda: self.app.open_page(self.app.server_page)
        )
        action_row.addWidget(server_action, 1)

        report_action = ActionCard(
            "处理结果", "查看最近一次处理摘要、明细和输出目录",
            "查看详情", quick_host,
            icon=FIF.DOCUMENT,
        )
        report_action.button.clicked.connect(
            lambda: self.app.open_page(self.app.report_page)
        )
        action_row.addWidget(report_action, 1)
        page.container_layout.addWidget(quick_host, 3)

        # 最近状态
        status_title = StrongBodyLabel("最近处理", page)
        apply_themed_style(
            status_title,
            lambda: f"color: {qt_theme.TEXT_MUTED}; background: transparent; font-size: {FONT_SIZE_XS}px;"
            f"text-transform: uppercase; letter-spacing: 0.5px;",
        )
        page.container_layout.addWidget(status_title)

        status_grid = QWidget(page)
        sg_layout = QGridLayout(status_grid)
        sg_layout.setContentsMargins(0, 0, 0, 0)
        sg_layout.setHorizontalSpacing(SPACING_LG)
        sg_layout.setVerticalSpacing(SPACING_LG)
        sg_layout.setColumnStretch(0, 1)
        sg_layout.setColumnStretch(1, 1)

        # 模组状态卡片
        mod_card, mod_gl = self._create_card("模组筛选")
        mod_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        mod_status_dot = StatusDot(mod_card)
        mod_status_label = StrongBodyLabel("未开始", mod_card)
        apply_themed_style(
            mod_status_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        mod_time_label = BodyLabel("最近时间：暂无", mod_card)
        mod_output_label = BodyLabel("输出位置：暂无", mod_card)
        for l in (mod_time_label, mod_output_label):
            l.setWordWrap(True)
            apply_label_tone(l, muted=True, size=FONT_SIZE_XS)

        mod_sr = QHBoxLayout()
        mod_sr.setSpacing(SPACING_SM)
        mod_sr.addWidget(mod_status_dot, 0, Qt.AlignVCenter)
        mod_sr.addWidget(mod_status_label, 1)
        mod_gl.addLayout(mod_sr)
        mod_gl.addWidget(mod_time_label)
        mod_gl.addWidget(mod_output_label)
        mod_gl.addStretch(1)

        mbr = QHBoxLayout()
        mbr.addStretch(1)
        mrb = PushButton("查看详情", mod_card)
        mrb.setObjectName("smallButton")
        mrb.clicked.connect(lambda: self.app.open_page(self.app.report_page))
        mbr.addWidget(mrb)
        mab = PushButton("前往处理", mod_card)
        mab.setObjectName("smallButton")
        mab.clicked.connect(lambda: self.app.open_page(self.app.mod_page))
        mbr.addWidget(mab)
        mod_gl.addLayout(mbr)

        # 开服状态卡片
        server_card, srv_gl = self._create_card("一键开服")
        srv_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        server_status_dot = StatusDot(server_card)
        server_status_label = StrongBodyLabel("未开始", server_card)
        apply_themed_style(
            server_status_label,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        server_time_label = BodyLabel("最近时间：暂无", server_card)
        server_output_label = BodyLabel("输出位置：暂无", server_card)
        for l in (server_time_label, server_output_label):
            l.setWordWrap(True)
            apply_label_tone(l, muted=True, size=FONT_SIZE_XS)

        ssr = QHBoxLayout()
        ssr.setSpacing(SPACING_SM)
        ssr.addWidget(server_status_dot, 0, Qt.AlignVCenter)
        ssr.addWidget(server_status_label, 1)
        srv_gl.addLayout(ssr)
        srv_gl.addWidget(server_time_label)
        srv_gl.addWidget(server_output_label)
        srv_gl.addStretch(1)

        sbr = QHBoxLayout()
        sbr.addStretch(1)
        srb = PushButton("查看详情", server_card)
        srb.setObjectName("smallButton")
        srb.clicked.connect(lambda: self.app.open_page(self.app.report_page))
        sbr.addWidget(srb)
        sab = PushButton("前往处理", server_card)
        sab.setObjectName("smallButton")
        sab.clicked.connect(lambda: self.app.open_page(self.app.server_page))
        sbr.addWidget(sab)
        srv_gl.addLayout(sbr)

        sg_layout.addWidget(mod_card, 0, 0)
        sg_layout.addWidget(server_card, 0, 1)
        page.container_layout.addWidget(status_grid, 2)

        page.container_layout.addStretch()

        tip = BodyLabel("支持文件夹、.mrpack 和 .zip 文件，也可以直接拖入窗口。", page)
        tip.setAlignment(Qt.AlignCenter)
        apply_label_tone(tip, muted=True, size=10)
        page.container_layout.addWidget(tip)

        return HomePageBuild(
            page=page,
            widgets=HomeWidgets(
                mod_status_dot=mod_status_dot,
                mod_status_label=mod_status_label,
                mod_time_label=mod_time_label,
                mod_output_label=mod_output_label,
                server_status_dot=server_status_dot,
                server_status_label=server_status_label,
                server_time_label=server_time_label,
                server_output_label=server_output_label,
            ),
        )

    # ═══════════════════════════════════════════
    # 模组筛选
    # ═══════════════════════════════════════════
    def build_mod_page(self) -> ModPageBuild:
        page = TaskPage(
            "modPage", "模组筛选",
            "选择输入源后开始整理，右侧会实时显示进度和处理记录。",
            self.app,
        )
        left_col, left_layout, right_col, right_layout = self._build_task_workspace(page)

        # — 左侧：输入 → 进度 —
        src_card, src_gl = self._create_card("输入源", "选择目录或整合包文件。", variant="subtle")
        src_card.setParent(left_col)
        src_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        mod_path_edit = LineEdit(src_card)
        mod_path_edit.setPlaceholderText("选择目录、.mrpack 或 .zip")
        mod_path_edit.setClearButtonEnabled(True)
        apply_input_style(mod_path_edit)
        src_gl.addWidget(mod_path_edit)
        src_gl.addLayout(
            self._build_path_buttons(
                src_card, "浏览目录", self.app.choose_mod_folder,
                "选择整合包", self.app.choose_mod_archive,
            )
        )

        out_card, out_gl = self._create_card("输出目录", "留空时将自动保存到输入目录附近；填写后会保存到指定位置。", variant="subtle")
        out_card.setParent(left_col)
        out_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        mod_out_edit = LineEdit(out_card)
        mod_out_edit.setPlaceholderText("可选：选择结果保存位置")
        mod_out_edit.setClearButtonEnabled(True)
        apply_input_style(mod_out_edit)
        out_gl.addWidget(mod_out_edit)
        out_gl.addLayout(
            self._build_path_buttons(out_card, "浏览输出目录", self.app.choose_mod_output_folder)
        )

        # 进度 + 指标（放在左侧，填充原本空白区域）
        board = StageBoard(
            "筛选进度",
            [
                ("scan", "读取目录"),
                ("classify", "首轮筛选"),
                ("second-pass", "进一步确认"),
                ("complete", "完成"),
            ],
            left_col,
        )
        metric_row = QHBoxLayout()
        metric_row.setSpacing(SPACING_SM)
        mk = MetricCard("服务端保留", "--", "建议保留在服务端", accent_color=INFO_COLOR)
        mc = MetricCard("纯客户端", "--", "建议从服务端移出", accent_color=SUCCESS_COLOR)
        mu = MetricCard("待确认", "--", "建议手动确认", accent_color=WARNING_COLOR)
        metric_row.addWidget(mk, 1)
        metric_row.addWidget(mc, 1)
        metric_row.addWidget(mu, 1)
        bl = board.layout()
        if isinstance(bl, QVBoxLayout):
            bl.addLayout(metric_row)

        start_btn = PrimaryPushButton("开始筛选", left_col)
        start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        start_btn.clicked.connect(self.app.start_mod_task)

        left_layout.addWidget(src_card)
        left_layout.addWidget(out_card)
        left_layout.addWidget(board, 3)
        left_layout.addWidget(start_btn)

        # — 右侧：状态 + 日志（扩大日志区域）—
        (
            sc, msd, msl, mstat, mpb, mpv, mdl, mol, mrb, _mrp,
        ) = self._build_status_card(
            "处理状态", "选择输入源后即可开始整理。",
            "打开结果目录",
            lambda: self.app.open_panel_path("mod", "result"),
            lambda: self.app.open_page(self.app.report_page),
            parent=right_col,
        )
        right_layout.addWidget(sc, 1)

        prev_card, prev_gl = self._create_card("处理记录", "这里会持续显示当前进度和处理详情。")
        prev_card.setParent(right_col)
        result_page, mod_summary, mod_log, mod_table, mod_hint = self._build_log_pages(
            prev_card, with_result_table=True,
        )
        result_page.hide()
        mod_summary.parentWidget().hide()
        log_container = mod_log.parentWidget()
        if log_container is not None:
            prev_gl.addWidget(log_container)
        right_layout.addWidget(prev_card, 8)

        assert mod_table is not None
        assert mod_hint is not None
        return ModPageBuild(
            page=page,
            panel=TaskPanelState(
                status_dot=msd,
                stage_label=msl,
                status_label=mstat,
                progress_bar=mpb,
                progress_value_label=mpv,
                download_label=mdl,
                output_label=mol,
                summary_edit=mod_summary,
                log_edit=mod_log,
                start_button=start_btn,
                result_button=mrb,
                extra_button=None,
                metric_cards={
                    "server-keep": mk,
                    "client-only": mc,
                    "unknown": mu,
                },
                stage_board=board,
                result_table=mod_table,
                result_hint_label=mod_hint,
            ),
            inputs=ModInputWidgets(path_edit=mod_path_edit, output_path_edit=mod_out_edit),
        )

    # ═══════════════════════════════════════════
    # 一键开服
    # ═══════════════════════════════════════════
    def build_server_page(self) -> ServerPageBuild:
        page = TaskPage(
            "serverPage", "一键开服",
            "选择客户端来源和服务端输出目录，右侧会实时显示当前进度。",
            self.app,
        )
        left_col, left_layout, right_col, right_layout = self._build_task_workspace(page)

        src_card, src_gl = self._create_card("客户端输入源", "选择客户端目录或整合包。", variant="subtle")
        src_card.setParent(left_col)
        src_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        srv_client_edit = LineEdit(src_card)
        srv_client_edit.setPlaceholderText("选择客户端目录、.mrpack 或 .zip")
        srv_client_edit.setClearButtonEnabled(True)
        apply_input_style(srv_client_edit)
        src_gl.addWidget(srv_client_edit)
        src_gl.addLayout(
            self._build_path_buttons(
                src_card, "浏览目录", self.app.choose_client_folder,
                "选择整合包", self.app.choose_server_archive,
            )
        )

        out_card, out_gl = self._create_card("输出目录", "建议选择空目录。", variant="subtle")
        out_card.setParent(left_col)
        out_gl.setContentsMargins(SPACING_MD + 2, SPACING_SM + 2, SPACING_MD + 2, SPACING_SM + 2)
        srv_out_edit = LineEdit(out_card)
        srv_out_edit.setPlaceholderText("选择服务端输出目录")
        srv_out_edit.setClearButtonEnabled(True)
        apply_input_style(srv_out_edit)
        out_gl.addWidget(srv_out_edit)
        out_gl.addLayout(
            self._build_path_buttons(out_card, "浏览输出目录", self.app.choose_output_folder)
        )

        # 进度放在左侧，填充空白
        board = StageBoard(
            "开服阶段",
            [
                ("scan", "识别客户端"),
                ("precheck", "匹配 Java"),
                ("installer", "下载安装器"),
                ("install", "安装服务端"),
                ("classify", "筛选模组"),
                ("verify", "启动验证"),
            ],
            left_col,
        )
        metric_row = QHBoxLayout()
        metric_row.setSpacing(SPACING_SM)
        metric_keep = MetricCard("服务端保留", "--", "识别为可用于服务端", accent_color=INFO_COLOR)
        metric_client = MetricCard("纯客户端", "--", "识别为仅客户端内容", accent_color=SUCCESS_COLOR)
        metric_final = MetricCard("最终复制", "--", "最终写入服务端的文件数", accent_color=WARNING_COLOR)
        metric_row.addWidget(metric_keep, 1)
        metric_row.addWidget(metric_client, 1)
        metric_row.addWidget(metric_final, 1)
        bl = board.layout()
        if isinstance(bl, QVBoxLayout):
            bl.addLayout(metric_row)

        start_btn = PrimaryPushButton("开始制作", left_col)
        start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        start_btn.clicked.connect(self.app.start_server_task)

        left_layout.addWidget(src_card)
        left_layout.addWidget(out_card)
        left_layout.addWidget(board, 3)
        left_layout.addWidget(start_btn)

        (
            status_card, ssd, ssl, sstat, spb, spv, sdl, sol, srb, _srp,
        ) = self._build_status_card(
            "处理状态", "确认输入源和输出目录后即可开始制作。",
            "打开服务端目录",
            lambda: self.app.open_panel_path("server", "result"),
            lambda: self.app.open_page(self.app.report_page),
            parent=right_col,
        )
        right_layout.addWidget(status_card, 1)

        prev_card, prev_gl = self._create_card("处理记录", "这里会持续显示制作过程和当前进度。")
        prev_card.setParent(right_col)
        _, srv_summary, srv_log, _, _ = self._build_log_pages(
            prev_card, with_result_table=False,
        )
        srv_summary.parentWidget().hide()
        log_container = srv_log.parentWidget()
        if log_container is not None:
            prev_gl.addWidget(log_container)
        right_layout.addWidget(prev_card, 8)

        return ServerPageBuild(
            page=page,
            panel=TaskPanelState(
                status_dot=ssd,
                stage_label=ssl,
                status_label=sstat,
                progress_bar=spb,
                progress_value_label=spv,
                download_label=sdl,
                output_label=sol,
                summary_edit=srv_summary,
                log_edit=srv_log,
                start_button=start_btn,
                result_button=srb,
                extra_button=None,
                metric_cards={
                    "server-keep": metric_keep,
                    "client-only": metric_client,
                    "final-copy": metric_final,
                },
                stage_board=board,
            ),
            inputs=ServerInputWidgets(
                client_path_edit=srv_client_edit,
                output_path_edit=srv_out_edit,
            ),
        )
    # ═══════════════════════════════════════════
    # 结果报告
    # ═══════════════════════════════════════════
    def build_report_page(self) -> ReportPageBuild:
        page = TaskPage(
            "reportPage", "处理结果",
            "这里会显示最近一次处理的摘要、明细和输出位置。",
            self.app,
        )

        mod_card, mod_l = self._create_card("模组筛选结果")
        mod_sr = QHBoxLayout()
        mod_sr.setSpacing(SPACING_SM)
        mod_sd = StatusDot(mod_card)
        mod_st = StrongBodyLabel("尚未开始模组筛选", mod_card)
        apply_themed_style(
            mod_st,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        mod_sr.addWidget(mod_sd, 0, Qt.AlignVCenter)
        mod_sr.addWidget(mod_st, 1)
        mod_tm = BodyLabel("最近时间：暂无", mod_card)
        mod_tm.setWordWrap(True)
        apply_themed_style(
            mod_tm,
            lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 500;",
        )
        mod_sum = PlainTextEdit(mod_card)
        mod_sum.setReadOnly(True)
        mod_sum.setMinimumHeight(72)
        mod_sum.setMaximumHeight(96)
        mod_sum.setPlainText("这里会显示最近一次模组筛选的处理摘要。")
        apply_read_only_editor_style(mod_sum)
        apply_themed_style(
            mod_sum,
            lambda: f"""
                color: {qt_theme.TEXT_PRIMARY};
                background-color: {qt_theme.SURFACE_ELEVATED};
                border: 1px solid {qt_theme.BORDER_STRONG};
                border-radius: {RADIUS_MD}px;
                font-size: {FONT_SIZE_XS}px;
            """,
        )

        mod_preview = QWidget(mod_card)
        mod_preview_layout = QVBoxLayout(mod_preview)
        mod_preview_layout.setContentsMargins(0, 0, 0, 0)
        mod_preview_layout.setSpacing(SPACING_SM)
        mod_hint = BodyLabel("尚未生成模组筛选结果。完成一次处理后，这里会自动显示结果明细。", mod_preview)
        mod_hint.setWordWrap(True)
        apply_themed_style(
            mod_hint,
            lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 500;",
        )
        mod_table = build_result_table(mod_preview)
        mod_table.setMinimumHeight(300)
        enable_filename_copy(mod_table, mod_hint)
        mod_preview_layout.addWidget(mod_hint)
        mod_preview_layout.addWidget(mod_table, 1)

        mod_l.addLayout(mod_sr)
        mod_l.addWidget(mod_tm)
        mod_l.addWidget(mod_sum)
        mod_l.addWidget(mod_preview, 1)

        mod_br = QHBoxLayout()
        mod_br.addStretch(1)
        mrb = PushButton("打开结果目录", mod_card)
        mrb.setObjectName("smallButton")
        mrb.setEnabled(False)
        mrb.clicked.connect(lambda: self.app.open_report_path("mod", "result"))
        mod_br.addWidget(mrb)
        mlb = PushButton("返回模组筛选", mod_card)
        mlb.setObjectName("smallButton")
        mlb.clicked.connect(lambda: self.app.open_page(self.app.mod_page))
        mod_br.addWidget(mlb)
        mod_l.addLayout(mod_br)

        sv_card, sv_l = self._create_card("一键开服结果")
        sv_sr = QHBoxLayout()
        sv_sr.setSpacing(SPACING_SM)
        sv_sd = StatusDot(sv_card)
        sv_st = StrongBodyLabel("尚未开始一键开服", sv_card)
        apply_themed_style(
            sv_st,
            lambda: f"color: {qt_theme.TEXT_PRIMARY}; background: transparent; font-size: {FONT_SIZE_MD}px; font-weight: 600;",
        )
        sv_sr.addWidget(sv_sd, 0, Qt.AlignVCenter)
        sv_sr.addWidget(sv_st, 1)
        sv_tm = BodyLabel("最近时间：暂无", sv_card)
        sv_tm.setWordWrap(True)
        apply_themed_style(
            sv_tm,
            lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 500;",
        )
        sv_sum = PlainTextEdit(sv_card)
        sv_sum.setReadOnly(True)
        sv_sum.setMinimumHeight(72)
        sv_sum.setMaximumHeight(96)
        sv_sum.setPlainText("完成一次服务端制作后，这里会显示最近一次处理摘要和输出入口。")
        apply_read_only_editor_style(sv_sum)
        apply_themed_style(
            sv_sum,
            lambda: f"""
                color: {qt_theme.TEXT_PRIMARY};
                background-color: {qt_theme.SURFACE_ELEVATED};
                border: 1px solid {qt_theme.BORDER_STRONG};
                border-radius: {RADIUS_MD}px;
                font-size: {FONT_SIZE_XS}px;
            """,
        )

        sv_preview = QWidget(sv_card)
        sv_preview_layout = QVBoxLayout(sv_preview)
        sv_preview_layout.setContentsMargins(0, 0, 0, 0)
        sv_preview_layout.setSpacing(SPACING_SM)
        sv_hint = BodyLabel("尚未生成服务端制作结果。完成一次处理后，这里会自动显示结果明细。", sv_preview)
        sv_hint.setWordWrap(True)
        apply_themed_style(
            sv_hint,
            lambda: f"color: {qt_theme.TEXT_SECONDARY}; background: transparent; font-size: {FONT_SIZE_XS}px; font-weight: 500;",
        )
        sv_table = build_result_table(sv_preview)
        sv_table.setMinimumHeight(300)
        enable_filename_copy(sv_table, sv_hint)
        sv_preview_layout.addWidget(sv_hint)
        sv_preview_layout.addWidget(sv_table, 1)

        sv_l.addLayout(sv_sr)
        sv_l.addWidget(sv_tm)
        sv_l.addWidget(sv_sum)
        sv_l.addWidget(sv_preview, 1)

        sv_br = QHBoxLayout()
        sv_br.addStretch(1)
        srb = PushButton("打开结果目录", sv_card)
        srb.setObjectName("smallButton")
        srb.setEnabled(False)
        srb.clicked.connect(lambda: self.app.open_report_path("server", "result"))
        sv_br.addWidget(srb)
        spb = PushButton("返回一键开服", sv_card)
        spb.setObjectName("smallButton")
        spb.clicked.connect(lambda: self.app.open_page(self.app.server_page))
        sv_br.addWidget(spb)
        sv_l.addLayout(sv_br)

        page.container_layout.addWidget(mod_card, 1)
        page.container_layout.addWidget(sv_card, 1)
        sv_card.hide()
        mod_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        sv_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        return ReportPageBuild(
            page=page,
            sections={
                "mod": ReportSectionState(
                    container_widget=mod_card,
                    status_dot=mod_sd,
                    status_label=mod_st,
                    time_label=mod_tm,
                    summary_edit=mod_sum,
                    log_edit=None,
                    result_button=mrb,
                    extra_button=None,
                    empty_state_widget=None,
                    empty_state_title=None,
                    empty_state_body=None,
                    preview_widget=mod_preview,
                    preview_table=mod_table,
                    preview_hint_label=mod_hint,
                ),
                "server": ReportSectionState(
                    container_widget=sv_card,
                    status_dot=sv_sd,
                    status_label=sv_st,
                    time_label=sv_tm,
                    summary_edit=sv_sum,
                    log_edit=None,
                    result_button=srb,
                    extra_button=None,
                    preview_widget=sv_preview,
                    preview_table=sv_table,
                    preview_hint_label=sv_hint,
                ),
            },
        )

    # ═══════════════════════════════════════════
    # 设置
    # ═══════════════════════════════════════════
    def build_settings_page(self) -> SettingsPageBuild:
        page = ScrollablePage(
            "settingsPage", "设置",
            "调整处理规则、默认路径、缓存清理和界面显示方式。",
            self.app,
        )

        grid = QWidget(page)
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(SPACING_MD)
        gl.setVerticalSpacing(SPACING_MD)
        gl.setColumnStretch(0, 1)
        gl.setColumnStretch(1, 1)

        # 筛选规则
        f_card, f_l = self._create_card("筛选规则", "影响模组筛选和开服时的模组处理。")
        f_l.setSpacing(SPACING_SM)
        f_dry = CheckBox("仅预览模组筛选结果，不移动原文件", f_card)
        f_dry.setChecked(False)
        f_l.addWidget(f_dry)
        f_offline = CheckBox("优先使用本地离线库（程序目录旁的 db.sqlite）", f_card)
        f_offline.setChecked(False)
        f_l.addWidget(f_offline)
        f_mc = CheckBox("查询 MC百科", f_card)
        f_mc.setChecked(True)
        f_cf = CheckBox("查询 CurseForge", f_card)
        f_sp = CheckBox("启用进一步确认", f_card)
        for cb in (f_mc, f_cf, f_sp):
            f_l.addWidget(cb)

        # 开服默认
        s_card, s_l = self._create_card("开服默认", "制作服务端时优先使用这些默认值。")
        s_l.setSpacing(SPACING_SM)
        sv_op = LineEdit(s_card)
        sv_op.setPlaceholderText("默认输出目录")
        sv_op.setClearButtonEnabled(True)
        apply_input_style(sv_op)
        self._add_control_row(s_l, "默认输出目录", sv_op, "选择输出目录时，会优先定位到这里。")
        sv_dl = self._build_download_source_combo()
        self._add_control_row(s_l, "下载源", sv_dl, "模组筛选和一键开服共用这一项下载源设置。")
        jv_rule = ComboBox(s_card)
        for t in ("自动匹配", "优先使用本机 Java", "只使用客户端自带 Java"):
            jv_rule.addItem(t)
        apply_input_style(jv_rule)
        jv_rule.setMaxVisibleItems(3)
        self._add_control_row(s_l, "Java 选择", jv_rule)
        auto_java_cb = CheckBox("找不到合适版本时，自动下载 Java 到输出目录", s_card)
        auto_java_cb.setChecked(True)
        s_l.addWidget(auto_java_cb)
        boot_timeout_combo = ComboBox(s_card)
        for code, label in SERVER_BOOT_TIMEOUT_MODE_OPTIONS:
            boot_timeout_combo.addItem(label, userData=code)
        apply_input_style(boot_timeout_combo)
        boot_timeout_combo.setMaxVisibleItems(4)
        self._add_control_row(
            s_l,
            "启动超时",
            boot_timeout_combo,
            "智能等待会根据处理进度自动延长；固定时长会在达到上限后停止等待。",
        )

        # 缓存
        c_card, c_l = self._create_card("缓存与存储", "整合包导入缓存保存在系统临时目录。")
        c_l.setSpacing(SPACING_SM)
        cache_hint = BodyLabel("当前版本固定使用系统临时目录，关闭程序时会自动做一次缓存清理。", c_card)
        cache_hint.setWordWrap(True)
        apply_label_tone(cache_hint, muted=True, size=FONT_SIZE_XS)
        c_l.addWidget(cache_hint)
        cl_btn = PushButton("清理整合包缓存", c_card)
        cl_btn.setObjectName("warningButton")
        cl_btn.clicked.connect(self.app.cleanup_import_cache)
        c_l.addWidget(cl_btn, 0, Qt.AlignLeft)

        # 界面
        i_card, i_l = self._create_card("界面设置")
        i_l.setSpacing(SPACING_SM)
        th_co = ComboBox(i_card)
        for t in ("深色", "浅色", "跟随系统"):
            th_co.addItem(t)
        apply_input_style(th_co)
        th_co.setMaxVisibleItems(3)
        th_co.setCurrentIndex(2)
        th_co.currentIndexChanged.connect(self.app.on_theme_changed)
        self._add_control_row(i_l, "主题", th_co)

        # 关于
        a_card, a_l = self._create_card("关于", variant="subtle")
        a_l.setSpacing(SPACING_XS)
        intro = BodyLabel("Auto Mod Classifier 3.0", a_card)
        intro.setWordWrap(True)
        apply_label_tone(intro, muted=False, size=FONT_SIZE_XS)
        a_l.addWidget(intro)
        author = BodyLabel("作者：yiyihehe", a_card)
        author.setWordWrap(True)
        apply_label_tone(author, muted=True, size=FONT_SIZE_XS)
        a_l.addWidget(author)
        tech = BodyLabel("技术栈：PySide6 + qfluentwidgets", a_card)
        tech.setWordWrap(True)
        apply_label_tone(tech, muted=True, size=FONT_SIZE_XS)
        a_l.addWidget(tech)

        action_bar = QWidget(page)
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(SPACING_SM)
        action_hint = BodyLabel("保存后，当前设置会在下次启动时继续生效。", action_bar)
        apply_label_tone(action_hint, muted=True, size=FONT_SIZE_XS)
        action_layout.addWidget(action_hint)
        action_layout.addStretch(1)
        reset_btn = PushButton("恢复默认", action_bar)
        reset_btn.clicked.connect(self.app.reset_settings)
        action_layout.addWidget(reset_btn)
        save_btn = PrimaryPushButton("保存设置", action_bar)
        save_btn.clicked.connect(self.app.save_settings)
        action_layout.addWidget(save_btn)

        gl.addWidget(f_card, 0, 0)
        gl.addWidget(s_card, 0, 1)
        gl.addWidget(c_card, 1, 0)
        gl.addWidget(i_card, 1, 1)
        gl.addWidget(a_card, 2, 0, 1, 2)
        page.container_layout.addWidget(grid)
        page.container_layout.addWidget(action_bar)
        page.container_layout.addStretch(1)

        return SettingsPageBuild(
            page=page,
            widgets=SettingsWidgets(
                filter_dry_run_checkbox=f_dry,
                filter_use_offline_db_checkbox=f_offline,
                filter_use_mcmod_checkbox=f_mc,
                filter_use_cf_checkbox=f_cf,
                filter_second_pass_checkbox=f_sp,
                server_output_path_edit=sv_op,
                server_download_source_combo=sv_dl,
                java_rule_combo=jv_rule,
                auto_download_java_checkbox=auto_java_cb,
                server_boot_timeout_combo=boot_timeout_combo,
                theme_combo=th_co,
                save_button=save_btn,
                reset_button=reset_btn,
            ),
        )
