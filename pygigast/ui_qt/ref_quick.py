"""上段の「直結を 0dB にする」（設計J §4.2: TG・S11 の基準は1クリックで届く所に。設計E §6.2）。

- TG・S11 のときだけ、連続/単発/停止の隣に出す（位置を動かさない: U-08）
- 押したら「広く取る」（機種の TG の範囲を 1MHz 刻み）で基準を取り、すぐ1回測って 0dB を見せる。0dB が縦軸の
  外なら見える REF レベルにする（コアの reference_take の show_zero。変えたことは知らせに出る）
- 取っている間は進み具合と「やめる」をその場に出す（U-19）
- 隣に、いま使っている基準（名前・取得の時刻・電源の入れ直し前の印）か「基準なし（dBm で表示）」
- 細かい操作（名前・平均・いまの条件で細かく取る・一覧）は右の「信号源」タブに残す
中身（出すか・理由・文）は screen_model.ref_quick_view（Web と同じ）。
"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from . import screen_model, sizes, texts
from .texts_devices import REF_QUICK_LABELS as L

if TYPE_CHECKING:
    from .main_window import MainWindow

# 基準の状態の文の幅の上限（上段を押し広げない。全文はツールチップ）
STATUS_MAX_W = 230


class RefQuick(QWidget):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self.win = win
        self._task_id: Optional[str] = None
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
        self.button.setEnabled(v["reason"] is None)
        self.button.setToolTip(L["tip"] + (f" — {v['reason']}" if v["reason"] else ""))
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
        name = texts.tpl("ref_direct_name", time=datetime.datetime.now().strftime("%H:%M"))
        self.win.call("reference_take", kind="wide", name=name, show_zero=True)

    def _cancel(self) -> None:
        if self._task_id:
            self.win.call("cancel_task", task_id=self._task_id, quiet_codes=("unknown_task",))
