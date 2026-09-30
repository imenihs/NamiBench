"""Qt のメイン画面（設計J §4.3 の案B の最小形）。

- 上段: モード・主な値（Center・Span・Start・Stop・RBW・REF・dB/div）・連続/単発/停止
- 中央: グラフ（縦軸は dBm の絶対値。REF レベルと dB/div を独立に持つ: J §4.1）
- 右: タブ（トレース・掃引／信号源）
- 下端: 状態バー（接続・掃引・掃引時間・捨てた回数・補正。常に出す: J §2・M-04）

コアへは api/local.py だけを使う（設計A §2）。イベントは制御スレッドから届くので、
シグナルで画面スレッドへ渡し直してから触る。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api.local import LocalApi
from ..core.units import format_freq
from . import texts
from .fields import DbField, FreqField, ValueField

DB_PER_DIV_CHOICES = (1.0, 2.0, 5.0, 10.0)
DIVISIONS = 10

# 配色（J §4.4 のダークの初期値。配色の編集は④以降）
DARK = {
    "graph_bg": "#15181d",
    "panel_bg": "#1e232b",
    "text": "#e6e9ee",
    "text_dim": "#aab3c0",
    "accent": "#4fa3ff",
    "warn": "#ffcc66",
    "grid": "#2a313c",
    "axis": "#3a4452",
    "trace1": "#ffd24a",
    "running": "#1f6f3a",
}


class _Bridge(QObject):
    """制御スレッドから画面スレッドへ渡し直す（QueuedConnection）。"""

    event = Signal(str, object)
    call = Signal(object)


class _TopBar(QWidget):
    """上段。左（モードと周波数）と右（RBW・振幅・連続/単発/停止）を、幅が足りれば1段、足りなければ2段に並べる。

    段を変えるのは表示だけで、状態は変えない（J §4.3.3）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._left_w = QWidget()
        self._right_w = QWidget()
        self.left = QHBoxLayout(self._left_w)
        self.right = QHBoxLayout(self._right_w)
        for lay in (self.left, self.right):
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._two_rows: Optional[bool] = None
        self._place(False)

    def _place(self, two_rows: bool) -> None:
        if two_rows == self._two_rows:
            return
        self._two_rows = two_rows
        self._grid.removeWidget(self._left_w)
        self._grid.removeWidget(self._right_w)
        self._grid.addWidget(self._left_w, 0, 0)
        if two_rows:
            self._grid.addWidget(self._right_w, 1, 0)
        else:
            self._grid.addWidget(self._right_w, 0, 1)

    def needed_one_row(self) -> int:
        return self._left_w.minimumSizeHint().width() + self._right_w.minimumSizeHint().width() + 8

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        w = max(self._left_w.minimumSizeHint().width(), self._right_w.minimumSizeHint().width())
        hint.setWidth(w)
        return hint

    def resizeEvent(self, e) -> None:
        self._place(e.size().width() < self.needed_one_row())
        super().resizeEvent(e)


class ConnectDialog(QDialog):
    """機器を一覧から選ぶ（設計B §7.3: 初めての機器は利用者が選ぶ）。"""

    def __init__(self, devices: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("機器に接続")
        self.list = QListWidget()
        for d in devices:
            mark = "（前に使った機器）" if d.get("known") else ""
            item = QListWidgetItem(f"{d.get('serial') or '（シリアル番号なし）'}  {d.get('port')}  {d.get('description', '')}{mark}")
            item.setData(Qt.ItemDataRole.UserRole, d)
            self.list.addItem(item)
        if devices:
            self.list.setCurrentRow(0)
        note = QLabel(
            "USB ID が FTDI 0403:6001 のポートを並べています。この部品は GigaSt 以外の機器にも使われるので、\n"
            "初めての機器は確認の掃引を1回送り、GigaSt の応答が返るかを確かめてから繋ぎます。"
            if devices else "GigaSt が見つかりません。USB ケーブルを確かめてください。"
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("接続")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(devices))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list)
        lay.addWidget(buttons)

    def selected(self) -> Optional[dict]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class MainWindow(QMainWindow):
    def __init__(self, api: LocalApi) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle("pyGigaSt")
        self._state: dict = {}
        self._ref_dbm = 0.0
        self._db_div = 10.0
        self._last_trace: Optional[dict] = None
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._bridge.call.connect(lambda fn: fn(), Qt.ConnectionType.QueuedConnection)

        self._build_menu()
        self._build_body()
        self._build_status()
        self._apply_style()

        self._unsub = api.subscribe(lambda kind, payload: self._bridge.event.emit(kind, payload))
        self._call("get_state", on_ok=self._apply_state)
        # 2回目からは、覚えた機器が見つかれば自動で繋ぐ（設計B §7.3）
        self._call("connect_last", on_ok=lambda r: None)

    # ------------------------------------------------------------------ 命令を呼ぶ

    def _call(self, name: str, on_ok: Optional[Callable[[dict], None]] = None,
              field: Optional[ValueField] = None, **kwargs) -> None:
        fut = self.api.call(name, **kwargs)

        def done(f) -> None:
            def in_gui() -> None:
                exc = f.exception()
                if exc is not None:
                    msg = texts.error_text(exc)
                    if field is not None:
                        field.flash_note(msg)
                    else:
                        self._status_note(msg)
                    return
                result = f.result()
                if isinstance(result, dict) and "adjustments" in result:
                    self._show_adjustments(result["adjustments"])
                if on_ok:
                    on_ok(result)

            self._bridge.call.emit(in_gui)

        fut.add_done_callback(done)

    # ------------------------------------------------------------------ 画面を組む

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("ファイル(&F)")
        act_quit = QAction("終了", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_dev = mb.addMenu("機器(&D)")
        act_conn = QAction("接続…", self)
        act_conn.triggered.connect(self._connect_dialog)
        m_dev.addAction(act_conn)
        act_sim = QAction("シミュレータに接続", self)
        act_sim.triggered.connect(lambda: self._call("connect", backend="simulator", serial="SIM00001"))
        m_dev.addAction(act_sim)
        act_disc = QAction("切断", self)
        act_disc.triggered.connect(lambda: self._call("disconnect"))
        m_dev.addAction(act_disc)

        m_cal = mb.addMenu("校正(&C)")
        act_legacy = QAction("旧校正ファイルを読み込む…", self)
        act_legacy.triggered.connect(self._import_legacy)
        m_cal.addAction(act_legacy)

        m_run = mb.addMenu("掃引(&S)")
        for label, key, fn in (
            ("連続", "F5", lambda: self._call("run", mode="continuous")),
            ("単発", "F6", lambda: self._call("run", mode="single")),
            ("停止", "F7", lambda: self._call("stop")),
        ):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(fn)
            m_run.addAction(act)

    def _build_body(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 4, 6, 0)
        outer.setSpacing(4)

        # ---- 上段（狭いときは2段に折り返す: J §4.3.3）
        self.topbar = _TopBar()
        top = self.topbar.left
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QPushButton] = {}
        for code, label, enabled, why in (
            ("sa", "SA", True, "スペクトラムアナライザ"),
            ("tg", "TG", True, "伝送特性（TG 出力を被測定物へ）。ノーマライズは次の回で実装"),
            ("s11", "S11", False, "反射（方向性結合器）は次の回で実装します"),
            ("sg", "SG", True, "信号源"),
            ("nf", "NF", False, "NF 測定は次の回で実装します"),
        ):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setEnabled(enabled)
            b.setToolTip(why)
            b.setMinimumHeight(40)
            f = QFont(b.font())
            f.setPointSize(12)
            b.setFont(f)
            b.clicked.connect(lambda _=False, c=code: self._call("set_mode", mode=c))
            self.mode_group.addButton(b)
            self.mode_buttons[code] = b
            top.addWidget(b)
        top.addSpacing(8)

        self.f_center = FreqField("Center", lambda hz: self._set_sweep(center_hz=hz, field=self.f_center))
        self.f_span = FreqField("Span", lambda hz: self._set_sweep(span_hz=hz, field=self.f_span))
        self.f_start = FreqField("Start", lambda hz: self._set_sweep(start_hz=hz, field=self.f_start))
        self.f_stop = FreqField("Stop", lambda hz: self._set_sweep(stop_hz=hz, field=self.f_stop))
        for f in (self.f_center, self.f_span, self.f_start, self.f_stop):
            top.addWidget(f)
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
        top.addWidget(self.f_ref)
        div_box = ValueField("dB/div", big=False)
        self.div_combo = QComboBox()
        for v in DB_PER_DIV_CHOICES:
            self.div_combo.addItem(f"{v:g} dB", v)
        self.div_combo.setCurrentIndex(DB_PER_DIV_CHOICES.index(10.0))
        self.div_combo.activated.connect(lambda i: self._set_div(self.div_combo.itemData(i)))
        div_box.layout().replaceWidget(div_box.edit, self.div_combo)
        div_box.edit.hide()
        top.addWidget(div_box)
        top.addStretch(1)

        self.btn_cont = QPushButton("連続")
        self.btn_single = QPushButton("単発")
        self.btn_stop = QPushButton("停止")
        for b, tip in ((self.btn_cont, "F5"), (self.btn_single, "F6"), (self.btn_stop, "F7")):
            b.setMinimumSize(78, 44)
            f = QFont(b.font())
            f.setPointSize(13)
            b.setFont(f)
            b.setToolTip(tip)
            top.addWidget(b)
        self.btn_cont.clicked.connect(lambda: self._call("run", mode="continuous"))
        self.btn_single.clicked.connect(lambda: self._call("run", mode="single"))
        self.btn_stop.clicked.connect(lambda: self._call("stop"))
        outer.addWidget(self.topbar)

        # 使っている RBW が好みと違う理由・信用できない条件（その場で出し続ける: U-02・U-08）
        self.cond_label = QLabel("")
        self.cond_label.setObjectName("warn")
        self.cond_label.setWordWrap(True)
        outer.addWidget(self.cond_label)

        # ---- グラフと右のタブ
        split = QSplitter(Qt.Orientation.Horizontal)
        pg.setConfigOptions(antialias=False)
        self.plot = pg.PlotWidget(background=DARK["graph_bg"])
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        pi = self.plot.getPlotItem()
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
        self.curve_head = self.plot.plot(pen=pg.mkPen(DARK["text_dim"], width=1))
        self.curve = self.plot.plot(pen=pg.mkPen(DARK["trace1"], width=1))
        split.addWidget(self.plot)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_trace_tab(), "トレース・掃引")
        self.tabs.addTab(self._build_source_tab(), "信号源")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        outer.addWidget(split, 1)
        self.setCentralWidget(central)
        self._update_y_axis()

    def _build_trace_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.points_auto = QCheckBox("自動")
        self.points_auto.setChecked(True)
        self.points_spin = QSpinBox()
        self.points_spin.setRange(2, 12000)
        self.points_spin.setEnabled(False)
        self.points_spin.setKeyboardTracking(False)
        self.points_auto.toggled.connect(self._points_auto_toggled)
        self.points_spin.valueChanged.connect(
            lambda v: None if self.points_auto.isChecked() else self._set_sweep(points=v))
        row = QHBoxLayout()
        row.addWidget(self.points_auto)
        row.addWidget(self.points_spin)
        form.addRow("点数", row)
        self.step_label = QLabel("")
        form.addRow("Step", self.step_label)
        self.est_label = QLabel("")
        form.addRow("掃引時間の見積もり", self.est_label)
        self.image_cancel = QCheckBox("イメージキャンセル（2回で1画面）")
        self.image_cancel.toggled.connect(lambda on: self._set_sweep(image_cancel=on))
        form.addRow(self.image_cancel)
        return w

    def _build_source_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.f_sg = FreqField("SG 周波数", lambda hz: self._call(
            "set_mode", mode=self._state.get("sweep", {}).get("mode", "sg"), sg_hz=hz, field=self.f_sg), big=False)
        form.addRow(self.f_sg)
        self.source_note = QLabel("SG 周波数は SG モードのときに使います。")
        self.source_note.setWordWrap(True)
        form.addRow(self.source_note)
        return w

    def _build_status(self) -> None:
        sb = self.statusBar()
        self.st_conn = QLabel()
        self.st_run = QLabel()
        self.st_time = QLabel()
        self.st_discard = QLabel()
        self.st_cal = QLabel()
        self.st_note = QLabel()
        for lab in (self.st_conn, self.st_run, self.st_time, self.st_discard, self.st_cal):
            sb.addWidget(lab)
        sb.addWidget(self.st_note, 1)

    def _apply_style(self) -> None:
        c = DARK
        self.setStyleSheet(f"""
            QWidget {{ background: {c['panel_bg']}; color: {c['text']}; }}
            QLineEdit, QComboBox, QSpinBox {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 2px; }}
            QPushButton {{ background: {c['graph_bg']}; border: 1px solid {c['axis']}; padding: 4px 10px; }}
            QPushButton:checked {{ background: {c['accent']}; color: {c['graph_bg']}; }}
            QPushButton:disabled {{ color: {c['axis']}; }}
            QPushButton[running="true"] {{ background: {c['running']}; }}
            QLabel#note {{ color: {c['warn']}; }}
            QLabel#warn {{ color: {c['warn']}; }}
            QStatusBar QLabel {{ padding: 0 8px; }}
        """)
        for ax in ("left", "bottom"):
            a = self.plot.getPlotItem().getAxis(ax)
            a.setPen(pg.mkPen(c["axis"]))
            a.setTextPen(pg.mkPen(c["text"]))

    # ------------------------------------------------------------------ 操作

    def _set_sweep(self, field: Optional[ValueField] = None, **kwargs) -> None:
        self._call("set_sweep", field=field, **kwargs)

    def _points_auto_toggled(self, auto: bool) -> None:
        self.points_spin.setEnabled(not auto)
        if auto:
            self._set_sweep(points_auto=True)
        else:
            self._set_sweep(points=self.points_spin.value())

    def _set_ref(self, v: float) -> None:
        # 表示だけの値。測定値は変わらない（J §4.1）
        self._ref_dbm = round(v, 2)
        self.f_ref.set_value(self._ref_dbm)
        self._update_y_axis()

    def _set_div(self, v: float) -> None:
        # dB/div を変えても REF レベルは動かない（J §4.1）
        self._db_div = v
        self._update_y_axis()

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
        pen = pg.mkPen(DARK["grid"], width=1)
        self._hgrid = [pg.InfiniteLine(pos=v, angle=0, pen=pen, movable=False) for v, _ in ticks]
        for line in self._hgrid:
            line.setZValue(-10)
            pi.addItem(line, ignoreBounds=True)
        self.f_ref.set_value(self._ref_dbm)

    def _connect_dialog(self) -> None:
        def show(result: dict) -> None:
            dlg = ConnectDialog(result["devices"], self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected():
                d = dlg.selected()
                self._status_note("接続しています…")
                self._call("connect", backend="gigast", serial=d.get("serial"), port=d.get("port"))

        self._call("list_devices", on_ok=show, backend="gigast")

    def _import_legacy(self) -> None:
        conn = self._state.get("connection", {})
        serial = (conn.get("identity") or {}).get("serial")
        if not serial:
            QMessageBox.information(self, "旧校正ファイル", "校正を当てる機器を決めるため、先に機器に接続してください。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "旧校正ファイル（gigast.txt / ba205.txt）", "", "テキスト (*.txt);;すべて (*)")
        if not path:
            return
        if QMessageBox.question(
            self, "旧校正ファイル", f"このファイルを、接続中の機器（{serial}）の校正として取り込みますか？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._call("import_legacy_calibration", on_ok=self._show_cal_summary, path=path, serial=serial)

    def _show_cal_summary(self, result: dict) -> None:
        s = result["summary"]
        bands = s["freq_response_db"]["bands"]
        lines = [
            f"元のファイル: {s['source'].get('file_name')}",
            f"全体のレベル: {s['level_offset_db']:+.1f} dB",
            "RBW ごと: " + "・".join(f"{format_freq(int(k))} {v:+.1f} dB" for k, v in s["rbw_offset_db"].items()),
            "周波数特性（band ごとの最小〜最大）:",
        ]
        for b in bands:
            lines.append(f"  {format_freq(b['start_hz'])}〜: {min(b['values']):+g} 〜 {max(b['values']):+g} dB")
        QMessageBox.information(self, "旧校正ファイルを取り込みました", "\n".join(lines))

    # ------------------------------------------------------------------ イベント

    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self._apply_state(payload)
        elif kind == "trace":
            self._draw_trace(payload)
        elif kind == "notice":
            self._status_note(texts.notice_text(payload))
        elif kind == "progress":
            if payload.get("kind") == "waiting_sweep_end":
                self._status_note(f"いまの掃引の終わりを待っています（残り約 {payload['remaining_s']:.1f} 秒）")

    def _show_adjustments(self, adjustments: list[dict]) -> None:
        fields = {"start_hz": self.f_start, "stop_hz": self.f_stop, "center_hz": self.f_center,
                  "span_hz": self.f_span, "rbw_hz": self.rbw_field, "sg_hz": self.f_sg}
        for adj in adjustments:
            w = fields.get(adj.get("field"))
            text = texts.adjustment_text(adj)
            if w is not None:
                w.flash_note(text)
            else:
                self._status_note(f"{texts.FIELD_NAMES.get(adj.get('field'), adj.get('field'))}: {text}")

    def _apply_state(self, st: dict) -> None:
        if st.get("version", -1) < self._state.get("version", -1):
            return
        self._state = st
        sw = st["sweep"]
        self.f_center.set_hz(sw["center_hz"])
        self.f_span.set_hz(sw["span_hz"])
        self.f_start.set_hz(sw["start_hz"])
        self.f_stop.set_hz(sw["stop_hz"])
        if sw.get("sg_hz") is not None:
            self.f_sg.set_hz(sw["sg_hz"])
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
        self.points_auto.blockSignals(True)
        self.points_auto.setChecked(sw.get("points_setting") in (None, 0))
        self.points_spin.setEnabled(not self.points_auto.isChecked())
        self.points_auto.blockSignals(False)
        self.points_spin.blockSignals(True)
        self.points_spin.setValue(sw["points"])
        self.points_spin.blockSignals(False)
        self.step_label.setText(format_freq(sw["step_hz"]) + ("（ゼロスパン）" if sw["step_hz"] == 0 else ""))
        self.est_label.setText(f"{sw['estimated_sweep_s']:.2f} 秒（前置き {sw['preroll_points']} 点を含む）")
        self.image_cancel.blockSignals(True)
        self.image_cancel.setChecked(sw["image_cancel"])
        self.image_cancel.blockSignals(False)
        self.source_note.setText(
            "SG モードで出力しています。" if sw["mode"] == "sg" else "SG 周波数は SG モードのときに使います。")
        self._update_x_axis()

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

    def _update_x_axis(self) -> None:
        sw = self._state.get("sweep")
        if not sw:
            return
        if sw["step_hz"] == 0:
            # ゼロスパンの時間軸は設計N（⑥）。いまは点の番号で出す
            self.plot.getPlotItem().setLabel("bottom", "点（ゼロスパン）", units="")
            self.plot.setXRange(0, max(1, sw["points"] - 1), padding=0)
        else:
            self.plot.getPlotItem().setLabel("bottom", "周波数", units="Hz")
            self.plot.setXRange(sw["start_hz"], sw["stop_hz"], padding=0)

    def _draw_trace(self, t: dict) -> None:
        self._last_trace = t
        ax = t["axis"]
        y = np.asarray(t["values"], dtype=np.float64)
        if ax["step_hz"] == 0:
            x = np.arange(ax["points"], dtype=np.float64)
        else:
            # 描くときだけ float にする（周波数の正本は Hz の整数のまま）
            x = (ax["start_hz"] + ax["step_hz"] * np.arange(ax["points"], dtype=np.int64)).astype(np.float64)
        head = int(t["flags"].get("transient_head_points") or 0)
        # 前置きが足りず過渡の可能性がある先頭の点は薄く描く（設計C §5.6）
        if head > 0:
            self.curve_head.setData(x[: head + 1], y[: head + 1])
            self.curve.setData(x[head:], y[head:])
        else:
            self.curve_head.setData([], [])
            self.curve.setData(x, y)

    def _status_note(self, text: str) -> None:
        self.st_note.setText(f"{time.strftime('%H:%M:%S')} {text}")

    def closeEvent(self, e) -> None:
        self._unsub()
        super().closeEvent(e)
