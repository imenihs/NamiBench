"""山と谷（設計G §3.8）・ピークの一覧の基準（設計P §9.7）の試験（コアと、コアの命令）。

- 谷の判定: 周りより 3dB 以上へこんだ点（山の判定の向きを逆に）・一番深い点・次の谷（左右）・次に深い
- ピークの一覧: 電力はノイズフロア（中央値）+ N 以上の山・谷は探さない。比は表示範囲の最大 − N 以上の山と、
  最大 − N 以下の谷（深い順）。中央値ではなく最大を基準にする
- マーカー: 置いたときの向きを持ち、ピーク追従はその向きで追う（後で別の向きで探しても変わらない）。
  谷のマーカーの N dB 帯域幅は N dB 上がる点。保存の形（defs）に向きが入り、前の形は山
- 命令: polarity の受け取り・電力の一覧の谷は失敗（dips_on_power）・マーカーは電力でも谷を探せる・TG の一覧は最大が基準
"""
from __future__ import annotations

import contextlib
import unittest
from unittest import mock

import numpy as np

from pygigast.core.devices.simulator import device as simdev
from pygigast.core.instrument import CommandError
from pygigast.core.marker import (
    BASE_MAX,
    DIP,
    LEFT,
    LOWER,
    PEAK,
    RIGHT,
    Kind,
    MarkerTable,
    extreme_index,
    find_extrema,
    marker_from_def,
    marker_to_def,
    n_db_bandwidth,
    next_peak_index,
    peak_list,
    top_peaks,
)
from tests.instrument_helpers import InstrumentCase
from tests.test_marker import f, power_view, ratio_view

MHZ = 1_000_000
# 帯域除去フィルタ（BEF）のような比のトレース: 通過域の山 1・5・9 と、ノッチ 3（−60）・7（−45）、両端の −30
#      0    1    2    3    4   5    6    7    8   9    10
BEF = [-30, -2, -30, -60, -30, 0, -30, -45, -30, -5, -30]

# シミュレータの TG に挟む帯域除去フィルタのノッチ（中心・深さ dB・幅）。シミュレータの TG は平らで谷が無いので、
# 基準を取った後でこれを入れて、比のトレースに谷を作る
NOTCHES = ((120 * MHZ, 30.0, 3 * MHZ), (170 * MHZ, 20.0, 3 * MHZ))


@contextlib.contextmanager
def bef_in_tg(is_on):
    """シミュレータの TG の出力にノッチを入れる（is_on() が真の間だけ）。試験と画面の写しの道具。"""
    orig = simdev.Synth.response

    def response(self, ds):
        ad = orig(self, ds).astype(float)
        if is_on() and ds.mode_bits & simdev.protocol.MODE_TG:
            f = ds.start_hz + ds.step_hz * np.arange(ds.points, dtype=np.float64)
            for fc, depth, width in NOTCHES:
                ad -= depth * simdev.AD_PER_DB * np.exp(-((f - fc) / width) ** 2)
        return np.clip(np.rint(ad), 0, simdev.AD_MAX).astype("<u2")

    with mock.patch.object(simdev.Synth, "response", response):
        yield


class DipFindingTest(unittest.TestCase):
    def test_excursion_rule_mirrored(self):
        # 山の判定（test_marker の test_excursion_rule）の上下を返した形: 3 は 1.5dB しかへこまないので谷でない
        v = ratio_view([0, -10, -8, -9.5, 0, -6, 0, 0])
        self.assertEqual(find_extrema(v, polarity=DIP).tolist(), [1, 5])
        self.assertEqual(find_extrema(v, excursion_db=1.0, polarity=DIP).tolist(), [1, 3, 5])
        # 谷のしきい値は「これより高い谷は除く」
        self.assertEqual(find_extrema(v, threshold=-7.0, polarity=DIP).tolist(), [1])
        # 山は変わらない
        self.assertEqual(find_extrema(v).tolist(), [0, 4])

    def test_deepest_point_and_next_dips(self):
        v = ratio_view([0, -10, 0, -20, 0, -15, 0])
        self.assertEqual(extreme_index(v, DIP), 3)
        self.assertEqual(extreme_index(v, PEAK), 0)
        self.assertEqual(next_peak_index(v, 3, RIGHT, polarity=DIP), 5)
        self.assertEqual(next_peak_index(v, 3, LEFT, polarity=DIP), 1)
        self.assertIsNone(next_peak_index(v, 5, RIGHT, polarity=DIP))
        # 次に深い = いまより浅い谷のうち最も深いもの
        self.assertEqual(next_peak_index(v, 3, LOWER, polarity=DIP), 5)
        self.assertEqual(next_peak_index(v, 5, LOWER, polarity=DIP), 1)
        self.assertIsNone(next_peak_index(v, 1, LOWER, polarity=DIP))

    def test_flagged_points_are_not_the_deepest(self):
        v = ratio_view([0, -50, -5, -20, 0], flags={"transient": np.array([0, 1, 0, 0, 0], bool)})
        self.assertEqual(extreme_index(v, DIP), 3)

    def test_top_dips_deepest_first(self):
        v = ratio_view(BEF)
        self.assertEqual(top_peaks(v, 2, polarity=DIP), [3, 7])
        self.assertEqual(top_peaks(v, 9, polarity=DIP), [3, 7, 0, 10])


class PeakListBaseTest(unittest.TestCase):
    def test_ratio_uses_maximum_not_median(self):
        v = ratio_view(BEF)
        # 中央値は −30 だが、比の一覧の基準は表示範囲の最大（0）
        peaks = peak_list(v, 10.0)
        self.assertEqual((peaks.base, peaks.base_level), (BASE_MAX, 0.0))
        self.assertEqual([r.freq_hz for r in peaks.rows], [f(5), f(1), f(9)])  # 最大 − 10 以上の山を高い順
        self.assertEqual([r.diff_db for r in peaks.rows], [0.0, -2.0, -5.0])
        self.assertEqual([r.freq_hz for r in peak_list(v, 3.0).rows], [f(5), f(1)])
        # 谷: 最大 − N 以下を深い順（同じ深さは低い周波数から）
        dips = peak_list(v, 10.0, polarity=DIP)
        self.assertEqual([r.freq_hz for r in dips.rows], [f(3), f(7), f(0), f(10)])
        self.assertEqual([r.diff_db for r in dips.rows], [-60.0, -45.0, -30.0, -30.0])
        self.assertEqual([r.freq_hz for r in peak_list(v, 40.0, polarity=DIP).rows], [f(3), f(7)])

    def test_same_filter_same_base_whatever_the_span(self):
        # 同じフィルタを狭い Span（通過域だけ）と広い Span（阻止域が大半）で見ても、基準は通過域の最大のまま
        narrow = ratio_view(BEF[4:7])
        wide = ratio_view(BEF + [-70] * 30)
        self.assertEqual(peak_list(narrow, 10.0).base_level, 0.0)
        self.assertEqual(peak_list(wide, 10.0).base_level, 0.0)

    def test_power_uses_median_noise_floor(self):
        v = power_view([-90] * 15 + [-40, -90, -85, -90])
        pl = peak_list(v, 10.0)
        self.assertEqual((pl.base, pl.base_level), ("noise_floor", -90.0))
        self.assertEqual([r.freq_hz for r in pl.rows], [f(15)])
        self.assertEqual(pl.rows[0].diff_db, 50.0)


class MarkerPolarityTest(unittest.TestCase):
    def setUp(self):
        self.v = ratio_view(BEF)
        self.t = MarkerTable()

    def test_dip_search_places_and_sets_polarity(self):
        r = self.t.peak_search(None, self.v, DIP)
        self.assertEqual((r.marker_id, r.freq_hz), ("M1", f(3)))
        self.assertEqual(self.t.get("M1").polarity, DIP)
        self.assertEqual(self.t.next_peak("M1", RIGHT, self.v, DIP).freq_hz, f(7))
        # 山で探し直すと山の向きになる
        self.assertEqual(self.t.peak_search("M1", self.v, PEAK).freq_hz, f(5))
        self.assertEqual(self.t.get("M1").polarity, PEAK)
        # 見つからなければ動かさず、向きも変えない
        flat = ratio_view([0] * 11)
        self.assertEqual(self.t.next_peak("M1", RIGHT, flat, DIP).code, "no_peak")
        self.assertEqual(self.t.get("M1").polarity, PEAK)

    def test_tracking_keeps_polarity(self):
        self.t.register_peak(self.v, f(3), DIP)
        self.t.register_peak(self.v, f(5), PEAK)
        self.t.set_peak_track("P1", True)
        self.t.set_peak_track("P2", True)
        # ノッチと山が 1 点ずつ右へ動いた掃引: 谷の P1 は谷、山の P2 は山を追う
        moved = ratio_view([-30] + BEF[:-1])
        self.assertEqual(sorted(self.t.update({1: moved})), ["P1", "P2"])
        self.assertEqual((self.t.get("P1").target_hz, self.t.get("P2").target_hz), (f(4), f(6)))
        # 後から別の向きで探しても、置いたマーカーの向きは変わらない（別のマーカーを山で探す）
        self.t.peak_search(None, moved, PEAK)
        self.assertEqual(self.t.get("P1").polarity, DIP)

    def test_top_n_dips(self):
        res = self.t.place_top_peaks(self.v, 2, DIP)
        self.assertEqual([(x.marker_id, x.freq_hz) for x in res], [("P1", f(3)), ("P2", f(7))])
        self.assertEqual({m.polarity for m in self.t.markers()}, {DIP})

    def test_n_db_bandwidth_upward_from_dip(self):
        #               0   1    2   3  4
        v = ratio_view([0, -4, -10, -6, 0])
        b = n_db_bandwidth(v, 2, 3.0, DIP)  # −10 から 3dB 上がる −7 との交点
        self.assertEqual(b.status, "ok")
        self.assertEqual(b.left.freq_hz, f(2) - 500_000)   # −10 と −4 の間
        self.assertEqual(b.right.freq_hz, f(2) + 750_000)  # −10 と −6 の間
        self.assertEqual(b.width_hz, 1_250_000)
        # 谷のマーカーの表の機能は上がる向き。山のマーカーは下がる向き（谷の底からは下がらないので端まで交点が無い）
        t = MarkerTable()
        t.add(Kind.MANUAL, 1, f(2), v, DIP)
        t.add(Kind.MANUAL, 1, f(2), v, PEAK)
        t.set_bandwidth("M1", (3.0,))
        t.set_bandwidth("M2", (3.0,))
        rows = {m["id"]: m for m in t.snapshot({1: v})["markers"]}
        self.assertEqual((rows["M1"]["polarity"], rows["M1"]["bandwidth"][0]["width_hz"]), (DIP, 1_250_000))
        self.assertEqual(rows["M2"]["bandwidth"][0]["status"], "edge")

    def test_defs_keep_polarity_and_old_form_is_peak(self):
        self.t.peak_search(None, self.v, DIP)
        d = marker_to_def(self.t.get("M1"))
        self.assertEqual(d["polarity"], DIP)
        self.assertEqual(marker_from_def(d).polarity, DIP)
        old = {k: v for k, v in d.items() if k != "polarity"}
        self.assertEqual(marker_from_def(old).polarity, PEAK)
        t = MarkerTable()
        self.assertEqual(t.from_defs({"markers": [d, {**d, "number": 2, "polarity": "up"}]}), [1])  # 読めない向きは飛ばす


class DipCommandsTest(InstrumentCase):
    def setUp(self):
        super().setUp()
        self.connect(50 * MHZ, 200 * MHZ)
        self.single()

    def test_power_list_has_no_dips_but_markers_do(self):
        with self.assertRaises(CommandError) as cm:
            self.call("peak_list", trace=1, floor_margin_db=10.0, polarity="dip")
        self.assertEqual(cm.exception.code, "dips_on_power")
        with self.assertRaises(CommandError) as cm:
            self.call("marker_top_peaks", trace=1, n=2, polarity="dip")
        self.assertEqual(cm.exception.code, "dips_on_power")
        with self.assertRaises(CommandError) as cm:
            self.call("peak_list", trace=1, polarity="up")
        self.assertEqual(cm.exception.code, "bad_polarity")
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        self.assertEqual((pl["base"], pl["polarity"]), ("noise_floor", "peak"))
        self.assertTrue(all(p["diff_db"] >= 10.0 for p in pl["peaks"]))
        # マーカーは電力でも谷を探せる（一番深い点・その向き）
        r = self.call("marker_peak", polarity="dip")
        st = self.call("get_state")
        m = {x["id"]: x for x in st["markers"]["markers"]}[r["id"]]
        self.assertEqual(m["polarity"], "dip")
        self.assertLess(m["level"], pl["base_level"])  # 一番深い点はノイズフロア（中央値）より下
        r = self.call("marker_add", kind="M", trace=1, polarity="dip")
        self.assertEqual({x["id"]: x for x in self.call("get_state")["markers"]["markers"]}[r["id"]]["polarity"], "dip")

    def test_tg_list_uses_maximum(self):
        dut = {"on": False}
        with bef_in_tg(lambda: dut["on"]):
            self.call("set_mode", mode="tg")
            r = self.call("reference_take", kind="wide", name="direct")
            self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
            dut["on"] = True  # 直結で基準を取った後にフィルタを挟む
            n0 = self.call("get_state")["run"]["sweep_count"]
            self.clear_events()
            self.call("run", mode="single")
            self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped" and p["run"]["sweep_count"] > n0)
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        self.assertEqual((pl["base"], pl["unit"]), ("max", "dB"))
        self.assertAlmostEqual(pl["base_level"], 0.0, delta=0.5)  # 通過域（基準と同じ 0dB）
        self.assertTrue(all(-10.0 <= p["diff_db"] <= 0.0 for p in pl["peaks"]))
        dips = self.call("peak_list", trace=1, floor_margin_db=10.0, polarity="dip")
        self.assertEqual(dips["polarity"], "dip")
        # ノッチ 2 つ（−30dB の 120MHz、−20dB の 170MHz）を深い順に
        self.assertEqual(len(dips["peaks"]), 2, dips["peaks"])
        self.assertAlmostEqual(dips["peaks"][0]["freq_hz"], 120 * MHZ, delta=MHZ)
        self.assertAlmostEqual(dips["peaks"][0]["diff_db"], -30.0, delta=1.5)
        self.assertAlmostEqual(dips["peaks"][1]["freq_hz"], 170 * MHZ, delta=MHZ)
        r = self.call("marker_register_peak", trace=1, freq_hz=dips["peaks"][0]["freq_hz"], polarity="dip")
        m = {x["id"]: x for x in self.call("get_state")["markers"]["markers"]}[r["id"]]
        self.assertEqual((m["kind"], m["polarity"]), ("P", "dip"))
        # 谷の P の 3dB 帯域幅はノッチの幅（谷から 3dB 上がる点の間）
        self.call("marker_function", id=r["id"], bandwidth_db=[3.0])
        b = {x["id"]: x for x in self.call("get_state")["markers"]["markers"]}[r["id"]]["bandwidth"][0]
        self.assertEqual(b["status"], "ok")
        self.assertLess(b["left_hz"], 120 * MHZ)
        self.assertGreater(b["right_hz"], 120 * MHZ)


if __name__ == "__main__":
    unittest.main()
