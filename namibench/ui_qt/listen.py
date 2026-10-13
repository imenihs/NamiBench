"""マーカーの位置を聞く（設計Q §6.8）。右のタブ「マーカー」の欄と、イベント `audio` を鳴らす部品。

- 聞くのは選んでいるマーカー。方式（AM・FM狭・FM放送・USB・LSB）を選んで「この位置を聞く」
- 復調はコアが行い、48kHz の float32 の音をイベント `audio` で流す。ここは QtMultimedia の QAudioSink で鳴らすだけ
  （パッケージを増やさない: 設計Q §6.8）。音量はここだけの値（コアの状態ではない）
- 音を出す機器が無いときは、鳴らせないことを欄に出す（音は流れ続ける）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGroupBox, QLabel, QPushButton, QSlider

from . import screen_model, sizes, texts, widget_sync
from .tab_traces import set_enabled

if TYPE_CHECKING:
    from .main_window import MainWindow

# 鳴らす側にためてよい音の長さ（秒）。超えた分は古い方を捨てる（遅れを積み上げない）
MAX_BUFFER_S = 0.5
DEFAULT_VOLUME = 50


class AudioPlayer:
    """float32・モノラルの音を QAudioSink へ押し込む。初めて音が来たときに作る（音を出す機器が無くても落ちない）。"""

    def __init__(self) -> None:
        self._sink = None
        self._io = None
        self._rate: Optional[int] = None
        self.available: Optional[bool] = None
        self.volume = DEFAULT_VOLUME / 100.0

    def _open(self, rate: int) -> None:
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

        self.close()
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            self.available = False
            self._rate = rate
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(int(rate))
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        sink = QAudioSink(dev, fmt)
        sink.setBufferSize(int(rate * 4 * MAX_BUFFER_S))
        sink.setVolume(self.volume)
        io = sink.start()
        if io is None or sink.error() != QAudio.Error.NoError:
            self.available = False
            self._rate = rate
            return
        self._sink, self._io, self._rate = sink, io, rate
        self.available = True

    def play(self, samples, rate: int) -> None:
        if rate != self._rate:
            self._open(rate)
        if not self.available or self._io is None:
            return
        data = np.asarray(samples, dtype="<f4").tobytes()
        free = self._sink.bytesFree()
        if free <= 0:
            return  # 鳴らす側が詰まっている: この塊は捨てる（遅れをためない）
        self._io.write(data[: free - free % 4])

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(1.0, float(v)))
        if self._sink is not None:
            self._sink.setVolume(self.volume)

    def close(self) -> None:
        if self._sink is not None:
            self._sink.stop()
        self._sink = None
        self._io = None
        self._rate = None


class ListenControl:
    """聞く欄（マーカーのタブ）と、メニュー・右クリック・状態バーから呼ばれる「聞く」「やめる」。"""

    def __init__(self, win: "MainWindow") -> None:
        self.win = win
        self.player = AudioPlayer()
        L = texts.LISTEN_LABELS
        g = QGroupBox(L["group"])
        form = sizes.form(g)
        self.mode = QComboBox()
        for code, name in texts.LISTEN_MODES.items():
            self.mode.addItem(name, code)
        self.mode.activated.connect(self._mode_changed)
        self.start_btn = QPushButton(L["start"])
        self.start_btn.clicked.connect(self.start_selected)
        self.stop_btn = QPushButton(L["stop"])
        self.stop_btn.clicked.connect(self.stop)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(DEFAULT_VOLUME)
        self.volume.valueChanged.connect(lambda v: self.player.set_volume(v / 100.0))
        self.now = QLabel("")
        self.now.setWordWrap(True)
        self.now.setObjectName("warn")
        row = sizes.hbox()
        for w in (self.mode, self.start_btn, self.stop_btn):
            row.addWidget(w)
        row.addStretch(1)
        form.addRow(L["mode"], row)
        form.addRow(L["volume"], self.volume)
        form.addRow(self.now)
        self.panel = g
        self._view: dict = {"shown": False, "active": False, "reason": texts.LISTEN_LABELS["not_iq"]}
        g.setVisible(False)

    # ---- 使えるか
    def start_reason(self) -> Optional[str]:
        return self._view.get("reason") or (None if self.win.selected_marker else texts.REASON_TEXTS["select_marker"])

    def apply(self, st: dict) -> None:
        self._view = screen_model.listen_view(st)
        v = self._view
        # IQ を持つ機器に繋いでいるときだけ出す（設計J §4.5。受信の欄と同じ決まり）
        self.panel.setVisible(bool(v.get("shown")))
        if v["active"] and v.get("mode"):
            widget_sync.set_data(self.mode, v["mode"])
        self.refresh()
        if not v["active"]:
            self.player.close()

    def refresh(self) -> None:
        """選んでいるマーカーが変わったときにも呼ぶ。"""
        v = self._view
        why = self.start_reason()
        set_enabled(self.start_btn, why, texts.LISTEN_LABELS["start"])
        set_enabled(self.mode, v.get("reason"))
        set_enabled(self.stop_btn, v.get("stop_reason"))
        text = v.get("now_text") or ""
        if v["active"] and self.player.available is False:
            text += "\n" + texts.TEMPLATES["audio_none"]
        self.now.setText(text)
        self.now.setVisible(bool(text))
        acts = self.win.actions
        from . import menus

        menus.set_reason(acts["marker_listen"], why)
        menus.set_reason(acts["listen_stop"], v.get("stop_reason"))

    # ---- 操作
    def start_selected(self) -> None:
        mid = self.win.selected_marker
        why = self.start_reason()
        if why:
            self.win.status_note(why)
            return
        self.win.call("listen_start", marker_id=mid, mode=self.mode.currentData())

    def _mode_changed(self, _i: int) -> None:
        # 聞いている間に方式を変えたら、同じマーカーで聞き直す
        if self._view.get("active") and self._view.get("marker_id"):
            self.win.call("listen_start", marker_id=self._view["marker_id"], mode=self.mode.currentData())

    def stop(self) -> None:
        self.win.call("listen_stop")

    def on_audio(self, payload: dict) -> None:
        was = self.player.available
        self.player.play(payload.get("samples"), int(payload.get("rate") or 48000))
        if was is None and self.player.available is False:
            self.refresh()

    def close(self) -> None:
        self.player.close()
