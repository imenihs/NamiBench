"""「利得を合わせる」（設計Q §6.5・実装リスト 8-10）。偽の DLL の信号を利得に比例させ、ある段より上で振り切れさせる。"""
from __future__ import annotations

import unittest

import numpy as np

from tests.test_instrument_rtlsdr import FakeHandle, FakeLib, MHZ, RtlSdrCase

TONE_HZ = 84_700_000


class GainHandle(FakeHandle):
    """入れた手動の利得に合わせて信号の大きさが変わる偽物（振幅 = base × 10^((利得 − 29.7dB)/20)）。"""

    def __init__(self, lib, index):
        super().__init__(lib, index)
        self.gain_tenth = 297

    def set_manual_gain(self, g):
        super().set_manual_gain(g)
        self.gain_tenth = g

    def read(self, nbytes):
        scale = 10 ** ((self.gain_tenth / 10.0 - 29.7) / 20.0)
        saved = self.lib.tones
        self.lib.tones = [(f, a * scale) for f, a in saved]
        try:
            return super().read(nbytes)
        finally:
            self.lib.tones = saved


class GainLib(FakeLib):
    def open(self, i):
        if i in self.opened:
            raise AssertionError("二重に開いた")
        self.opened.add(i)
        h = GainHandle(self, i)
        self.handles.append(h)
        return h


class FitGainTest(RtlSdrCase):
    def setUp(self):
        super().setUp()
        self.lib = GainLib(self.serials)

    def fit(self):
        self.clear_events()
        r = self.call("rtlsdr_fit_gain")
        self.assertIn("task_id", r)
        return self.wait_for(lambda k, p: k == "notice" and p["code"] in ("task_done", "task_failed", "task_cancelled")
                             and p["detail"].get("task") == "rtlsdr_fit_gain")

    def test_picks_highest_step_without_overload(self):
        # 29.7dB で 0.9（振り切れない）、次の段 32.8dB で約 1.28（振り切れる）
        self.lib.tones = [(TONE_HZ, 0.9)]
        self.connect_rtl()
        self.call("rtlsdr_set", gain_db=49.6)
        self.call("set_trace", number=1, action="max_hold")
        self.single()
        n = self.fit()
        self.assertEqual(n["code"], "task_done")
        res = n["detail"]["result"]
        self.assertAlmostEqual(res["gain_db"], 29.7)
        self.assertFalse(res["overload"])
        self.assertAlmostEqual(res["previous_gain_db"], 49.6)
        # 最大保持の蓄積を捨てた（知らせは task_done の1件に入れる。trace_reset は別に出さない）
        self.assertEqual(res["traces_reset"], [1])
        self.assertEqual(self.notices("trace_reset"), [])
        st = self.call("get_state")
        self.assertAlmostEqual(st["rtlsdr"]["gain_db"], 29.7)
        self.assertAlmostEqual(st["rtlsdr"]["gain_db_actual"], 29.7)
        self.assertFalse(st["rtlsdr"]["agc"])
        self.assertFalse(st["rtlsdr"]["fitting"])
        # 設定に残す（次の起動も同じ段）
        self.assertAlmostEqual(self.inst._settings.get("rtlsdr")["gain_db"], 29.7)
        # 上の段から試した（49.6 から 29.7 まで、振り切れた段では次の段へ）
        gains = [c[1] for c in self.lib.handles[0].calls if c[0] == "set_manual_gain"]
        self.assertIn(496, gains)
        self.assertEqual(gains[-1], 297)
        # 合わせた後の掃引は振り切れない
        self.single()
        self.assertFalse(self.call("get_state")["rtlsdr"]["overload"])

    def test_texts_for_the_one_notice(self):
        from namibench.ui_qt import texts

        self.lib.tones = [(TONE_HZ, 0.9)]
        self.connect_rtl()
        self.call("rtlsdr_set", gain_db=49.6)
        self.call("set_trace", number=1, action="max_hold")
        self.single()
        n = self.fit()
        v = texts.notice_view(n)
        self.assertIn("29.7 dB", v["text"])
        self.assertIn("49.6 dB", v["text"])
        self.assertIn("トレース 1", v["text"])
        self.assertIn("1", v["traces"])

    def test_lowest_step_still_overloads(self):
        self.lib.tones = [(TONE_HZ, 50.0)]
        self.connect_rtl()
        n = self.fit()
        res = n["detail"]["result"]
        self.assertEqual(res["gain_db"], 0.0)
        self.assertTrue(res["overload"])
        from namibench.ui_qt import texts

        self.assertIn("いちばん小さい段でも振り切れています", texts.notice_view(n)["text"])

    def test_refused_under_agc_and_without_rtlsdr(self):
        from namibench.core.instrument_common import CommandError

        with self.assertRaises(CommandError) as cm:
            self.call("rtlsdr_fit_gain")
        self.assertEqual(cm.exception.code, "rtlsdr_not_connected")
        self.connect_rtl()
        self.call("rtlsdr_set", agc=True)
        with self.assertRaises(CommandError) as cm:
            self.call("rtlsdr_fit_gain")
        self.assertEqual(cm.exception.code, "rtlsdr_fit_needs_manual_gain")

    def test_button_reason(self):
        from namibench.ui_qt import screen_model, texts

        v = screen_model.rtl_view({"rtlsdr": {"connected": False}})
        self.assertEqual(v["fit_reason"], texts.RTL_LABELS["not_rtlsdr"])
        v = screen_model.rtl_view({"rtlsdr": {"connected": True, "agc": True}})
        self.assertEqual(v["fit_reason"], texts.RTL_LABELS["fit_needs_manual"])
        v = screen_model.rtl_view({"rtlsdr": {"connected": True, "agc": False, "fitting": True}})
        self.assertEqual(v["fit_reason"], texts.RTL_LABELS["fit_running"])
        v = screen_model.rtl_view({"rtlsdr": {"connected": True, "agc": False}})
        self.assertIsNone(v["fit_reason"])

    def test_cancel_restores_previous_gain(self):
        import threading

        self.lib.tones = [(TONE_HZ, 50.0)]
        self.connect_rtl()
        self.call("rtlsdr_set", gain_db=49.6)
        h = self.lib.handles[0]
        gate = threading.Event()
        orig = h.read

        def slow(nbytes):
            gate.wait(5)
            return orig(nbytes)

        h.read = slow
        self.clear_events()
        r = self.call("rtlsdr_fit_gain")
        self.assertTrue(self.call("get_state")["rtlsdr"]["fitting"])
        self.call("cancel_task", task_id=r["task_id"])
        gate.set()
        n = self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                          and p["detail"].get("task") == "rtlsdr_fit_gain")
        self.assertEqual(n["code"], "task_cancelled")
        h.read = orig
        st = self.call("get_state")
        self.assertAlmostEqual(st["rtlsdr"]["gain_db"], 49.6)
        self.assertAlmostEqual(st["rtlsdr"]["gain_db_actual"], 49.6)
        self.assertEqual([c for c in h.calls if c[0] == "set_manual_gain"][-1][1], 496)


if __name__ == "__main__":
    unittest.main()
