"""掃引条件モデルの sweep_kind "fft"（設計Q §6.4）の試験。RTL-SDR の機種のプロファイルで確かめる。"""
from __future__ import annotations

import unittest

from pygigast.core.devices.rtlsdr import driver
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import Mode, SweepRequest

MHZ = 1_000_000
KHZ = 1_000
FMIN = 24 * MHZ
FMAX = 1766 * MHZ


def adj_map(s):
    return {a.field: a for a in s.adjustments}


class Base(unittest.TestCase):
    def setUp(self):
        self.profile = driver.default_profile()
        self.m = SweepModel(self.profile, driver.timing_fn(self.profile))
        self.cur = self.m.default()

    def at(self, start, stop, current=None, **kw):
        return self.m.apply(SweepRequest(start_hz=start, stop_hz=stop, edited=("start_hz", "stop_hz"), **kw),
                            current or self.cur)

    def req(self, current=None, **kw):
        return self.m.apply(SweepRequest(**kw), current or self.cur)

    def assertAxisOk(self, s):
        ax = s.axis
        _, n = self.m.fft_setting(s.rbw_hz)
        self.assertEqual(ax.step_hz, 2_048_000 // n)
        self.assertEqual(ax.start_hz, s.start_hz)
        self.assertEqual(ax.points, -(-(s.stop_hz - s.start_hz) // ax.step_hz) + 1 - (1 if s.tail_gap else 0))
        self.assertLessEqual(ax.points, 200_000)
        self.assertTrue(FMIN <= ax.start_hz and ax.last_hz <= FMAX)
        self.assertEqual(s.preroll_points, 0)
        self.assertEqual(s.transient_head_points, 0)
        self.assertIsNone(s.points_setting)
        for v in (s.start_hz, s.stop_hz, ax.step_hz, ax.points):
            self.assertIs(type(v), int)


class TestFft(Base):
    def test_kind(self):
        self.assertEqual(self.m.sweep_kind, "fft")
        self.assertEqual(self.m.fft_setting(1500), ("hann", 2048))
        self.assertEqual(self.m.fft_setting(3770), ("flattop", 2048))

    def test_profile_rbw_matches_enbw(self):
        from pygigast.core.devices.rtlsdr import spectrum as sp
        for c in self.profile["rbw"]["choices"]:
            self.assertEqual(c["rbw_hz"], sp.rbw_hz(c["window"], c["fft_n"], 2_048_000), c)

    def test_default_uses_profile_default_range(self):
        # 範囲いっぱいは1回の測定に約 117s かかるので、機種のプロファイルの既定の範囲（FM 放送帯）で始める
        s = self.cur
        self.assertEqual((s.start_hz, s.stop_hz), (76 * MHZ, 95 * MHZ))
        self.assertEqual((s.rbw_hz, s.axis.step_hz, s.axis.points), (1500, 1000, 19_001))
        self.assertNotIn("rbw_hz", adj_map(s))
        self.assertEqual(s.mode, Mode.SA)
        self.assertAxisOk(s)

    def test_default_range_outside_profile_rejected(self):
        for a, b in ((FMIN - 1, 95 * MHZ), (76 * MHZ, FMAX + 1), (95 * MHZ, 95 * MHZ), (95 * MHZ, 76 * MHZ)):
            p = driver.default_profile()
            p["freq"]["default_start_hz"], p["freq"]["default_stop_hz"] = a, b
            with self.assertRaises(ValueError, msg=(a, b)):
                SweepModel(p, driver.timing_fn(p))

    def test_default_full_range_widens_rbw(self):
        # 既定の範囲を持たないプロファイルは範囲いっぱい
        p = driver.default_profile()
        del p["freq"]["default_start_hz"], p["freq"]["default_stop_hz"]
        s = SweepModel(p, driver.timing_fn(p)).default()
        self.assertEqual((s.start_hz, s.stop_hz), (FMIN, FMAX))
        self.assertEqual(s.rbw_preference_hz, 1500)
        # 1500/3000/6000/12000Hz では点数が 20 万を超える。24kHz（格子 16kHz）で 108,876 点
        self.assertEqual(s.rbw_hz, 24000)
        self.assertEqual(s.axis.points, 108_876)
        self.assertEqual(adj_map(s)["rbw_hz"].reason, "rbw_points_max")
        self.assertAxisOk(s)

    def test_fm_band(self):
        s = self.at(76 * MHZ, 95 * MHZ, rbw_preference_hz=1500)
        self.assertEqual((s.rbw_hz, s.axis.step_hz, s.axis.points), (1500, 1000, 19_001))
        self.assertNotIn("rbw_hz", adj_map(s))
        # 25 位置 ×（53ms ＋ 8×2048×2 = 32768B の読み 8ms）
        self.assertAlmostEqual(s.estimated_sweep_s, 25 * (0.053 + 32768 / 4_096_000), places=6)
        self.assertAxisOk(s)

    def test_realtime_estimate(self):
        s = self.at(84_200_000, 85_000_000, rbw_preference_hz=188)
        self.assertEqual(s.axis.step_hz, 125)
        # 1位置・中心を動かさない: 読みだけ（8×16384×2 = 262144B = 64ms）
        self.assertAlmostEqual(s.estimated_sweep_s, 262144 / 4_096_000, places=6)

    def test_points_max_boundary(self):
        # N−1 / N / N+1: 格子 1kHz で 199,999 / 200,000 / 200,001 点
        cur = self.at(100 * MHZ, 101 * MHZ, rbw_preference_hz=1500)
        for span, rbw in ((199_998_000, 1500), (199_999_000, 1500), (199_999_001, 3000)):
            s = self.at(100 * MHZ, 100 * MHZ + span, current=cur)
            self.assertEqual(s.rbw_hz, rbw, span)
            self.assertEqual(s.rbw_preference_hz, 1500)
            self.assertAxisOk(s)
            if rbw != 1500:
                self.assertEqual(adj_map(s)["rbw_hz"].reason, "rbw_points_max")

    def test_preference_kept_and_restored(self):
        wide = self.at(100 * MHZ, 900 * MHZ, rbw_preference_hz=188)
        self.assertGreater(wide.rbw_hz, 188)
        self.assertEqual(wide.rbw_preference_hz, 188)
        # 同じ条件を続けても知らせを繰り返さない
        again = self.at(100 * MHZ, 900 * MHZ, current=wide)
        self.assertNotIn("rbw_hz", adj_map(again))
        narrow = self.at(100 * MHZ, 101 * MHZ, current=wide)
        self.assertEqual(narrow.rbw_hz, 188)

    def test_flattop_ladder_stays_flattop(self):
        s = self.at(FMIN, FMAX, rbw_preference_hz=471)
        self.assertEqual(self.m.fft_setting(s.rbw_hz)[0], "flattop")
        self.assertAxisOk(s)

    def test_unknown_rbw_rejected(self):
        with self.assertRaises(ValueError):
            self.req(rbw_preference_hz=180000)

    def test_zero_span_not_supported(self):
        s = self.req(center_hz=100 * MHZ, span_hz=0, edited=("center_hz", "span_hz"))
        self.assertEqual(s.span_hz, 1000)
        self.assertEqual(adj_map(s)["span_hz"].reason, "zero_span_unsupported")
        self.assertAxisOk(s)

    def test_min_span(self):
        for span, want in ((999, 1000), (1000, 1000), (1001, 1001)):
            s = self.req(center_hz=100 * MHZ, span_hz=span, edited=("center_hz", "span_hz"))
            self.assertEqual(s.span_hz, want)
            self.assertAxisOk(s)

    def test_points_request_ignored(self):
        s = self.req(points=5000)
        self.assertIsNone(s.points_setting)
        self.assertEqual(adj_map(s)["points"].reason, "points_by_rbw")
        s2 = self.req(points=0)
        self.assertNotIn("points", adj_map(s2))

    def test_unsupported_mode_and_image_cancel(self):
        s = self.req(mode=Mode.TG, image_cancel=True)
        self.assertEqual(s.mode, Mode.SA)
        self.assertFalse(s.image_cancel)
        a = adj_map(s)
        self.assertEqual(a["mode"].reason, "mode_unsupported")
        self.assertEqual(a["image_cancel"].reason, "image_cancel_unsupported")

    def test_sg_rejected(self):
        with self.assertRaises(ValueError):
            self.req(sg_hz=100 * MHZ)

    def test_unit_is_1hz(self):
        s = self.at(76_000_001, 76_500_003, rbw_preference_hz=188)
        self.assertEqual((s.start_hz, s.stop_hz), (76_000_001, 76_500_003))
        self.assertAxisOk(s)
        self.assertGreaterEqual(s.axis.last_hz, s.stop_hz)

    def test_center_rules_unchanged(self):
        # 範囲いっぱいで Center に 30MHz: Center を守り Span を縮める（設計C §5.11）
        s = self.req(center_hz=30 * MHZ, edited=("center_hz",))
        self.assertEqual((s.center_hz, s.span_hz), (30 * MHZ, 12 * MHZ))
        self.assertEqual(adj_map(s)["span_hz"].reason, "keep_center")
        self.assertIsNotNone(s.alternative)
        # Span 固定: Span を守って Center を動かす
        base = self.at(100 * MHZ, 110 * MHZ)
        locked = self.req(current=base, span_lock=True)
        s = self.req(current=locked, center_hz=FMAX, edited=("center_hz",))
        self.assertEqual((s.start_hz, s.stop_hz), (FMAX - 10 * MHZ, FMAX))
        self.assertAxisOk(s)

    def test_limits(self):
        for start, want in ((FMIN - 1, FMIN), (FMIN, FMIN), (FMIN + 1, FMIN + 1)):
            s = self.req(start_hz=start, edited=("start_hz",))
            self.assertEqual(s.start_hz, want)
        for stop, want in ((FMAX - 1, FMAX - 1), (FMAX, FMAX), (FMAX + 1, FMAX)):
            s = self.at(FMAX - 3 * MHZ, stop, rbw_preference_hz=1500)
            self.assertEqual(s.stop_hz, want)
            self.assertAxisOk(s)

    def test_tail_gap_at_upper_limit(self):
        # Span が格子で割り切れず、超えた最後の点が上限の外 → 1点減らして印
        s = self.at(FMAX - 1_000_500, FMAX, rbw_preference_hz=1500)
        self.assertTrue(s.tail_gap)
        self.assertLess(s.axis.last_hz, s.stop_hz)
        self.assertAxisOk(s)
        s2 = self.at(FMAX - 1_000_000, FMAX, rbw_preference_hz=1500)
        self.assertFalse(s2.tail_gap)
        self.assertEqual(s2.axis.last_hz, FMAX)

    def test_estimates_every_rbw_and_edges(self):
        for rbw in self.m._rbw_choices:
            for start, stop in ((FMIN, FMIN + 700 * KHZ), (FMIN, FMIN + 3 * MHZ), (FMAX - 3 * MHZ, FMAX),
                                (FMAX - 700 * KHZ, FMAX)):
                s = self.at(start, stop, rbw_preference_hz=rbw)
                self.assertGreater(s.estimated_sweep_s, 0)
                self.assertAxisOk(s)


if __name__ == "__main__":
    unittest.main()
