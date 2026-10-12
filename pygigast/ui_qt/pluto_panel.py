"""PlutoSDR の欄（設計Q §7）。中身は screen_pluto.pluto_view（Web と同じ）。

- 受信（右のタブ「トレース・掃引」）: 受信の利得（dB の打てるドロップダウン。REF の欄と同じ部品で、いまの範囲の 1dB
  刻みのどの値も打てる。一覧は自動と 10dB 刻み）・「利得を合わせる」・単位。
  設定の利得がいまの範囲で使えないときは、使う値を利得の欄の下に注意の色で出す（U-25）。受信の利得の補正の表が
  無いときは、利得の欄の下に「利得の補正なし」の印（理由はツールチップ: 設計Q §7.6）
- 送信（右のタブ「信号源」）: 送信の減衰（SG・TG の出力のレベル。0.25dB 刻み）・TG 高調波の平均除去（設計Q §7.7。
  選択肢の文字にいまの条件での1回の掃引の見積もり。幅は秒を 999.9 にした見本で決めた固定の幅: イメージ除去の欄と同じ）
  と、出力中の表示
- どちらも PlutoSDR に繋いでいるときだけ出す（設計J §4.5）。使えない操作は消さずに無効にして理由を出す（U-08）
- 大きさと余白は sizes.py の決まり（J §4.11）。名前は欄の左（S-05）。欄に付くボタンは欄の列の下の行
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QPushButton

from . import sizes, widget_sync
from .fields import DbChoiceField, DbField
from .screen_pluto import pluto_view, tg_harmonic_width_samples
from .tab_traces import SameWidth, set_enabled
from .texts_pluto import PLUTO_LABELS as L

if TYPE_CHECKING:
    from .main_window import MainWindow


class PlutoRxPanel(QGroupBox):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(L["group"])
        self.win = win
        form = sizes.form(self)
        # 範囲・一覧は繋いだ機器と LO の帯で決まる（apply で入れる）。値は 1dB 刻み（整数）
        self.gain = DbChoiceField(L["gain"], "dB", self._gain_typed, (), signed=False, decimals=0, big=False,
                                  special=L["auto"], on_special=self._auto_chosen)
        self.gain.setToolTip(L["gain_tip"])
        form.addRow(self.gain.name, self.gain)
        self.gain_used = QLabel("")
        self.gain_used.setObjectName("warn")
        self.gain_used.hide()
        form.addRow("", self.gain_used)
        self.gain_cal = QLabel(L["gain_cal_none"])
        self.gain_cal.setObjectName("warn")
        self.gain_cal.hide()
        form.addRow("", self.gain_cal)
        self.fit_btn = QPushButton(L["fit_gain"])
        self.fit_btn.clicked.connect(lambda: win.call("plutosdr_fit_gain"))
        form.addRow("", sizes.buttons_row(self.fit_btn))
        self.unit_text = QLabel("")
        self.unit_text.setWordWrap(True)
        form.addRow(self.unit_text)
        self._last: dict = {}
        self.hide()

    def apply(self, st: dict) -> None:
        v = pluto_view(st)
        self.setVisible(v["shown"])
        self._last = v
        self._show_gain(v)
        set_enabled(self.gain, v["gain_reason"], L["gain_tip"])
        set_enabled(self.fit_btn, v["fit_reason"], L["fit_gain_tip"])
        self.gain_used.setText(v["gain_used_text"])
        self.gain_used.setVisible(bool(v["gain_used_text"]))
        self.gain_cal.setText(v["gain_cal_mark"])
        self.gain_cal.setToolTip(v["gain_cal_tip"])
        self.gain_cal.setVisible(bool(v["gain_cal_mark"]))
        self.unit_text.setText(v["unit_text"])
        self.unit_text.setVisible(bool(v["unit_text"]))

    def _show_gain(self, v: dict) -> None:
        # 一覧と範囲は LO の帯で変わる（打っている間の文字は _Edit が守る）
        self.gain.set_spec(v["gain_choices"], False, v["gain_lo"], v["gain_hi"], 0)
        if v["agc"] or v["gain_db"] is None:
            self.gain.show_special(v["gain_base"])
        else:
            self.gain.set_value(float(v["gain_db"]))

    def _gain_typed(self, g: float) -> None:
        # 同じ値を送り直さない（利得を送るとトレースの蓄積を捨てる。欄を離れたときに一覧の同じ文字の行が選ばれても
        # 何もしない）
        v = self._last
        if not v.get("agc") and v.get("gain_setting") == float(g):
            self.gain.set_value(float(g))
            return
        self.win.call("plutosdr_set", field=self.gain, gain_db=float(g), agc=False)

    def _auto_chosen(self) -> None:
        if self._last.get("agc"):
            self.gain.show_special(self._last.get("gain_base", 0.0))
            return
        self.win.call("plutosdr_set", field=self.gain, agc=True)


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
        self.tg_harmonic = QComboBox()
        self.tg_harmonic.activated.connect(self._tg_harmonic_chosen)
        self._tg_width = SameWidth([self.tg_harmonic], tg_harmonic_width_samples())
        self._tg_value = None
        form.addRow(L["tg_harmonic"], self.tg_harmonic)
        self.state = QLabel("")
        self.state.setObjectName("dim")
        form.addRow(self.state)
        self._step = 0.25
        self.hide()

    def _commit(self, v: float) -> None:
        self.win.call("plutosdr_set", field=self.atten, tx_atten_db=float(v))

    def _tg_harmonic_chosen(self, i: int) -> None:
        # 利用者が選んだときだけ届く（activated。状態を映すときは知らせを止めている: widget_sync）
        code = self.tg_harmonic.itemData(i)
        if code is None or int(code) == self._tg_value:
            return
        self.win.call("plutosdr_set", tg_harmonic_avg=int(code))

    def apply(self, st: dict) -> None:
        v = pluto_view(st)
        self.setVisible(v["shown"])
        self.atten.set_value(v["tx_atten_db"])
        set_enabled(self.atten, v["tx_atten_reason"], L["tx_atten_tip"])  # S11 のオープンの後も（設計Q §7.8）
        tg = v["tg_harmonic"]
        self._tg_value = tg["value"]
        widget_sync.set_items(self.tg_harmonic, [(it["text"], it["code"]) for it in tg["items"]])
        widget_sync.set_data(self.tg_harmonic, tg["value"])
        set_enabled(self.tg_harmonic, tg["reason"], L["tg_harmonic_tip"])
        self.state.setText(v["tx_state"])
        self.state.setVisible(bool(v["tx_state"]))
