"""ゼロスパンの時間軸と流す表示（設計N §1）のコアの計算（core/zero_span.py・units の時間・掃引条件の時間の見積もり）。

- 各点の時刻 = 掃引の開始時刻 +（前置き + 点番号）× 1点あたりの時間（推定）
- 流す表示: いちばん新しい点が 0、古いものは負。持つのは直近 60 秒。時刻が重なったら前の掃引の後ろに並べる
- 描く値（区切りごとの最大・最小保持は最小）は掃引の間を NaN（空白）にし、別の掃引の点を線でつながない
- 区間の最大・最小・平均（パワー平均）は全部の点から。最大・最小は位置も返す
- 時間の文字の読み書き（ms・s・μs）と、Web（units.js）が同じ結果になること
"""
import json
import math
import shutil
import subprocess
import unittest

import numpy as np

from pygigast.core import zero_span as zs
from pygigast.core import view_units
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.gigast.driver import timing_fn
from pygigast.core.instrument import GIGAST_PROFILE_PATH
from pygigast.core.storage import adopted_from_dict, adopted_to_dict
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import Mode, SweepRequest
from pygigast.core.units import ParseError, format_time_us, parse_time_us, time_axis_unit

MHZ = 1_000_000
KHZ = 1_000


class PointTimeTest(unittest.TestCase):
    def test_first_point_and_offsets(self):
        # 前置き 215 点を先に測るので、表示する最初の点は開始から 215 × 0.14ms 後
        self.assertAlmostEqual(zs.first_point_time(1000.0, 215, 0.00014), 1000.0 + 0.0301)
        self.assertEqual(zs.offsets_us(4, 0.00014).tolist(), [0, 140, 280, 420])
        self.assertEqual(zs.x_range_us(0, 500, 0.00014), (0, 499 * 140))
        self.assertEqual(zs.x_range_us(10, 500, 0.00014), (-10_000_000, 0))

    def test_model_sets_point_time(self):
        prof = load_profile(GIGAST_PROFILE_PATH)
        m = SweepModel(prof, timing_fn(prof))
        sa = m.apply(SweepRequest(center_hz=100 * MHZ, span_hz=0, edited=("center_hz", "span_hz")), m.default())
        self.assertAlmostEqual(sa.point_time_s, 0.00014)  # SA・Sample 0（設計B §7.2）
        tg = m.apply(SweepRequest(mode=Mode.TG), sa)
        self.assertAlmostEqual(tg.point_time_s, 0.000084)  # 信号源を出しているモード
        # 記録・保存の条件にも入り、読み戻せる（記録の各点の時刻を後から出せる）
        self.assertAlmostEqual(adopted_from_dict(adopted_to_dict(tg)).point_time_s, 0.000084)
        old = adopted_to_dict(tg)
        del old["point_time_s"]
        self.assertEqual(adopted_from_dict(old).point_time_s, 0.0)  # 前の版の条件は 0

    def test_rbw15k_zero_span_warning(self):
        prof = load_profile(GIGAST_PROFILE_PATH)
        m = SweepModel(prof, timing_fn(prof))
        z = m.apply(SweepRequest(center_hz=100 * MHZ, span_hz=0, edited=("center_hz", "span_hz"),
                                 rbw_preference_hz=15 * KHZ), m.default())
        self.assertIn("rbw15k_zero_span", z.warnings)
        self.assertNotIn("rbw15k_unstable", z.warnings)
        s = m.apply(SweepRequest(span_hz=1 * MHZ, edited=("span_hz",)), z)
        self.assertIn("rbw15k_unstable", s.warnings)

    def test_view_option(self):
        self.assertIsNone(view_units.check_options(zero_span_s=60))
        for bad in (5, -1, True, "10"):
            self.assertEqual(view_units.check_options(zero_span_s=bad), "zero_span_s")
        self.assertEqual(view_units.restore({"zero_span_s": 0})["zero_span_s"], 0)
        self.assertEqual(view_units.restore({"zero_span_s": 7})["zero_span_s"], 10)  # 読めない値は既定
        self.assertEqual(view_units.restore(None)["zero_span_s"], 10)


def _sweep(level, n=5):
    return np.full(n, level, dtype=np.float32)


class HistoryTest(unittest.TestCase):
    def test_window_relative_to_newest(self):
        h = zs.History()
        # 1点 0.1s・5点（0.4s）の掃引を 1s ごとに
        for k in range(3):
            h.add_sweep(k, 100.0 + k, 0.1, {1: (_sweep(-10.0 - k), {})})
        w = h.window(1, 10)
        self.assertEqual(w.x_us[-1], 0)  # いちばん新しい点が 0
        self.assertEqual(w.x_us[0], -2_400_000)
        self.assertEqual(w.sweep_no.tolist(), [0] * 5 + [1] * 5 + [2] * 5)
        # 表示する長さで切る（−1s より古い点は出さない）
        w1 = h.window(1, 1)
        self.assertTrue((w1.x_us >= -1_000_000).all())
        self.assertEqual(w1.sweep_no.tolist(), [1, 2, 2, 2, 2, 2])
        self.assertIsNone(h.window(2, 10))

    def test_overlap_is_placed_after(self):
        h = zs.History()
        h.add_sweep(0, 100.0, 0.1, {1: (_sweep(-10.0), {})})
        t0 = h.add_sweep(1, 100.2, 0.1, {1: (_sweep(-11.0), {})})  # 前の掃引（〜100.4）と重なる
        self.assertAlmostEqual(t0, 100.5)
        w = h.window(1, 10)
        self.assertTrue((np.diff(w.x_us) > 0).all())

    def test_keep_and_clear(self):
        h = zs.History(keep_s=2.0)
        for k in range(10):
            h.add_sweep(k, 100.0 + k, 0.1, {1: (_sweep(-10.0), {}), 2: (_sweep(-20.0), {})})
        self.assertEqual(sorted(set(h.window(1, 60).sweep_no.tolist())), [7, 8, 9])
        h.clear(2)
        self.assertIsNone(h.window(2, 60))
        self.assertIsNotNone(h.window(1, 60))
        h.clear()
        self.assertIsNone(h.window(1, 60))


class BinsTest(unittest.TestCase):
    def test_gaps_are_blank_and_max_per_bin(self):
        h = zs.History()
        lv = np.array([-30, -20, -40, -35, -50], dtype=np.float32)
        h.add_sweep(0, 100.0, 0.001, {1: (lv, {})})
        h.add_sweep(1, 100.5, 0.001, {1: (lv - 1, {})})
        w = h.window(1, 1)
        start, step, v, _f = zs.bins(w, 1, 0.001, max_bins=1000)
        self.assertAlmostEqual(step, 1000.1, places=3)  # 1点あたりより少し広い（1回の掃引の中に空の区切りを作らない）
        self.assertTrue(np.isnan(v).any())  # 掃引の間（約 0.5s）は空白
        self.assertAlmostEqual(float(np.nanmax(v)), -20.0)
        _s, _st, vmin, _f = zs.bins(w, 1, 0.001, use_min=True, max_bins=1000)
        self.assertAlmostEqual(float(np.nanmin(vmin)), -51.0)

    def test_adjacent_bins_of_different_sweeps_are_split(self):
        # 区切りより短い途切れ: 隣の区切りが別の掃引なら後ろを空白にして線をつながない
        h = zs.History()
        h.add_sweep(0, 100.0, 0.01, {1: (_sweep(-10.0, 10), {})})
        h.add_sweep(1, 100.0 + 0.09 + 0.012, 0.01, {1: (_sweep(-20.0, 10), {})})
        w = h.window(1, 1)
        _s, _st, v, _f = zs.bins(w, 1, 0.01, max_bins=50)  # 区切り 20ms > 途切れ 12ms
        finite = np.flatnonzero(np.isfinite(v))
        self.assertTrue(len(finite) > 2)
        runs = np.split(finite, np.flatnonzero(np.diff(finite) > 1) + 1)
        self.assertEqual(len(runs), 2)  # 2回の掃引が別の線になる

    def test_flags_are_kept_for_dimming(self):
        h = zs.History()
        h.add_sweep(0, 100.0, 0.001, {1: (_sweep(-10.0), {"out_of_cal_range": np.ones(5, bool)})})
        _s, _st, _v, f = zs.bins(h.window(1, 1), 1, 0.001)
        self.assertTrue(f["out_of_cal_range"].any())


class IntervalStatsTest(unittest.TestCase):
    def test_power_average_and_positions(self):
        x = np.array([0, 10, 20, 30], dtype=np.int64)
        lv = np.array([-10.0, -20.0, np.nan, -30.0])
        ok = np.array([True, True, True, True])
        s = zs.interval_stats(x, lv, ok, 0, 30)
        self.assertEqual((s["max"], s["max_us"], s["min"], s["min_us"]), (-10.0, 0, -30.0, 30))
        self.assertAlmostEqual(s["avg"], 10 * math.log10((0.1 + 0.01 + 0.001) / 3))
        self.assertEqual(s["points"], 3)
        # 区間で切る・向きは問わない・印で外した点は数えない
        s = zs.interval_stats(x, lv, np.array([False, True, True, True]), 25, 5)
        self.assertEqual((s["max"], s["points"]), (-20.0, 1))
        self.assertIsNone(zs.interval_stats(x, lv, ok, 21, 29))


class TimeTextTest(unittest.TestCase):
    INPUTS = ["12.5", "12.5ms", "-1.2 s", "300us", "300 μs", "0.0005s", "−3", "1e3", "", "5 m", ".5"]
    VALUES = [0, 1, -1, 999, 1000, -5234123, 12345678, 499.5, 500.5, -0.4]

    def test_parse_format_unit(self):
        self.assertEqual(parse_time_us("12.5"), 12500)
        self.assertEqual(parse_time_us("-1.2 s"), -1_200_000)
        self.assertEqual(parse_time_us("300us"), 300)
        with self.assertRaises(ParseError):
            parse_time_us("5 m")
        self.assertEqual(format_time_us(-5234123), "-5234.123 ms")
        self.assertEqual(format_time_us(0), "0.000 ms")
        self.assertEqual(time_axis_unit(10_000_000), (1e6, "s"))
        self.assertEqual(time_axis_unit(69_860), (1e3, "ms"))
        self.assertEqual(time_axis_unit(1_000_000), (1e3, "ms"))
        self.assertEqual(time_axis_unit(500), (1.0, "μs"))

    @unittest.skipUnless(shutil.which("node"), "Node が無い")
    def test_same_as_web(self):
        from tests.test_web_server import UI_WEB

        script = (
            "const u = require(process.argv[1]);"
            "const inp = JSON.parse(process.argv[2]); const vals = JSON.parse(process.argv[3]);"
            "const p = inp.map(t => { try { return u.parseTimeUs(t); } catch (e) { return null; } });"
            "const f = vals.map(v => u.formatTimeUs(v));"
            "const a = [10e6, 69860, 1e6, 500].map(s => u.timeAxisUnit(s));"
            "console.log(JSON.stringify({p, f, a}));"
        )
        out = subprocess.run(["node", "-e", script, str(UI_WEB / "units.js"), json.dumps(self.INPUTS),
                              json.dumps(self.VALUES)], capture_output=True, text=True, encoding="utf-8",
                             timeout=30, check=True).stdout
        got = json.loads(out)
        expect = []
        for t in self.INPUTS:
            try:
                expect.append(parse_time_us(t))
            except ParseError:
                expect.append(None)
        self.assertEqual(got["p"], expect)
        self.assertEqual(got["f"], [format_time_us(v) for v in self.VALUES])
        self.assertEqual([tuple(a) for a in got["a"]], [time_axis_unit(s) for s in (10e6, 69860, 1e6, 500)])


if __name__ == "__main__":
    unittest.main()
