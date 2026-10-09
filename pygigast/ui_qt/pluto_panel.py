"""PlutoSDR の欄（設計Q §7）。中身は screen_pluto.pluto_view（Web と同じ）。

- 受信（右のタブ「トレース・掃引」）: 受信の利得（自動と、いまの範囲で使える 1dB 刻みの値）・「利得を合わせる」・単位。
  設定の利得がいまの範囲で使えないときは、使う値を利得の欄の下に注意の色で出す（U-25）
- 送信（右のタブ「信号源」）: 送信の減衰（SG・TG の出力のレベル。0.25dB 刻み）と、出力中の表示
- どちらも PlutoSDR に繋いでいるときだけ出す（設計J §4.5）。使えない操作は消さずに無効にして理由を出す（U-08）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）。欄に付くボタンは欄の列の下の行
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QPushButton

from . import sizes, widget_sync
from .fields import DbField
from .screen_pluto import pluto_view
from .tab_traces import set_enabled
from .texts_pluto import PLUTO_LABELS as L

if TYPE_CHECKING:
    from .main_window import MainWindow


class PlutoRxPanel(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(L["group"])
        self.win = win
        form = sizes.form(self)
        self.gain = QComboBox()
        self.gain.setToolTip(L["gain_tip"])
        self.gain.activated.connect(self._gain_chosen)
        form.addRow(L["gain"], self.gain)
        self.gain_used = QLabel("")
        self.gain_used.setObjectName("warn")
        self.gain_used.hide()
        form.addRow("", self.gain_used)
        self.fit_btn = QPushButton(L["fit_gain"])
        self.fit_btn.clicked.connect(lambda: win.call("plutosdr_fit_gain"))
        form.addRow("", sizes.buttons_row(self.fit_btn))
        self.unit_text = QLabel("")
        self.unit_text.setWordWrap(True)
        form.addRow(self.unit_text)
        self._key: tuple = ()
        self.hide()

    def apply(self, st: dict) -> None:
        v = pluto_view(st)
        self.setVisible(v["shown"])
        widget_sync.when_idle(self.gain, "gain", lambda: self._show_gain(v))
        set_enabled(self.gain, v["reason"], L["gain_tip"])
        set_enabled(self.fit_btn, v["fit_reason"], L["fit_gain_tip"])
        self.gain_used.setText(v["gain_used_text"])
        self.gain_used.setVisible(bool(v["gain_used_text"]))
        self.unit_text.setText(v["unit_text"])
        self.unit_text.setVisible(bool(v["unit_text"]))

    def _show_gain(self, v: dict) -> None:
        key = tuple(g["value"] for g in v["gains"])
        if key != self._key:
            self._key = key
            self.gain.blockSignals(True)
            self.gain.clear()
            for g in v["gains"]:
                self.gain.addItem(g["text"], g["value"])
            self.gain.blockSignals(False)
            widget_sync.fit_all(self)
        idx = 0 if v["agc"] or v["gain_db"] is None else self.gain.findData(float(v["gain_db"]))
        widget_sync.set_index(self.gain, max(0, idx))

    def _gain_chosen(self, _i: int) -> None:
        g = self.gain.currentData()
        if g is None:
            self.win.call("plutosdr_set", agc=True)
        else:
            self.win.call("plutosdr_set", gain_db=float(g), agc=False)


class _AttenField(DbField):
    """送信の減衰の欄（0.25dB 刻みなので小数 2 桁で出す）。"""

    def set_value(self, v: float) -> None:
        self._value = v
        self.edit.show_value(f"{v:.2f} dB")


class PlutoTxPanel(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(L["tx_group"])
        self.win = win
        form = sizes.form(self)
        self.atten = _AttenField(L["tx_atten"], "dB", self._commit, big=False)
        self.atten.setToolTip(L["tx_atten_tip"])
        form.addRow(self.atten.name, self.atten)
        self.state = QLabel("")
        self.state.setObjectName("dim")
        form.addRow(self.state)
        self._step = 0.25
        self.hide()

    def _commit(self, v: float) -> None:
        self.win.call("plutosdr_set", field=self.atten, tx_atten_db=float(v))

    def apply(self, st: dict) -> None:
        v = pluto_view(st)
        self.setVisible(v["shown"])
        self.atten.set_value(v["tx_atten_db"])
        set_enabled(self.atten, v["reason"], L["tx_atten_tip"])
        self.state.setText(v["tx_state"])
        self.state.setVisible(bool(v["tx_state"]))
