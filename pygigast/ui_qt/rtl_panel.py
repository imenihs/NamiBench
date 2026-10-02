"""右のタブ「トレース・掃引」の受信の欄（RTL-SDR: 設計Q §6.5・§6.6）。

- 受信の利得: 機器から読んだ段（29 段）と AGC。REF レベルとは連動しない（Q §6.5）
- 「利得を合わせる」: 振り切れないいちばん大きい段をコアが選ぶ（Q §6.5）。AGC の間は理由を出して無効
- 周波数の補正（ppm）: 数で入れる。個体の校正としてシリアル番号ごとに保存（コアが行う）
- 個体の校正: マーカーを既知の信号に置き、本当の周波数・レベルを入れて「ppm をこの信号で合わせる」
  「レベルをこの信号で合わせる」（Q §6.6。計算と保存はコア）
- 欄は RTL-SDR に繋いでいるときだけ出す（設計J §4.5: 別の機種の欄は出さない）。繋いでいる間に
  使えない操作（AGC の間のレベルの校正・利得を合わせるなど）は、消さずに無効にして理由を出す（U-08）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）。欄に付くボタンは欄の列の下の行に置く
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QPushButton, QSpinBox

from . import screen_model, sizes, texts, widget_sync
from .fields import DbField, FreqField
from .tab_traces import set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

PPM_LIMIT = 1000


class RtlPanel(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        L = texts.RTL_LABELS
        super().__init__(L["group"])
        self.win = win
        form = sizes.form(self)
        self.gain = QComboBox()
        self.gain.setToolTip(L["gain_tip"])
        self.gain.activated.connect(self._gain_chosen)
        form.addRow(L["gain"], self.gain)
        # 「利得を合わせる」（1回押し: 設計Q §6.5）。利得の欄のすぐ下に置く
        self.fit_btn = QPushButton(L["fit_gain"])
        self.fit_btn.clicked.connect(lambda: win.call("rtlsdr_fit_gain"))
        form.addRow("", sizes.buttons_row(self.fit_btn))
        self.ppm = QSpinBox()
        self.ppm.setRange(-PPM_LIMIT, PPM_LIMIT)
        self.ppm.setKeyboardTracking(False)
        self.ppm.setToolTip(L["ppm_tip"])
        sizes.set_width(self.ppm, "int")
        self.ppm.valueChanged.connect(lambda v: win.call("rtlsdr_set", field=None, ppm=int(v)))
        form.addRow(L["ppm"], self.ppm)
        self.cal_text = QLabel("")
        self.cal_text.setWordWrap(True)
        self.cal_text.setObjectName("dim")
        form.addRow(self.cal_text)
        self.unit_text = QLabel("")
        self.unit_text.setWordWrap(True)
        form.addRow(self.unit_text)
        self.shared_text = QLabel("")
        self.shared_text.setWordWrap(True)
        self.shared_text.setObjectName("warn")
        form.addRow(self.shared_text)

        # ---- 個体の校正（マーカーの読み値から: Q §6.6）。ボタンは欄の下の行（右のパネルの幅を広げない: J §4.3.3）
        self.marker = QComboBox()
        form.addRow(L["cal_marker"], self.marker)
        self.true_freq = FreqField(L["cal_true_freq"], lambda hz: self.true_freq.set_hz(int(hz)), big=False)
        self.ppm_btn = QPushButton(L["cal_ppm_button"])
        self.ppm_btn.clicked.connect(self._cal_ppm)
        form.addRow(self.true_freq.name, self.true_freq)
        form.addRow("", sizes.buttons_row(self.ppm_btn))
        self.true_dbm = DbField(L["cal_true_dbm"], "dBm", lambda v: self.true_dbm.set_value(float(v)), big=False)
        self.true_dbm.set_value(-30.0)
        self.level_btn = QPushButton(L["cal_level_button"])
        self.level_btn.clicked.connect(self._cal_level)
        form.addRow(self.true_dbm.name, self.true_dbm)
        form.addRow("", sizes.buttons_row(self.level_btn))
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setObjectName("warn")
        self.result.hide()
        form.addRow(self.result)
        self._view: dict = {}
        self._gains_key: tuple = ()
        self.hide()  # 状態を受けるまでは繋いでいない扱い

    # ---- 状態を映す
    def apply(self, st: dict) -> None:
        v = screen_model.rtl_view(st)
        self._view = v
        self.setVisible(v["shown"])
        widget_sync.when_idle(self.gain, "gain", lambda: self._show_gain(v))
        widget_sync.set_value(self.ppm, int(v["ppm"] or 0))
        set_enabled(self.gain, v["reason"], texts.RTL_LABELS["gain_tip"])
        set_enabled(self.ppm, v["reason"], texts.RTL_LABELS["ppm_tip"])
        set_enabled(self.fit_btn, v["fit_reason"], texts.RTL_LABELS["fit_gain_tip"])
        self.cal_text.setText(v["cal_text"] if v["connected"] else v["reason"])
        self.unit_text.setText(v["unit_text"])
        self.unit_text.setVisible(bool(v["unit_text"]))
        self.shared_text.setText(v["shared_text"])
        self.shared_text.setVisible(bool(v["shared_text"]))
        self.refresh_markers()

    def _show_gain(self, v: dict) -> None:
        """利得の一覧と選んでいる段（一覧を開いている間は呼ばれない: widget_sync）。"""
        key = tuple(g["value"] for g in v["gains"])
        if key != self._gains_key:
            self._gains_key = key
            self.gain.blockSignals(True)
            self.gain.clear()
            for g in v["gains"]:
                self.gain.addItem(g["text"], g["value"])
            self.gain.blockSignals(False)
        want = None if v["agc"] else v["gain_db"]
        idx = self.gain.findData(want) if want is not None else 0
        if want is not None and idx < 0 and self.gain.count() > 1:
            # 設定の値が段に無いとき（別の機器の値など）は一番近い段を見せる
            vals = [self.gain.itemData(i) for i in range(1, self.gain.count())]
            idx = 1 + min(range(len(vals)), key=lambda i: abs(vals[i] - want))
        widget_sync.set_index(self.gain, max(0, idx))

    def refresh_markers(self) -> None:
        """マーカーの一覧（表のマーカーの行）。選んでいるマーカーを既定にする。"""
        ids = [m["id"] for m in self.win.marker_snap.get("markers", [])]
        cur = self.marker.currentData() or self.win.selected_marker
        widget_sync.set_items(self.marker, [(i, i) for i in ids])
        if self.win.selected_marker in ids:
            cur = self.win.selected_marker
        widget_sync.set_data(self.marker, cur)
        if self.true_freq.hz() <= 0:
            # 本当の周波数の欄は、初めはマーカーの周波数（そこから直して入れる）
            row = next((m for m in self.win.marker_snap.get("markers", []) if m["id"] == self.marker.currentData()), None)
            if row and row.get("freq_hz"):
                self.true_freq.set_hz(int(row["freq_hz"]))
        v = self._view
        no_marker = None if ids else texts.RTL_LABELS["no_marker"]
        set_enabled(self.marker, v.get("reason") or no_marker)
        set_enabled(self.ppm_btn, v.get("ppm_reason") or no_marker, texts.RTL_LABELS["cal_ppm_tip"])
        set_enabled(self.level_btn, v.get("level_reason") or no_marker, texts.RTL_LABELS["cal_level_tip"])
        set_enabled(self.true_freq, v.get("ppm_reason") or no_marker)
        set_enabled(self.true_dbm, v.get("level_reason") or no_marker)

    # ---- 操作
    def _gain_chosen(self, _i: int) -> None:
        g = self.gain.currentData()
        if g is None:
            self.win.call("rtlsdr_set", agc=True)
        else:
            self.win.call("rtlsdr_set", gain_db=float(g), agc=False)

    def _marker_id(self) -> Optional[str]:
        return self.marker.currentData()

    def _cal_ppm(self) -> None:
        hz = self.true_freq.hz()
        if hz <= 0:
            self.true_freq.flash_note(texts.ERRORS["bad_value"])
            return
        self.win.call("rtlsdr_calibrate_ppm", on_ok=lambda r: self._done(screen_model.ppm_done_text(r)),
                      field=self.true_freq, marker_id=self._marker_id(), true_freq_hz=int(hz))

    def _cal_level(self) -> None:
        v = self.true_dbm.value()
        self.win.call("rtlsdr_calibrate_level", on_ok=lambda r: self._done(screen_model.level_done_text(r)),
                      field=self.true_dbm, marker_id=self._marker_id(), true_dbm=float(v))

    def _done(self, text: str) -> None:
        self.result.setText(text)
        self.result.show()
        self.win.status_note(text)
