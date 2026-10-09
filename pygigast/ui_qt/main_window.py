"""Qt のメイン画面（設計J §4.3 の案B）。

- 上段: モード・主な値（Center・Span・Start・Stop・RBW・REF・dB/div）・連続/単発/停止
- 中央: グラフ（縦軸は REF レベルと dB/div の2つ: J §4.1）とマーカー表を、境目をドラッグできる縦の分割に
  （J §4.3.2）。右: タブ（トレース・掃引／マーカー／表示／信号源: J §4.5）
- 下: 長い処理の進み具合（H §4.1）と状態バー（接続・掃引・補正・信号源・記録・処理の数: J §2・M-04）

コアへは api/local.py だけを使う（設計A §2）。イベントは制御スレッドから届くので、
シグナルで画面スレッドへ渡し直してから触る。縦軸（REF・dB/div・単位）もコアの状態が正本で、
画面は状態を映し、変えるときは set_amplitude を送る。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ..api.local import LocalApi
from ..core.report_layout import norm_out_of_range
from ..core.units import drag_ref_level, format_freq, level_text
from . import (device_menu, display_view, look, marker_rows, menu_table, menus, png_data, screen_model, screen_s11, sizes,
               texts, theme, widget_sync)
from .display_control import DisplayControl
from .waterfall_view import LEFT_AXIS_W
from .docks import Parts
from .fields import FreqField, RefLevelField, SpanLockButton, ValueField
from .files import FileActions
from .graph import make_plot
from .listen import ListenControl
from .mark_bands import MarkBands
from .marker_table import MarkerPanel
from .mouse_reading import MouseReading
from .plot_layers import MarkerLayer, TraceLayer
from .prefs import UiPrefs
from .pluto_panel import PlutoRxPanel
from .rtl_panel import RtlPanel
from .settings_dialog import SettingsDialog
from .statusbar import StatusBar
from .web_access import WebControl
from .tab_display import DisplayTab
from .tab_markers import MarkerTab
from .tab_source import SourceTab
from .tab_traces import TraceTab, disable_item
from .tasks import TaskPanel
from .theme import DARK, stylesheet
from .time_axis import apply_x_axis
from .presets_dialog import PresetsDialog, save_new
from .ref_quick import RefQuick
from .run_buttons import RunButtons
from .topbar import TopBar

DB_PER_DIV_CHOICES = (1.0, 2.0, 5.0, 10.0)
DIVISIONS = 10
DEFAULT_REF = 0.0
DEFAULT_DIV = 10.0
# 周波数軸の上のホイール1段で Span を何倍にするか（奥へ回すと拡大 = Span を狭く）
ZOOM_STEP = 0.8

# モード（コード・ボタンの文字・名前・使えない理由）は texts.MODES（Web と同じ）
MODES = texts.MODES
MODE_NAMES = texts.MODE_NAMES

__all__ = ["MainWindow", "DARK"]


class _Bridge(QObject):
    """制御スレッドから画面スレッドへ渡し直す（QueuedConnection）。"""

    event = Signal(str, object)
    call = Signal(object)


class MainWindow(QMainWindow):
    MODE_NAMES = MODE_NAMES

    def __init__(self, api: LocalApi, prefs: Optional[UiPrefs] = None, check_unfinished: bool = True,
                 web: Optional[dict] = None) -> None:
        super().__init__()
        self.api = api
        self.prefs = prefs or UiPrefs()
        # 画面の配色（コアの配色を写す: 設計J §4.4）。部品は同じ辞書を見るので、中身だけを入れ替える
        self.pal = dict(DARK)
        self.setWindowTitle("pyGigaSt")
        self.state: dict = {}
        self._ref_dbm = DEFAULT_REF
        self._db_div = DEFAULT_DIV
        self._axis_shown: Optional[tuple] = None  # 描いている縦軸（単位・一番上・1目盛）
        self._amp_pending = 0
        self._drag_ref0 = 0.0
        self.selected_marker: Optional[str] = None
        self.marker_snap: dict = {"markers": [], "xy": {}}
        self.marker_row_list: list[dict] = []
        self._requested: set = set()
        self._had_trace = False
        self._bridge = _Bridge()
        # 窓を壊した後に届いた知らせ・命令の応答は捨てる（部品はもう無い。窓を閉じる間際に返ってきた応答で起きる）
        self._bridge.event.connect(lambda kind, payload: isValid(self) and self._on_event(kind, payload),
                                   Qt.ConnectionType.QueuedConnection)
        self._bridge.call.connect(lambda fn: isValid(self) and fn(), Qt.ConnectionType.QueuedConnection)
        self.files = FileActions(self)
        self.device_window = None  # 機器の一覧の窓（J §4.7。device_menu.open_list が作る）
        self.cal_window = None  # 校正の画面（設計K。cal_dialog.open_calibration が作る）
        self._presets: Optional[PresetsDialog] = None

        # 描き方・色・大きさの決まり（設計J §4.11）は部品を作る前に入れる（部品が出るときに当たる）
        theme.install(QApplication.instance(), self.pal)
        self._build_plot()
        self._build_menu()
        self._build_body()
        self._wire_buttons()
        self._build_status()
        # Web の口（設計A §4.3.2）。web は起動の引数（--web・--lan）。無ければ設定で覚えた開くか・ポート
        self.web = WebControl(self, web)
        self.setStyleSheet(stylesheet(self.pal))
        self._style_axes()
        self.setAcceptDrops(True)

        self._unsub = api.subscribe(lambda kind, payload: self._bridge.event.emit(kind, payload))
        self.call("get_state", on_ok=self._apply_state)
        # 2回目からは、覚えた機器が見つかれば自動で繋ぐ（設計B §7.3）。繋げなければ機器の一覧を開く（J §4.7）
        device_menu.connect_last(self)
        if check_unfinished:
            # 起動時にまとめ終わっていない記録を示して聞く（H §4）
            self.files.check_unfinished()

    # ------------------------------------------------------------------ 命令を呼ぶ

    def call(self, name: str, /, on_ok: Optional[Callable[[dict], None]] = None,
             field: Optional[ValueField] = None, quiet_codes: tuple[str, ...] = (),
             on_error: Optional[Callable[[BaseException], None]] = None, **kwargs) -> None:
        """命令を呼ぶ。quiet_codes の失敗は何も出さない（コアにまだ無い命令など）。on_error は失敗したときにも呼ぶ。"""
        fut = self.api.call(name, **kwargs)

        def done(f) -> None:
            def in_gui() -> None:
                exc = f.exception()
                if exc is not None:
                    if on_error is not None:
                        on_error(exc)
                    if getattr(exc, "code", None) in quiet_codes:
                        return
                    msg = texts.error_text(exc)
                    if field is not None:
                        field.flash_note(msg)
                    else:
                        self.status_note(msg)
                    return
                result = f.result()
                if isinstance(result, dict) and "adjustments" in result:
                    self._show_adjustments(result["adjustments"], result.get("alternative"))
                if on_ok:
                    on_ok(result)

            self._bridge.call.emit(in_gui)

        fut.add_done_callback(done)

    _call = call

    # ---- 利用者に聞く（試験では差し替える）

    def confirm(self, title: str, text: str, ok: Optional[str] = None) -> bool:
        """確かめる窓。ボタンは押したら何が起きるかの語（ok。省くと「続ける」）と「やめる」（「Yes・No」にしない）。"""
        box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, self)
        yes = box.addButton(ok or texts.LABELS["confirm_ok"], QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton(texts.LABELS["cancel"], QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.setEscapeButton(no)
        box.exec()
        return box.clickedButton() is yes

    def ask_text(self, title: str, label: str, text: str = "") -> Optional[str]:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value if ok else None

    def ask_open_paths(self, title: str, folder: str, filt: str) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, title, folder, filt)
        return paths

    def ask_save_path(self, title: str, default: str, filt: str) -> Optional[str]:
        path, _ = QFileDialog.getSaveFileName(self, title, default, filt)
        return path or None

    def ask_folder(self, title: str) -> Optional[str]:
        return QFileDialog.getExistingDirectory(self, title) or None

    def ask_color(self, title: str, current: str) -> Optional[str]:
        c = QColorDialog.getColor(QColor(current), self, title)
        return c.name() if c.isValid() else None

    def ask_key(self, title: str) -> Optional[str]:
        """押したキーを1つ受け取る（キーの割り当て: J §4.6）。やめたら None。"""
        from .settings_keys import KeyCaptureDialog

        d = KeyCaptureDialog(self, title)
        return d.key if d.exec() else None

    # ------------------------------------------------------------------ 画面を組む

    def _build_plot(self) -> None:
        pg.setConfigOptions(antialias=False)
        self.plot, gsig = make_plot(self.pal["graph_bg"])
        gsig.range_selected.connect(self._on_range_selected)
        gsig.freq_wheel.connect(self._on_freq_wheel)
        gsig.level_wheel.connect(self._on_level_wheel)
        gsig.level_drag.connect(self._on_level_drag)
        gsig.context_requested.connect(self._on_context_requested)
        gsig.clicked.connect(self._on_graph_clicked)
        self._graph_signals = gsig
        self.plot.setMenuEnabled(False)
        pi = self.plot.getPlotItem()
        # 軸の上の操作は軸のツールチップに書く（隠れた操作を作らない: U-12）
        pi.getAxis("bottom").setToolTip(texts.LABELS["axis_x_tip"])
        pi.getAxis("left").setToolTip(texts.LABELS["axis_y_tip"])
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        # 横の目盛線は自分で引く。軸の部品に描かせると、軸の外にはみ出す一番上の数字（REF）が切られる
        pi.showGrid(x=True, y=False, alpha=0.35)
        pi.layout.setContentsMargins(0, 10, 8, 0)
        self._hgrid: list[pg.InfiniteLine] = []
        pi.setLabel("bottom", texts.LABELS["freq"], units="Hz")
        pi.setLabel("left", texts.LABELS["level"], units="dBm")
        left = pi.getAxis("left")
        left.enableAutoSIPrefix(False)
        # ウォーターフォール・密度表示の縦軸と同じ幅（横軸の画素をそろえる: 設計P §2 の連動）
        left.setWidth(LEFT_AXIS_W)
        # REF レベルは最も読みたい値なので、軸の端でも数字を隠さない（J §4.3）
        left.setStyle(hideOverlappingLabels=False)
        self.traces = TraceLayer(self.plot, self.pal)
        self.mark_bands = MarkBands(self.plot, self.pal)  # 範囲の外・意味の無い区間の帯（設計J §4.8）
        self.marker_layer = MarkerLayer(self.plot, self.pal, self._on_xy_moved)
        # マウスの位置の読み値（設計G §2: 波形の値をカーソルの近くに）
        self.mouse_reading = MouseReading(self.plot, self.traces.shown_payloads,
                                          lambda: (self.state.get("sweep") or {}).get("step_hz") == 0)

    def _build_menu(self) -> None:
        """メニューバー（J §4.3.1）とキー（J §4.6）。並びと語は texts（Web と同じ）。上段・タブ・表のボタンも同じ項目を呼ぶ。"""
        self.actions = menus.build(self, self.plot, menu_table.table(self))

    def _build_body(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 4, 6, 0)
        outer.setSpacing(4)

        # ---- 上段（狭いときは2段に折り返す: J §4.3.3）
        self.topbar = TopBar()
        top = self.topbar.left
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QPushButton] = {}
        for code, label, _name, why in MODES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setEnabled(why is None)
            sizes.set_tier(b, "L")  # 上段は大の段（J §4.11）
            b.clicked.connect(lambda _=False, c=code: self.actions[f"mode_{c}"].trigger())
            self.mode_group.addButton(b)
            self.mode_buttons[code] = b
            top.addWidget(b)
        sizes.same_width_buttons(list(self.mode_buttons.values()), sizes.FONT_L_BUTTON)
        top.addSpacing(sizes.GAP)
        self.f_center = FreqField("Center", lambda hz: self._set_sweep(center_hz=hz, field=self.f_center))
        self.f_span = FreqField("Span", lambda hz: self._set_sweep(span_hz=hz, field=self.f_span))
        self.f_start = FreqField("Start", lambda hz: self._set_sweep(start_hz=hz, field=self.f_start))
        self.f_stop = FreqField("Stop", lambda hz: self._set_sweep(stop_hz=hz, field=self.f_stop))
        # Span 固定の切り替え（設計C §5.11 の 2）。Span の欄に付けて、状態を欄の上で見せる
        self.span_lock = SpanLockButton(self.pal["accent"], self.pal["text_dim"])
        self.span_lock.setToolTip(texts.LABELS["span_lock_tip"])
        sizes.set_tier(self.span_lock, "S")
        self.span_lock.clicked.connect(lambda on: self._set_sweep(span_lock=bool(on), field=self.f_span))
        self.f_span.add_header_widget(self.span_lock)
        for w in (self.f_center, self.f_span, self.f_start, self.f_stop):
            top.addWidget(w)
        top = self.topbar.right
        rbw_box = ValueField("RBW", big=True)
        self.rbw_combo = QComboBox()
        self._rbw_key: tuple = ()
        self.rbw_combo.activated.connect(self._rbw_chosen)
        rbw_box.layout().replaceWidget(rbw_box.edit, self.rbw_combo)
        rbw_box.edit.hide()
        self.rbw_field = rbw_box
        top.addWidget(rbw_box)
        # REF レベル: 打てるドロップダウン（+20〜−120・10dB 刻み）と欄の上のホイール 1dB（J §4.1）
        self.f_ref = RefLevelField(texts.LABELS["ref_level"], "dBm", self._set_ref, big=True)
        # いまの波形の最大値が上から1目盛下に来るよう REF を合わせる（J §4.1）
        self.btn_ref_auto = QPushButton(texts.LABELS["ref_auto"])
        sizes.set_tier(self.btn_ref_auto, "S")
        self.btn_ref_auto.clicked.connect(lambda: self.actions["ref_auto"].trigger())
        self.f_ref.add_header_widget(self.btn_ref_auto)
        top.addWidget(self.f_ref)
        div_box = ValueField("dB/div", big=True)
        self.div_field = div_box
        self.div_combo = QComboBox()
        for v in DB_PER_DIV_CHOICES:
            self.div_combo.addItem(f"{v:g} dB", v)
        self._div_key: tuple = ()
        self.div_combo.setCurrentIndex(DB_PER_DIV_CHOICES.index(DEFAULT_DIV))
        self.div_combo.activated.connect(lambda i: self._set_div(self.div_combo.itemData(i)))
        div_box.layout().replaceWidget(div_box.edit, self.div_combo)
        div_box.edit.hide()
        top.addWidget(div_box)
        # RBW・REF・dB/div の欄も上段の段（周波数の欄と同じ高さと字: J §4.3・§4.11）。幅は値が全部見える幅
        for c in (self.rbw_combo, self.div_combo):
            sizes.set_tier(c, "L")
            widget_sync.fit_combo(c)
        top.addStretch(1)
        # 連続・単発・停止は真上の行に掃引の進み具合を持つ並びに入れる（設計J §4.13）
        self.run_box = RunButtons()
        self.btn_cont = QPushButton(texts.MENU_ITEMS["run_cont"])
        self.btn_single = QPushButton(texts.MENU_ITEMS["run_single"])
        self.btn_stop = QPushButton(texts.MENU_ITEMS["stop"])
        for b in (self.btn_cont, self.btn_single, self.btn_stop):
            b.setProperty("run", True)  # 上段で最も大きいボタン（字と最小幅: J §4.3・§4.11）
            sizes.set_tier(b, "L")
            self.run_box.add_button(b)
        top.addWidget(self.run_box)
        # ボタンとメニューは同じ項目を呼ぶ（J §4.3.1）
        self.btn_cont.clicked.connect(lambda: self.actions["run_cont"].trigger())
        self.btn_single.clicked.connect(lambda: self.actions["run_single"].trigger())
        self.btn_stop.clicked.connect(lambda: self.actions["stop"].trigger())
        # TG・S11 のときだけ「基準を取る」といまの基準（J §4.2: 基準を取るは1クリックで届く所）
        self.ref_quick = RefQuick(self)
        top.addWidget(self.ref_quick)
        self.topbar.align_bottom()
        outer.addWidget(self.topbar)

        # 条件の注意（周波数オフセットで換算中・末尾の欠け・振り切れ・チューナーの中心が固定。その場で出し続ける: U-02）。
        # RBW の知らせはここに出さず、RBW の欄そのものに付ける（U-25: _apply_rbw）
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("warn")
        self.cond_label.setWordWrap(True)
        outer.addWidget(self.cond_label)

        # ---- グラフ・マーカー表・右のタブ。どれも切り離せる部分（J §4.3.2: docks.py）
        self.marker_panel = MarkerPanel(self)
        self.marker_panel.setMinimumHeight(90)

        self.trace_tab = TraceTab(self)
        self.rtl_panel = RtlPanel(self)
        self.trace_tab.add_device_section(self.rtl_panel)
        self.pluto_panel = PlutoRxPanel(self)
        self.trace_tab.add_device_section(self.pluto_panel)
        self.marker_tab = MarkerTab(self)
        self.listen = ListenControl(self)
        self.marker_tab.body.insertWidget(1, self.listen.panel)
        self._settings: Optional[SettingsDialog] = None
        self.display_tab = DisplayTab(self)
        self.source_tab = SourceTab(self)
        tabs = {"tab-trace": self.trace_tab, "tab-marker": self.marker_tab, "tab-view": self.display_tab,
                "tab-source": self.source_tab}
        # グラフの下のウォーターフォール・密度表示と再生の帯（設計P: display_control.py）
        self.display = DisplayControl(self)
        self.parts = Parts(self, self.plot, self.marker_panel, tabs, self.marker_tab.peak_group,
                           self.marker_tab.body, lower={"waterfall": self.display.waterfall,
                                                        "density": self.display.density}, bar=self.display.bar)
        self.display.wire_parts()
        self.marker_tab.pk_detach.clicked.connect(lambda: self.parts.detach("peaks"))
        outer.addWidget(self.parts.inner, 1)
        self.task_panel = TaskPanel(self)
        outer.addWidget(self.task_panel)
        self.setCentralWidget(central)
        # ドロップダウンは全部、一番長い選択肢が切れない幅にする（J §4.3）
        widget_sync.fit_all(self)
        # 並び（切り離した部分・位置・大きさ）は次の起動で戻す（J §4.3.2。画面ごとの覚え書き: O §1.1）
        self.parts.restore(self.prefs.get("layout"))
        self._add_part_menu()
        self._update_y_axis()

    def _add_part_menu(self) -> None:
        """メニュー「表示」に部分ごとの「別ウィンドウで開く」（J §4.3.2。Web のメニューと同じ並び・語・コード）。"""
        view = next((a.menu() for a in self.menuBar().actions()
                     if a.menu() is not None and a.text().startswith(texts.MENU_TITLES["view"])), None)
        if view is None:
            return
        view.addSeparator()
        for key in texts.DETACH_PARTS:
            act = view.addAction(texts.w("detach_item", part=texts.LABELS["part_" + key]))
            act.setObjectName("detach_" + key)
            act.triggered.connect(lambda _=False, k=key: self.parts.detach(k))

    def window_actions(self) -> list[QAction]:
        """窓のキーの項目（グラフだけで効くものを除く）。切り離した窓にも付けてキーを効かせる。"""
        return [a for c, a in self.actions.items() if c not in menu_table.GRAPH_ONLY]

    def show_part(self, key: str) -> None:
        self.parts.show_part(key)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.parts.first_show()

    def _build_status(self) -> None:
        self.status = StatusBar(self)
        # 試験と他の部品が使う名前（項目は texts.status_items の key）
        lab = self.status.labels
        self.st_conn, self.st_run, self.st_time, self.st_discard = lab["conn"], lab["run"], lab["time"], lab["discard"]
        self.st_cal, self.st_norm, self.st_src, self.st_rec = lab["cal"], lab["norm"], lab["src"], lab["rec"]
        self.st_tasks, self.st_listen, self.st_rtltcp = lab["tasks"], lab["listen"], lab["rtltcp"]
        self.st_note = self.status.note
        self.note_button = self.status.note_button

    def _style_axes(self) -> None:
        for ax in ("left", "bottom"):
            a = self.plot.getPlotItem().getAxis(ax)
            a.setPen(pg.mkPen(self.pal["axis"]))
            a.setTextPen(pg.mkPen(self.pal["text"]))

    def _wire_buttons(self) -> None:
        """ボタンのツールチップに、メニューと同じキーを出す（U-12）。"""
        a = self.actions
        self.btn_cont.setToolTip(menus.tooltip_for(a["run_cont"], texts.LABELS["run_cont_tip"]))
        self.btn_single.setToolTip(menus.tooltip_for(a["run_single"], texts.LABELS["run_single_tip"]))
        self.btn_stop.setToolTip(menus.tooltip_for(a["stop"], texts.LABELS["stop_tip"]))
        for code, _label, name, why in MODES:
            self.mode_buttons[code].setToolTip(
                menus.tooltip_for(a[f"mode_{code}"], name) + (f" — {why}" if why else ""))
        if hasattr(self, "marker_panel"):
            self.marker_panel.apply_polarity()  # 山／谷と探すボタンのツールチップのキー
        self._update_ref_auto()

    # ------------------------------------------------------------------ 掃引・元に戻す

    def _set_sweep(self, field: Optional[ValueField] = None, **kwargs) -> None:
        self.call("set_sweep", field=field, **kwargs)

    def _undo_redo(self, name: str) -> None:
        self.call(name, quiet_codes=("unknown_command", "nothing_to_undo", "nothing_to_redo"))

    def _toggle_run(self) -> None:
        """Pause キー: 連続 ⇔ 停止（J §4.6）。"""
        state = self.state.get("run", {}).get("state")
        if state in ("continuous", "single", "step_sweep"):
            self.actions["stop"].trigger()
        elif state == "stopped":
            self.actions["run_cont"].trigger()

    def _apply_rbw(self, st: dict) -> None:
        """RBW の選択肢は機器ごと（設計Q §6.4: FFT の機器は窓ごとにまとめる）。

        欄には実際に使う RBW を出し、いまの条件で使えない RBW は一覧に残して無効にし理由を出す（設計C §5.14・U-08）。
        """
        groups = screen_model.rbw_groups(st)
        key = tuple((g["window"], tuple((c["hz"], c["reason"]) for c in g["choices"])) for g in groups)
        pref = st["sweep"]["rbw_hz"]
        combo = self.rbw_combo

        def show() -> None:
            # 一覧を開いている間は組み替えない・選び直さない（閉じたときに最新の状態で入れる）
            if key != self._rbw_key:
                self._rbw_key = key
                was = combo.blockSignals(True)
                combo.clear()
                for g in groups:
                    if g["label"]:
                        # 窓の名前の見出し（選べない行）。説明はツールチップ
                        combo.addItem(g["label"], None)
                        disable_item(combo, combo.count() - 1, g["note"])
                    for c in g["choices"]:
                        combo.addItem(("  " if g["label"] else "") + c["text"], c["hz"])
                        if c["reason"]:
                            disable_item(combo, combo.count() - 1, c["reason"])
                        elif g["note"]:
                            combo.setItemData(combo.count() - 1, g["note"], Qt.ItemDataRole.ToolTipRole)
                combo.blockSignals(was)
                self.topbar.relayout()  # 選択肢の幅が変わると上段の幅が変わる（J §4.3.3）
            i = combo.findData(pref)
            if i >= 0 and i != combo.currentIndex():
                widget_sync.set_index(combo, i)

        widget_sync.when_idle(combo, "rbw", show)
        # いまの RBW で信用できない条件は、欄を注意の色にして文はツールチップに（U-25。Web と同じ rbw_field_view）。
        # 再生中は RBW を変えない（ライブの条件: 設計P §5。理由はツールチップの最後: rbw_field_view の reason）
        v = screen_model.rbw_field_view(st)
        theme.set_warn(combo, v["warn"])
        combo.setEnabled(v["reason"] is None)
        combo.setToolTip(v["tip"])

    def _rbw_chosen(self, i: int) -> None:
        hz = self.rbw_combo.itemData(i)
        if hz is not None:
            self._set_sweep(rbw_hz=hz, field=self.rbw_field)

    # ------------------------------------------------------------------ 縦軸（J §4.1。正本はコアの amplitude）

    def ref_level(self) -> float:
        """縦軸の一番上の値（dB 系は REF レベル、|Γ|・SWR は下端 + 10 目盛: 設計R §3）。"""
        a = self.axis()
        return a["top"] if a["linear"] else self._ref_dbm

    def db_div(self) -> float:
        """縦軸の1目盛（dB 系は dB/div、|Γ|・SWR はその単位の /div）。"""
        a = self.axis()
        return a["div"] if a["linear"] else self._db_div

    def display_unit(self) -> str:
        """縦軸の単位 = 表示している測定中のトレースの最初の単位（Web と同じ screen_s11.axis_unit）。"""
        return screen_s11.axis_unit(self.state)

    def axis(self) -> dict:
        """縦軸の決め方（dB 系か、下端の決まった |Γ|・SWR か: screen_s11.axis_view）。"""
        return screen_s11.axis_view(self.state, self.display_unit())

    def _send_amplitude(self, **kw) -> None:
        self._amp_pending += 1

        def done(r: Optional[dict] = None) -> None:
            self._amp_pending = max(0, self._amp_pending - 1)
            if self._amp_pending == 0 and r:
                self._take_amplitude(r.get("amplitude") or {})

        fut = self.api.call("set_amplitude", **kw)
        fut.add_done_callback(lambda f: self._bridge.call.emit(
            lambda: done(None if f.exception() else f.result())))

    def _take_amplitude(self, amp: dict) -> None:
        ref = amp.get("ref_level")
        div = amp.get("db_per_div")
        ref = DEFAULT_REF if ref is None else float(ref)
        div = DEFAULT_DIV if div is None else float(div)
        if (ref, div) != (self._ref_dbm, self._db_div):
            self._ref_dbm, self._db_div = ref, div
            self._update_y_axis()
        elif self._axis_shown != self._axis_key():
            self._update_y_axis()  # |Γ|・SWR の1目盛・縦軸の単位が変わった（設計R §3）

    def _axis_key(self) -> tuple:
        a = self.axis()
        return (a["linear"], a["unit"], a["top"], a["div"])

    def _set_ref(self, v: float) -> None:
        # 表示だけの値。測定値は変わらない（J §4.1）。画面はすぐ動かし、コアへ送る
        why = self.axis()["ref_reason"]
        if why:
            self.f_ref.flash_note(why)  # |Γ|・SWR の縦軸は下端が決まっていて REF を持たない（設計R §3）
            return
        self._ref_dbm = round(v, 2)
        self._update_y_axis()
        self._send_amplitude(ref_level=self._ref_dbm)

    def _set_div(self, v: float) -> None:
        # dB/div を変えても REF レベルは動かない（J §4.1）。|Γ|・SWR は1目盛だけを選ぶ（下端は決まった値: 設計R §3）
        a = self.axis()
        if a["linear"]:
            amp = self.state.setdefault("amplitude", {})
            amp[a["div_key"]] = float(v)  # すぐ描き直し、コアからの戻りで確かめる
            self._update_y_axis()
            self._send_amplitude(**{a["div_key"]: float(v)})
            return
        self._db_div = float(v)
        self._update_y_axis()
        self._send_amplitude(db_per_div=int(v))

    def _ref_auto(self) -> None:
        """表示中のトレースの最大値が上から1目盛下に来るよう REF レベルを合わせる（J §4.1・§4.8）。"""
        v = self._ref_auto_view()
        if v["level"] is None:
            self.f_ref.flash_note(v["reason"])
            return
        self._set_ref(v["level"])

    def _ref_auto_view(self) -> dict:
        """表示中のトレース（測定中の 1〜4・重ねた参照トレース）から screen_model.ref_auto_view（Web と同じ規則）。"""
        why = self.axis()["ref_reason"]
        if why:
            return {"level": None, "reason": why}
        shown = [str(n) for n in (1, 2, 3, 4) if self.traces.live_payload(n) is not None]
        shown += [tid for tid in self.traces.files if self.traces.file_drawn(tid)]
        return screen_model.ref_auto_view(self.state, shown, self.display_unit(), self._db_div,
                                          has_wave=bool(shown))

    def _update_ref_auto(self) -> None:
        why = self._ref_auto_view()["reason"]
        menus.set_reason(self.actions["ref_auto"], why)
        self.btn_ref_auto.setEnabled(why is None)
        self.btn_ref_auto.setToolTip(texts.LABELS["ref_auto_tip"] + (f" — {why}" if why else ""))

    def _on_level_wheel(self, steps: int, ctrl: bool) -> None:
        a = self.axis()
        if ctrl or a["linear"]:
            # Ctrl+ホイール: 1目盛を1段（奥へ回すと大きく）。|Γ|・SWR の縦軸は REF を持たないので、ホイールは
            # いつも1目盛を変える（下端は決まった値: 設計R §3）
            values = [c["value"] for c in a["div_choices"]]
            cur = self.db_div()
            i = values.index(cur) if cur in values else len(values) - 1
            i = min(max(i + (1 if steps > 0 else -1), 0), len(values) - 1)
            self._set_div(values[i])
        else:
            # ホイール1段で REF レベルを1目盛（奥へ回すと REF が上がる。数値の欄のホイールと同じ向き）
            self._set_ref(self._ref_dbm + steps * self._db_div)

    def _on_level_drag(self, start: bool, frac: float) -> None:
        if self.axis()["linear"]:
            return  # |Γ|・SWR の縦軸は下端が決まっていて動かさない（設計R §3）
        # つかんだ目盛がマウスに付いてくる: 下へ引くと、より上の値が見える（REF が上がる）
        if start:
            self._drag_ref0 = self._ref_dbm
        # 動かした量が1目盛の整数倍に近ければ吸い付く（Web と同じ計算: core/units.drag_ref_level）
        self._set_ref(drag_ref_level(self._drag_ref0, frac, self._db_div, DIVISIONS))

    def _update_y_axis(self) -> None:
        a = self.axis()
        self._axis_shown = self._axis_key()
        top, div = (a["top"], a["div"]) if a["linear"] else (self._ref_dbm, self._db_div)
        bottom = top - DIVISIONS * div
        pi = self.plot.getPlotItem()
        # 端ちょうどの目盛は範囲の外と見なされて数字が消えるので、わずかに広げる
        eps = div * 1e-3
        pi.setYRange(bottom - eps, top + eps, padding=0)
        # 目盛の数字は REF の欄と同じ形（0.1 dB まで・負号「−」）。単位を換えた REF の端数を出さない。
        # |Γ|・SWR は1目盛を書き分けられる桁（設計R §3）
        def tick(v: float) -> str:
            return screen_s11.tick_text(v, a["decimals"]) if a["linear"] else level_text(v)

        ticks = [(top - i * div, tick(top - i * div)) for i in range(DIVISIONS + 1)]
        pi.getAxis("left").setTicks([ticks, []])
        for line in self._hgrid:
            pi.removeItem(line)
        pen = pg.mkPen(self.pal["grid"], width=1)  # 配色を変えたら描き直す（look.apply_palette）
        self._hgrid = [pg.InfiniteLine(pos=v, angle=0, pen=pen, movable=False) for v, _ in ticks]
        for line in self._hgrid:
            line.setZValue(-10)
            pi.addItem(line, ignoreBounds=True)
        self.f_ref.set_value(top)
        self._apply_div_choices(a)
        i = self.div_combo.findData(div)
        if i >= 0:
            widget_sync.set_index(self.div_combo, i)
        fill = (self.state.get("view") or {}).get("draw_style") == "fill"
        self.traces.set_fill(fill, bottom)
        if hasattr(self, "display"):
            # 密度表示の縦はグラフと同じ範囲・同じ目盛の数字（|Γ|・SWR は下端 + 10 目盛: 設計R §3）
            self.display.apply_y(top, div, self.display_unit(), a["decimals"] if a["linear"] else 1)

    def _apply_div_choices(self, a: dict) -> None:
        """1目盛の欄の名前と選択肢（dB/div か、|Γ|・SWR の /div: 設計R §3）と、REF の欄の使えない理由。"""
        key = tuple((c["value"], c["text"]) for c in a["div_choices"])
        if key != self._div_key:
            self._div_key = key
            widget_sync.set_items(self.div_combo, [(c["text"], c["value"]) for c in a["div_choices"]])
            self.div_field.name_label.setText(a["div_label"])
            self.topbar.relayout()  # 選択肢の幅が変わると上段の幅が変わる（J §4.3.3）
        why = a["ref_reason"]
        self.f_ref.setEnabled(why is None)
        self.f_ref.setToolTip(why or "")

    def _update_unit(self) -> None:
        unit = self.display_unit()
        self.f_ref.set_unit(unit)
        self.traces.set_unit(unit)
        self.trace_tab.update_file_checks()
        n = self.state.get("normalization") or {}
        label = texts.LABELS["level"]
        if n.get("used") and not n.get("applied"):
            label = texts.LABELS["level_no_ref"]
        if screen_s11.formats.is_linear_unit(unit):
            self.plot.getPlotItem().setLabel("left", unit, units="")  # |Γ|・SWR はレベルではないので名前だけ（設計R §3）
        else:
            self.plot.getPlotItem().setLabel("left", label, units=unit)
        # 比（dB）のときは 0dB の線を目盛線と別の色で描く（E §6.6）
        self.traces.set_zero_line(unit == "dB")
        if self._axis_shown != self._axis_key():
            self._update_y_axis()  # 縦軸の単位が dB 系と |Γ|・SWR の間で変わった（設計R §3）

    def redraw_style(self) -> None:
        self._update_y_axis()

    # ------------------------------------------------------------------ 周波数（J §3。範囲の収め方はコア: 設計C §5.1・§5.11）

    def _freq_ops_reason(self) -> Optional[str]:
        # 横軸は再生中なら再生の表示の範囲（display_view.freq_view: 設計P §5）
        fv = display_view.freq_view(self.state)
        if not fv:
            return texts.REASON_TEXTS["no_sweep"]
        if fv["step_hz"] == 0:
            return texts.REASON_TEXTS["zero_span_axis"]
        return None

    def _on_range_selected(self, x0: float, x1: float) -> None:
        # ドラッグは Start と Stop を同時に触ったものとして扱う（C §5.1）
        if self._freq_ops_reason():
            return
        self._set_sweep(start_hz=int(round(x0)), stop_hz=int(round(x1)),
                        edited=["start_hz", "stop_hz"], field=self.f_start)

    def _on_freq_wheel(self, x: float, steps: int) -> None:
        """カーソルの位置の周波数を画面上の同じ位置に保って拡大・縮小する。"""
        sw = self.state.get("sweep")
        fv = display_view.freq_view(self.state)
        if self._freq_ops_reason() or not sw:
            return
        if sw.get("span_lock_hz") is not None and not fv.get("playback"):
            # Span 固定中は幅を変えない。命令を送らず、Span の欄で変えることをその場で知らせる（C §5.12）
            self.f_span.flash_note(texts.LABELS["span_lock_wheel"])
            return
        k = ZOOM_STEP ** steps
        start, stop = fv["start_hz"], fv["stop_hz"]
        new_start = x - (x - start) * k
        new_stop = x + (stop - x) * k
        span = max(int(round(new_stop - new_start)), 1)
        center = int(round(new_start)) + span // 2
        # Center と Span を同時に触った命令として送る（範囲の端では C §5.11 の規則に従う）
        self._set_sweep(center_hz=center, span_hz=span, edited=["center_hz", "span_hz"], field=self.f_span)

    def _on_context_requested(self, x: float, y: float, screen_pos: QPoint) -> None:
        self._context_menu(x, y).exec(screen_pos)

    def _context_menu(self, x: float, y: float) -> QMenu:
        """右クリックのメニュー。どれも名前の付いた操作として出す（J §3・U-12・G §3.4）。"""
        m = QMenu(self)
        m.setToolTipsVisible(True)
        why = self._freq_ops_reason()
        hz = int(round(x))
        # 換算後の周波数（設計O）。ゼロスパンは時間（設計N §1）
        shown = texts.tpl("paren", text=screen_model.x_text(self.state, x)) if self.state.get("sweep") else ""
        act = m.addAction(texts.LABELS["ctx_center"] + ("" if why else shown))
        act.setObjectName("ctx_center")
        act.triggered.connect(lambda: self._set_sweep(center_hz=hz, field=self.f_center))
        act2 = m.addAction(texts.LABELS["ctx_marker"] + shown)
        act2.setObjectName("ctx_marker")
        act2.triggered.connect(lambda: self._marker_add_at(x, y))
        act3 = m.addAction(texts.DISPLAY_TEXTS["ctx_bookmark"])
        act3.setObjectName("ctx_bookmark")
        act3.triggered.connect(lambda: self.display.add_bookmark(hz))
        for a, reason in ((act, why), (act2, self._marker_reason()), (act3, why)):
            if reason:
                a.setEnabled(False)
                a.setToolTip(reason)
        level = round(y, 1)
        act = m.addAction(texts.LABELS["ctx_ref"].replace("{level}", f"{level:.1f}").replace("{unit}", self.display_unit()))
        act.setObjectName("ctx_ref")
        act.triggered.connect(lambda: self._set_ref(level))
        ref_why = self.axis()["ref_reason"]
        if ref_why:
            # |Γ|・SWR の縦軸は REF を持たない（設計R §3）。消さずに無効にして理由（U-08）
            act.setEnabled(False)
            act.setToolTip(ref_why)
        sel = self.selected_marker
        if sel:
            m.addSeparator()
            freq_only = screen_model.zero_span_view(self.state)["freq_only"]
            for target, name in (("center", "ctx_m_center"), ("ref", "ctx_m_ref"), ("span", "ctx_m_span")):
                a = m.addAction(texts.LABELS[name].replace("{m}", sel))
                a.setObjectName(name)
                a.triggered.connect(lambda _=False, t=target: self.marker_to(sel, t))
                if freq_only and target != "ref":
                    a.setEnabled(False)  # 時間の位置は周波数の条件にできない（設計N §1）
                    a.setToolTip(freq_only)
            # マーカーの位置を聞く（設計Q §6.8）。使えないときは理由を出す（U-08）
            a = m.addAction(texts.LABELS["ctx_m_listen"].replace("{m}", sel).replace("{start}", texts.LISTEN_LABELS["start"]))
            a.setObjectName("ctx_m_listen")
            why = self.listen.start_reason()
            a.setEnabled(why is None)
            a.setToolTip(why or "")
            a.triggered.connect(self.listen.start_selected)
        return m

    # ------------------------------------------------------------------ マーカー（設計G。命令は A §4.3.1）

    def select_marker(self, mid: Optional[str]) -> None:
        self.selected_marker = mid
        self._show_markers()

    def _selected_row(self) -> Optional[dict]:
        return next((m for m in self.marker_snap.get("markers", []) if m["id"] == self.selected_marker), None)

    def _first_visible_trace(self) -> int:
        for s in self.state.get("traces") or []:
            if s.get("visible") and s.get("has_values"):
                return int(s["number"])
        return 1

    def _select_result(self, r: dict) -> None:
        if isinstance(r, dict) and r.get("id"):
            self.select_marker(r["id"])

    def _peak(self, direction: str) -> None:
        # 山／谷はマーカー表の上の切り替え（G §3.8。ピークの一覧と同じ選択）
        kw = {"direction": direction, "trace": self._first_visible_trace(), "polarity": self.marker_panel.polarity}
        if self.selected_marker:
            kw["id"] = self.selected_marker
        self.call("marker_peak", on_ok=self._select_result, **kw)

    def _marker_step(self, sign: int) -> None:
        if not self.selected_marker:
            self.status_note(texts.REASON_TEXTS["select_marker"])
            return
        shift = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.call("marker_set", id=self.selected_marker, step=sign * (10 if shift else 1))

    def _marker_reason(self) -> Optional[str]:
        """マーカーを置けない理由。ゼロスパンでも時間の位置に置ける（設計N §1）。"""
        return None if self.state.get("sweep") else texts.REASON_TEXTS["no_sweep"]

    def _marker_add_at(self, x: float, y: float) -> None:
        """その位置に M を置く。見えているトレースのうち縦に最も近いものに載せる（G §3.2）。
        向きは置いたときの山／谷の選択（G §3.8: N dB 帯域幅・追従の向き）。ゼロスパンの x は時間（μs）。"""
        if self._marker_reason():
            return
        trace = self.traces.nearest_trace(x, y) or self._first_visible_trace()
        self.call("marker_add", on_ok=self._select_result, kind="M", trace=trace, freq_hz=int(round(x)),
                  polarity=self.marker_panel.polarity)

    def _marker_add_peak(self) -> None:
        # マーカー追加: 山は最大の点・谷は一番深い点に新しい M（G §3.8）
        self.call("marker_add", on_ok=self._select_result, kind="M", trace=self._first_visible_trace(),
                  polarity=self.marker_panel.polarity)

    def _on_graph_clicked(self, x: float, y: float) -> None:
        self._marker_add_at(x, y)

    def _marker_at_mouse(self) -> None:
        vb = self.plot.getPlotItem().getViewBox()
        scene = self.plot.mapToScene(self.plot.mapFromGlobal(QCursor.pos()))
        if not vb.sceneBoundingRect().contains(scene) or self._marker_reason():
            return
        p = vb.mapSceneToView(scene)
        self._marker_add_at(p.x(), p.y())

    def marker_to(self, mid: str, target: str) -> None:
        def done(r: dict) -> None:
            if "amplitude" in r:
                self._take_amplitude(r["amplitude"])

        self.call("marker_to", on_ok=done, field=self.f_center if target == "center" else None, id=mid,
                  target=target)

    def _selected_to(self, target: str) -> None:
        if not self.selected_marker:
            self.status_note(texts.REASON_TEXTS["select_marker"])
            return
        self.marker_to(self.selected_marker, target)

    def _marker_remove(self) -> None:
        # マーカー表で子の行（機能・オフセットのカーソル・XY）を選んでいたら、その行だけを消す
        row = self.marker_panel.current_row()
        if row is not None:
            self.marker_panel.remove_row(row)
        elif self.selected_marker:
            self.call("marker_remove", id=self.selected_marker)

    def _marker_clear(self) -> None:
        n = len(self.marker_snap.get("markers", []))
        if n and not self.confirm(texts.MENU_ITEMS["marker_clear"], texts.w("marker_clear_confirm", n=n),
                                texts.LABELS["do_clear_all"]):
            return
        self.call("marker_clear")

    def _marker_copy(self) -> None:
        text = self.marker_panel.copy_tsv()
        self.status_note(texts.tpl("marker_copied", n=text.count(chr(10)) - 1))

    def _on_xy_moved(self, key: str, value: float) -> None:
        self.call("set_xy", **{key: int(round(value)) if key.endswith("_hz") else round(float(value), 2)})

    def _apply_markers(self, snap: dict) -> None:
        # 横軸の種類と、ゼロスパンの区間の最大・最小・平均も持つ（マーカー表の列と行: 設計N §1）
        self.marker_snap = {"markers": snap.get("markers", []), "xy": snap.get("xy") or {},
                            "x_kind": snap.get("x_kind") or "freq", "interval": snap.get("interval")}
        ids = {m["id"] for m in self.marker_snap["markers"]}
        if self.selected_marker not in ids:
            self.selected_marker = None
        self._show_markers()

    def _show_markers(self) -> None:
        self.marker_row_list = marker_rows.rows_from_snapshot(self.marker_snap)
        self.marker_panel.set_rows(self.marker_row_list, self.selected_marker)
        self.marker_tab.apply(self._selected_row(), self.marker_snap)
        self.marker_layer.enabled = self._marker_reason() is None
        self.marker_layer.unit = self.display_unit()  # 縦軸と単位の違うトレースのマーカーは描かない（設計R §3）
        self.marker_layer.update(self.marker_row_list, self.selected_marker, self.marker_snap["xy"])
        why = None if self.selected_marker else texts.REASON_TEXTS["select_marker"]
        for code in ("marker_left", "marker_right", "marker_ref", "marker_remove"):
            menus.set_reason(self.actions[code], why)
        # ゼロスパンの位置は時間なので、周波数の条件（Center・Start/Stop）にはできない（設計N §1）
        freq_only = screen_model.zero_span_view(self.state)["freq_only"]
        menus.set_reason(self.actions["marker_center"], why or freq_only)
        sel = self._selected_row()
        menus.set_reason(self.actions["marker_span"],
                         why or freq_only or (None if sel and sel.get("ref") else texts.REASON_TEXTS["delta_ref"]))
        if hasattr(self, "listen"):
            self.listen.refresh()
            self.rtl_panel.refresh_markers()

    # ------------------------------------------------------------------ 画像のための中身（設計I §3.2）

    def source_text(self) -> str:
        """信号源が出ていることの表示（U-05: 状態は画面が示す）。掃引している間だけ出す。"""
        return texts.source_text(self.state)

    def png_data(self) -> dict:
        return png_data.build(self)

    # ------------------------------------------------------------------ ヘルプ・機器・校正（中身は device_menu）

    def _show_key_table(self) -> None:
        device_menu.show_key_table(self)

    def _show_about(self) -> None:
        device_menu.show_about(self)

    def _connect_dialog(self) -> None:
        device_menu.open_list(self)

    def _disconnect(self) -> None:
        device_menu.disconnect(self)

    def _settings_dialog(self, page: Optional[str] = None) -> None:
        if self._settings is None:
            self._settings = SettingsDialog(self)
            self._settings.finished.connect(lambda _r: setattr(self, "_settings", None))
        if page:
            self._settings.open_page(page)
        self._settings.show()
        self._settings.raise_()

    def open_presets(self) -> None:
        if self._presets is None:
            self._presets = PresetsDialog(self)
            self._presets.finished.connect(lambda _r: setattr(self, "_presets", None))
        self._presets.show()
        self._presets.raise_()

    def save_preset(self) -> None:
        save_new(self, self._presets.reload if self._presets is not None else None)

    def reset_palette(self, base: str) -> None:
        look.reset_palette(self, base)

    def apply_keymap(self) -> None:
        look.apply_keymap(self)

    def open_calibration(self, kind: str) -> None:
        """メニュー「校正」の項目（設計K §4.1。旧校正ファイルの取り込みも同じ画面: K §4.5）。"""
        from .cal_dialog import open_calibration

        open_calibration(self, kind)

    # ------------------------------------------------------------------ イベント

    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self._apply_state(payload)
        elif kind == "trace":
            self._on_trace(payload)
        elif kind == "markers":
            self._apply_markers(payload)
        elif kind == "notice":
            self._on_notice(payload)
        elif kind == "progress":
            if payload.get("kind") == "task":
                self.task_panel.on_progress(payload)
                if self.cal_window is not None:
                    self.cal_window.on_progress(payload)
            elif payload.get("kind") == "waiting_sweep_end":
                self.status_note(texts.waiting_text(payload["remaining_s"]))
        elif kind == "sweep_progress":
            self.run_box.set_progress(screen_model.sweep_progress_view(payload))
        elif kind == "audio":
            self.listen.on_audio(payload)
        elif kind == "devices":
            device_menu.on_devices(self, payload)
        elif kind in ("waterfall", "density"):
            self.display.on_event(kind, payload)

    def _on_trace(self, payload: dict) -> None:
        t = {"trace": 1, "source": "live", **payload}
        if t["source"] == "file":
            self.traces.update_file(t)
            self.trace_tab.update_file_checks()
        else:
            self.traces.update_live(t)
            if not self._had_trace:
                self._had_trace = True
                self._update_ref_auto()
                self._update_unit()

    def _on_notice(self, n: dict) -> None:
        """知らせの出し方は texts.notice_view（Web と同じ文）。トレースの知らせはその行にも出す。"""
        code = n.get("code", "")
        if code in ("task_done", "task_failed", "task_cancelled"):
            self.files.on_task_end(code, n.get("detail") or {})
            return
        if code == "nf_guide":
            # NF モードに初めて入った: 必要な外部機器とつなぎ方を1回だけ出す（設計M）
            from .nf_dialog import open_guide

            open_guide(self)
            return
        if code == "reference_value_changed":
            self.source_tab.refresh()  # 一覧の行の基準の値（別の画面から直したときも: 設計E §6.7。Web と同じ）
        v = texts.notice_view(n, self.state)
        for num, text in v["traces"].items():
            row = self.trace_tab.rows.get(int(num)) if str(num).isdigit() else None
            if row is not None:
                row.flash(text)
        self.display_tab.on_notice(v)  # 単位が変わった（欄の下）・比の表示に変わった（窓）: J §4.10
        self.status_note(v["text"])

    def _trace_note(self, number, text: str) -> None:
        row = self.trace_tab.rows.get(number) if isinstance(number, int) else None
        if row is not None:
            row.flash(text)
        self.status_note(texts.tpl("trace_note", n=number, text=text) if number else text)

    def _show_adjustments(self, adjustments: list[dict], alternative: Optional[dict] = None) -> None:
        # Span 固定を外した知らせは Span の欄に出す（固定の切り替えは Span の欄にある: 設計C §5.13）
        fields = {"start_hz": self.f_start, "stop_hz": self.f_stop, "center_hz": self.f_center,
                  "span_hz": self.f_span, "span_lock": self.f_span, "rbw_hz": self.rbw_field,
                  "sg_hz": self.source_tab.f_sg}
        for adj in adjustments:
            w = fields.get(adj.get("field"))
            text = texts.adjustment_text(adj, texts.freq_offset_of(self.state))
            action = None
            if alternative and adj.get("reason") in texts.RULE_REASONS and w is not None:
                # 規則で動いた欄の知らせに、反対の規則の結果を「その1回だけ」のボタンで添える（C §5.11 の 3）
                action = (texts.alternative_label(alternative),
                          lambda alt=alternative, f=w: self._apply_alternative(alt, f))
            if w is not None:
                w.flash_note(text, action)
            else:
                self.status_note(f"{texts.FIELD_NAMES.get(adj.get('field'), adj.get('field'))}: {text}")

    def _apply_alternative(self, alt: dict, field: ValueField) -> None:
        """反対の規則の結果を、その1回だけの命令として送る（固定 Span は変わらない: C §5.11 の 3）。"""
        def done(result: dict) -> None:
            a = result.get("adopted") or {}
            if "center_hz" in a:
                field.flash_note(texts.alternative_done_text(a["center_hz"], a["span_hz"]))

        self.call("set_sweep", on_ok=done, field=field, start_hz=alt["start_hz"], stop_hz=alt["stop_hz"],
                  edited=["start_hz", "stop_hz"], one_shot=True)

    def _apply_state(self, st: dict) -> None:
        if st.get("version", -1) < self.state.get("version", -1):
            return
        before = (self.state.get("connection") or {}).get("state")
        self.state = st
        device_menu.on_state(self, st, before)
        sw = st["sweep"]
        look.apply_view(self, st)  # 配色・周波数オフセット・描き方（設計J §4.4・O）
        fv = display_view.freq_view(st)  # 再生中は再生の表示の範囲（設計P §5）
        self.f_center.set_hz(fv["center_hz"])
        self.f_span.set_hz(fv["span_hz"])
        self.f_start.set_hz(fv["start_hz"])
        self.f_stop.set_hz(fv["stop_hz"])
        self._apply_rbw(st)
        cond = texts.condition_lines(st)
        self.cond_label.setText("\n".join(cond))
        self.cond_label.setVisible(bool(cond))
        reasons = screen_model.mode_reasons(st)
        for code, b in self.mode_buttons.items():
            b.setChecked(code == sw["mode"])
            why = reasons.get(code)
            b.setEnabled(why is None)
            b.setToolTip(menus.tooltip_for(self.actions[f"mode_{code}"], texts.MODE_NAMES[code])
                         + (f" — {why}" if why else ""))
            menus.set_reason(self.actions[f"mode_{code}"], why)
        self.span_lock.show_lock(sw.get("span_lock_hz"), sw["span_hz"], format_freq)
        # Span 固定はライブの条件（再生中は理由付きで使えない: 設計P §5）
        lock = screen_model.sweep_reasons(st)["span_lock"]
        self.span_lock.setEnabled(lock is None)
        self.span_lock.setToolTip(texts.LABELS["span_lock_tip"] + (f" — {lock}" if lock else ""))
        if self._amp_pending == 0:
            self._take_amplitude(st.get("amplitude") or {})
        self._update_x_axis()
        self.display.apply_state(st)
        self.trace_tab.apply_state(st)
        self.rtl_panel.apply(st)
        self.pluto_panel.apply(st)
        self.listen.apply(st)
        if self._settings is not None:
            self._settings.apply(st)
        if self.cal_window is not None:
            self.cal_window.apply_state(st)
        self.source_tab.apply_state(st)
        self.ref_quick.apply(st)
        for n in self.traces.apply_trace_states(st.get("traces") or []):
            self._request_trace(n)
        for tid in self.traces.apply_files(st.get("files") or []):
            self._request_trace(tid)
        # 基準の範囲外の帯（設計E §6.4。Web の画面・PNG と同じ区間: report_layout.norm_out_of_range）
        self.traces.set_bands(norm_out_of_range(st))
        self.mark_bands.set_marks(screen_model.marks_view(st))
        self._update_unit()
        self._update_ref_auto()
        self.marker_tab.apply_peaks()  # ピークの一覧の基準（ノイズフロア・最大）は表示の量で変わる（P §9.7）
        if "markers" in st:
            self._apply_markers(st["markers"])
        self.task_panel.apply_tasks(st.get("tasks") or [])
        self._apply_files_menu(st)
        self._apply_status(st)

    def _request_trace(self, tid) -> None:
        """値があるのに描けていないトレース（途中から繋いだ・表示に戻した）を取りに行く。1回だけ。"""
        key = (tid, self.state.get("run", {}).get("sweep_count"))
        if key in self._requested:
            return
        self._requested.add(key)
        self.call("get_trace", on_ok=self._on_trace, quiet_codes=("no_trace_data", "unknown_trace", "bad_trace"),
                  id=tid)

    def _apply_files_menu(self, st: dict) -> None:
        rec = st.get("recording") or {}
        has = bool(rec.get("active") or rec.get("stopped"))
        a = self.actions
        menus.set_reason(a["record_start"], texts.ERRORS["already_recording"] if has else None)
        menus.set_reason(a["record_stop"], None if rec.get("active") else texts.ERRORS["not_recording"])
        menus.set_reason(a["record_finish"], None if has else texts.ERRORS["not_recording"])
        has_data = any(s.get("has_values") for s in st.get("traces") or [])
        menus.set_reason(a["save"], None if has_data else texts.REASON_TEXTS["no_data_to_save"])

    def _apply_status(self, st: dict) -> None:
        self.status.apply(st)
        run = st["run"]
        conn = st["connection"]
        self.btn_cont.setProperty("running", run["state"] == "continuous")
        self.btn_single.setProperty("running", run["state"] == "single")
        for b in (self.btn_cont, self.btn_single):
            b.style().unpolish(b)
            b.style().polish(b)
        # 連続・単発は未接続・再生中は使えない（理由をツールチップに: U-08・設計P §9.4）
        why = screen_model.run_reason(st)
        for b, tip, code in ((self.btn_cont, "run_cont_tip", "run_cont"), (self.btn_single, "run_single_tip",
                                                                            "run_single")):
            b.setEnabled(why is None)
            b.setToolTip(menus.tooltip_for(self.actions[code], texts.LABELS[tip]) + (f" — {why}" if why else ""))
            menus.set_reason(self.actions[code], why)
        self.btn_stop.setEnabled(screen_model.can_stop(st))  # ステップ掃引の間も「停止」でやめる（Web と同じ）

    def _update_x_axis(self) -> None:
        # 横軸は x_axis_view（周波数・ゼロスパンの時間。再生中は再生の表示の範囲: 設計N §1・設計P §5）
        apply_x_axis(self)

    # ------------------------------------------------------------------ 知らせ・ドラッグ＆ドロップ・閉じる

    def status_note(self, text: str, folder: Optional[Path] = None) -> None:
        """一度きりの知らせ（U-05）。保存したものなら、そこから保存先を開ける（U-11・I §3.4）。"""
        self.status.show_note(text, folder)

    _status_note = status_note

    @staticmethod
    def _dropped_paths(mime) -> list[str]:
        if not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(".h5")]

    def dragEnterEvent(self, e) -> None:
        if self._dropped_paths(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e) -> None:
        paths = self._dropped_paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.files.open_files(paths)

    def closeEvent(self, e) -> None:
        # 処理中・記録中に閉じるときは、何が失われるかを言って聞く（H §4.1・U-18）
        lost = []
        for t in self.state.get("tasks") or []:
            kind = t.get("kind")
            lost.append(texts.w("close_lost_task", task=texts.TASK_NAMES.get(kind, kind),
                                what=texts.TASK_CANCELLED.get(kind, "")))
        rec = self.state.get("recording") or {}
        if rec.get("active") or rec.get("stopped"):
            lost.append(texts.LABELS["close_lost_record"])
        from .cal_view import main_close_line

        cal_line = main_close_line(self.state)  # 校正の画面の保存していない作業（設計K §4.1・U-18）
        if cal_line:
            lost.append(cal_line)
        if lost and not self.confirm(texts.LABELS["close_title"], texts.LABELS["close_confirm"] + "\n" + "\n".join(lost),
                                    texts.LABELS["do_close"]):
            e.ignore()
            return
        self.listen.close()
        self.web.shutdown()
        self.prefs.set("layout", self.parts.save())
        self.parts.close_windows()
        self._unsub()
        super().closeEvent(e)
