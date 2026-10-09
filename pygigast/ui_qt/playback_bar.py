"""再生の帯（設計P §5・§9.4）: 再生／一時停止・時間の軸のつまみ・位置（時刻と何番目か）・速さ・保存時の分解能・ライブに戻る。

記録のファイルを開いて再生している間だけ出す（ライブの測定中は出ない: P §9.4）。中身は display_view.playback_view
（Web の帯と同じ文）。つまみを動かすとカーソルをそこへ動かす（コアへの命令は呼ぶ側が送る）。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSlider, QWidget

from . import sizes, texts, widget_sync

D = texts.DISPLAY_TEXTS
# つまみを動かしている間にカーソルを送る間隔（ミリ秒）。動かすたびに送ると読み込みが追いつかない
SEEK_MS = 120


class PlaybackBar(QWidget):
    def __init__(self, call: Callable[..., None]) -> None:
        super().__init__()
        self._call = call
        row = sizes.hbox(self)
        row.setContentsMargins(sizes.INSET, sizes.GAP, sizes.INSET, sizes.GAP)
        self.play = QPushButton(D["play"])
        self.play.clicked.connect(self._toggle)
        row.addWidget(self.play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._slid)
        row.addWidget(self.slider, 1)
        self.position = QLabel("")
        row.addWidget(self.position)
        row.addSpacing(sizes.SECTION_GAP)
        row.addWidget(QLabel(D["speed"]))
        self.speed = QComboBox()
        self.speed.activated.connect(lambda i: self._call("playback_speed", speed=int(self.speed.itemData(i))))
        row.addWidget(self.speed)
        self.resolution = QLabel("")
        self.resolution.setObjectName("dim")
        row.addWidget(self.resolution)
        row.addSpacing(sizes.SECTION_GAP)
        self.live = QPushButton(D["back_to_live"])
        self.live.clicked.connect(lambda: self._call("playback_close"))
        row.addWidget(self.live)
        self._playing = False
        self._speeds_key: tuple = ()
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(SEEK_MS)
        self._seek_timer.timeout.connect(self._send_seek)
        self._updating = False
        self.hide()

    def reset(self) -> None:
        """記録が替わった: 前の記録のつまみの送りかけを捨てる（前の記録の位置を新しい記録へ送らない）。"""
        self._seek_timer.stop()

    def _toggle(self) -> None:
        self._call("playback_pause" if self._playing else "playback_play")

    def _slid(self, value: int) -> None:
        if not self._updating:
            self._seek_timer.start()

    def _send_seek(self) -> None:
        self._call("playback_seek", index=int(self.slider.value()))

    def apply(self, v: dict) -> None:
        """display_view.playback_view の中身を映す。"""
        self.setVisible(bool(v.get("visible")))
        if not v.get("visible"):
            return
        self._playing = bool(v["playing"])
        self.play.setText(v["play_label"])
        key = tuple((s["code"], s["text"]) for s in v["speeds"])
        if key != self._speeds_key:
            self._speeds_key = key
            was = self.speed.blockSignals(True)
            self.speed.clear()
            for s in v["speeds"]:
                self.speed.addItem(s["text"], s["code"])
            self.speed.blockSignals(was)
            widget_sync.fit_combo(self.speed)
        widget_sync.set_data(self.speed, v["speed"])
        self._updating = True
        self.slider.setMaximum(max(0, int(v["total"]) - 1))
        if not self.slider.isSliderDown() and not self._seek_timer.isActive():
            self.slider.setValue(int(v["index"]))
        self._updating = False
        self.position.setText(v["position"])
        self.resolution.setText(v["resolution"])
