"""計測器の本体: トレース間の差「A − B」（設計F §5.4・設計A §4.3.1）。シミュレータと一時フォルダで行う。"""
import csv
import json
import unittest

import numpy as np

from pygigast.core.instrument import CommandError
from pygigast.core.storage.h5file import FileHeader, TraceData, save_snapshot
from pygigast.core.types import FreqAxis, Quantity, ValueKind
from tests.instrument_helpers import SERIAL, InstrumentCase

START, STOP = 50_000_000, 200_000_000


def _watt(dbm):
    return np.power(10.0, (np.asarray(dbm, dtype=np.float64) - 30.0) / 10.0)


class DifferenceTest(InstrumentCase):
    def live(self, number=1):
        t = self.call("get_trace", id=number)
        a = t["axis"]
        freqs = a["start_hz"] + a["step_hz"] * np.arange(a["points"], dtype=np.int64)
        return freqs, np.asarray(t["values"], dtype=np.float64), t

    def write_file(self, name, axis, values, quantity=Quantity.POWER):
        """参照トレースにするファイルを作る（値はリニア: 電力は W・比は振幅比）。"""
        path = self.root / name
        td = TraceData(number=1, values=np.asarray(values, dtype=np.float64), quantity=quantity,
                       value_kind=ValueKind.REAL, axis=axis)
        save_snapshot(path, FileHeader("gigast-v5", "TEST"), None, [td], quantity=quantity)
        return path

    def open_file(self, path):
        n = self.wait_task(self.call("open", paths=[str(path)])["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        return n["detail"]["result"]["files"][0]["file_id"] + ".1"

    def diff_event(self, pred=lambda p: True):
        return self.wait_for(lambda k, p: k == "trace" and p["trace"] == "D" and pred(p))

    def test_live_minus_file_on_same_axis(self):
        self.connect(START, STOP)
        self.single()
        freqs, db, _ = self.live(1)
        # B = A − 3dB（電力を半分）。差は +3.0103dB（値なしの点は値なしのまま）
        tid = self.open_file(self.write_file("half.h5", FreqAxis(int(freqs[0]), int(freqs[1] - freqs[0]),
                                                                 freqs.size), _watt(db) * 0.5))
        self.clear_events()
        r = self.call("set_difference", a=1, b=tid)
        self.assertEqual(r["difference"], {"a": 1, "b": tid, "active": True, "error": None})
        d = self.diff_event()
        self.assertEqual((d["source"], d["unit"], d["quantity"], d["a"], d["b"]),
                         ("difference", "dB", "ratio", 1, tid))
        self.assertEqual(d["values"].dtype, np.float32)
        self.assertEqual(d["axis"]["points"], freqs.size)
        self.assertEqual(d["origin"]["a"], {"source": "live", "trace": 1})
        self.assertEqual(d["origin"]["b"]["source"], "file")
        ok = np.isfinite(db)
        np.testing.assert_allclose(d["values"][ok], 10 * np.log10(2), atol=1e-3)
        self.assertTrue(np.all(np.isnan(d["values"][~ok])))
        self.assertEqual(self.call("get_trace", id="D")["trace"], "D")
        self.assertTrue(self.call("get_state")["difference"]["active"])

    def test_different_axis_is_interpolated_over_overlap_only(self):
        self.connect(START, STOP)
        self.single()
        freqs, db, _ = self.live(1)
        # B は 80〜150MHz・1MHz 間隔で、dB が周波数に比例（dB での線形補間なら A の点で正確に一致する）
        bax = FreqAxis(80_000_000, 1_000_000, 71)
        bf = bax.freqs()
        bdb = -50.0 + (bf - 80_000_000) / 1e6 * 0.1
        tid = self.open_file(self.write_file("slope.h5", bax, _watt(bdb)))
        self.clear_events()
        self.call("set_difference", a=1, b=tid)
        d = self.diff_event()
        inside = (freqs >= 80_000_000) & (freqs <= 150_000_000)
        expect = db - (-50.0 + (freqs - 80_000_000) / 1e6 * 0.1)
        ok = inside & np.isfinite(db)
        np.testing.assert_allclose(d["values"][ok], expect[ok], atol=2e-3)
        # 重ならない点は値なし
        self.assertTrue(np.all(np.isnan(d["values"][~inside])))

    def test_refused_quantity_mismatch_no_overlap_unknown(self):
        self.connect(START, STOP)
        self.single()
        ratio = self.open_file(self.write_file("s21.h5", FreqAxis(START, 1_000_000, 151), np.ones(151),
                                               Quantity.RATIO))
        far = self.open_file(self.write_file("far.h5", FreqAxis(300_000_000, 1_000_000, 10), np.ones(10) * 1e-6))
        for b, code in ((ratio, "diff_quantity_mismatch"), (far, "diff_no_overlap"), ("F99.1", "diff_unknown_trace"),
                        (5, "diff_unknown_trace")):
            with self.assertRaises(CommandError) as cm:
                self.call("set_difference", a=1, b=b)
            self.assertEqual(cm.exception.code, code)
        # 断ったら選ばない
        self.assertEqual(self.call("get_state")["difference"],
                         {"a": None, "b": None, "active": False, "error": None})

    def test_updates_per_sweep_and_clear(self):
        self.connect(START, STOP)
        self.call("set_trace", number=2, action="fixed")
        self.call("set_difference", a=1, b=2)
        # 2 にまだ値が無い: 選んでおき、作れるまで待つ（理由ではない）
        self.assertEqual(self.call("get_state")["difference"], {"a": 1, "b": 2, "active": False, "error": None})
        self.call("set_trace", number=2, action="update")
        self.single()
        self.call("set_trace", number=2, action="fixed")
        self.clear_events()
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "state" and p["run"]["sweep_count"] >= 4)
        self.call("stop")
        sweeps = [p["sweep_no"] for k, p in list(self.events) if k == "trace" and p["trace"] == "D"]
        self.assertGreaterEqual(len(sweeps), 3)
        self.assertEqual(len(sweeps), len(set(sweeps)))  # 掃引ごとに1回
        # 1 を捨てると差は作れない（値を待つ）
        self.call("stop")
        self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped")
        self.call("set_trace", number=1, action="max_hold")
        self.call("clear_trace", number=1)
        self.assertFalse(self.call("get_state")["difference"]["active"])
        self.call("clear_difference")
        self.assertEqual(self.call("get_state")["difference"]["a"], None)

    def test_mode_change_to_ratio_reports_mismatch(self):
        self.connect(100_000_000, 200_000_000)
        self.single()
        freqs, db, _ = self.live(1)
        tid = self.open_file(self.write_file("p.h5", FreqAxis(int(freqs[0]), int(freqs[1] - freqs[0]), freqs.size),
                                             _watt(db)))
        self.call("set_difference", a=1, b=tid)
        # TG（比）に切り替えて基準を当てると、A が比になり、電力のファイルとは差を作らない
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="wide")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.wait_for(lambda k, p: k == "notice" and p["code"] == "difference_unavailable"
                      and p["detail"]["code"] == "diff_quantity_mismatch")
        st = self.call("get_state")["difference"]
        self.assertEqual((st["active"], st["error"]["code"]), (False, "diff_quantity_mismatch"))

    def test_csv_with_difference(self):
        self.connect(START, STOP)
        self.single()
        freqs, db, _ = self.live(1)
        bax = FreqAxis(80_000_000, 1_000_000, 71)
        tid = self.open_file(self.write_file("flat.h5", bax, _watt(np.full(71, -40.0))))
        self.call("set_difference", a=1, b=tid)
        out = self.root / "d.csv"
        n = self.wait_task(self.call("export_csv", path=str(out), traces=["D"], unit="dB")["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        with open(out, newline="", encoding="ascii") as f:
            meta = json.loads(f.readline()[len("# meta: "):])
            rows = list(csv.reader(f))
        self.assertEqual(meta["traces"][0]["info"]["trace"], "D")
        self.assertEqual(rows[0][:2], ["frequency_hz", "traceD_dB"])
        got = {int(r[0]): r[1] for r in rows[1:]}
        for f_hz, v in zip(freqs, db):
            if 80_000_000 <= f_hz <= 150_000_000 and np.isfinite(v):
                self.assertAlmostEqual(float(got[int(f_hz)]), v + 40.0, delta=2e-3)
            elif not 80_000_000 <= f_hz <= 150_000_000 and int(f_hz) in got:
                self.assertEqual(got[int(f_hz)], "")  # 重ならない点は空欄
        # 差が無いときに "D" を選ぶと断る
        self.call("clear_difference")
        with self.assertRaises(CommandError) as cm:
            self.call("export_csv", path=str(out), traces=["D"])
        self.assertEqual(cm.exception.code, "no_trace_data")

    def test_selection_persists_and_rebinds_when_file_reopened(self):
        self.connect(START, STOP)
        self.single()
        freqs, db, _ = self.live(1)
        path = self.write_file("keep.h5", FreqAxis(int(freqs[0]), int(freqs[1] - freqs[0]), freqs.size), _watt(db))
        tid = self.open_file(path)
        self.call("set_difference", a=1, b=tid)
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")["difference"]
        self.assertEqual((st["a"], st["b"], st["active"]), (1, None, False))
        self.assertEqual(st["error"]["code"], "diff_unknown_trace")
        self.assertEqual(st["error"]["missing"][0]["path"], str(path))
        tid2 = self.open_file(path)
        st = self.call("get_state")["difference"]
        # 開き直したファイルへ結び直す。A（生きたトレース 1）はまだ値が無いので、差は値を待つ
        self.assertEqual((st["a"], st["b"], st["error"], st["active"]), (1, tid2, None, False))
        self.call("connect", backend="simulator", serial=SERIAL)
        self.single()
        d = self.diff_event()
        self.assertEqual(d["b"], tid2)


if __name__ == "__main__":
    unittest.main()
