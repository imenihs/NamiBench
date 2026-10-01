"""マーカー・カーソル・ピーク探索（設計G §3.1〜§3.5）の試験。"""
from __future__ import annotations

import json
import unittest

import numpy as np

from pygigast.core.marker import (
    LEFT,
    LOWER,
    MAX_PER_KIND,
    RIGHT,
    SIDE_ABOVE,
    SIDE_BELOW,
    Kind,
    MarkerTable,
    Sign,
    ThresholdSetting,
    TraceView,
    find_peaks,
    highest_index,
    n_db_bandwidth,
    next_peak_index,
    peak_list,
    pick_trace,
    reading_at,
    threshold_band,
    top_peaks,
)
from pygigast.core.types import FreqAxis, Frame, Quantity, ValueKind

MHZ = 1_000_000


def ratio_view(db, start=100 * MHZ, step=1 * MHZ, disp=None, flags=None, trace=1, notes=()):
    """dB の並びから比のトレースを作る（リニアの振幅比 = 10^(dB/20)）。"""
    db = np.asarray(db, dtype=float)
    axis = FreqAxis(start, step, len(db))
    vals = 10 ** (db / 20)
    s, e = disp if disp else (axis.start_hz, axis.last_hz)
    return TraceView.build(trace, axis, vals, Quantity.RATIO, s, e, flags, notes)


def power_view(dbm, start=100 * MHZ, step=1 * MHZ, trace=1):
    dbm = np.asarray(dbm, dtype=float)
    axis = FreqAxis(start, step, len(dbm))
    return TraceView.build(trace, axis, 10 ** ((dbm - 30) / 10), Quantity.POWER, axis.start_hz, axis.last_hz)


def f(i, start=100 * MHZ, step=1 * MHZ):
    return start + i * step


class TraceViewTest(unittest.TestCase):
    def test_power_to_dbm_and_ratio_to_db(self):
        pv = power_view([-10.0, -20.0])
        self.assertAlmostEqual(pv.level_at(0), -10.0)
        self.assertEqual(pv.unit, "dBm")
        rv = ratio_view([-3.0, 0.0])
        self.assertAlmostEqual(rv.level_at(0), -3.0)
        self.assertEqual(rv.unit, "dB")

    def test_complex_ratio_reads_magnitude(self):
        axis = FreqAxis(0, 1, 2)
        v = TraceView.build(1, axis, np.array([1j * 0.5, -1.0 + 0j]), Quantity.RATIO, 0, 1)
        self.assertAlmostEqual(v.level_at(0), 20 * np.log10(0.5))

    def test_from_frame_uses_display_range(self):
        axis = FreqAxis(100 * MHZ, MHZ, 11)
        fr = Frame(axis=axis, values=np.ones(11), quantity=Quantity.RATIO, value_kind=ValueKind.REAL,
                   time=0.0, duration_s=0.1, sweep_no=1, display_start_hz=102 * MHZ, display_stop_hz=107 * MHZ + 500_000)
        v = TraceView.from_frame(2, fr)
        self.assertEqual((v.lo, v.hi), (2, 8))
        self.assertIsNone(v.nearest_index(108 * MHZ))
        self.assertEqual(v.nearest_index(101 * MHZ), None)

    def test_nearest_index_tie_goes_low(self):
        v = ratio_view([0, 0, 0])
        self.assertEqual(v.nearest_index(f(0) + MHZ // 2), 0)
        self.assertEqual(v.nearest_index(f(0) + MHZ // 2 + 1), 1)

    def test_no_reference_is_no_value_and_zero_power_is_no_value(self):
        v = ratio_view([0, -1, -2], flags={"no_reference": np.array([False, True, False])})
        self.assertIsNone(v.level_at(1))
        pv = TraceView.build(1, FreqAxis(0, 1, 2), np.array([0.0, 1e-3]), Quantity.POWER, 0, 1)
        self.assertIsNone(pv.level_at(0))
        self.assertAlmostEqual(pv.level_at(1), 0.0)

    def test_rejects_float_range(self):
        with self.assertRaises(TypeError):
            TraceView.build(1, FreqAxis(0, 1, 2), np.ones(2), Quantity.RATIO, 0.0, 1)


class PeakTest(unittest.TestCase):
    def test_highest_at_first_and_last_displayed_point(self):
        v = ratio_view([10, 5, 0, 5, 9])
        self.assertEqual(highest_index(v), 0)
        v = ratio_view([0, 5, 0, 5, 10])
        self.assertEqual(highest_index(v), 4)
        # 表示範囲の外にある高い点は探さない
        v = ratio_view([20, 5, 0, 7, 30], disp=(f(1), f(3)))
        self.assertEqual(highest_index(v), 3)

    def test_highest_skips_flagged_points(self):
        v = ratio_view([0, 30, 5, 20, 1], flags={"transient": np.array([0, 1, 0, 0, 0], bool),
                                                  "out_of_cal_range": np.array([0, 0, 0, 1, 0], bool)})
        self.assertEqual(highest_index(v), 2)

    def test_flat_trace(self):
        v = ratio_view([-5.0] * 6)
        self.assertEqual(highest_index(v), 0)
        self.assertEqual(find_peaks(v).size, 0)
        self.assertIsNone(next_peak_index(v, 0, RIGHT))

    def test_all_no_value(self):
        v = ratio_view([0, 0], flags={"no_reference": np.array([True, True])})
        self.assertIsNone(highest_index(v))

    def test_excursion_rule(self):
        #          0   1  2   3   4  5   6  7
        v = ratio_view([0, 10, 8, 9.5, 0, 6, 0, 0])
        # 3 は左の 8 まで 1.5dB しか下がらない（10 に当たる）のでピークでない。5 は 6dB 盛り上がる
        self.assertEqual(find_peaks(v).tolist(), [1, 5])
        self.assertEqual(find_peaks(v, excursion_db=1.0).tolist(), [1, 3, 5])
        self.assertEqual(find_peaks(v, threshold=7.0).tolist(), [1])

    def test_edge_point_peak_needs_inner_drop(self):
        v = ratio_view([10, 0, 5, 0, 12])
        self.assertEqual(find_peaks(v).tolist(), [0, 2, 4])
        v = ratio_view([10, 8, 0])
        self.assertEqual(find_peaks(v).tolist(), [0])  # 右側の最小 0 から 10dB
        v = ratio_view([10, 9])
        self.assertEqual(find_peaks(v).size, 0)

    def test_plateau_uses_lowest_frequency(self):
        v = ratio_view([0, 10, 10, 10, 0])
        self.assertEqual(find_peaks(v).tolist(), [1])

    def test_no_value_splits_runs(self):
        nr = np.array([0, 0, 1, 0, 0], bool)
        v = ratio_view([0, 10, 0, 5, 0], flags={"no_reference": nr})
        self.assertEqual(find_peaks(v).tolist(), [1, 3])

    def test_lo_leak_points_are_compared_but_never_chosen(self):
        # 設計J §4.8: 0Hz の局発の漏れの帯（2〜4）の点はピークにしない。帯の端の裾（5）も
        # 帯の中の点と比べるので局所最大にならず、ピークにならない。帯の外の 8 だけが残る
        #               0  1  2   3   4   5  6  7  8   9
        db = [0, 0, 20, 40, 20, 12, 0, 0, 15, 0]
        leak = np.zeros(10, bool)
        leak[2:5] = True
        v = ratio_view(db, flags={"lo_leak": leak})
        self.assertEqual(highest_index(v), 8)
        self.assertEqual(find_peaks(v).tolist(), [8])
        self.assertEqual(top_peaks(v, 5), [8])
        self.assertEqual(next_peak_index(v, 3, RIGHT), 8)
        self.assertIsNone(next_peak_index(v, 8, LEFT))
        self.assertEqual(next_peak_index(v, 3, LOWER), 8)
        _, rows = peak_list(v, floor_margin_db=3.0)
        self.assertEqual([r.freq_hz for r in rows], [f(8)])
        # 漏れの帯の点しか残らなければピークは無い
        only = ratio_view([0, 40, 0], flags={"lo_leak": np.array([True, True, True])})
        self.assertIsNone(highest_index(only))
        self.assertEqual(find_peaks(only).size, 0)

    def test_next_peak_directions(self):
        v = ratio_view([0, 10, 0, 20, 0, 15, 0])
        self.assertEqual(next_peak_index(v, 3, RIGHT), 5)
        self.assertEqual(next_peak_index(v, 3, LEFT), 1)
        self.assertIsNone(next_peak_index(v, 5, RIGHT))
        self.assertEqual(next_peak_index(v, 3, LOWER), 5)
        self.assertEqual(next_peak_index(v, 5, LOWER), 1)
        self.assertIsNone(next_peak_index(v, 1, LOWER))

    def test_peak_list_and_top(self):
        db = [0] * 20
        db[3], db[10], db[15] = 30, 20, 4
        v = ratio_view(db)
        floor, rows = peak_list(v, floor_margin_db=6.0)
        self.assertEqual(floor, 0.0)
        self.assertEqual([r.freq_hz for r in rows], [f(3), f(10)])
        self.assertAlmostEqual(rows[1].height_db, 20.0)
        self.assertEqual(top_peaks(v, 2), [3, 10])
        self.assertEqual(top_peaks(v, 10), [3, 10, 15])


class BandTest(unittest.TestCase):
    def test_3db_bandwidth_interpolated(self):
        #                 0    1   2  3   4    5
        v = ratio_view([-10, -4, 0, -2, -6, -10])
        b = n_db_bandwidth(v, 2, 3.0)
        self.assertEqual(b.status, "ok")
        # 左: 1(−4) と 2(0) の間で −3 → 2 から 3/4 だけ 1 へ
        self.assertEqual(b.left.freq_hz, f(2) - 750_000)
        # 右: 3(−2) と 4(−6) の間で −3 → 3 から 1/4 だけ 4 へ
        self.assertEqual(b.right.freq_hz, f(3) + 250_000)
        self.assertEqual(b.width_hz, 1_000_000 + 1_000_000)
        self.assertEqual(b.center_hz, b.left.freq_hz + b.width_hz // 2)
        self.assertIsInstance(b.left.freq_hz, int)
        self.assertAlmostEqual(b.q, b.center_hz / b.width_hz)

    def test_crossing_exactly_on_point(self):
        v = ratio_view([-3, 0, -3])
        b = n_db_bandwidth(v, 1, 3.0)
        self.assertEqual((b.left.freq_hz, b.right.freq_hz), (f(0), f(2)))

    def test_bandwidth_one_side_outside_range(self):
        v = ratio_view([-10, -1, 0, -1, -10], disp=(f(1), f(4)))
        b = n_db_bandwidth(v, 2, 3.0)
        self.assertEqual(b.status, "edge")
        self.assertEqual(b.left.status, "edge")
        self.assertEqual(b.right.status, "ok")
        self.assertIsNone(b.width_hz)

    def test_bandwidth_stops_at_no_value_region(self):
        nr = np.array([0, 1, 0, 0, 0], bool)
        v = ratio_view([-10, -10, 0, -1, -10], flags={"no_reference": nr})
        b = n_db_bandwidth(v, 2, 3.0)
        self.assertEqual(b.left.status, "no_value_region")
        self.assertEqual(b.status, "no_value_region")

    def test_bandwidth_from_no_value_marker(self):
        nr = np.array([0, 1, 0], bool)
        v = ratio_view([0, 0, 0], flags={"no_reference": nr})
        self.assertEqual(n_db_bandwidth(v, 1, 3.0).status, "no_marker_value")

    def test_large_frequency_stays_exact(self):
        start = 12_000 * MHZ + 7
        v = ratio_view([-10, 0, -10], start=start, step=3)
        b = n_db_bandwidth(v, 1, 5.0)
        self.assertEqual(b.left.freq_hz, start + 3 - 2)  # −3 × 0.5 = −1.5 → 偶数丸めで −2
        self.assertEqual(b.right.freq_hz, start + 3 + 2)

    def test_threshold_below_region(self):
        # S11 のように谷がしきい値より下の範囲
        v = ratio_view([-5, -12, -20, -15, -8])
        b = threshold_band(v, 2, -10.0, SIDE_BELOW)
        self.assertEqual(b.status, "ok")
        self.assertEqual(b.left.freq_hz, f(1) - int(round(1_000_000 * 2 / 7)))
        self.assertEqual(b.right.freq_hz, f(3) + int(round(1_000_000 * 5 / 7)))

    def test_threshold_marker_not_in_region_and_above(self):
        v = ratio_view([-5, -12, -20, -15, -8])
        self.assertEqual(threshold_band(v, 0, -10.0, SIDE_BELOW).status, "not_in_region")
        b = threshold_band(v, 0, -10.0, SIDE_ABOVE)
        self.assertEqual(b.status, "edge")
        self.assertEqual(b.left.status, "edge")
        self.assertEqual(b.right.status, "ok")


class MarkerTableTest(unittest.TestCase):
    def setUp(self):
        self.v = ratio_view([0, 10, 0, 20, 0, 15, 0, -5, -5, -5])
        self.views = {1: self.v}
        self.t = MarkerTable()

    def test_numbering_uses_smallest_free(self):
        a = self.t.add(Kind.MANUAL, 1, f(1), self.v)
        b = self.t.add(Kind.MANUAL, 1, f(2), self.v)
        p = self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.assertEqual((a.marker_id, b.marker_id, p.marker_id), ("M1", "M2", "P1"))
        self.t.remove("M1")
        self.assertEqual(self.t.add(Kind.MANUAL, 1, f(4), self.v).marker_id, "M1")
        self.assertEqual([m.id for m in self.t.markers()], ["P1", "M1", "M2"])

    def test_table_full(self):
        for i in range(MAX_PER_KIND):
            self.assertTrue(self.t.add(Kind.MANUAL, 1, f(i % 10), self.v).ok)
        r = self.t.add(Kind.MANUAL, 1, f(0), self.v)
        self.assertEqual(r.code, "table_full")
        self.assertTrue(self.t.add(Kind.PEAK, 1, f(0), self.v).ok)

    def test_numeric_entry_snaps_and_reports(self):
        r = self.t.add(Kind.MANUAL, 1, f(2) + 300_000, self.v)
        self.assertTrue(r.snapped)
        self.assertEqual(r.freq_hz, f(2))
        r = self.t.set_frequency("M1", f(4), self.v)
        self.assertFalse(r.snapped)
        r = self.t.set_frequency("M1", f(20), self.v)
        self.assertEqual(r.code, "out_of_range")
        self.assertEqual(self.t.get("M1").target_hz, f(4))
        with self.assertRaises(TypeError):
            self.t.set_frequency("M1", 1.0e8, self.v)

    def test_step_clamps_at_range_edges(self):
        self.t.add(Kind.MANUAL, 1, f(1), self.v)
        self.assertEqual(self.t.step("M1", -10, self.v).freq_hz, f(0))
        self.assertEqual(self.t.step("M1", 10, self.v).freq_hz, f(9))
        self.assertEqual(self.t.step("M1", -1, self.v).freq_hz, f(8))

    def test_click_picks_vertically_nearest_trace(self):
        v2 = ratio_view([-30] * 10, trace=2)
        views = {1: self.v, 2: v2}
        self.assertEqual(pick_trace(views, f(3), 5.0), 1)
        self.assertEqual(pick_trace(views, f(3), -25.0), 2)
        r = self.t.place_at_click(views, f(3) + 100, -25.0)
        self.assertEqual((r.marker_id, self.t.get("M1").trace, r.freq_hz), ("M1", 2, f(3)))
        self.assertEqual(self.t.place_at_click(views, f(30), 0).code, "out_of_range")

    def test_peak_creates_m_when_nothing_selected_and_moves_selected(self):
        r = self.t.peak_search(None, self.v)
        self.assertEqual((r.marker_id, r.freq_hz), ("M1", f(3)))
        self.t.add(Kind.PEAK, 1, f(0), self.v)
        r = self.t.peak_search("P1", self.v)
        self.assertEqual(self.t.get("P1").target_hz, f(3))
        self.assertEqual(self.t.next_peak("P1", RIGHT, self.v).freq_hz, f(5))
        self.assertEqual(self.t.next_peak("P1", RIGHT, self.v).code, "no_peak")
        self.assertEqual(self.t.get("P1").target_hz, f(5))
        self.assertEqual(self.t.next_peak("P1", LEFT, self.v).freq_hz, f(3))
        self.assertEqual(self.t.next_peak("P1", LOWER, self.v).freq_hz, f(5))

    def test_peak_on_flat_and_no_value_trace(self):
        flat = ratio_view([-5] * 4)
        self.assertEqual(self.t.peak_search(None, flat).freq_hz, f(0))
        nov = ratio_view([0, 0], flags={"no_reference": np.array([True, True])})
        self.assertEqual(self.t.peak_search(None, nov).code, "no_peak")

    def test_register_and_top_n(self):
        _, rows = peak_list(self.v, floor_margin_db=3.0)
        r = self.t.register_peak(self.v, rows[0].freq_hz)
        self.assertEqual((r.marker_id, r.freq_hz), ("P1", f(3)))
        self.t.add(Kind.MANUAL, 1, f(0), self.v)
        self.t.set_ref("M1", "P1")
        self.t.add_freq_offset("P1", Sign.PLUS, MHZ)
        res = self.t.place_top_peaks(self.v, 2)
        self.assertEqual([(x.marker_id, x.freq_hz) for x in res], [("P1", f(3)), ("P2", f(5))])
        self.assertEqual(len(self.t.get("P1").freq_offsets), 1)
        self.assertEqual(self.t.get("M1").ref, "P1")
        res = self.t.place_top_peaks(self.v, 99)
        self.assertEqual(len(res), 3)

    def test_delta_and_ref_removal(self):
        self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.t.add(Kind.MANUAL, 1, f(5), self.v)
        self.t.set_ref("M1", "P1")
        row = self.t.snapshot(self.views)["markers"][1]
        self.assertEqual(row["delta"], {"status": "ok", "freq_hz": 2 * MHZ, "level_db": -5.0})
        with self.assertRaises(ValueError):
            self.t.set_ref("M1", "M1")
        self.t.remove("P1")
        self.assertIsNone(self.t.get("M1").ref)

    def test_delta_unit_mismatch_and_ref_out_of_range(self):
        pv = power_view([-10] * 10, trace=2)
        views = {1: self.v, 2: pv}
        self.t.add(Kind.MANUAL, 1, f(3), self.v)
        self.t.add(Kind.MANUAL, 2, f(5), pv)
        self.t.set_ref("M2", "M1")
        d = self.t.snapshot(views)["markers"][1]["delta"]
        self.assertEqual((d["status"], d["freq_hz"], d["level_db"]), ("unit_mismatch", 2 * MHZ, None))
        narrow = {1: ratio_view(self.v.level_db, disp=(f(5), f(9))), 2: pv}
        d = self.t.snapshot(narrow)["markers"][1]["delta"]
        self.assertEqual(d["status"], "ref_unavailable")

    def test_marker_outside_new_axis_is_kept_and_returns(self):
        self.t.add(Kind.MANUAL, 1, f(2), self.v)
        narrow = {1: ratio_view(self.v.level_db, disp=(f(4), f(9)))}
        row = self.t.snapshot(narrow)["markers"][0]
        self.assertEqual((row["status"], row["freq_hz"], row["target_hz"]), ("out_of_range", None, f(2)))
        # 軸の刻みが変わっても置いた周波数は書き換えず、読むときに最も近い点へ載せる
        coarse = {1: ratio_view([0, 1, 2, 3], start=f(0) - 700_000, step=3 * MHZ)}
        row = self.t.snapshot(coarse)["markers"][0]
        self.assertEqual((row["status"], row["freq_hz"]), ("ok", f(0) - 700_000 + 3 * MHZ))
        self.assertEqual(self.t.snapshot(self.views)["markers"][0]["freq_hz"], f(2))

    def test_no_trace_and_notes(self):
        self.t.add(Kind.MANUAL, 3, f(2))
        self.assertEqual(self.t.snapshot(self.views)["markers"][0]["status"], "no_trace")
        v = ratio_view([0, 1, 2], flags={"transient": np.array([1, 0, 0], bool)}, notes=("rbw15k_single",))
        t = MarkerTable()
        t.add(Kind.MANUAL, 1, f(0), v)
        row = t.snapshot({1: v})["markers"][0]
        self.assertEqual(row["notes"], ["rbw15k_single", "transient"])
        self.assertEqual(row["status"], "ok")

    def test_peak_track(self):
        self.t.add(Kind.PEAK, 1, f(2), self.v)
        with self.assertRaises(ValueError):
            self.t.add(Kind.MANUAL, 1, f(0), self.v)
            self.t.set_peak_track("M1", True)
        self.t.set_peak_track("P1", True)
        self.assertEqual(self.t.update(self.views), ["P1"])
        # f(2) から近いのは f(1) と f(3)（同じ近さ）→ 高い f(3)
        self.assertEqual(self.t.get("P1").target_hz, f(3))
        moved = ratio_view([0, 10, 0, 0, 22, 0, 15, 0, -5, -5])
        self.assertEqual(self.t.update({1: moved}), ["P1"])
        self.assertEqual(self.t.get("P1").target_hz, f(4))
        self.assertEqual(self.t.update({1: ratio_view([0] * 10)}), [])
        self.assertEqual(self.t.get("P1").target_hz, f(4))
        self.assertEqual(self.t.update({1: ratio_view(moved.level_db, disp=(f(6), f(9)))}), [])

    def test_freq_offsets(self):
        self.t.add(Kind.MANUAL, 1, f(3), self.v)
        self.t.add_freq_offset("M1", Sign.BOTH, 2 * MHZ)
        self.t.add_freq_offset("M1", Sign.PLUS, 9 * MHZ)
        rows = self.t.snapshot(self.views)["markers"][0]["freq_offsets"]
        self.assertEqual([r["id"] for r in rows], ["M1.f1+", "M1.f1-", "M1.f2+"])
        self.assertEqual((rows[0]["freq_hz"], rows[0]["level"], rows[0]["delta_level_db"]), (f(5), 15.0, -5.0))
        self.assertEqual((rows[1]["freq_hz"], rows[1]["delta_level_db"]), (f(1), -10.0))
        self.assertEqual((rows[2]["status"], rows[2]["target_hz"]), ("out_of_range", f(12)))
        with self.assertRaises(ValueError):
            self.t.add_freq_offset("M1", Sign.PLUS, 0)

    def test_offsets_follow_tracking_parent(self):
        self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.t.set_peak_track("P1", True)
        self.t.add_freq_offset("P1", Sign.MINUS, MHZ)
        moved = ratio_view([0, 10, 0, 0, 22, 0, 15, 0, -5, -5])
        self.t.update({1: moved})
        row = self.t.snapshot({1: moved})["markers"][0]["freq_offsets"][0]
        self.assertEqual(row["freq_hz"], f(3))

    def test_level_offsets(self):
        self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.t.add_level_offset("P1", Sign.MINUS, 3.0)
        self.t.add_level_offset("P1", Sign.BOTH, 1.0)
        rows = self.t.snapshot(self.views)["markers"][0]["level_offsets"]
        self.assertEqual([(r["id"], r["level"]) for r in rows], [("P1.l1-", 17.0), ("P1.l2+", 21.0), ("P1.l2-", 19.0)])
        narrow = {1: ratio_view(self.v.level_db, disp=(f(5), f(9)))}
        rows = self.t.snapshot(narrow)["markers"][0]["level_offsets"]
        self.assertEqual(rows[0]["status"], "parent_unavailable")

    def test_marker_functions_in_snapshot(self):
        v = ratio_view([-70, -60, -10, -1, 0, -2, -10, -60, -70])
        t = MarkerTable()
        t.add(Kind.PEAK, 1, f(4), v)
        t.set_bandwidth("P1", (3.0, 60.0))
        t.set_threshold("P1", ThresholdSetting(-5.0, SIDE_ABOVE))
        row = t.snapshot({1: v})["markers"][0]
        bw3, bw60 = row["bandwidth"]
        self.assertEqual((bw3["status"], bw60["status"]), ("ok", "ok"))
        self.assertAlmostEqual(row["shape_factor"], bw60["width_hz"] / bw3["width_hz"])
        self.assertEqual(row["threshold"]["status"], "ok")
        t.set_bandwidth("P1", (3.0, 80.0))
        row = t.snapshot({1: v})["markers"][0]
        self.assertEqual(row["bandwidth"][1]["status"], "edge")
        self.assertIsNone(row["shape_factor"])
        with self.assertRaises(ValueError):
            t.set_bandwidth("P1", (3.0, 6.0, 60.0))

    def test_xy_cursors(self):
        self.t.set_xy(x1_hz=f(1), x2_hz=f(20), y1=-3.0, y2=2.5)
        xy = self.t.snapshot(self.views)["xy"]
        self.assertEqual((xy["dx_hz"], xy["dy"], xy["x1_status"], xy["x2_status"]), (19 * MHZ, 5.5, "ok", "out_of_range"))
        self.t.set_xy(y2=None)
        self.assertIsNone(self.t.snapshot(self.views)["xy"]["dy"])
        with self.assertRaises(TypeError):
            self.t.set_xy(x1_hz=1.5)

    def test_snapshot_is_json_serialisable(self):
        self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.t.add(Kind.MANUAL, 1, f(5), self.v)
        self.t.set_ref("M1", "P1")
        self.t.add_freq_offset("P1", Sign.BOTH, MHZ)
        self.t.add_level_offset("P1", Sign.MINUS, 3.0)
        self.t.set_bandwidth("P1", (3.0,))
        self.t.set_threshold("M1", ThresholdSetting(0.0, SIDE_BELOW))
        self.t.set_xy(x1_hz=f(0), y1=1.0)
        snap = self.t.snapshot(self.views)
        self.assertEqual(json.loads(json.dumps(snap, allow_nan=False)), snap)

    def test_reading_at(self):
        self.assertEqual(reading_at(self.v, f(3) + 10)["level"], 20.0)
        self.assertEqual(reading_at(self.v, f(30))["status"], "out_of_range")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.v = ratio_view([0, 10, 0, 20, 0])
        self.views = {1: self.v}
        self.t = MarkerTable()
        self.t.add(Kind.PEAK, 1, f(3), self.v)
        self.t.add(Kind.MANUAL, 1, f(1), self.v)

    def test_center(self):
        c = self.t.center_command("P1", self.views)
        self.assertEqual((c.request.center_hz, c.request.edited), (f(3), ("center_hz",)))

    def test_ref_level(self):
        c = self.t.ref_level_command("P1", self.views)
        self.assertEqual((c.ref_level, c.unit), (20.0, "dB"))

    def test_span_from_delta(self):
        self.assertEqual(self.t.span_from_delta_command("P1", self.views).code, "no_ref")
        self.t.set_ref("P1", "M1")
        c = self.t.span_from_delta_command("P1", self.views)
        self.assertEqual((c.request.start_hz, c.request.stop_hz, c.request.edited), (f(1), f(3), ("start_hz", "stop_hz")))
        self.t.set_frequency("M1", f(3), self.v)
        self.assertEqual(self.t.span_from_delta_command("P1", self.views).code, "same_frequency")

    def test_out_of_range_marker_gives_no_command(self):
        narrow = {1: ratio_view(self.v.level_db, disp=(f(2), f(4)))}
        self.assertEqual(self.t.center_command("M1", narrow).code, "out_of_range")
        self.assertEqual(self.t.ref_level_command("M1", narrow).code, "out_of_range")
        self.t.set_ref("P1", "M1")
        self.assertEqual(self.t.span_from_delta_command("P1", narrow).code, "ref_unavailable")


if __name__ == "__main__":
    unittest.main()
