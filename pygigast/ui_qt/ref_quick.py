"""上段の「基準を取る」（設計J §4.2: TG・S11 の基準は1クリックで届く所に。設計E §6.2）。

- TG・S11 のときだけ、連続/単発/停止の隣に出す（位置を動かさない: U-08）
- 押したら「広く取る」（機種の TG の範囲を 1MHz 刻み）で基準を取り、すぐ1回測って 0dB を見せる。0dB が縦軸の
  外なら見える REF レベルにする（コアの reference_take の show_zero。変えたことは知らせに出る）。基準の値は信号源の
  タブの「基準の値」の欄の値を使い（設計E §6.7）、0dB の代わりにその値を見せる
- S11 では同じ場所のボタンが次の1段になる: 「オープンを測る」→「ショートを測る」（設計E §6.5）。ショートで基準が
  できたら、すぐ1回測って見せる
- 取っている間は進み具合と「やめる」をその場に出す（U-19）
- 隣に、いま使っている基準（名前・取得の時刻・基準の値（0dB でなければ）・電源の入れ直し前の印）か「基準なし（dBm で表示）」
- 細かい操作（名前・平均・いまの条件で細かく取る・一覧）は右の「信号源」タブに残す
中身（出すか・理由・文）は screen_model.ref_quick_view（Web と同じ）。
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from . import screen_model, screen_s11, sizes, texts
from .texts_devices import REF_QUICK_LABELS as L

if TYPE_CHECKING:
    from .main_window import MainWindow

# 基準の状態の文の幅の上限。小の字で2行まで（上段の高さ 40px の中）に収め、上段を押し広げない（全文はツールチップ）。
# 1行で 230px 取ると、TG の 1920 幅で RBW 以降が2行目へ落ちた（設計の PDCA 2巡目）
STATUS_MAX_W = 150


class RefQuick(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._task_id: Optional[str] = None
        self._take: Optional[dict] = None  # 押したときに送る中身（screen_model.ref_quick_view の take）
        lay = QHBoxLayout(self)
        lay.setContentsMargins(sizes.GAP, 0, 0, 0)
        lay.setSpacing(sizes.GAP)
        self.button = QPushButton(L["button"])
        sizes.set_tier(self.button, "L")  # 上段のボタン（J §4.11）
        self.button.clicked.connect(self.take)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(STATUS_MAX_W)
        self.status.setMaximumHeight(sizes.H_L)
        f = QFont(self.status.font())
        f.setPixelSize(sizes.FONT_S)
        self.status.setFont(f)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 1回の掃引の途中は進みが分からないので、動いていることだけを示す
        self.bar.setMaximumWidth(80)
        self.bar.setTextVisible(False)
        self.cancel = QPushButton(L["cancel"])
        sizes.set_tier(self.cancel, "L")
        self.cancel.clicked.connect(self._cancel)
        for w in (self.button, self.bar, self.cancel, self.status):
            lay.addWidget(w)
        self.bar.hide()
        self.cancel.hide()
        self.hide()

    def apply(self, st: dict) -> None:
        v = screen_model.ref_quick_view(st)
        self.setVisible(v["visible"])
        if self.button.text() != v["label"]:
            self.button.setText(v["label"])  # S11 は次の1段（オープンを測る → ショートを測る: 設計E §6.5）
            sizes.fit_button(self.button)  # 前の文字の幅のまま伸びて残らないように（S-03）
        self.button.setEnabled(v["reason"] is None)
        self.button.setToolTip(v["button_tip"])
        self._take = v["take"]
        self._task_id = v["task_id"]
        self.bar.setVisible(v["taking"])
        self.cancel.setVisible(v["taking"])
        self.cancel.setEnabled(not v["cancelling"])
        text = v["progress_text"] if v["taking"] else v["text"]
        self.status.setText(text)
        # 1行で読める幅を取る（上限まで。長い文は折り返し、全文はツールチップ）
        w = max((self.status.fontMetrics().horizontalAdvance(t) for t in text.splitlines()), default=0)
        self.status.setMinimumWidth(min(w + 4, STATUS_MAX_W))
        self.status.setToolTip("\n".join(t for t in (text, v["tip"]) if t))
        self.win.topbar.relayout()  # 出る・消える・文の幅が変わると上段の幅が変わる（J §4.3.3）

    def take(self) -> None:
        """TG は「広く取る」で基準を取り、S11 は次の1段（オープン → ショート。範囲は広く: 設計E §6.5）。"""
        now = datetime.datetime.now().strftime("%H:%M")
        t = dict(self._take or {"standard": None, "kind": "wide", "show_zero": True})
        if t.get("standard"):
            kw = {"standard": t["standard"], "name": screen_s11.s11_ref_name(now), "show_zero": bool(t["show_zero"])}
            if t.get("kind"):
                kw["kind"] = t["kind"]
        else:
            # 基準の値は信号源のタブの欄の値（設計E §6.7。S11 には送らない: 全反射 = 0dB）
            kw = {"kind": "wide", "name": texts.tpl("ref_direct_name", time=now), "show_zero": True,
                  "value_db": self.win.source_tab.take_value.value()}
        self.win.call("reference_take", **kw)

    def _cancel(self) -> None:
        if self._task_id:
            self.win.call("cancel_task", task_id=self._task_id, quiet_codes=("unknown_task",))
