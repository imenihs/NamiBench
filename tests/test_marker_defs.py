"""MarkerTable の置き方の書き出し・読み戻し（to_defs / from_defs）と set_trace。"""
from __future__ import annotations

import json
import unittest

from pygigast.core.marker import Kind, MarkerTable, Sign, ThresholdSetting
from tests.test_marker import f, ratio_view
from tests.instrument_helpers import InstrumentCase


def filled_table() -> MarkerTable:
    v = ratio_view([0, 10, 0, 20, 0, 15, 0, -5, -5, -5])
    t = MarkerTable()
    t.add(Kind.PEAK, 1, f(3), v)
    t.add(Kind.MANUAL, 2, f(5), v)
    t.add(Kind.MANUAL, 1, f(1), v)
    t.set_ref("M1", "P1")
    t.set_peak_track("P1", True)
    t.add_freq_offset("M1", Sign.BOTH, 500_000)
    t.add_level_offset("P1", Sign.MINUS, 3.0)
    t.set_bandwidth("P1", (3.0, 60.0))
    t.set_threshold("M2", ThresholdSetting(-10.0, "below"))
    t.set_xy(x1_hz=f(1), x2_hz=f(4), y1=-3.5, y2=None)
    t.excursion_db = 6.0
    t.peak_threshold = -40.0
    return t


class MarkerDefsTest(unittest.TestCase):
    def test_round_trip_through_json(self):
        t = filled_table()
        d = json.loads(json.dumps(t.to_defs()))
        u = MarkerTable()
        self.assertEqual(u.from_defs(d), [])
        self.assertEqual(u.to_defs(), t.to_defs())
        self.assertEqual([m.id for m in u.markers()], ["P1", "M1", "M2"])
        self.assertEqual(u.get("M1").ref, "P1")
        self.assertTrue(u.get("P1").peak_track)
        self.assertEqual(u.get("M1").trace, 2)
        self.assertEqual((u.xy.x1_hz, u.xy.x2_hz, u.xy.y1, u.xy.y2), (f(1), f(4), -3.5, None))
        self.assertEqual((u.excursion_db, u.peak_threshold), (6.0, -40.0))
        # 読み値も同じ（同じトレースに載る）
        views = {1: ratio_view([0, 10, 0, 20, 0, 15, 0, -5, -5, -5]),
                 2: ratio_view([0, 10, 0, 20, 0, 15, 0, -5, -5, -5], trace=2)}
        self.assertEqual(u.snapshot(views), t.snapshot(views))

    def test_bad_rows_are_skipped_and_refs_dropped(self):
        t = filled_table()
        d = t.to_defs()
        rows = d["markers"]
        p1 = rows[0]
        rows[0] = {"kind": "X", "number": 1}  # 読めない行（P1 を消す）
        rows.append(dict(rows[1]))  # 同じ番号の2行目
        rows.append({**rows[1], "number": 99})  # 番号が範囲の外
        rows.append({**p1, "kind": "M", "number": 5, "peak_track": True})  # M の追従は持てない
        u = MarkerTable()
        self.assertEqual(u.from_defs(d), [0, 3, 4])
        self.assertIsNone(u.get("M1").ref)  # 相手（P1）がいない
        self.assertFalse(u.get("M5").peak_track)
        self.assertEqual([m.id for m in u.markers()], ["M1", "M2", "M5"])

    def test_list_of_rows_and_bad_xy(self):
        t = filled_table()
        u = MarkerTable()
        u.set_xy(x1_hz=f(0))
        u.from_defs(t.to_defs()["markers"])  # 行の並びだけの形
        self.assertEqual(len(u.markers()), 3)
        self.assertIsNone(u.xy.x1_hz)  # 表を作り直すので XY も外れる
        u.from_defs({"markers": [], "xy": {"x1_hz": 1.5}})  # int でない X
        self.assertIsNone(u.xy.x1_hz)

    def test_xy_y_kind_round_trip(self):
        """Y を置いたときの縦軸の単位の種類も戻す。種類の無い前の版・読めない種類は分からない（None）。"""
        t = MarkerTable()
        t.set_xy(y1=2.0, y2=-40.0)
        t.set_y_kind("y1", "swr")
        t.set_y_kind("y2", "power")
        u = MarkerTable()
        u.from_defs(json.loads(json.dumps(t.to_defs())))
        self.assertEqual((u.xy.y1, u.xy.y1_kind, u.xy.y2, u.xy.y2_kind), (2.0, "swr", -40.0, "power"))
        u.from_defs({"markers": [], "xy": {"y1": 2.0, "y2": -40.0, "y2_kind": "dBm"}})
        self.assertEqual((u.xy.y1_kind, u.xy.y2_kind), (None, None))
        # 置き直した Y の種類は、呼ぶ側が入れ直すまで分からない
        t.set_xy(y1=1.5)
        self.assertEqual((t.xy.y1_kind, t.xy.y2_kind), (None, "power"))

    def test_set_trace(self):
        t = filled_table()
        t.set_trace("M1", 3)
        self.assertEqual(t.get("M1").trace, 3)
        self.assertEqual(t.get("M1").target_hz, f(5))
        with self.assertRaises(TypeError):
            t.set_trace("M1", "2")
        with self.assertRaises(KeyError):
            t.set_trace("M8", 1)


class InstrumentMarkerRestoreTest(InstrumentCase):
    def test_markers_survive_restart(self):
        self.connect()
        self.single()
        self.call("marker_add", kind="P", trace=1)
        self.call("marker_add", kind="M", trace=1, freq_hz=80_000_000)
        self.call("marker_set", id="M1", ref="P1", trace=2)
        self.call("set_xy", x1_hz=60_000_000)
        before = self.call("get_state")["markers"]
        self.inst.shutdown()
        self.inst = self.make_instrument()
        after = self.call("get_state")["markers"]
        self.assertEqual([(r["id"], r["trace"], r["target_hz"], r["ref"]) for r in after["markers"]],
                         [(r["id"], r["trace"], r["target_hz"], r["ref"]) for r in before["markers"]])
        self.assertEqual(after["markers"][1]["trace"], 2)
        self.assertEqual(after["xy"]["x1_hz"], 60_000_000)


if __name__ == "__main__":
    unittest.main()
