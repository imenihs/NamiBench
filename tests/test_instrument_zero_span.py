"""計測器の本体: ゼロスパンの時間軸と流す表示（設計N §1）。シミュレータで通す。

- トレースのイベントは時間軸（axis.kind "time"・位置はマイクロ秒）。1回の掃引は 0 から、流す表示は −長さ〜0 の区切り
- マーカー・XY カーソルは時間軸の表に置き、ゼロスパンを抜けたら周波数軸の表に戻る（前回終了時の状態にも両方が入る）
- 区間の最大・最小・平均（X1〜X2、無ければ表示している範囲）をマーカー表の状態に出す
- 周波数を前提にする命令（Center・Span へ・N dB 帯域幅・しきい値・周波数のオフセット・ピークの一覧）は断る
- CSV は時間（推定）付き。表示する長さは表示の設定（命令 set_view の zero_span_s）
"""
import csv
import json

import numpy as np

from namibench.core.instrument_common import CommandError
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000


class ZeroSpanTest(InstrumentCase):
    def enter(self, length=10, sweeps=3):
        self.connect()
        self.call("set_view", zero_span_s=length)
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=0)
        for _ in range(sweeps):
            self.single()

    def last_trace(self, n=1):
        return [p for k, p in self.events if k == "trace" and p.get("trace") == n][-1]

    def test_stream_payload_and_one_sweep(self):
        self.enter(length=10)
        p = self.call("get_trace", id=1)
        ax = p["axis"]
        self.assertEqual((ax["kind"], p["x_kind"]), ("time", "time"))
        self.assertAlmostEqual(ax["start_us"] + ax["step_us"] / 2, -10_000_000 + ax["step_us"])
        self.assertEqual(ax["points"], len(p["values"]))
        self.assertTrue(np.isfinite(np.asarray(p["values"])).any())
        self.assertEqual(p["display"], {"start_us": -10_000_000, "stop_us": 0, "tail_gap": False})
        st = self.call("get_state")
        self.assertAlmostEqual(st["sweep"]["point_time_s"], 0.00014)
        # 1回の掃引: 表示する最初の点が 0、1点 140μs
        self.call("set_view", zero_span_s=0)
        p = self.call("get_trace", id=1)
        self.assertEqual(p["axis"], {"kind": "time", "start_us": 0.0, "step_us": 140.0, "points": 500})
        self.assertEqual(len(p["values"]), 500)

    def test_bad_length(self):
        self.connect()
        with self.assertRaises(CommandError) as cm:
            self.call("set_view", zero_span_s=5)
        self.assertEqual(cm.exception.field, "zero_span_s")

    def test_markers_on_time_axis_and_back(self):
        self.connect()
        self.single()
        placed = self.call("marker_add", kind="M", trace=1, freq_hz=100 * MHZ)["freq_hz"]
        self.call("set_view", zero_span_s=10)
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=0)
        self.single()
        self.single()
        st = self.call("get_state")
        self.assertEqual(st["markers"]["x_kind"], "time")
        self.assertEqual(st["markers"]["markers"], [])  # 周波数のマーカーは時間軸に出さない
        r = self.call("marker_add", kind="M", trace=1, freq_hz=-50_000)
        self.assertEqual(r["id"], "M1")
        self.call("set_xy", x1_hz=-2_000_000, x2_hz=0)
        st = self.call("get_state")
        m = st["markers"]["markers"][0]
        self.assertEqual(m["status"], "ok")
        self.assertLessEqual(abs(m["freq_hz"] + 50_000), 140)
        iv = st["markers"]["interval"]
        self.assertEqual((iv["range"], iv["x0_us"], iv["x1_us"]), ("xy", -2_000_000, 0))
        row = iv["rows"][0]
        self.assertEqual(row["trace"], 1)
        self.assertGreaterEqual(row["max"], row["avg"])
        self.assertGreaterEqual(row["avg"], row["min"])
        self.assertGreater(row["points"], 0)
        # 区間の外の X を外すと表示している範囲
        self.call("set_xy", x2_hz=None)
        self.assertEqual(self.call("get_state")["markers"]["interval"]["range"], "window")
        # ピーク: 時間の位置の最大の点へ
        r = self.call("marker_peak", id="M1")
        self.assertEqual(r["code"], "ok")
        # 抜けたら周波数のマーカーに戻る
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=10 * MHZ)
        st = self.call("get_state")
        self.assertEqual(st["markers"]["x_kind"], "freq")
        self.assertEqual([x["id"] for x in st["markers"]["markers"]], ["M1"])
        self.assertNotIn("interval", st["markers"])
        # 前回終了時の状態に両方入る
        saved = self.inst._state_to_save()
        self.assertEqual(saved["markers"][0]["target_hz"], placed)
        self.assertEqual(len(saved["time_markers"]), 1)
        self.assertEqual(saved["time_xy"]["x1_hz"], -2_000_000)

    def test_frequency_operations_are_refused(self):
        self.enter(length=0, sweeps=1)
        self.call("marker_add", kind="M", trace=1, freq_hz=1000)
        for name, kw in (("marker_to", {"id": "M1", "target": "center"}),
                         ("marker_to", {"id": "M1", "target": "span"}),
                         ("marker_function", {"id": "M1", "bandwidth_db": [3.0]}),
                         ("marker_function", {"id": "M1", "threshold": {"level": -20, "side": "below"}}),
                         ("marker_offset_add", {"id": "M1", "type": "freq", "sign": "+", "delta": 1000}),
                         ("peak_list", {"trace": 1}),
                         ("marker_top_peaks", {"trace": 1, "n": 2})):
            with self.assertRaises(CommandError, msg=name) as cm:
                self.call(name, **kw)
            self.assertEqual(cm.exception.code, "zero_span_axis")
        # REF へ・レベルのオフセット・機能を外すのは使える
        self.call("marker_to", id="M1", target="ref")
        self.call("marker_offset_add", id="M1", type="level", sign="-", delta=3.0)
        self.call("marker_function", id="M1", bandwidth_db=[], threshold=None)

    def test_ref_auto_uses_window(self):
        self.enter(length=10)
        st = self.call("get_state")
        peak = st["ref_auto"]["peaks"]["1"]
        self.assertEqual(peak["unit"], "dBm")
        self.assertIsNotNone(peak["max"])

    def test_csv_with_time(self):
        self.enter(length=10)
        path = self.root / "zs.csv"
        r = self.call("export_csv", path=str(path))
        done = self.wait_task(r["task_id"])
        self.assertEqual(done["code"], "task_done")
        lines = path.read_text(encoding="ascii").splitlines()
        meta = json.loads(lines[0][len("# meta: "):])
        self.assertEqual(meta["kind"], "zero_span")
        self.assertEqual(meta["time_axis"]["zero"], "newest_point")
        self.assertAlmostEqual(meta["time_axis"]["point_time_s"], 0.00014)
        rows = list(csv.reader(lines[1:]))
        self.assertEqual(rows[0], ["time_s", "time_unix", "sweep", "trace1_dBm"])
        times = [float(x[0]) for x in rows[1:]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[-1], 0.0)
        self.assertGreaterEqual(len({x[2] for x in rows[1:]}), 2)  # 何回かの掃引の点
        # 参照トレース（ファイル）は時間軸に載らない
        with self.assertRaises(CommandError) as cm:
            self.call("export_csv", path=str(self.root / "x.csv"), traces=["F1.1"])
        self.assertEqual(cm.exception.code, "zero_span_csv_live_only")
        # 1回の掃引: 表示する最初の点から
        self.call("set_view", zero_span_s=0)
        r = self.call("export_csv", path=str(path))
        self.wait_task(r["task_id"])
        rows = list(csv.reader(path.read_text(encoding="ascii").splitlines()[1:]))
        self.assertEqual((rows[1][0], rows[2][0]), ("0.000000", "0.000140"))
        self.assertEqual(len(rows) - 1, 500)

    def test_restart_in_zero_span_keeps_time_axis(self):
        # 前回終了時がゼロスパンなら、起動した直後から時間軸のマーカー表（列の名前も時間）
        self.enter(length=60, sweeps=1)
        self.call("marker_add", kind="M", trace=1, freq_hz=-10_000)
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["step_hz"], 0)
        self.assertEqual(st["view"]["zero_span_s"], 60)
        self.assertEqual(st["markers"]["x_kind"], "time")
        self.assertEqual([m["id"] for m in st["markers"]["markers"]], ["M1"])

    def test_preset_keeps_zero_span(self):
        self.enter(length=1, sweeps=1)
        saved = self.call("preset_save", name="ゼロスパン")
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=10 * MHZ)
        self.call("set_view", zero_span_s=10)
        self.call("preset_load", id=saved["id"])
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["step_hz"], st["sweep"]["center_hz"]), (0, 100 * MHZ))
        self.assertEqual(st["view"]["zero_span_s"], 1)  # 表示する長さは表示の設定としてプリセットに入る

    def test_condition_change_clears_stream(self):
        self.enter(length=10)
        self.assertTrue(self.inst._zs.has(1))
        self.call("set_sweep", center_hz=200 * MHZ, span_hz=0)
        self.assertFalse(self.inst._zs.has(1))
