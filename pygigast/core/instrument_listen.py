"""計測器の本体のうち、マーカーの位置を聞く機能（設計Q §6.8・§4.3 の②）。

Instrument（instrument.py）に混ぜて使う。`_` で始まるメソッドは、断りが無ければ制御スレッドから呼ばれる。

- 聞けるのは IQ を持つ機器（RTL-SDR）だけ。GigaSt のゼロスパンの AM は実験の項目のまま（設計Q §4.3）なので、
  いまは理由 listen_needs_iq_device で受け付けない
- 聞いている間のチューナーの中心 = マーカー − 300kHz（機種の範囲の下限を割るときだけ ＋300kHz）。
  中心は動かさない（リアルタイムの動き）。表示の範囲がその使える幅に収まらなければ、使える幅に変えて知らせる
- 復調（core/listen の Demodulator）は機器スレッドで IQ を読むたびに行い、48kHz の float32 の音を
  制御スレッドへ渡してイベント `audio` で流す。鳴らすのは画面の側（設計Q §6.8）
- やめたら聞く前の条件（掃引条件と、止まっていたなら止まった状態）に戻す
"""
from __future__ import annotations

import dataclasses
from typing import Optional

import numpy as np

from .instrument_common import CommandError
from .instrument_rtlsdr import CAUSE_LISTEN
from .listen import AUDIO_RATE, LISTEN_TUNER_OFFSET_HZ, Demodulator
from .listen import Mode as ListenMode


@dataclasses.dataclass
class _ListenRuntime:
    """機器スレッドが使う復調の状態。制御スレッドは丸ごと置き換える（中身を書き換えるのは freq_hz だけ）。"""

    demod: Demodulator
    marker_id: str
    mode: str
    freq_hz: int
    offset_hz: float


class ListenMixin:
    """マーカーの位置を聞く（Instrument に混ぜる）。"""

    def _init_listen(self) -> None:
        # 聞いている間の中身（状態・戻す条件）。None は聞いていない。機器スレッドは tuner_center_hz を読む
        self._listen: Optional[dict] = None
        self._listen_rt: Optional[_ListenRuntime] = None

    def _listen_state(self) -> dict:
        lst = self._listen
        if lst is None:
            return {"active": False, "marker_id": None, "mode": None, "freq_hz": None, "tuner_center_hz": None,
                    "audio_rate": AUDIO_RATE}
        return {"active": True, "marker_id": lst["marker_id"], "mode": lst["mode"], "freq_hz": lst["freq_hz"],
                "tuner_center_hz": lst["tuner_center_hz"], "audio_rate": AUDIO_RATE}

    # ------------------------------------------------------------------ 命令

    def _cmd_listen_start(self, marker_id, mode: str = "am") -> dict:
        if self._conn != "connected" or self._device is None:
            raise CommandError("not_connected")
        if not self._is_rtlsdr():
            raise CommandError("listen_needs_iq_device", None, {"model": self._profile.get("model")})
        try:
            m = ListenMode(mode)
        except ValueError:
            raise CommandError("bad_value", "mode", {"choices": [x.value for x in ListenMode]}) from None
        self._check_not_taking_reference()
        if self._rtl_tcp_center is not None and self._listen is None:
            # rtl_tcp の相手がチューナーの中心を決めている間は、マーカーの位置へ動かせない
            raise CommandError("listen_rtltcp_active")
        row = self._listen_marker_row(marker_id)
        f = int(row["freq_hz"])
        fmin, fmax = self._rtlsdr_range()
        tuner = f - LISTEN_TUNER_OFFSET_HZ
        if tuner < fmin:
            tuner = f + LISTEN_TUNER_OFFSET_HZ  # 下限を割るときだけ上へずらす（設計Q §6.8 の実装で決めたこと）
        prev = self._listen
        info = {
            "marker_id": row["id"], "mode": m.value, "freq_hz": f, "tuner_center_hz": tuner,
            "model": self._profile.get("model"),
            # 聞く前の条件（別のマーカー・方式へ切り替えたときは最初の条件のまま）
            "prev_adopted": self._adopted if prev is None else prev["prev_adopted"],
            "prev_run": self._run if prev is None else prev["prev_run"],
        }
        self._listen = info
        try:
            limited = self._rtlsdr_fit_display(tuner, CAUSE_LISTEN, record_undo=False) is not None
        except CommandError:
            self._listen = prev
            raise
        fs = int(self._profile["fft"]["sample_rate_hz"])
        self._listen_rt = _ListenRuntime(Demodulator(m, fs, offset_hz=f - tuner), row["id"], m.value, f,
                                         float(f - tuner))
        if self._run not in ("continuous",):
            # IQ は測っている間だけ届くので、止まっていれば連続で測る（やめたら止まった状態へ戻す）
            self._cmd_run("continuous")
        self._push_job()
        self._changed()
        return {"listen": self._listen_state(), "span_limited": limited}

    def _cmd_listen_stop(self) -> dict:
        if self._listen is not None:
            self._listen_end("user")
        return {"listen": self._listen_state()}

    def _listen_marker_row(self, marker_id) -> dict:
        m = self._marker(marker_id)
        row = next((r for r in self._marker_snap.get("markers", []) if r.get("id") == m.id), None)
        if row is None or row.get("freq_hz") is None:
            raise CommandError("marker_no_value", "marker_id", {"id": m.id})
        return row

    # ------------------------------------------------------------------ やめる

    def _listen_end(self, reason: str, restore: bool = True) -> None:
        """聞くのをやめ、聞く前の条件に戻す（機種が変わっていたら戻さない）。"""
        info = self._listen
        if info is None:
            return
        self._listen = None
        self._listen_rt = None
        if restore and info["model"] == self._profile.get("model"):
            prev = dataclasses.replace(info["prev_adopted"], adjustments=(), alternative=None)
            try:
                self._rtlsdr_check_condition(prev)
            except CommandError:
                # rtl_tcp の相手がつながった後で、戻す条件が使える幅に収まらない: 相手の中心の使える幅へ
                prev = None
            if prev is not None:
                self._set_adopted(prev)
            elif self._rtl_tcp_center is not None:
                self._rtlsdr_fit_display(self._rtl_tcp_center, "rtltcp_clients", record_undo=False)
            if info["prev_run"] == "stopped" and self._run in ("continuous", "single"):
                self._cmd_stop()
        self._push_job()
        self._notice("listen_stopped", {"reason": reason, "marker_id": info["marker_id"]})
        self._changed()

    # ------------------------------------------------------------------ マーカーを動かしたとき

    def _listen_follow_marker(self) -> None:
        """マーカー表を作り直したあと: 聞いているマーカーの周波数へ復調の位置を合わせる。

        チューナーの中心は動かさない（使える幅の中で位置だけを変える）。マーカーを消したら聞くのをやめる。
        """
        info = self._listen
        rt = self._listen_rt
        if info is None or rt is None:
            return
        row = next((r for r in self._marker_snap.get("markers", []) if r.get("id") == info["marker_id"]), None)
        if row is None:
            # マーカー表を作っている途中なので、やめるのは制御スレッドの次の仕事にする
            self._q.put(("call", lambda: self._listen_end("marker_removed")))
            return
        f = row.get("freq_hz")
        if f is None or int(f) == info["freq_hz"]:
            return
        _grid, h = self._rtlsdr_grid_half(self._adopted.rbw_hz)
        if abs(int(f) - info["tuner_center_hz"]) > h:
            return  # 使える幅の外: いまの位置のまま聞く
        info["freq_hz"] = int(f)
        rt.freq_hz = int(f)

    # ------------------------------------------------------------------ 機器スレッド

    def _listen_feed(self, rt: _ListenRuntime, iq: np.ndarray, center: int, fs: int) -> None:
        """読み取った IQ を復調し、音を制御スレッドへ渡す（機器スレッドから呼ぶ）。"""
        off = float(rt.freq_hz - center)
        if off != rt.offset_hz:
            rt.demod.set_offset(off)
            rt.offset_hz = off
        audio = rt.demod.process(iq)
        if audio.size:
            self._q.put(("call", lambda: self._listen_emit(rt, audio)))

    def _listen_emit(self, rt: _ListenRuntime, audio: np.ndarray) -> None:
        if self._listen_rt is not rt:
            return  # やめた後に届いた音は流さない
        self._emit("audio", {"rate": AUDIO_RATE, "samples": audio, "marker_id": rt.marker_id, "mode": rt.mode,
                             "freq_hz": rt.freq_hz})
