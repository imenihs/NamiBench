"""計測器の本体: トレース・単発の回数・条件の変更・元に戻す・マーカー・状態の保存（設計A §4.3.1）。"""
import unittest

import numpy as np

from pygigast.core.instrument import CommandError
from tests.instrument_helpers import InstrumentCase


class TraceCommandsTest(InstrumentCase):
    def test_single_runs_max_count_of_traces(self):
        # 単発 = 回数を持つトレースのうち最大の N 回（設計F §5.2.1）
        self.connect()
        self.call("set_trace", number=1, action="average", count=3)
        self.call("set_trace", number=2, action="max_hold", count=2, visible=True)
        st = self.single()
        self.assertEqual((st["run"]["single_target"], st["run"]["single_done"]), (3, 3))
        tr = {t["number"]: t for t in st["traces"]}
        self.assertEqual((tr[1]["current_count"], tr[1]["count_target"]), (3, 3))
        self.assertEqual(tr[2]["current_count"], 2)
        # 2回目の単発は蓄積を捨ててから数え直す
        st = self.single()
        self.assertEqual({t["number"]: t for t in st["traces"]}[1]["current_count"], 3)

    def test_single_without_counted_trace_is_one_sweep(self):
        self.connect()
        st = self.single()
        self.assertEqual((st["run"]["single_target"], st["run"]["single_done"]), (1, 1))

    def test_trace_event_shape(self):
        self.connect()
        self.call("set_trace", number=1, action="average", count=2)
        self.single()
        t = self.wait_for(lambda k, p: k == "trace" and p["trace"] == 1 and p["count"] == 2)
        self.assertEqual((t["source"], t["quantity"], t["unit"], t["action"]), ("live", "power", "dBm", "average"))
        self.assertEqual(t["average_type"], "power")
        self.assertEqual(t["values"].dtype, np.float32)
        self.assertEqual(len(t["values"]), t["axis"]["points"])
        self.assertIn("transient", t["flags"]["points"])
        self.assertIn("transient_head_points", t["flags"])
        # 100MHz の −30dBm の信号が見える（補正なし）
        f = t["axis"]["start_hz"] + t["axis"]["step_hz"] * np.arange(t["axis"]["points"])
        near = abs(f - 100_000_000) <= 1_000_000
        self.assertAlmostEqual(float(np.nanmax(t["values"][near])), -30.0, delta=1.5)
        m = self.wait_for(lambda k, p: k == "markers")
        self.assertIn("markers", m)
        self.assertIn("xy", m)

    def test_hidden_trace_emits_nothing(self):
        self.connect()
        self.single()
        numbers = {p["trace"] for k, p in self.events if k == "trace"}
        self.assertEqual(numbers, {1})

    def test_set_trace_errors_and_clear(self):
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=5, action="update")
        self.assertEqual(cm.exception.code, "bad_trace")
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=1, action="average")  # 平均は回数が要る
        self.assertEqual(cm.exception.code, "bad_trace_setting")
        r = self.call("set_trace", number=1, action="average", count=4, average_type="log")
        self.assertIn("log_average_reads_low", r["notices"])
        self.connect()
        self.single()
        r = self.call("clear_trace", number=1)
        self.assertEqual(r["trace"]["current_count"], 0)

    def test_condition_change_resets_once(self):
        self.connect()
        self.call("set_trace", number=1, action="max_hold")
        self.single()
        self.clear_events()
        self.call("set_sweep", span_hz=100_000_000)
        n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "trace_reset")
        self.assertEqual((n["detail"]["traces"], n["detail"]["cause"]), ([1], "axis"))
        resets = [p for k, p in self.events if k == "notice" and p["code"] == "trace_reset"]
        self.assertEqual(len(resets), 1)
        st = self.call("get_state")
        self.assertEqual(st["traces"][0]["action"], "max_hold")  # 動作は OFF にしない

    def test_undo_redo_span(self):
        self.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000)
        before = self.call("get_state")["sweep"]
        self.call("set_sweep", span_hz=20_000_000)
        self.assertEqual(self.call("get_state")["sweep"]["span_hz"], 20_000_000)
        r = self.call("undo")
        self.assertEqual((r["adopted"]["start_hz"], r["adopted"]["stop_hz"]),
                         (before["start_hz"], before["stop_hz"]))
        self.assertTrue(self.call("get_state")["undo"]["can_redo"])
        r = self.call("redo")
        self.assertEqual(r["adopted"]["span_hz"], 20_000_000)
        # トレースの設定も戻せる
        self.call("set_trace", number=3, action="min_hold", visible=True)
        self.call("undo")
        t3 = self.call("get_state")["traces"][2]
        self.assertEqual((t3["action"], t3["visible"]), ("update", False))
        self.call("undo")
        self.call("undo")
        with self.assertRaises(CommandError) as cm:
            self.call("undo")
        self.assertEqual(cm.exception.code, "nothing_to_undo")


class MarkerCommandsTest(InstrumentCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.single()

    def test_marker_add_peak_and_move(self):
        r = self.call("marker_add", kind="M", trace=1)
        self.assertEqual(r["id"], "M1")
        self.assertAlmostEqual(r["freq_hz"], 100_000_000, delta=1_000_000)
        r = self.call("marker_add", kind="P", trace=1, freq_hz=150_000_123)
        self.assertTrue(r["snapped"])
        r = self.call("marker_set", id="M1", step=5)
        rows = {m["id"]: m for m in self.call("get_state")["markers"]["markers"]}
        self.assertEqual(r["freq_hz"], rows["M1"]["freq_hz"])
        self.call("marker_set", id="M1", ref="P1", freq_hz=120_000_000)
        rows = {m["id"]: m for m in self.call("get_state")["markers"]["markers"]}
        self.assertEqual(rows["M1"]["delta"]["status"], "ok")
        with self.assertRaises(CommandError):
            self.call("marker_set", id="M1", peak_track=True)  # M は追従できない
        self.call("marker_set", id="P1", peak_track=True)
        self.call("marker_remove", id="P1")
        self.assertIsNone({m["id"]: m for m in self.call("get_state")["markers"]["markers"]}["M1"]["ref"])
        with self.assertRaises(CommandError) as cm:
            self.call("marker_remove", id="P9")
        self.assertEqual(cm.exception.code, "unknown_marker")
        self.call("marker_clear")
        self.assertEqual(self.call("get_state")["markers"]["markers"], [])

    def test_peak_commands(self):
        r = self.call("marker_peak")  # 何も無ければ M を作る
        self.assertEqual(r["id"], "M1")
        self.call("marker_set", id="M1", freq_hz=60_000_000)
        r = self.call("marker_peak", id="M1", direction="right")
        self.assertAlmostEqual(r["freq_hz"], 100_000_000, delta=1_000_000)
        with self.assertRaises(CommandError):
            self.call("marker_peak", id="M1", direction="up")
        r = self.call("marker_top_peaks", trace=1, n=2)
        self.assertGreaterEqual(len(r["placed"]), 1)
        self.assertEqual(r["placed"][0]["id"], "P1")
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0)
        self.assertEqual(pl["unit"], "dBm")
        self.assertTrue(any(abs(p["freq_hz"] - 100_000_000) <= 1_000_000 for p in pl["peaks"]))
        r = self.call("marker_register_peak", trace=1, freq_hz=pl["peaks"][0]["freq_hz"])
        self.assertTrue(r["id"].startswith("P"))

    def test_offsets_functions_xy(self):
        self.call("marker_add", kind="M", trace=1)
        self.call("marker_offset_add", id="M1", type="freq", sign="±", delta=10_000_000)
        self.call("marker_offset_add", id="M1", type="level", sign="-", delta=3.0)
        row = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(len(row["freq_offsets"]), 2)
        self.assertEqual(len(row["level_offsets"]), 1)
        self.call("marker_offset_remove", id="M1", type="freq", index=0)
        with self.assertRaises(CommandError):
            self.call("marker_offset_remove", id="M1", type="freq", index=3)
        self.call("marker_function", id="M1", bandwidth_db=[3.0], threshold={"level": -60.0, "side": "above"})
        row = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(row["bandwidth"][0]["n_db"], 3.0)
        self.assertEqual(row["threshold"]["side"], "above")
        r = self.call("set_xy", x1_hz=80_000_000, x2_hz=120_000_000, y1=-40.0)
        self.assertEqual(r["xy"]["dx_hz"], 40_000_000)
        self.call("set_xy", x1_hz=None)
        self.assertIsNone(self.call("get_state")["markers"]["xy"]["x1_hz"])
        with self.assertRaises(CommandError):
            self.call("set_xy", x2_hz=1.5)

    def test_marker_to_center_ref_span(self):
        m = self.call("marker_add", kind="M", trace=1)
        r = self.call("marker_to", id="M1", target="center")
        self.assertEqual(r["adopted"]["center_hz"], m["freq_hz"])
        self.assertTrue(self.call("get_state")["undo"]["can_undo"])
        # 範囲の端で Center を守る規則（設計C §5.11）: Span を 1000MHz にしてから 100MHz を Center に
        self.call("set_sweep", start_hz=-100_000_000, stop_hz=12_100_000_000)
        self.single()
        self.call("marker_set", id="M1", freq_hz=100_000_000)
        f = self.call("get_state")["markers"]["markers"][0]["freq_hz"]
        r = self.call("marker_to", id="M1", target="center")
        self.assertEqual(r["adopted"]["center_hz"], f)
        self.assertEqual(r["adopted"]["start_hz"], -100_000_000)
        self.assertIn("keep_center", {a["reason"] for a in r["adjustments"]})
        r = self.call("marker_to", id="M1", target="ref")
        self.assertEqual(r["amplitude"]["unit"], "dBm")
        self.assertIsNotNone(r["amplitude"]["ref_level"])
        with self.assertRaises(CommandError) as cm:
            self.call("marker_to", id="M1", target="span")
        self.assertEqual(cm.exception.code, "no_ref")


class TraceLineTest(InstrumentCase):
    """トレースの線（設計F §9）: 5つから選ぶ・既定は実線・変えても蓄積を捨てない・トレースの設定と一緒に覚える。"""

    def lines(self):
        return [t["line"] for t in self.call("get_state")["traces"]]

    def test_default_and_choices(self):
        self.assertEqual(self.lines(), ["solid"] * 4)
        for code in ("thin", "solid", "thick", "dash", "dot"):
            r = self.call("set_trace", number=3, line=code)
            self.assertEqual(r["trace"]["line"], code)
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=3, line="wavy")
        self.assertEqual(cm.exception.code, "bad_trace_setting")
        self.assertEqual(self.lines()[2], "dot")  # 読めない線では何も変えない

    def test_changing_line_keeps_accumulation(self):
        self.connect()
        self.call("set_trace", number=2, action="average", count=3, visible=True)
        self.single()
        before = {t["number"]: t for t in self.call("get_state")["traces"]}[2]
        self.assertEqual(before["current_count"], 3)
        self.clear_events()
        self.call("set_trace", number=2, line="thick")
        after = {t["number"]: t for t in self.call("get_state")["traces"]}[2]
        self.assertEqual((after["line"], after["current_count"], after["has_values"]), ("thick", 3, True))
        # 捨てた知らせを出さず、描き直すトレースの中身に線が入る
        self.assertFalse([p for k, p in self.events if k == "notice" and p.get("code") == "trace_reset"])
        t = self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        self.assertEqual((t["line"], t["count"]), ("thick", 3))

    def test_line_survives_restart(self):
        self.call("set_trace", number=1, line="thin")
        self.call("set_trace", number=2, action="average", count=8, visible=True, line="thick")
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertEqual(self.lines(), ["thin", "thick", "solid", "solid"])

    def test_old_or_broken_saved_line_is_solid(self):
        # 前の版の設定（線が無い）・読めない線は実線にし、ほかの欄は戻す
        from pygigast.core.instrument_display import apply_trace_settings
        from pygigast.core.trace import Trace

        t = Trace(2)
        apply_trace_settings(t, {"number": 2, "action": "max_hold", "count": 4, "visible": True})
        self.assertEqual((t.action.value, t.count, t.line.value), ("max_hold", 4, "solid"))
        t.configure(line="dash")
        apply_trace_settings(t, {"number": 2, "action": "average", "count": 8, "visible": True, "line": "wavy"})
        self.assertEqual((t.action.value, t.count, t.line.value), ("average", 8, "solid"))


class PersistTest(InstrumentCase):
    def test_trace_and_marker_settings_survive_restart(self):
        # 終了時の状態を次の起動で戻す（設計O §1.2）
        self.call("set_trace", number=2, action="average", count=8, visible=True, average_type="voltage")
        self.call("marker_add", kind="M", trace=1, freq_hz=123_000_000)
        self.call("marker_add", kind="P", trace=2, freq_hz=150_000_000)
        self.call("marker_set", id="M1", ref="P1")
        self.call("set_xy", y1=-20.0)
        self.call("set_amplitude", ref_level=-10.0, db_per_div=5)
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")
        t2 = st["traces"][1]
        self.assertEqual((t2["action"], t2["count"], t2["visible"], t2["average_type"]),
                         ("average", 8, True, "voltage"))
        rows = {m["id"]: m for m in st["markers"]["markers"]}
        self.assertEqual(rows["M1"]["target_hz"], 123_000_000)
        self.assertEqual(rows["M1"]["ref"], "P1")
        self.assertEqual(rows["P1"]["trace"], 2)
        self.assertEqual(st["markers"]["xy"]["y1"], -20.0)
        self.assertEqual(st["amplitude"]["db_per_div"], 5)


if __name__ == "__main__":
    unittest.main()
