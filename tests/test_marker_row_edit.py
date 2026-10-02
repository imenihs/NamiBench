"""マーカー表の行だけを消す・セルを直す決まり（G §3.7）。Qt と Web が同じ marker_rows の決まりを使う。"""
import unittest

from pygigast.api.web import view
from pygigast.core.units import ParseError
from pygigast.ui_qt import marker_rows as mr

SNAP = {
    "markers": [
        {"id": "M1", "kind": "M", "trace": 1, "freq_hz": 434_600_000, "level": -3.47, "unit": "dB", "status": "ok",
         "ref": None, "delta": {}, "freq_offsets": [], "level_offsets": [
             {"id": "M1.l0", "sign": "-", "offset_db": 3.0, "level": -6.47, "freq_hz": None, "status": "ok"}],
         "bandwidth": [{"n_db": 3.0, "status": "ok", "left_hz": 430_168_000, "right_hz": 437_477_000,
                        "left_status": "ok", "right_status": "ok"},
                       {"n_db": 20.0, "status": "ok", "left_hz": 428_872_000, "right_hz": 440_401_000,
                        "left_status": "ok", "right_status": "ok"}],
         "shape_factor": 1.58,
         "threshold": {"level": -60.0, "side": "above", "status": "ok", "left_hz": 418_625_000,
                       "right_hz": 456_181_000, "left_status": "ok", "right_status": "ok"}},
        {"id": "M2", "kind": "M", "trace": 1, "freq_hz": 450_000_000, "level": -50.0, "unit": "dB", "status": "ok",
         "ref": "M1", "delta": {"freq_hz": 15_400_000, "level_db": -46.5, "status": "ok"},
         "freq_offsets": [], "level_offsets": []},
    ],
    "xy": {"x1_hz": 425_000_000, "x2_hz": 430_000_000, "dx_hz": 5_000_000, "y1": -40.0, "y2": None},
}


class RemoveTest(unittest.TestCase):
    def setUp(self):
        self.rows = {r["id"]: r for r in mr.rows_from_snapshot(SNAP)}

    def cmd(self, rid):
        return mr.remove_command(self.rows[rid], SNAP)

    def test_each_row_removes_only_itself(self):
        self.assertEqual(self.cmd("M1"), ("marker_remove", {"id": "M1"}))
        self.assertEqual(self.cmd("M1.bw3"), ("marker_function", {"id": "M1", "bandwidth_db": [20.0]}))
        self.assertEqual(self.cmd("M1.bw20"), ("marker_function", {"id": "M1", "bandwidth_db": [3.0]}))
        self.assertEqual(self.cmd("M1.sf"), ("marker_function", {"id": "M1", "bandwidth_db": [3.0]}))
        self.assertEqual(self.cmd("M1.th"), ("marker_function", {"id": "M1", "threshold": None}))
        lvl = next(k for k, r in self.rows.items() if r.get("offset") == ("level", 0))
        self.assertEqual(self.cmd(lvl), ("marker_offset_remove", {"id": "M1", "type": "level", "index": 0}))
        self.assertEqual(self.cmd("X2"), ("set_xy", {"x2_hz": None}))


class EditTest(unittest.TestCase):
    def setUp(self):
        self.list = mr.rows_from_snapshot(SNAP)
        self.rows = {r["id"]: r for r in self.list}

    def edit(self, rid, col, text, off=0):
        spec = mr.edit_spec(self.rows[rid], col, self.list, SNAP)
        self.assertIsNotNone(spec, (rid, col))
        return mr.edit_command(spec, text, off)

    def test_cells(self):
        self.assertEqual(self.edit("M1", mr.COL_FREQ, "433"), ("marker_set", {"id": "M1", "freq_hz": 433_000_000}))
        # 周波数オフセット 100MHz を出している表では、打った値から差し引く
        self.assertEqual(self.edit("M1", mr.COL_FREQ, "533", off=100_000_000)[1]["freq_hz"], 433_000_000)
        # デルタの相手 M1 から +10MHz
        self.assertEqual(self.edit("M2", mr.COL_DFREQ, "10")[1]["freq_hz"], 444_600_000)
        lvl = next(k for k, r in self.rows.items() if r.get("offset") == ("level", 0))
        self.assertEqual(self.edit(lvl, mr.COL_DVALUE, "-6"),
                         ("marker_offset_set", {"id": "M1", "type": "level", "index": 0, "delta": -6.0}))
        self.assertEqual(self.edit("X2", mr.COL_DFREQ, "2")[1], {"x2_hz": 427_000_000})
        self.assertEqual(self.edit("Y1", mr.COL_VALUE, "-35")[1], {"y1": -35.0})
        with self.assertRaises(ParseError):
            self.edit("M1", mr.COL_FREQ, "abc")

    def test_not_editable(self):
        self.assertIsNone(mr.edit_spec(self.rows["M1"], mr.COL_VALUE, self.list, SNAP))  # 測った値は直せない
        self.assertIsNone(mr.edit_spec(self.rows["M1"], mr.COL_DFREQ, self.list, SNAP))  # 相手が無い
        self.assertIsNone(mr.edit_spec(self.rows["M1.bw3"], mr.COL_FREQ, self.list, SNAP))


class WebRowsTest(unittest.TestCase):
    def test_web_rows_carry_remove_and_edits(self):
        v = view.markers_view(SNAP, "dB", 0)
        rows = {r["id"]: r for r in v["rows"]}
        self.assertEqual(rows["M1.th"]["remove"], {"cmd": "marker_function", "args": {"id": "M1", "threshold": None}})
        self.assertIn(str(mr.COL_FREQ), rows["M1"]["edits"])
        self.assertNotIn(str(mr.COL_VALUE), rows["M1"]["edits"])
        self.assertEqual(rows["M1.bw3"]["edits"], {})

    def test_web_app_uses_rows(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "pygigast/ui_web"
        html = (root / "index.html").read_text(encoding="utf-8")
        js = (root / "app.js").read_text(encoding="utf-8") + (root / "app_menu.js").read_text(encoding="utf-8")
        self.assertIn('@dblclick="startEdit(r, i)"', html)
        self.assertIn('@click="rowClick(r)"', html)
        self.assertIn("removeSelectedRow()", js)


if __name__ == "__main__":
    unittest.main()
