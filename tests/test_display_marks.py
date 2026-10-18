"""機器の範囲の外・意味の無い点の印と帯、REF の自動（設計J §4.8・§4.1）。

- 点の印と帯・REF の自動の最大値（core/display_marks.py）を計算だけで確かめる
- シミュレータ（0Hz に局発の漏れがある）の SA・TG で、トレースの印・状態の帯・マーカーの注意・REF の自動の最大値
- 画面の共通部分（screen_model）: 帯の説明・PNG の注意・表示中の複数トレースの最大
"""
import math
import unittest

import numpy as np

from namibench.core import display_marks as dm
from namibench.core.devices.base import load_profile
from namibench.core.devices.rtlsdr.driver import PROFILE_PATH as RTL_PROFILE
from namibench.core.instrument import GIGAST_PROFILE_PATH
from namibench.ui_qt import screen_model, texts
from tests.instrument_helpers import InstrumentCase

GIGAST = load_profile(GIGAST_PROFILE_PATH)
RTL = load_profile(RTL_PROFILE)
MHZ = 1_000_000


class PointMarksTest(unittest.TestCase):
    def test_sa_lo_leak_and_negative_side(self):
        f = np.array([-100, -6, -5, -1, 0, 5, 6, 100]) * MHZ
        m = dm.point_marks(f, "sa", GIGAST)
        self.assertEqual(set(m), {"lo_leak", "negative_mirror"})
        self.assertEqual(m["lo_leak"].tolist(), [False, False, True, True, True, True, False, False])
        self.assertEqual(m["negative_mirror"].tolist(), [True, True, True, True, False, False, False, False])
        # SG も受けた信号を見るので同じ
        self.assertEqual(set(dm.point_marks(f, "sg", GIGAST)), {"lo_leak", "negative_mirror"})

    def test_positive_only_has_no_negative_mark(self):
        m = dm.point_marks(np.array([50, 100]) * MHZ, "sa", GIGAST)
        self.assertEqual(set(m), {"lo_leak"})
        self.assertFalse(m["lo_leak"].any())

    def test_tg_and_s11_outside_range(self):
        f = np.array([-100 * MHZ, 0, 999_999, 1 * MHZ, 4000 * MHZ, 4000 * MHZ + 100])
        for mode in ("tg", "s11"):
            m = dm.point_marks(f, mode, GIGAST)
            self.assertEqual(set(m), {"outside_tg_range"})
            self.assertEqual(m["outside_tg_range"].tolist(), [True, True, True, False, False, True])

    def test_profile_without_bands_marks_nothing(self):
        # RTL-SDR は局発の漏れの帯を持たない（DC は機器の層で値なしにしている）
        self.assertEqual(dm.point_marks(np.array([80, 90]) * MHZ, "sa", RTL), {})
        self.assertEqual(dm.point_marks(np.array([0, 1]) * MHZ, "nf", GIGAST), {})


class DisplayMarksTest(unittest.TestCase):
    def test_sa_bands_do_not_overlap(self):
        got = dm.display_marks("sa", GIGAST, -100 * MHZ, 100 * MHZ)
        self.assertEqual(got, [{"kind": "negative_mirror", "start_hz": -100 * MHZ, "stop_hz": -5 * MHZ},
                               {"kind": "lo_leak", "start_hz": -5 * MHZ, "stop_hz": 5 * MHZ}])

    def test_clipped_to_display(self):
        self.assertEqual(dm.display_marks("sa", GIGAST, 2 * MHZ, 50 * MHZ),
                         [{"kind": "lo_leak", "start_hz": 2 * MHZ, "stop_hz": 5 * MHZ}])
        self.assertEqual(dm.display_marks("sa", GIGAST, 50 * MHZ, 100 * MHZ), [])

    def test_tg_both_ends(self):
        got = dm.display_marks("tg", GIGAST, -100 * MHZ, 5000 * MHZ)
        self.assertEqual(got, [{"kind": "outside_tg_range", "start_hz": -100 * MHZ, "stop_hz": 1 * MHZ},
                               {"kind": "outside_tg_range", "start_hz": 4000 * MHZ, "stop_hz": 5000 * MHZ}])
        self.assertEqual(dm.display_marks("tg", GIGAST, 10 * MHZ, 20 * MHZ), [])

    def test_zero_span_has_no_bands(self):
        self.assertEqual(dm.display_marks("sa", GIGAST, 0, 0), [])


class UsableMaxTest(unittest.TestCase):
    def test_excludes_flags_nan_and_outside_range(self):
        f = np.array([-10, -2, 0, 2, 10, 20]) * MHZ
        y = np.array([-50.0, -40.0, 4.0, np.nan, -45.0, -1.0])
        flags = dm.point_marks(f, "sa", GIGAST)
        flags["transient"] = np.array([False, False, False, False, False, False])
        self.assertEqual(dm.usable_max(y, f, flags), -1.0)
        # 表示範囲（Stop 15MHz）の外の点は使わない。0Hz の漏れ（+4）は印で除く
        self.assertEqual(dm.usable_max(y, f, flags, -10 * MHZ, 15 * MHZ), -45.0)
        flags["transient"][4] = True
        self.assertEqual(dm.usable_max(y, f, flags, -10 * MHZ, 15 * MHZ), -50.0)
        self.assertIsNone(dm.usable_max(y, f, flags, -3 * MHZ, 3 * MHZ))

    def test_auto_ref_level(self):
        self.assertAlmostEqual(dm.auto_ref_level([-33.3, None, -40.0], 10), -23.3)
        self.assertAlmostEqual(dm.auto_ref_level([-33.31], 10), -23.3)  # 0.1dB へ切り上げ
        self.assertIsNone(dm.auto_ref_level([None, math.nan], 10))


class ScreenModelTest(unittest.TestCase):
    def state(self):
        return {
            "sweep": {"mode": "sa"},
            "display_marks": dm.display_marks("sa", GIGAST, -100 * MHZ, 100 * MHZ),
            "ref_auto": {"peaks": {"1": {"unit": "dBm", "max": -40.0}, "2": {"unit": "dBm", "max": -30.0},
                                   "F1.1": {"unit": "dBm", "max": -20.0}, "D": {"unit": "dB", "max": 3.0},
                                   "3": {"unit": "dBm", "max": None}}},
        }

    def test_marks_view_and_png_warnings(self):
        v = screen_model.marks_view(self.state())
        self.assertEqual([m["label"] for m in v], [texts.DISPLAY_MARKS["negative_mirror"], texts.DISPLAY_MARKS["lo_leak"]])
        self.assertEqual(v[1]["color"], "warn")
        self.assertIn("局部発振器の漏れ（入力の信号ではありません）", v[1]["tip"])
        self.assertIn("5", v[1]["tip"])
        w = screen_model.png_warnings(self.state())
        self.assertIn(v[0]["tip"], w)
        self.assertIn(v[1]["tip"], w)

    def test_ref_auto_uses_highest_shown_trace_of_axis_unit(self):
        st = self.state()
        v = screen_model.ref_auto_view(st, ["1", "2"], "dBm", 10, has_wave=True)
        self.assertAlmostEqual(v["level"], -20.0)
        v = screen_model.ref_auto_view(st, ["1", "2", "F1.1", "D"], "dBm", 10, has_wave=True)
        self.assertAlmostEqual(v["level"], -10.0)  # 参照トレースが最も高い。単位の違う差（dB）は使わない
        v = screen_model.ref_auto_view(st, ["D"], "dB", 5, has_wave=True)
        self.assertAlmostEqual(v["level"], 8.0)

    def test_ref_auto_reasons(self):
        st = self.state()
        self.assertEqual(screen_model.ref_auto_view(st, [], "dBm", 10, has_wave=False)["reason"],
                         texts.REASON_TEXTS["no_wave"])
        v = screen_model.ref_auto_view(st, ["3"], "dBm", 10, has_wave=True)
        self.assertIsNone(v["level"])
        self.assertEqual(v["reason"], texts.REASON_TEXTS["no_auto_points"])


class InstrumentMarksTest(InstrumentCase):
    """シミュレータ（0Hz に局発の漏れ +1.9dBm）で、印・帯・マーカーの注意・REF の自動の最大値。"""

    def sweep(self, mode):
        self.connect(-100 * MHZ, 100 * MHZ)
        self.call("set_mode", mode=mode)
        self.call("set_sweep", start_hz=-100 * MHZ, stop_hz=100 * MHZ)
        st = self.single()
        tr = self.wait_for(lambda k, p: k == "trace" and p["trace"] == 1)
        return st, tr

    def test_sa_flags_bands_markers_and_auto(self):
        st, tr = self.sweep("sa")
        ax = tr["axis"]
        f = ax["start_hz"] + ax["step_hz"] * np.arange(ax["points"], dtype=np.int64)
        pts = tr["flags"]["points"]
        np.testing.assert_array_equal(pts["lo_leak"], (f >= -5 * MHZ) & (f <= 5 * MHZ))
        np.testing.assert_array_equal(pts["negative_mirror"], f < 0)
        self.assertNotIn("outside_tg_range", pts)
        self.assertEqual([m["kind"] for m in st["display_marks"]], ["negative_mirror", "lo_leak"])
        # 0Hz の漏れはトレースの最大だが、REF の自動の最大には入らない
        y = np.asarray(tr["values"], dtype=np.float64)
        i0 = int(np.argmin(np.abs(f)))
        self.assertGreater(y[i0], -10.0)
        peak = st["ref_auto"]["peaks"]["1"]
        self.assertEqual(peak["unit"], "dBm")
        self.assertLess(peak["max"], -20.0)
        keep = ~pts["lo_leak"] & ~pts["transient"] & np.isfinite(y) & (f <= 100 * MHZ)
        self.assertAlmostEqual(peak["max"], float(y[keep].max()), places=4)
        # マーカーの読み値に注意が付く
        self.call("marker_add", kind="M", trace=1, freq_hz=0)
        self.call("marker_add", kind="M", trace=1, freq_hz=-50 * MHZ)
        rows = {m["id"]: m for m in self.call("get_state")["markers"]["markers"]}
        self.assertIn("lo_leak", rows["M1"]["notes"])
        self.assertIn("negative_mirror", rows["M2"]["notes"])
        self.assertNotIn("lo_leak", rows["M2"]["notes"])

    def test_sa_peak_search_skips_lo_leak(self):
        # 設計J §4.8: ピーク・次のピーク・上位 N 個・ピーク追従・ピークの一覧は 0Hz の漏れの帯の点を探さない
        st, tr = self.sweep("sa")
        band = (-5 * MHZ, 5 * MHZ)

        def outside(hz):
            return not band[0] <= hz <= band[1]

        r = self.call("marker_peak", direction="highest", trace=1)
        self.assertTrue(outside(r["freq_hz"]))
        # 漏れ（+1.9dBm）の次に高いのはシミュレータの 100MHz の −30dBm の信号
        self.assertGreater(r["freq_hz"], 95 * MHZ)
        mid = r["id"]
        for d in ("left", "right", "lower"):
            try:
                r2 = self.call("marker_peak", id=mid, direction=d)
            except Exception:  # noqa: BLE001  ピークが無ければ no_peak（動かない）
                continue
            self.assertTrue(outside(r2["freq_hz"]), d)
        placed = self.call("marker_top_peaks", trace=1, n=5)["placed"]
        self.assertTrue(placed)
        self.assertTrue(all(outside(p["freq_hz"]) for p in placed))
        pl = self.call("peak_list", trace=1, floor_margin_db=3.0)
        self.assertTrue(pl["peaks"])
        self.assertTrue(all(outside(p["freq_hz"]) for p in pl["peaks"]))
        # ピーク追従の P を漏れの帯の中から始めても、次の掃引で帯の外のピークへ動く
        self.call("marker_clear")
        self.call("marker_add", kind="P", trace=1, freq_hz=0)
        self.call("marker_set", id="P1", peak_track=True)
        self.single()
        p1 = self.call("get_state")["markers"]["markers"][0]
        self.assertTrue(outside(p1["freq_hz"]))
        # 手で置く M は漏れの帯の中にも置ける
        r = self.call("marker_add", kind="M", trace=1, freq_hz=0)
        self.assertEqual(r["freq_hz"], 0)

    def test_tg_outside_range(self):
        st, tr = self.sweep("tg")
        ax = tr["axis"]
        f = ax["start_hz"] + ax["step_hz"] * np.arange(ax["points"], dtype=np.int64)
        pts = tr["flags"]["points"]
        np.testing.assert_array_equal(pts["outside_tg_range"], f < 1 * MHZ)
        self.assertNotIn("lo_leak", pts)
        self.assertEqual(st["display_marks"], [{"kind": "outside_tg_range", "start_hz": -100 * MHZ, "stop_hz": 1 * MHZ}])
        y = np.asarray(tr["values"], dtype=np.float64)
        keep = ~pts["outside_tg_range"] & ~pts["transient"] & np.isfinite(y) & (f <= 100 * MHZ)
        self.assertAlmostEqual(st["ref_auto"]["peaks"]["1"]["max"], float(y[keep].max()), places=4)
        self.call("marker_add", kind="M", trace=1, freq_hz=-20 * MHZ)
        m = self.call("get_state")["markers"]["markers"][0]
        self.assertIn("outside_tg_range", m["notes"])


if __name__ == "__main__":
    unittest.main()
