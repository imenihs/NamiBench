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

import math
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api.local import LocalApi
from ..core.units import format_freq
from . import device_menu, marker_rows, menus, png_data, texts
from .fields import DbField, FreqField, SpanLockButton, ValueField
from .files import FileActions
from .graph import make_plot
from .marker_table import MarkerPanel
from .plot_layers import MarkerLayer, TraceLayer
from .prefs import UiPrefs
from .tab_display import DisplayTab
from .tab_markers import MarkerTab
from .tab_source import SourceTab
from .tab_traces import TraceTab
from .tasks import TaskPanel
from .theme import DARK, stylesheet
from .topbar import ConnectDialog, TopBar

DB_PER_DIV_CHOICES = (1.0, 2.0, 5.0, 10.0)
DIVISIONS = 10
DEFAULT_REF = 0.0
DEFAULT_DIV = 10.0
# 周波数軸の上のホイール1段で Span を何倍にするか（奥へ回すと拡大 = Span を狭く）
ZOOM_STEP = 0.8
# マーカー表に使う高さの既定（グラフとの分割。境目を動かすまではこの割合を保つ）
TABLE_RATIO = 0.24

# モード（コード・ボタンの文字・名前・使えない理由）。並びが Ctrl+1〜5 の順（J §4.6）
MODES = (
    ("sa", "SA", "スペクトラムアナライザ", None),
    ("tg", "TG", "伝送特性（TG 出力を被測定物へ）", None),
    ("s11", "S11", "反射", "反射（方向性結合器）は次の回で実装します"),
    ("sg", "SG", "信号源", None),
    ("nf", "NF", "NF 測定", "NF 測定は次の回で実装します"),
)
MODE_NAMES = {code: name for code, _l, name, _w in MODES}

__all__ = ["MainWindow", "ConnectDialog", "DARK"]


class _Bridge(QObject):
    """制御スレッドから画面スレッドへ渡し直す（QueuedConnection）。"""

    event = Signal(str, object)
    call = Signal(object)


class MainWindow(QMainWindow):
    MODE_NAMES = MODE_NAMES

    def __init__(self, api: LocalApi, prefs: Optional[UiPrefs] = None, check_unfinished: bool = True) -> None:
        super().__init__()
        self.api = api
        self.prefs = prefs or UiPrefs()
        self.pal = DARK
        self.setWindowTitle("pyGigaSt")
        self.state: dict = {}
        self._ref_dbm = DEFAULT_REF
        self._db_div = DEFAULT_DIV
        self._amp_pending = 0
        self._drag_ref0 = 0.0
        self.selected_marker: Optional[str] = None
        self.marker_snap: dict = {"markers": [], "xy": {}}
        self.marker_row_list: list[dict] = []
        self._requested: set = set()
        self._had_trace = False
        self._note_folder: Optional[Path] = None
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._bridge.call.connect(lambda fn: fn(), Qt.ConnectionType.QueuedConnection)
        self.files = FileActions(self)

        self._build_plot()
        self._build_menu()
        self._build_body()
        self._wire_buttons()
        self._build_status()
        self.setStyleSheet(stylesheet(self.pal))
        self._style_axes()
        self.setAcceptDrops(True)

        self._unsub = api.subscribe(lambda kind, payload: self._bridge.event.emit(kind, payload))
        self.call("get_state", on_ok=self._apply_state)
        # 2回目からは、覚えた機器が見つかれば自動で繋ぐ（設計B §7.3）
        self.call("connect_last", on_ok=lambda r: None)
        if check_unfinished:
            # 起動時にまとめ終わっていない記録を示して聞く（H §4）
            self.files.check_unfinished()

    # ------------------------------------------------------------------ 命令を呼ぶ

    def call(self, name: str, /, on_ok: Optional[Callable[[dict], None]] = None,
             field: Optional[ValueField] = None, quiet_codes: tuple[str, ...] = (), **kwargs) -> None:
        """命令を呼ぶ。quiet_codes の失敗は何も出さない（コアにまだ無い命令など）。"""
        fut = self.api.call(name, **kwargs)

        def done(f) -> None:
            def in_gui() -> None:
                exc = f.exception()
                if exc is not None:
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

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def ask_text(self, title: str, label: str, text: str = "") -> Optional[str]:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value if ok else None

    def ask_open_paths(self, title: str, folder: str, filt: str) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(self, title, folder, filt)
        return paths

    def ask_save_path(self, title: str, default: str, filt: str) -> Optional[str]:
        path, _ = QFileDialog.getSaveFileName(self, title, default, filt)
        return path or None

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
        pi.getAxis("bottom").setToolTip(
            "ホイール: カーソルの位置を中心に拡大・縮小\n"
            "グラフの中を横にドラッグ: その範囲を Start/Stop に\n"
            "グラフの中をクリック: 近いトレースに M を置く\n"
            "右クリック: この位置への操作（Center に・マーカー・REF に）")
        pi.getAxis("left").setToolTip(
            "ホイール: REF レベルを1目盛ずつ\n"
            "Ctrl+ホイール: dB/div を1段\n"
            "ドラッグ: REF レベルを連続で動かす")
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        # 横の目盛線は自分で引く。軸の部品に描かせると、軸の外にはみ出す一番上の数字（REF）が切られる
        pi.showGrid(x=True, y=False, alpha=0.35)
        pi.layout.setContentsMargins(0, 10, 8, 0)
        self._hgrid: list[pg.InfiniteLine] = []
        pi.setLabel("bottom", "周波数", units="Hz")
        pi.setLabel("left", "レベル", units="dBm")
        left = pi.getAxis("left")
        left.enableAutoSIPrefix(False)
        # REF レベルは最も読みたい値なので、軸の端でも数字を隠さない（J §4.3）
        left.setStyle(hideOverlappingLabels=False)
        self.traces = TraceLayer(self.plot, self.pal)
        self.marker_layer = MarkerLayer(self.plot, self.pal, self._on_xy_moved)

    def _build_menu(self) -> None:
        """メニューバー（J §4.3.1）とキー（J §4.6）。上段・タブ・表のボタンも同じ項目を呼ぶ。"""
        I = menus.Item
        mode_items = [
            I(f"mode_{code}", f"{label}（{name}）", (f"Ctrl+{i + 1}",),
              (lambda c=code: self.call("set_mode", mode=c)) if why is None else None, why)
            for i, (code, label, name, why) in enumerate(MODES)
        ]
        f = self.files
        table = [
            ("ファイル(&F)", [
                I("open", "開く…", ("Ctrl+O",), lambda: f.open_files()),
                I("save", "保存…", ("Ctrl+S",), f.save),
                I("export", "書き出し（CSV）…", ("Ctrl+E",), f.export),
                None,
                I("png_save", "PNG を保存", ("Ctrl+P",), f.png_save),
                I("png_copy", "PNG をクリップボードへ", ("Ctrl+Shift+C",), f.png_copy),
                None,
                I("record_start", "記録を開始", (), f.record_start),
                I("record_stop", "記録を止める", (), f.record_stop),
                I("record_finish", "記録をまとめる…", (), f.record_finish),
                None,
                I("quit", "終了", ("Ctrl+Q",), self.close),
            ]),
            ("編集(&E)", [
                I("undo", "条件の変更を戻す", ("Ctrl+Z",), lambda: self._undo_redo("undo")),
                I("redo", "条件の変更をやり直す", ("Ctrl+Y",), lambda: self._undo_redo("redo")),
            ]),
            ("機器(&D)", [
                I("connect", "接続…", (), self._connect_dialog),
                I("connect_sim", "シミュレータに接続", (),
                  lambda: self.call("connect", backend="simulator", serial="SIM00001")),
                I("disconnect", "切断", (), lambda: self.call("disconnect")),
                None,
                I("self_test", "自己診断の結果", (), None, menus.NOT_YET_DIAG),
            ]),
            ("掃引(&S)", [
                I("run_cont", "連続", ("F5",), lambda: self.call("run", mode="continuous")),
                I("run_single", "単発", ("F6",), lambda: self.call("run", mode="single")),
                I("stop", "停止", ("F7",), lambda: self.call("stop")),
                I("pause", "連続 ⇔ 停止", ("Pause",), self._toggle_run),
            ]),
            ("モード(&M)", mode_items),
            ("振幅(&A)", [
                I("ref_auto", "REF レベルを波形に合わせる（自動）", (), self._ref_auto),
            ]),
            ("マーカー(&K)", [
                I("marker_peak", "選んでいるマーカーをピークへ", ("P",), lambda: self._peak("highest"), None, True),
                I("marker_peak_left", "次のピーク（左）", ("[",), lambda: self._peak("left"), None, True),
                I("marker_peak_right", "次のピーク（右）", ("]",), lambda: self._peak("right"), None, True),
                I("marker_peak_lower", "次に高いピーク", (), lambda: self._peak("lower")),
                I("marker_left", "マーカーを左へ1点（Shift で10点）", ("Left", "Shift+Left"),
                  lambda: self._marker_step(-1), None, True),
                I("marker_right", "マーカーを右へ1点（Shift で10点）", ("Right", "Shift+Right"),
                  lambda: self._marker_step(+1), None, True),
                I("marker_here", "マウスの位置に M を置く", ("M",), self._marker_at_mouse, None, True),
                I("marker_add", "マーカーを追加（ピークに M）", (), self._marker_add_peak),
                None,
                I("marker_center", "マーカーの周波数を Center に", (), lambda: self._selected_to("center")),
                I("marker_ref", "マーカーの値を REF レベルに", (), lambda: self._selected_to("ref")),
                I("marker_span", "デルタの2点を Start・Stop に", (), lambda: self._selected_to("span")),
                None,
                I("marker_remove", "選んでいるマーカーを消す", (), self._marker_remove),
                I("marker_clear", "マーカーを全部消す", (), self._marker_clear),
                I("marker_copy", "マーカー表をコピー（タブ区切り）", (), self._marker_copy),
            ]),
            ("校正(&C)", [
                I("cal_freq", "周波数特性…", (), None, menus.NOT_YET_CAL),
                I("cal_rbw", "RBW…", (), None, menus.NOT_YET_CAL),
                I("cal_nf", "NF の増幅度…", (), None, menus.NOT_YET_CAL),
                None,
                I("cal_legacy", "旧校正ファイルを読み込む…", (), self._import_legacy),
            ]),
            ("表示(&V)", [
                I("view_colors", "配色…", (), None, "配色の編集は次の回で実装します（設計J §4.4）"),
                I("view_waterfall", "ウォーターフォール", (), None, "ウォーターフォールは次の回で実装します（設計P §3）"),
                I("view_density", "密度表示", (), None, "密度表示は次の回で実装します（設計P §4）"),
            ]),
            ("設定(&O)", [
                I("settings", "アプリ設定…", (), None, menus.NOT_YET_SETTINGS),
                I("settings_keys", "キー操作…", (), None, menus.NOT_YET_SETTINGS),
            ]),
            ("ヘルプ(&H)", [
                I("help_keys", "キー操作の一覧", (), self._show_key_table),
                I("help_about", "pyGigaSt について", (), self._show_about),
            ]),
        ]
        self.actions = menus.build(self, self.plot, table)

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
            b.setMinimumHeight(40)
            font = QFont(b.font())
            font.setPointSize(12)
            b.setFont(font)
            b.clicked.connect(lambda _=False, c=code: self.actions[f"mode_{c}"].trigger())
            self.mode_group.addButton(b)
            self.mode_buttons[code] = b
            top.addWidget(b)
        top.addSpacing(8)
        self.f_center = FreqField("Center", lambda hz: self._set_sweep(center_hz=hz, field=self.f_center))
        self.f_span = FreqField("Span", lambda hz: self._set_sweep(span_hz=hz, field=self.f_span))
        self.f_start = FreqField("Start", lambda hz: self._set_sweep(start_hz=hz, field=self.f_start))
        self.f_stop = FreqField("Stop", lambda hz: self._set_sweep(stop_hz=hz, field=self.f_stop))
        # Span 固定の切り替え（設計C §5.11 の 2）。Span の欄に付けて、状態を欄の上で見せる
        self.span_lock = SpanLockButton(self.pal["accent"], self.pal["text_dim"])
        self.span_lock.setToolTip(
            "ON: Center を変えても Span を保つ（範囲を超える分は Center が動く）\n"
            "OFF: Center を保ち、範囲を超える分だけ Span を縮める")
        self.span_lock.clicked.connect(lambda on: self._set_sweep(span_lock=bool(on), field=self.f_span))
        self.f_span.add_header_widget(self.span_lock)
        for w in (self.f_center, self.f_span, self.f_start, self.f_stop):
            top.addWidget(w)
        top = self.topbar.right
        rbw_box = ValueField("RBW", big=False)
        self.rbw_combo = QComboBox()
        for hz in (180000, 30000, 15000):
            self.rbw_combo.addItem(format_freq(hz), hz)
        self.rbw_combo.activated.connect(
            lambda i: self._set_sweep(rbw_hz=self.rbw_combo.itemData(i), field=self.rbw_field))
        rbw_box.layout().replaceWidget(rbw_box.edit, self.rbw_combo)
        rbw_box.edit.hide()
        self.rbw_field = rbw_box
        top.addWidget(rbw_box)
        self.f_ref = DbField("REF レベル", "dBm", self._set_ref, big=False)
        # いまの波形の最大値が上から1目盛下に来るよう REF を合わせる（J §4.1）
        self.btn_ref_auto = QPushButton("自動")
        self.btn_ref_auto.setObjectName("small")
        self.btn_ref_auto.clicked.connect(lambda: self.actions["ref_auto"].trigger())
        self.f_ref.add_header_widget(self.btn_ref_auto)
        top.addWidget(self.f_ref)
        div_box = ValueField("dB/div", big=False)
        self.div_combo = QComboBox()
        for v in DB_PER_DIV_CHOICES:
            self.div_combo.addItem(f"{v:g} dB", v)
        self.div_combo.setCurrentIndex(DB_PER_DIV_CHOICES.index(DEFAULT_DIV))
        self.div_combo.activated.connect(lambda i: self._set_div(self.div_combo.itemData(i)))
        div_box.layout().replaceWidget(div_box.edit, self.div_combo)
        div_box.edit.hide()
        top.addWidget(div_box)
        top.addStretch(1)
        self.btn_cont = QPushButton("連続")
        self.btn_single = QPushButton("単発")
        self.btn_stop = QPushButton("停止")
        for b in (self.btn_cont, self.btn_single, self.btn_stop):
            b.setMinimumSize(78, 44)
            font = QFont(b.font())
            font.setPointSize(13)
            b.setFont(font)
            top.addWidget(b)
        # ボタンとメニューは同じ項目を呼ぶ（J §4.3.1）
        self.btn_cont.clicked.connect(lambda: self.actions["run_cont"].trigger())
        self.btn_single.clicked.connect(lambda: self.actions["run_single"].trigger())
        self.btn_stop.clicked.connect(lambda: self.actions["stop"].trigger())
        outer.addWidget(self.topbar)

        # 使っている RBW が好みと違う理由・信用できない条件（その場で出し続ける: U-02・U-08）
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("warn")
        self.cond_label.setWordWrap(True)
        outer.addWidget(self.cond_label)

        # ---- グラフとマーカー表（縦の分割。境目をドラッグできる: J §4.3.2）と右のタブ
        self.marker_panel = MarkerPanel(self)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)
        self.vsplit.addWidget(self.plot)
        self.vsplit.addWidget(self.marker_panel)
        self.vsplit.setCollapsible(0, False)
        self.vsplit.setCollapsible(1, False)
        self.vsplit.setStretchFactor(0, 4)
        self.vsplit.setStretchFactor(1, 1)
        self.marker_panel.setMinimumHeight(90)

        self.trace_tab = TraceTab(self)
        self.marker_tab = MarkerTab(self)
        self.display_tab = DisplayTab(self)
        self.source_tab = SourceTab(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.trace_tab, "トレース・掃引")
        self.tabs.addTab(self.marker_tab, "マーカー")
        self.tabs.addTab(self.display_tab, "表示")
        self.tabs.addTab(self.source_tab, "信号源")
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.hsplit = QSplitter(Qt.Orientation.Horizontal)
        self.hsplit.addWidget(self.vsplit)
        self.hsplit.addWidget(self.tabs)
        self.hsplit.setStretchFactor(0, 4)
        self.hsplit.setStretchFactor(1, 1)
        self.hsplit.setCollapsible(0, False)
        outer.addWidget(self.hsplit, 1)
        self.task_panel = TaskPanel(self)
        outer.addWidget(self.task_panel)
        self.setCentralWidget(central)
        self._restore_splitters()
        self._update_y_axis()

    def _restore_splitters(self) -> None:
        # 分割比は次の起動で戻す（J §4.3.2。画面ごとの覚え書き: O §1.1）
        self._split_user = False
        for key, sp in (("vsplit", self.vsplit), ("hsplit", self.hsplit)):
            sizes = self.prefs.get(key)
            if sizes:
                try:
                    sp.setSizes([int(x) for x in sizes])
                    self._split_user = self._split_user or key == "vsplit"
                except (TypeError, ValueError):
                    pass
        # 利用者が境目を動かすまでは、窓の大きさが変わっても表の高さの割合を保つ
        self.vsplit.splitterMoved.connect(lambda *_: setattr(self, "_split_user", True))
        self.vsplit.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.vsplit and ev.type() == QEvent.Type.Resize and not self._split_user:
            h = ev.size().height()
            table = max(self.marker_panel.minimumHeight(), int(h * TABLE_RATIO))
            self.vsplit.setSizes([max(1, h - table), table])
        return super().eventFilter(obj, ev)

    def _build_status(self) -> None:
        sb = self.statusBar()
        self.st_conn = QLabel()
        self.st_run = QLabel()
        self.st_time = QLabel()
        self.st_discard = QLabel()
        self.st_cal = QLabel()
        self.st_norm = QLabel()
        self.st_src = QLabel()
        self.st_src.setObjectName("warn")
        self.st_rec = QLabel()
        self.st_rec.setObjectName("warn")
        self.st_tasks = QLabel()
        self.st_note = QLabel()
        self.note_button = QPushButton("フォルダを開く")
        self.note_button.setObjectName("small")
        self.note_button.hide()
        self.note_button.clicked.connect(self._open_note_folder)
        for lab in (self.st_conn, self.st_run, self.st_time, self.st_discard, self.st_cal, self.st_norm,
                    self.st_src, self.st_rec, self.st_tasks):
            sb.addWidget(lab)
        sb.addWidget(self.st_note, 1)
        sb.addWidget(self.note_button)

    def _style_axes(self) -> None:
        for ax in ("left", "bottom"):
            a = self.plot.getPlotItem().getAxis(ax)
            a.setPen(pg.mkPen(self.pal["axis"]))
            a.setTextPen(pg.mkPen(self.pal["text"]))

    def _wire_buttons(self) -> None:
        """ボタンのツールチップに、メニューと同じキーを出す（U-12）。"""
        a = self.actions
        self.btn_cont.setToolTip(menus.tooltip_for(a["run_cont"], "連続で掃引する"))
        self.btn_single.setToolTip(menus.tooltip_for(a["run_single"], "単発で掃引する"))
        self.btn_stop.setToolTip(menus.tooltip_for(a["stop"], "いまの掃引の終わりで止める"))
        for code, _label, name, why in MODES:
            self.mode_buttons[code].setToolTip(
                menus.tooltip_for(a[f"mode_{code}"], name) + (f" — {why}" if why else ""))
        self._update_ref_auto()

    # ------------------------------------------------------------------ 掃引・元に戻す

    def _set_sweep(self, field: Optional[ValueField] = None, **kwargs) -> None:
        self.call("set_sweep", field=field, **kwargs)

    def _undo_redo(self, name: str) -> None:
        self.call(name, quiet_codes=("unknown_command", "nothing_to_undo", "nothing_to_redo"))

    def _toggle_run(self) -> None:
        """Pause キー: 連続 ⇔ 停止（J §4.6）。"""
        state = self.state.get("run", {}).get("state")
        if state in ("continuous", "single"):
            self.actions["stop"].trigger()
        elif state == "stopped":
            self.actions["run_cont"].trigger()

    # ------------------------------------------------------------------ 縦軸（J §4.1。正本はコアの amplitude）

    def ref_level(self) -> float:
        return self._ref_dbm

    def db_div(self) -> float:
        return self._db_div

    def display_unit(self) -> str:
        """縦軸の単位。コアの amplitude.unit が決まっていればそれ、無ければ見えているトレースの単位。"""
        amp = self.state.get("amplitude") or {}
        if amp.get("unit") in ("dBm", "dB"):
            return amp["unit"]
        p = self.traces.any_live()
        if p is not None and p.get("unit"):
            return p["unit"]
        for s in self.state.get("traces") or []:
            if s.get("visible") and s.get("unit"):
                return s["unit"]
        return "dBm"

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

    def _set_ref(self, v: float) -> None:
        # 表示だけの値。測定値は変わらない（J §4.1）。画面はすぐ動かし、コアへ送る
        self._ref_dbm = round(v, 2)
        self._update_y_axis()
        self._send_amplitude(ref_level=self._ref_dbm)

    def _set_div(self, v: float) -> None:
        # dB/div を変えても REF レベルは動かない（J §4.1）
        self._db_div = float(v)
        self._update_y_axis()
        self._send_amplitude(db_per_div=int(v))

    def _ref_auto(self) -> None:
        """いまの波形の最大値が上から1目盛下に来るよう REF レベルを合わせる。"""
        peak = self._trace_max()
        if peak is None:
            self.f_ref.flash_note("合わせる波形がまだありません")
            return
        # 表示の桁（0.1dB）へ切り上げる。切り捨てると最大値が1目盛の線をわずかに超える
        self._set_ref(math.ceil((peak + self._db_div) * 10 - 1e-9) / 10)

    def _trace_max(self) -> Optional[float]:
        best = None
        stop = self.state.get("sweep", {}).get("stop_hz")
        for n in (1, 2, 3, 4):
            t = self.traces.live_payload(n)
            if not t:
                continue
            y = np.asarray(t["values"], dtype=np.float64)
            ax = t["axis"]
            if ax.get("step_hz", 0) > 0 and stop is not None:
                # Stop の先に出た最後の点（C §5.3）は表示の外なので数えない
                x = ax["start_hz"] + ax["step_hz"] * np.arange(ax["points"], dtype=np.int64)
                y = y[x <= stop]
            y = y[np.isfinite(y)]
            if y.size:
                best = float(y.max()) if best is None else max(best, float(y.max()))
        return best

    def _update_ref_auto(self) -> None:
        why = None if self.traces.any_live() is not None else "波形がまだありません（掃引すると使えます）"
        menus.set_reason(self.actions["ref_auto"], why)
        self.btn_ref_auto.setEnabled(why is None)
        self.btn_ref_auto.setToolTip(
            "いまの波形の最大値が上から1目盛下に来るよう REF レベルを合わせる" + (f" — {why}" if why else ""))

    def _on_level_wheel(self, steps: int, ctrl: bool) -> None:
        if ctrl:
            # Ctrl+ホイール: dB/div を1段（奥へ回すと大きく）
            i = DB_PER_DIV_CHOICES.index(self._db_div) if self._db_div in DB_PER_DIV_CHOICES else 3
            i = min(max(i + (1 if steps > 0 else -1), 0), len(DB_PER_DIV_CHOICES) - 1)
            self._set_div(DB_PER_DIV_CHOICES[i])
        else:
            # ホイール1段で REF レベルを1目盛（奥へ回すと REF が上がる。数値の欄のホイールと同じ向き）
            self._set_ref(self._ref_dbm + steps * self._db_div)

    def _on_level_drag(self, start: bool, frac: float) -> None:
        # つかんだ目盛がマウスに付いてくる: 下へ引くと、より上の値が見える（REF が上がる）
        if start:
            self._drag_ref0 = self._ref_dbm
        self._set_ref(self._drag_ref0 + frac * DIVISIONS * self._db_div)

    def _update_y_axis(self) -> None:
        top = self._ref_dbm
        bottom = top - DIVISIONS * self._db_div
        pi = self.plot.getPlotItem()
        # 端ちょうどの目盛は範囲の外と見なされて数字が消えるので、わずかに広げる
        eps = self._db_div * 1e-3
        pi.setYRange(bottom - eps, top + eps, padding=0)
        ticks = [(top - i * self._db_div, f"{top - i * self._db_div:g}") for i in range(DIVISIONS + 1)]
        pi.getAxis("left").setTicks([ticks, []])
        for line in self._hgrid:
            pi.removeItem(line)
        pen = pg.mkPen(self.pal["grid"], width=1)
        self._hgrid = [pg.InfiniteLine(pos=v, angle=0, pen=pen, movable=False) for v, _ in ticks]
        for line in self._hgrid:
            line.setZValue(-10)
            pi.addItem(line, ignoreBounds=True)
        self.f_ref.set_value(self._ref_dbm)
        i = self.div_combo.findData(self._db_div)
        if i >= 0 and i != self.div_combo.currentIndex():
            self.div_combo.setCurrentIndex(i)
        self.traces.set_fill(self.display_tab.fill_on() if hasattr(self, "display_tab") else False, bottom)

    def _update_unit(self) -> None:
        unit = self.display_unit()
        self.f_ref.set_unit(unit)
        self.traces.set_unit(unit)
        self.trace_tab.update_file_checks()
        n = self.state.get("normalization") or {}
        label = "レベル"
        if n.get("used") and not n.get("applied"):
            label = "レベル（基準なし・受けた電力）"
        self.plot.getPlotItem().setLabel("left", label, units=unit)
        # 比（dB）のときは 0dB の線を目盛線と別の色で描く（E §6.6）
        self.traces.set_zero_line(unit == "dB")

    def redraw_style(self) -> None:
        self._update_y_axis()

    # ------------------------------------------------------------------ 周波数（J §3。範囲の収め方はコア: 設計C §5.1・§5.11）

    def _freq_ops_reason(self) -> Optional[str]:
        sw = self.state.get("sweep")
        if not sw:
            return "掃引条件がまだありません"
        if sw["step_hz"] == 0:
            return "ゼロスパンの横軸は周波数ではありません"
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
        if self._freq_ops_reason() or not sw:
            return
        k = ZOOM_STEP ** steps
        start, stop = sw["start_hz"], sw["stop_hz"]
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
        act = m.addAction("ここを Center に" + ("" if why else f"（{format_freq(hz)}）"))
        act.setObjectName("ctx_center")
        act.triggered.connect(lambda: self._set_sweep(center_hz=hz, field=self.f_center))
        act2 = m.addAction("ここにマーカー" + ("" if why else f"（{format_freq(hz)}）"))
        act2.setObjectName("ctx_marker")
        act2.triggered.connect(lambda: self._marker_add_at(x, y))
        for a in (act, act2):
            if why:
                a.setEnabled(False)
                a.setToolTip(why)
        level = round(y, 1)
        act = m.addAction(f"REF をここに（{level:.1f} {self.display_unit()}）")
        act.setObjectName("ctx_ref")
        act.triggered.connect(lambda: self._set_ref(level))
        sel = self.selected_marker
        if sel:
            m.addSeparator()
            for text, target, name in ((f"{sel} の周波数を Center に", "center", "ctx_m_center"),
                                       (f"{sel} の値を REF レベルに", "ref", "ctx_m_ref"),
                                       (f"{sel} とデルタの基準を Start・Stop に", "span", "ctx_m_span")):
                a = m.addAction(text)
                a.setObjectName(name)
                a.triggered.connect(lambda _=False, t=target: self.marker_to(sel, t))
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
        kw = {"direction": direction, "trace": self._first_visible_trace()}
        if self.selected_marker:
            kw["id"] = self.selected_marker
        self.call("marker_peak", on_ok=self._select_result, **kw)

    def _marker_step(self, sign: int) -> None:
        if not self.selected_marker:
            self.status_note("動かすマーカーを選んでください（表の行・ピークのボタン）")
            return
        shift = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.call("marker_set", id=self.selected_marker, step=sign * (10 if shift else 1))

    def _marker_add_at(self, x: float, y: float) -> None:
        """その位置に M を置く。見えているトレースのうち縦に最も近いものに載せる（G §3.2）。"""
        if self._freq_ops_reason():
            return
        trace = self.traces.nearest_trace(x, y) or self._first_visible_trace()
        self.call("marker_add", on_ok=self._select_result, kind="M", trace=trace, freq_hz=int(round(x)))

    def _marker_add_peak(self) -> None:
        self.call("marker_add", on_ok=self._select_result, kind="M", trace=self._first_visible_trace())

    def _on_graph_clicked(self, x: float, y: float) -> None:
        self._marker_add_at(x, y)

    def _marker_at_mouse(self) -> None:
        vb = self.plot.getPlotItem().getViewBox()
        scene = self.plot.mapToScene(self.plot.mapFromGlobal(QCursor.pos()))
        if not vb.sceneBoundingRect().contains(scene) or self._freq_ops_reason():
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
            self.status_note("マーカーを選んでください")
            return
        self.marker_to(self.selected_marker, target)

    def _marker_remove(self) -> None:
        if self.selected_marker:
            self.call("marker_remove", id=self.selected_marker)

    def _marker_clear(self) -> None:
        n = len(self.marker_snap.get("markers", []))
        if n and not self.confirm("マーカーを全部消す", f"マーカー {n} 本（オフセット・機能の設定を含む）を消します。"):
            return
        self.call("marker_clear")

    def _marker_copy(self) -> None:
        text = self.marker_panel.copy_tsv()
        self.status_note(f"マーカー表をコピーしました（{text.count(chr(10)) - 1} 行）")

    def _on_xy_moved(self, key: str, value: float) -> None:
        self.call("set_xy", **{key: int(round(value)) if key.endswith("_hz") else round(float(value), 2)})

    def _apply_markers(self, snap: dict) -> None:
        self.marker_snap = {"markers": snap.get("markers", []), "xy": snap.get("xy") or {}}
        ids = {m["id"] for m in self.marker_snap["markers"]}
        if self.selected_marker not in ids:
            self.selected_marker = None
        self._show_markers()

    def _show_markers(self) -> None:
        self.marker_row_list = marker_rows.rows_from_snapshot(self.marker_snap)
        self.marker_panel.set_rows(self.marker_row_list, self.selected_marker)
        self.marker_tab.apply(self._selected_row(), self.marker_snap)
        self.marker_layer.enabled = self._freq_ops_reason() is None
        self.marker_layer.update(self.marker_row_list, self.selected_marker, self.marker_snap["xy"])
        why = None if self.selected_marker else "マーカーを選んでください"
        for code in ("marker_left", "marker_right", "marker_center", "marker_ref", "marker_remove"):
            menus.set_reason(self.actions[code], why)
        sel = self._selected_row()
        menus.set_reason(self.actions["marker_span"],
                         why or (None if sel and sel.get("ref") else "デルタの基準を選ぶと使えます"))

    # ------------------------------------------------------------------ 画像のための中身（設計I §3.2）

    def source_text(self) -> str:
        """信号源が出ていることの表示（U-05: 状態は画面が示す）。掃引している間だけ出す。"""
        st = self.state
        if not st:
            return ""
        running = st["run"]["state"] in ("continuous", "single", "stopping", "reference")
        if st["connection"]["state"] != "connected" or not running:
            return ""
        mode = st["sweep"]["mode"]
        if mode == "sg" and st["sweep"].get("sg_hz") is not None:
            return f"SG 出力中 {format_freq(st['sweep']['sg_hz'])}"
        if mode in ("tg", "s11"):
            return "TG 出力中"
        return ""

    def png_data(self) -> dict:
        return png_data.build(self)

    # ------------------------------------------------------------------ ヘルプ・機器・校正（中身は device_menu）

    def _show_key_table(self) -> None:
        device_menu.show_key_table(self)

    def _show_about(self) -> None:
        device_menu.show_about(self)

    def _connect_dialog(self) -> None:
        device_menu.connect_dialog(self)

    def _import_legacy(self) -> None:
        device_menu.import_legacy(self)

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
            elif payload.get("kind") == "waiting_sweep_end":
                self.status_note(f"いまの掃引の終わりを待っています（残り約 {payload['remaining_s']:.1f} 秒）")

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
        code = n.get("code", "")
        d = n.get("detail") or {}
        if code in ("task_done", "task_failed", "task_cancelled"):
            self.files.on_task_end(code, d)
        elif code == "trace":
            for c in d.get("codes") or []:
                self._trace_note(d.get("trace"), texts.TRACE_NOTICES.get(c, c))
        elif code == "trace_reset":
            for num in d.get("traces") or []:
                self._trace_note(num, texts.trace_reset_text(d))
        elif code in texts.TRACE_NOTICES:
            self._trace_note(d.get("trace"), texts.TRACE_NOTICES[code])
        elif code == "normalization":
            text = texts.normalization_text({**(self.state.get("normalization") or {}), **d, "used": True})
            self.status_note(text or "ノーマライズの状態が変わりました")
        else:
            self.status_note(texts.notice_text(n))

    def _trace_note(self, number, text: str) -> None:
        row = self.trace_tab.rows.get(number) if isinstance(number, int) else None
        if row is not None:
            row.flash(text)
        self.status_note(f"トレース {number}: {text}" if number else text)

    def _show_adjustments(self, adjustments: list[dict], alternative: Optional[dict] = None) -> None:
        fields = {"start_hz": self.f_start, "stop_hz": self.f_stop, "center_hz": self.f_center,
                  "span_hz": self.f_span, "rbw_hz": self.rbw_field, "sg_hz": self.source_tab.f_sg}
        for adj in adjustments:
            w = fields.get(adj.get("field"))
            text = texts.adjustment_text(adj)
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
        self.state = st
        sw = st["sweep"]
        self.f_center.set_hz(sw["center_hz"])
        self.f_span.set_hz(sw["span_hz"])
        self.f_start.set_hz(sw["start_hz"])
        self.f_stop.set_hz(sw["stop_hz"])
        i = self.rbw_combo.findData(sw["rbw_preference_hz"])
        if i >= 0:
            self.rbw_combo.setCurrentIndex(i)
        cond = []
        if sw["rbw_hz"] != sw["rbw_preference_hz"]:
            cond.append(f"RBW は {format_freq(sw['rbw_hz'])} で測っています（TG・NF・S11、または Span 50MHz 超では 180kHz 固定。条件から出ると {format_freq(sw['rbw_preference_hz'])} に戻ります）")
        cond += [texts.WARNINGS.get(w, w) for w in sw.get("warnings", [])]
        if sw.get("tail_gap"):
            cond.append("Stop の手前 1 Step 未満はデータがありません（機器の上限）")
        self.cond_label.setText("\n".join(cond))
        self.cond_label.setVisible(bool(cond))
        for code, b in self.mode_buttons.items():
            b.setChecked(code == sw["mode"])
        self.span_lock.show_lock(sw.get("span_lock_hz"), sw["span_hz"], format_freq)
        self.plot.getPlotItem().getViewBox().freq_ops_enabled = self._freq_ops_reason() is None
        if self._amp_pending == 0:
            self._take_amplitude(st.get("amplitude") or {})
        self._update_x_axis()
        self.trace_tab.apply_state(st)
        self.source_tab.apply_state(st)
        for n in self.traces.apply_trace_states(st.get("traces") or []):
            self._request_trace(n)
        for tid in self.traces.apply_files(st.get("files") or []):
            self._request_trace(tid)
        n = st.get("normalization") or {}
        self.traces.set_bands(n.get("out_of_range_hz") if n.get("used") else [])
        self._update_unit()
        self._update_ref_auto()
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
        menus.set_reason(a["record_start"], "もう記録しています" if has else None)
        menus.set_reason(a["record_stop"], None if rec.get("active") else "記録していません")
        menus.set_reason(a["record_finish"], None if has else "記録していません")
        has_data = any(s.get("has_values") for s in st.get("traces") or [])
        menus.set_reason(a["save"], None if has_data else "保存する値がありません（掃引してください）")

    def _apply_status(self, st: dict) -> None:
        conn = st["connection"]
        ident = conn.get("identity") or {}
        cs = texts.CONN_STATES.get(conn["state"], conn["state"])
        who = f" {ident.get('serial', '')} {ident.get('port', '')}" if ident else ""
        self.st_conn.setText(f"● {cs}{who}")
        run = st["run"]
        rs = texts.RUN_STATES.get(run["state"], run["state"])
        if run["state"] == "single" or (run["state"] == "stopped" and run.get("last_stop_reason") == "single_done"):
            rs += f" {run['single_done']}/{run['single_target']} 回"
        self.st_run.setText(f"掃引: {rs}")
        self.st_time.setText(f"掃引時間 {run['last_sweep_s']:.2f} 秒" if run.get("last_sweep_s") else "掃引時間 —")
        self.st_discard.setText(f"捨てた掃引 {run['discard_count']} 回")
        cal = st.get("calibration")
        self.st_cal.setText("補正: " + (f"校正データ {cal['created_at']}" if cal else "校正データなし（補正なしの値）"))
        for lab, text in ((self.st_norm, texts.normalization_text(st.get("normalization") or {})),
                          (self.st_src, self.source_text()),
                          (self.st_rec, self._recording_text(st.get("recording") or {})),
                          (self.st_tasks, f"処理中 {len(st.get('tasks') or [])} 件" if st.get("tasks") else "")):
            lab.setText(text)
            lab.setVisible(bool(text))
        running = run["state"] in ("continuous", "single")
        self.btn_cont.setProperty("running", run["state"] == "continuous")
        self.btn_single.setProperty("running", run["state"] == "single")
        for b in (self.btn_cont, self.btn_single):
            b.style().unpolish(b)
            b.style().polish(b)
        connected = conn["state"] == "connected"
        self.btn_cont.setEnabled(connected)
        self.btn_single.setEnabled(connected)
        self.btn_stop.setEnabled(running or run["state"] == "stopping")

    @staticmethod
    def _recording_text(rec: dict) -> str:
        if rec.get("active"):
            s = int(rec.get("elapsed_s") or 0)
            return f"● 記録中 {s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}・{rec.get('sweeps', 0)} 回"
        if rec.get("stopped"):
            return "記録を止めました（まとめていません）"
        return ""

    def _update_x_axis(self) -> None:
        sw = self.state.get("sweep")
        if not sw:
            return
        if sw["step_hz"] == 0:
            # ゼロスパンの時間軸は設計N（⑥）。いまは点の番号で出す
            self.plot.getPlotItem().setLabel("bottom", "点（ゼロスパン）", units="")
            self.plot.setXRange(0, max(1, sw["points"] - 1), padding=0)
        else:
            self.plot.getPlotItem().setLabel("bottom", "周波数", units="Hz")
            self.plot.setXRange(sw["start_hz"], sw["stop_hz"], padding=0)

    # ------------------------------------------------------------------ 知らせ・ドラッグ＆ドロップ・閉じる

    def status_note(self, text: str, folder: Optional[Path] = None) -> None:
        """一度きりの知らせ（U-05）。保存したものなら、そこから保存先を開ける（U-11・I §3.4）。"""
        self.st_note.setText(f"{time.strftime('%H:%M:%S')} {text}")
        self.st_note.setToolTip(text)
        self._note_folder = Path(folder) if folder else None
        self.note_button.setVisible(folder is not None)

    _status_note = status_note

    def _open_note_folder(self) -> None:
        if self._note_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._note_folder)))

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
            lost.append(f"・{texts.TASK_NAMES.get(kind, kind)}: やめます（{texts.TASK_CANCELLED.get(kind, '')}）")
        rec = self.state.get("recording") or {}
        if rec.get("active") or rec.get("stopped"):
            lost.append("・記録: 保存ファイルにまとめていません。作業ファイルは残り、次の起動でまとめるか聞きます")
        if lost and not self.confirm("閉じる", "閉じると次のようになります。閉じますか？\n" + "\n".join(lost)):
            e.ignore()
            return
        self.prefs.set("vsplit", self.vsplit.sizes())
        self.prefs.set("hsplit", self.hsplit.sizes())
        self._unsub()
        super().closeEvent(e)
