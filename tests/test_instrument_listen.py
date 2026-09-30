"""マーカーの位置を聞く機能（設計Q §6.8）を、計測器の本体と偽物の RTL-SDR で確かめる。"""
from __future__ import annotations

import unittest

import numpy as np

from pygigast.api.local import _SimulatorBackend
from pygigast.core.instrument_common import CommandError
from pygigast.core.listen import AUDIO_RATE, LISTEN_TUNER_OFFSET_HZ
from tests.test_instrument_rtlsdr import MHZ, RtlSdrCase


class TestListen(RtlSdrCase):
    def test_listen_produces_audio_and_restores_conditions(self):
        # 1kHz・変調度 50% の AM は合成が面倒なので、搬送波の横に −1kHz の弱い成分を置いて USB/AM で音にする
        self.lib.tones = [(84_700_000, 0.2), (84_701_000, 0.05)]
        self.connect_rtl(start=84 * MHZ, stop=86 * MHZ)
        before = self.call("get_state")["sweep"]
        self.single()
        m = self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.clear_events()
        res = self.call("listen_start", marker_id=m["id"], mode="am")
        lst = res["listen"]
        self.assertTrue(lst["active"])
        self.assertEqual(lst["freq_hz"], 84_700_000)
        self.assertEqual(lst["tuner_center_hz"], 84_700_000 - LISTEN_TUNER_OFFSET_HZ)
        # 2MHz の Span は使える幅に収まらないので、チューナーの中心の使える幅へ変えて知らせる
        self.assertTrue(res["span_limited"])
        self.assertTrue(self.notices("rtlsdr_span_limited"))
        st = self.call("get_state")
        self.assertEqual(st["run"]["state"], "continuous")
        self.assertEqual(st["rtlsdr"]["fixed_center"]["cause"], "listening")
        self.assertLessEqual(st["sweep"]["span_hz"], 2_000_000)
        chunks = []
        for _ in range(3):
            a = self.wait_for(lambda k, p: k == "audio" and id(p) not in {id(c) for c in chunks})
            chunks.append(a)
        a = chunks[0]
        self.assertEqual(a["rate"], AUDIO_RATE)
        self.assertEqual(a["samples"].dtype, np.float32)
        self.assertGreater(a["samples"].size, 0)
        self.assertEqual((a["marker_id"], a["mode"], a["freq_hz"]), (m["id"], "am", 84_700_000))
        audio = np.concatenate([c["samples"] for c in chunks])
        # 搬送波 0.2 と 1kHz 離れた 0.05 の AM の包絡線 → 1kHz の音（振幅 約 0.05）
        self.assertGreater(float(np.abs(audio[len(audio) // 2:]).max()), 0.01)
        # 使える幅を超える条件は受け付けない
        with self.assertRaises(CommandError) as cm:
            self.call("set_sweep", start_hz=80 * MHZ, stop_hz=90 * MHZ)
        self.assertEqual(cm.exception.detail["cause"], "listening")
        self.clear_events()
        self.call("listen_stop")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped")
        st = self.call("get_state")
        self.assertFalse(st["listen"]["active"])
        self.assertIsNone(st["rtlsdr"]["fixed_center"])
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (before["start_hz"], before["stop_hz"]))
        self.assertEqual(self.notices("listen_stopped")[0]["detail"]["reason"], "user")

    def test_listen_follows_marker_and_stops_when_removed(self):
        self.lib.tones = [(84_700_000, 0.2)]
        self.connect_rtl(start=84_500_000, stop=85_000_000)
        self.single()
        m = self.call("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.call("listen_start", marker_id=m["id"], mode="fm_narrow")
        self.call("marker_set", id=m["id"], freq_hz=84_710_000)
        self.assertEqual(self.call("get_state")["listen"]["freq_hz"], 84_710_000)
        self.wait_for(lambda k, p: k == "audio" and p["freq_hz"] == 84_710_000)
        self.clear_events()
        self.call("marker_remove", id=m["id"])
        n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "listen_stopped")
        self.assertEqual(n["detail"]["reason"], "marker_removed")

    def test_listen_refused_on_gigast(self):
        self.call("connect", backend="simulator", serial=_SimulatorBackend.SERIAL)
        self.call("run", mode="single")
        self.wait_for(lambda k, p: k == "trace")
        m = self.call("marker_add", kind="M", trace=1)
        with self.assertRaises(CommandError) as cm:
            self.call("listen_start", marker_id=m["id"], mode="am")
        self.assertEqual(cm.exception.code, "listen_needs_iq_device")

    def test_bad_mode_and_not_connected(self):
        with self.assertRaises(CommandError) as cm:
            self.call("listen_start", marker_id="M1", mode="am")
        self.assertEqual(cm.exception.code, "not_connected")
        self.connect_rtl()
        self.single()
        m = self.call("marker_add", kind="M", trace=1)
        with self.assertRaises(CommandError) as cm:
            self.call("listen_start", marker_id=m["id"], mode="cw")
        self.assertEqual(cm.exception.code, "bad_value")
        # 聞いていないときの停止は何もしない
        self.assertFalse(self.call("listen_stop")["listen"]["active"])


if __name__ == "__main__":
    unittest.main()
