"""基準の値（設計E §6.7: 表示 = 基準との比 + 基準の値）の試験。

- 当てる: 比に基準の値を足す。S11 の基準には当てない（全反射 = 0dB）
- 範囲（±100dB）と、ファイルへの保存・読込（版 3。版 2 以前は 0dB）・一覧
- 値を直したとき: トレースの値と蓄積（平均の領域ごと・保持）・流す表示の点を同じだけ動かす
- 縦軸に結び付いた値（REF・ピーク探索のしきい値・比の dB で置いた XY の Y・比の dB で決めたしきい値）を同じだけずらし、
  電力・|Γ|・SWR・NF で決めた値と種類の分からない前の版の Y は動かさない（ccf9359 の決まり）
- 計測器: 取る・直す・上書き合成・S11 は受けない・固定のトレースは固定したときの基準のまま・保存と CSV に残る
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import h5py
import numpy as np

from pygigast.core.instrument import CommandError
from pygigast.core.marker import MarkerTable, ThresholdSetting
from pygigast.core.marker.markers import Y_KIND_NF, Y_KIND_POWER, Kind
from pygigast.core.normalize import (
    VALUE_DB_LIMIT,
    ReferenceFileError,
    check_value_db,
    list_references,
    load_reference,
    new_reference,
    s11_segment_from_frames,
    save_reference,
)
from pygigast.core.ref_value import shift_ratio_levels
from pygigast.core.trace import TraceAction
from pygigast.core.trace.average import AverageType
from pygigast.core.trace.trace import Trace
from pygigast.core.types import FreqAxis, Frame, Mode, Quantity, ValueKind
from pygigast.core.zero_span import History
from tests.instrument_helpers import SERIAL, InstrumentCase
from tests.test_normalize import CONN, MHZ, PROFILE, apply, db_to_w, linear_db, make_frame, ref_from, to_db_ratio

S = "SER1"


class ApplyValueTest(unittest.TestCase):
    def test_display_is_ratio_plus_value(self):
        ref = new_reference("ATT", PROFILE, S, value_db=40.0)
        ref.merge(ref_from(100 * MHZ, MHZ, 11, linear_db))
        f = FreqAxis(100 * MHZ, MHZ, 11).freqs()
        meas = make_frame(100 * MHZ, MHZ, 11, db_to_w(linear_db(f) + 20.0 - 40.0))  # 利得 20dB のアンプの後に ATT 40dB
        res = apply(meas, ref, serial=S)
        self.assertTrue(res.applied)
        self.assertEqual(res.value_db, 40.0)
        np.testing.assert_allclose(to_db_ratio(res.frame.values), 20.0, atol=1e-9)
        ref.value_db = -12.5
        res = apply(meas, ref, serial=S)
        np.testing.assert_allclose(to_db_ratio(res.frame.values), -20.0 - 12.5, atol=1e-9)
        ref.value_db = 0.0
        np.testing.assert_allclose(to_db_ratio(apply(meas, ref, serial=S).frame.values), -20.0, atol=1e-9)

    def test_s11_reference_is_not_shifted(self):
        ref = new_reference("bridge", PROFILE, S, mode=Mode.S11, value_db=40.0)
        self.assertEqual(ref.applied_value_db, 0.0)
        f = FreqAxis(100 * MHZ, MHZ, 5).freqs()
        o = make_frame(100 * MHZ, MHZ, 5, db_to_w(linear_db(f)), mode=Mode.S11)
        ref.merge(s11_segment_from_frames([o], [o], connected_at=CONN, taken_at=2000.0))
        res = apply(make_frame(100 * MHZ, MHZ, 5, db_to_w(linear_db(f) - 6.0), mode=Mode.S11), ref, serial=S)
        self.assertEqual(res.value_db, 0.0)
        np.testing.assert_allclose(to_db_ratio(res.frame.values), -6.0, atol=1e-9)

    def test_range(self):
        self.assertEqual(VALUE_DB_LIMIT, 100.0)
        for ok in (-100, -100.0, 0, 99.9, 100.0):
            self.assertEqual(check_value_db(ok), float(ok))
        for bad in (100.1, -100.1, float("nan"), float("inf"), True, "40", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                check_value_db(bad)
        with self.assertRaises(ValueError):
            new_reference("x", PROFILE, S, value_db=150.0)


class StoreValueTest(unittest.TestCase):
    def test_round_trip_list_and_old_version(self):
        ref = new_reference("ATT", PROFILE, S, value_db=-40.0)
        ref.merge(ref_from(MHZ, MHZ, 4, linear_db))
        with tempfile.TemporaryDirectory() as d:
            p = save_reference(d, ref)
            with h5py.File(p, "r") as f:
                self.assertEqual(int(f.attrs["version"]), 3)
                self.assertEqual(float(f.attrs["value_db"]), -40.0)
            self.assertEqual(load_reference(p).value_db, -40.0)
            self.assertEqual([i.value_db for i in list_references(d)], [-40.0])
            # 版 2 以前は基準の値を持たない: 0dB として読む
            with h5py.File(p, "r+") as f:
                f.attrs["version"] = 2
                del f.attrs["value_db"]
            self.assertEqual(load_reference(p).value_db, 0.0)
            # 範囲の外・数でない値は壊れたファイル
            for bad in (250.0, "x"):
                with h5py.File(p, "r+") as f:
                    f.attrs["version"] = 3
                    f.attrs["value_db"] = bad
                with self.assertRaises(ReferenceFileError):
                    load_reference(p)
            self.assertEqual(list_references(d), [])


def ratio_frame(db, points=5):
    axis = FreqAxis(100 * MHZ, MHZ, points)
    v = np.full(points, 10.0 ** (np.asarray(db, dtype=float) / 20.0))
    return Frame(axis=axis, values=v, quantity=Quantity.RATIO, value_kind=ValueKind.REAL, time=0.0, duration_s=0.1,
                 sweep_no=1, display_start_hz=axis.start_hz, display_stop_hz=axis.last_hz)


class TraceScaleTest(unittest.TestCase):
    def db(self, t):
        return 20.0 * np.log10(np.abs(t.values))

    def test_average_domains_and_holds(self):
        """平均（電圧・パワー・対数）と最大・最小保持: 動かした後に動かした値の掃引を足すと、最初から動かした値で
        平均・保持したのと同じになる（蓄積も同じだけ動いている）。"""
        cases = [(TraceAction.AVERAGE, at) for at in (AverageType.VOLTAGE, AverageType.POWER, AverageType.LOG)]
        cases += [(TraceAction.MAX_HOLD, None), (TraceAction.MIN_HOLD, None), (TraceAction.UPDATE, None)]
        for action, at in cases:
            with self.subTest(action=action, average=at):
                a = Trace(1, action, count=4 if action == TraceAction.AVERAGE else None, average_type=at)
                b = Trace(1, action, count=4 if action == TraceAction.AVERAGE else None, average_type=at)
                for db in (-20.0, -10.0):
                    a.apply(ratio_frame(db))
                    b.apply(ratio_frame(db + 40.0))
                a.scale_db(40.0)
                np.testing.assert_allclose(self.db(a), self.db(b), atol=1e-9)
                a.apply(ratio_frame(-30.0 + 40.0))
                b.apply(ratio_frame(-30.0 + 40.0))
                np.testing.assert_allclose(self.db(a), self.db(b), atol=1e-9)

    def test_no_values_and_zero(self):
        t = Trace(1)
        t.scale_db(10.0)
        self.assertIsNone(t.values)
        t.apply(ratio_frame(-3.0))
        before = t.values.copy()
        t.scale_db(0.0)
        np.testing.assert_array_equal(t.values, before)


class HistoryShiftTest(unittest.TestCase):
    def test_shift_one_trace(self):
        h = History()
        h.add_sweep(1, 100.0, 0.001, {1: (np.array([-10.0, -11.0], np.float32), {}),
                                      2: (np.array([-5.0, -6.0], np.float32), {})})
        h.shift_db(1, 40.0)
        np.testing.assert_allclose(h.window(1, 10).level_db, [30.0, 29.0])
        np.testing.assert_allclose(h.window(2, 10).level_db, [-5.0, -6.0])
        np.testing.assert_allclose(h.latest(1).level_db, [30.0, 29.0])
        h.shift_db(9, 1.0)  # 無いトレースは何もしない


class ShiftLevelsTest(unittest.TestCase):
    def test_only_values_set_in_ratio_db(self):
        amp = {"ref_level": 0.0}
        t = MarkerTable()
        t.xy.y1, t.xy.y1_kind = -20.0, "db"
        t.xy.y2, t.xy.y2_kind = -50.0, Y_KIND_POWER
        t.peak_threshold = -60.0
        tables = [t]
        m_db = t.add(Kind.MANUAL, 1, 100 * MHZ, None)
        self.assertTrue(m_db.ok, m_db)
        t.get(m_db.marker_id).threshold = ThresholdSetting(-3.0, "below", fmt="db", power=False)
        m_pw = t.add(Kind.MANUAL, 1, 101 * MHZ, None)
        t.get(m_pw.marker_id).threshold = ThresholdSetting(-40.0, "below", fmt="db", power=True)
        m_swr = t.add(Kind.MANUAL, 1, 102 * MHZ, None)
        t.get(m_swr.marker_id).threshold = ThresholdSetting(2.0, "below", fmt="swr", power=False)
        shift_ratio_levels(amp, tables, 40.0)
        self.assertEqual(amp["ref_level"], 40.0)
        self.assertEqual((t.xy.y1, t.xy.y2), (20.0, -50.0))
        self.assertEqual(t.peak_threshold, -20.0)
        self.assertEqual(t.get(m_db.marker_id).threshold.level, 37.0)
        self.assertEqual(t.get(m_pw.marker_id).threshold.level, -40.0)
        self.assertEqual(t.get(m_swr.marker_id).threshold.level, 2.0)
        # |Γ|・SWR・NF・種類の分からない前の版の Y は動かさない。REF が未設定（画面の既定 0）なら 0 からずらす
        for kind in ("gamma", "swr", Y_KIND_NF, None):
            t.xy.y1, t.xy.y1_kind = 0.5, kind
            shift_ratio_levels({"ref_level": None}, tables, 10.0)
            self.assertEqual(t.xy.y1, 0.5, kind)
        amp = {"ref_level": None}
        shift_ratio_levels(amp, [], -10.0)
        self.assertEqual(amp["ref_level"], -10.0)


class InstrumentValueTest(InstrumentCase):
    def take(self, kind="wide", **kw):
        self.clear_events()  # 取った後の1回の掃引（すぐ測って見せる）を、前の掃引の知らせと取り違えない
        r = self.call("reference_take", kind=kind, **kw)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        return n["detail"]["result"]

    def tg(self):
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")

    def trace_db(self, n=1):
        return np.asarray(self.call("get_trace", id=n)["values"], dtype=float)

    def ratio_state(self):
        """取った後の1回の掃引（すぐ測って見せる）が、いま選んでいる基準の比で終わるまで待つ。"""
        end = time.monotonic() + 30
        while True:
            st = self.call("get_state")
            ref = self.inst._ref
            if (st["run"]["state"] == "stopped" and st["normalization"].get("applied") and ref is not None
                    and self.inst._trace_refs.get(1) == ref.ref_id):
                return st
            self.assertLess(time.monotonic(), end, "基準の比の掃引が来ない")
            time.sleep(0.05)

    def test_take_with_value_and_edit_later(self):
        self.tg()
        res = self.take(name="ATT", value_db=40.0, show_zero=True)
        # 取ってすぐ基準の値が見える REF（上から1目盛下: 40 + 10）
        self.assertEqual(res["ref_level"], 50.0)
        st = self.ratio_state()
        self.assertEqual(st["normalization"]["selected"]["value_db"], 40.0)
        v = self.trace_db()
        self.assertLess(abs(float(np.nanmedian(v)) - 40.0), 1.0)
        refs = self.call("reference_list")["references"]
        self.assertEqual(refs[0]["value_db"], 40.0)
        ref_id = res["reference_id"]
        # 縦軸に結び付いた値: 比の dB で置いた Y・電力で置いた Y・ピーク探索のしきい値・比の dB のしきい値
        self.call("set_xy", y1=30.0, y2=-50.0)
        self.assertEqual(self.inst._markers.xy.y1_kind, "db")
        self.inst._markers.xy.y2_kind = Y_KIND_POWER  # 電力の縦軸で置いた Y（前の掃引の）
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150_000_000)
        self.call("marker_function", id=m["id"], threshold={"level": 37.0, "side": "below"})
        self.call("set_amplitude", ref_level=45.0)
        self.clear_events()
        # 直す（測り直さない）: トレースもしきい値も REF も同じだけ動き、絵は変わらない
        out = self.call("reference_set_value", id=ref_id, value_db=-10.0)
        self.assertEqual((out["value_db"], out["previous"]), (-10.0, 40.0))
        n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "reference_value_changed")
        self.assertEqual(n["detail"], {"name": "ATT", "value_db": -10.0, "previous": 40.0})
        st = self.call("get_state")
        np.testing.assert_allclose(self.trace_db(), v - 50.0, atol=1e-3)
        self.assertEqual(st["normalization"]["selected"]["value_db"], -10.0)
        self.assertEqual(st["amplitude"]["ref_level"], -5.0)
        self.assertEqual(st["markers"]["xy"]["y1"], -20.0)
        self.assertEqual(st["markers"]["xy"]["y2"], -50.0)
        row = next(r for r in st["markers"]["markers"] if r["id"] == m["id"])
        self.assertEqual(row["threshold"]["level"], -13.0)
        # ファイルにも残る（測り直さない）・次の掃引も新しい値
        self.assertEqual(self.call("reference_list")["references"][0]["value_db"], -10.0)
        self.single()
        self.assertLess(abs(float(np.nanmedian(self.trace_db())) + 10.0), 1.0)
        # 同じ値は何も動かさない
        self.clear_events()
        self.call("reference_set_value", id=ref_id, value_db=-10.0)
        self.assertEqual(self.call("get_state")["amplitude"]["ref_level"], -5.0)

    def test_errors_and_s11(self):
        self.tg()
        ref_id = self.take()["reference_id"]
        for bad, code in ((100.5, "reference_value_range"), (float("nan"), "bad_value"), ("40", "bad_value")):
            with self.assertRaises(CommandError) as cm:
                self.call("reference_set_value", id=ref_id, value_db=bad)
            self.assertEqual(cm.exception.code, code)
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="wide", value_db=-101.0)
        self.assertEqual(cm.exception.code, "reference_value_range")
        self.call("set_mode", mode="s11")
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="fine", standard="open", value_db=10.0)
        self.assertEqual(cm.exception.code, "reference_value_s11")
        r = self.call("reference_take", kind="fine", standard="open", value_db=0.0)  # 0 は受ける（足さない）
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short")
        self.wait_task(r["task_id"])
        s11 = next(x for x in self.call("reference_list")["references"] if x["mode"] == "s11")
        self.assertEqual(s11["value_db"], 0.0)
        with self.assertRaises(CommandError) as cm:
            self.call("reference_set_value", id=s11["id"], value_db=3.0)
        self.assertEqual(cm.exception.code, "reference_value_s11")

    def test_fixed_trace_from_another_reference_stays(self):
        self.tg()
        self.take(name="A")
        self.ratio_state()
        self.call("set_trace", number=2, visible=True)
        self.single()
        self.call("set_trace", number=2, action="fixed")
        fixed = self.trace_db(2)
        b = self.take(name="B", value_db=0.0)["reference_id"]
        self.ratio_state()
        live = self.trace_db(1)
        self.call("reference_set_value", id=b, value_db=6.0)
        np.testing.assert_allclose(self.trace_db(1), live + 6.0, atol=1e-3)
        np.testing.assert_allclose(self.trace_db(2), fixed, atol=1e-6)  # A の比のまま

    def test_fine_merge_takes_the_new_value(self):
        self.tg()
        ref_id = self.take(value_db=0.0)["reference_id"]
        self.ratio_state()
        self.call("set_amplitude", ref_level=10.0)
        res = self.take(kind="fine", value_db=20.0)
        self.assertEqual(res["reference_id"], ref_id)  # 同じ基準へ上書き合成
        st = self.call("get_state")
        self.assertEqual(st["normalization"]["selected"]["value_db"], 20.0)
        self.assertEqual(st["amplitude"]["ref_level"], 30.0)  # 直したのと同じだけずらす
        # 値を渡さない上書き合成は、その基準の値のまま
        self.take(kind="fine")
        self.assertEqual(self.call("get_state")["normalization"]["selected"]["value_db"], 20.0)

    def test_power_axis_is_not_shifted(self):
        """いまの縦軸が電力（SA に移った）なら REF は動かさない（単位の変更と同じ: 比の縦軸の間だけ）。"""
        self.tg()
        ref_id = self.take(value_db=0.0)["reference_id"]
        self.ratio_state()
        self.call("set_mode", mode="sa")
        self.single()
        self.call("set_amplitude", ref_level=-10.0)
        self.call("reference_set_value", id=ref_id, value_db=30.0)
        st = self.call("get_state")
        self.assertEqual(st["amplitude"]["ref_level"], -10.0)
        self.assertEqual(st["normalization"]["selected"]["value_db"], 30.0)

    def test_saved_file_and_csv_carry_the_value(self):
        self.tg()
        self.take(name="ATT", value_db=40.0)
        self.ratio_state()
        h5 = self.root / "a.h5"
        r = self.call("save", path=str(h5))
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        found = []
        with h5py.File(h5, "r") as f:
            def look(_name, obj):
                a = obj.attrs.get("normalization")
                if a is not None:
                    found.append(json.loads(a.decode() if isinstance(a, bytes) else a))
            f.visititems(look)
        self.assertTrue(found)
        self.assertEqual({n["value_db"] for n in found}, {40.0})
        csv_path = self.root / "a.csv"
        r = self.call("export_csv", path=str(csv_path))
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        first = Path(csv_path).read_text(encoding="utf-8").splitlines()[0]
        meta = json.loads(first.removeprefix("# meta: "))
        self.assertEqual(meta["normalization"]["value_db"], 40.0)
        # 値はトレースの値（比 + 基準の値）
        rows = Path(csv_path).read_text(encoding="utf-8").splitlines()[2:]
        vals = [float(x.split(",")[1]) for x in rows if x.split(",")[1]]
        self.assertLess(abs(float(np.median(vals)) - 40.0), 1.0)


if __name__ == "__main__":
    unittest.main()
