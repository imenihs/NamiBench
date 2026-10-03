"""S11（反射。設計E §6.5・設計R §3・§5.1・設計L §1.1）を本体とシミュレータで通す試験。

- オープン → ショートの2段で基準を取り、被測定物（シミュレータは 145MHz で整合する共振）のリターンロスが出る
- ショートはオープンと同じ範囲（kind を省いたらオープンの kind）。組めなくなったオープンは捨てて知らせる
- シミュレータは基準を取る間、測る標準器を繋いだことにする（実機は利用者が繋ぎ替える）
- トレースごとの表示の形式（|Γ|・SWR）: 読み値・しきい値・縦軸の1目盛・CSV・保存して開いた参照トレース
"""
from __future__ import annotations

import csv
import time
import unittest
from pathlib import Path

import numpy as np

from pygigast.core.instrument_common import CommandError
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000


class S11Case(InstrumentCase):
    def s11_reference(self, kind="fine", start=100 * MHZ, stop=200 * MHZ):
        self.connect(start, stop)
        self.call("set_mode", mode="s11")
        r = self.call("reference_take", kind=kind, standard="open")
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["detail"]["result"]["next"], "short", n)
        r = self.call("reference_take", standard="short")
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        return n

    def ratio_trace(self):
        self.clear_events()
        self.single()
        return self.call("get_trace", id=1)

    @staticmethod
    def freqs(t):
        ax = t["axis"]
        return ax["start_hz"] + ax["step_hz"] * np.arange(ax["points"])


class ReferenceFlowTest(S11Case):
    def test_open_short_then_return_loss_dip(self):
        self.s11_reference()
        st = self.call("get_state")
        self.assertEqual(st["normalization"]["selected"]["mode"], "s11")
        self.assertIsNone(st["normalization"]["s11_open"])
        t = self.ratio_trace()
        self.assertEqual(t["quantity"], "ratio")
        self.assertEqual(t["unit"], "dB")
        v = np.asarray(t["values"], dtype=float)
        f = self.freqs(t)
        i = int(np.nanargmin(v))
        self.assertLess(abs(int(f[i]) - 145 * MHZ), 3 * MHZ)
        self.assertLess(v[i], -20.0)
        # 共振から離れた所は全反射に近い（基準 = オープンとショートの振幅の平均: 0dB 付近）
        self.assertLess(abs(float(np.nanmedian(v[f < 115 * MHZ]))), 1.5)

    def test_open_state_and_short_follows_open_kind(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        r = self.call("reference_take", kind="wide", standard="open")
        self.wait_task(r["task_id"])
        st = self.call("get_state")
        o = st["normalization"]["s11_open"]
        self.assertEqual(o["kind"], "wide")
        self.assertIsNotNone(o["taken_at"])
        # 違う kind のショートは組めない（オープンと同じ範囲で測る）
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="fine", standard="short")
        self.assertEqual(cm.exception.code, "s11_open_missing")
        r = self.call("reference_take", standard="short")
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        self.assertEqual(n["detail"]["kind"], "wide")

    def test_standard_is_required_in_s11(self):
        self.connect()
        self.call("set_mode", mode="s11")
        with self.assertRaises(CommandError) as cm:
            self.call("reference_take", kind="wide")
        self.assertEqual(cm.exception.code, "s11_standard_required")

    def test_simulator_attaches_the_standard_while_taking(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        synth = self.inst._device.port.synth
        seen = []
        orig = synth.reflection

        def spy(f):
            seen.append(synth.standard)
            return orig(f)

        synth.reflection = spy
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        self.assertIn("open", seen)
        self.assertIsNone(synth.standard)  # 取り終えたら被測定物に戻る

    def test_open_discarded_when_it_cannot_pair(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        self.clear_events()
        # 「いまの条件で細かく」のオープンは、周波数軸が変わるとショートと組めない
        self.call("set_sweep", start_hz=120 * MHZ, stop_hz=180 * MHZ)
        n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "s11_open_discarded")
        self.assertEqual(n["detail"]["reason"], "axis")
        self.assertIsNone(self.call("get_state")["normalization"]["s11_open"])

    def test_wide_open_survives_axis_change_but_not_mode_change(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        r = self.call("reference_take", kind="wide", standard="open")
        self.wait_task(r["task_id"])
        self.call("set_sweep", start_hz=120 * MHZ, stop_hz=180 * MHZ)
        self.assertIsNotNone(self.call("get_state")["normalization"]["s11_open"])
        self.clear_events()
        self.call("set_mode", mode="tg")
        n = self.wait_for(lambda k, p: k == "notice" and p["code"] == "s11_open_discarded")
        self.assertEqual(n["detail"]["reason"], "mode")


class FormatTest(S11Case):
    def test_format_rules(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=1, format="swr")
        self.assertEqual(cm.exception.code, "format_s11_only")
        for f in ("phase", "group_delay", "smith", "polar"):
            with self.assertRaises(CommandError) as cm:
                self.call("set_trace", number=1, format=f)
            self.assertEqual(cm.exception.code, "format_needs_phase")
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=1, format="nonsense")
        self.assertEqual(cm.exception.code, "bad_trace_setting")
        self.call("set_mode", mode="s11")
        r = self.call("set_trace", number=1, format="swr")
        self.assertEqual(r["trace"]["format"], "swr")

    def test_swr_readings_threshold_and_axis(self):
        self.s11_reference()
        self.ratio_trace()
        self.call("set_trace", number=1, format="swr")
        t = self.call("get_trace", id=1)
        self.assertEqual((t["unit"], t["format"]), ("SWR", "swr"))
        v = np.asarray(t["values"], dtype=float)
        self.assertGreaterEqual(float(np.nanmin(v)), 1.0)
        st = self.call("get_state")
        self.assertEqual(st["traces"][0]["unit"], "SWR")
        self.assertEqual(st["traces"][0]["format_effective"], "swr")
        self.assertEqual(st["traces"][0]["measurement"], "s11")
        r = self.call("marker_peak", polarity="dip", trace=1)
        self.call("marker_function", id=r["id"], threshold={"level": 2.0, "side": "below"})
        m = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(m["unit"], "SWR")
        self.assertLess(m["level"], 1.2)
        th = m["threshold"]
        self.assertEqual(th["status"], "ok")
        self.assertLess(th["left_hz"], 145 * MHZ)
        self.assertGreater(th["right_hz"], 145 * MHZ)
        # 形式を |Γ| に替えても、しきい値は同じ反射の大きさ（0.33）で同じ範囲
        self.call("set_trace", number=1, format="gamma")
        m2 = self.call("get_state")["markers"]["markers"][0]
        self.assertEqual(m2["unit"], "|Γ|")
        self.assertEqual(m2["threshold"]["level"], 0.33)
        self.assertEqual(m2["threshold"]["left_hz"], th["left_hz"])
        # REF は |Γ|・SWR の縦軸に無い
        with self.assertRaises(CommandError) as cm:
            self.call("marker_to", id=r["id"], target="ref")
        self.assertEqual(cm.exception.code, "ref_level_linear")
        # ピークの一覧のレベルも形式の値（差の列は dB のまま）
        pl = self.call("peak_list", trace=1, floor_margin_db=10.0, polarity="dip")
        self.assertEqual(pl["unit"], "|Γ|")
        self.assertTrue(pl["peaks"])
        self.assertLess(pl["peaks"][0]["level"], 0.2)

    def test_amplitude_per_div(self):
        self.connect()
        r = self.call("set_amplitude", swr_per_div=0.5)
        self.assertEqual(r["amplitude"]["swr_per_div"], 0.5)
        r = self.call("set_amplitude", gamma_per_div=0.05)
        self.assertEqual(r["amplitude"]["gamma_per_div"], 0.05)
        with self.assertRaises(CommandError):
            self.call("set_amplitude", swr_per_div=0.3)
        with self.assertRaises(CommandError):
            self.call("set_amplitude", gamma_per_div=True)

    def test_csv_and_saved_file_keep_the_format(self):
        self.s11_reference()
        self.ratio_trace()
        self.call("set_trace", number=1, format="swr")
        path = self.root / "s11.csv"
        r = self.call("export_csv", path=str(path), traces=[1])
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        rows = list(csv.reader(l for l in path.read_text(encoding="ascii").splitlines() if not l.startswith("#")))
        self.assertEqual(rows[0], ["frequency_hz", "trace1_SWR"])
        vals = [float(x[1]) for x in rows[1:] if x[1]]
        self.assertGreaterEqual(min(vals), 1.0)
        self.assertLessEqual(max(vals), 100.0)
        h5 = self.root / "s11.h5"
        r = self.call("save", path=str(h5))
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        r = self.call("open", paths=[str(h5)])
        res = self.wait_task(r["task_id"])["detail"]["result"]
        tid = res["files"][0]["traces"][0]["id"]
        self.assertEqual(res["files"][0]["traces"][0]["unit"], "SWR")
        ft = self.call("get_trace", id=tid)
        self.assertEqual((ft["unit"], ft["format"]), ("SWR", "swr"))


def db_to_swr(db: float) -> float:
    g = 10 ** (db / 20)
    return (1 + g) / (1 - g)


class UnitFollowTest(S11Case):
    """表示の単位で持つ値は、その値の単位だけに従って直す（設計書 O §1.4・設計R §3）。dB の縦軸を前提にした操作
    （REF を動かす）は |Γ|・SWR の縦軸では行わない（設計J §4.7 の末尾）。"""

    def test_xy_y_follows_the_axis_format(self):
        """縦軸の形式が dB → SWR → |Γ| → dB と替わっても、Y カーソルは同じ反射の大きさのまま（数字だけ単位が替わる）。"""
        self.s11_reference()
        self.ratio_trace()
        self.call("set_xy", y1=-3.0, y2=-10.0)
        self.call("set_trace", number=1, format="swr")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], db_to_swr(-3.0), places=6)
        self.assertAlmostEqual(xy["y2"], db_to_swr(-10.0), places=6)
        self.assertAlmostEqual(xy["dy"], db_to_swr(-10.0) - db_to_swr(-3.0), places=6)
        self.call("set_trace", number=1, format="gamma")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], 10 ** (-3.0 / 20), places=6)
        self.call("set_trace", number=1, format="db")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], -3.0, places=6)
        self.assertAlmostEqual(xy["y2"], -10.0, places=6)
        # 縦軸が SWR のトレースのままなら、ほかのトレースの形式を替えても Y カーソルは動かない
        self.call("set_trace", number=1, format="swr")
        y1 = self.call("get_state")["markers"]["xy"]["y1"]
        self.call("set_trace", number=2, format="gamma")
        self.assertAlmostEqual(self.call("get_state")["markers"]["xy"]["y1"], y1, places=9)
        # 元に戻す（Ctrl+Z）で形式が dB に戻っても、Y カーソルは同じ反射の大きさの dB に戻る
        self.call("undo")
        self.call("undo")
        self.assertAlmostEqual(self.call("get_state")["markers"]["xy"]["y1"], -3.0, places=6)

    def test_show_zero_only_on_a_db_axis(self):
        """縦軸が SWR（基準を取る前に選んでおいた）なら、ショートで基準を取っても REF を動かさず知らせもしない。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        self.call("set_trace", number=1, format="swr")
        self.call("set_amplitude", ref_level=-60.0)
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short", show_zero=True)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        self.assertNotIn("ref_level", n["detail"]["result"])
        self.assertEqual(self.call("get_state")["amplitude"]["ref_level"], -60.0)
        # dB の縦軸なら、0dB が見えるよう REF を動かす（J §4.7 の末尾のまま）
        self.call("set_trace", number=1, format="db")
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short", show_zero=True)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["detail"]["result"].get("ref_level"), 10.0)

    def test_power_unit_change_does_not_shift_swr_threshold(self):
        """SWR で決めたしきい値は、電力の単位・外部補正を替えてもずらさない。電力で決めたしきい値は今までどおりずらす。"""
        self.s11_reference()
        self.ratio_trace()
        self.call("set_trace", number=1, format="swr")
        r = self.call("marker_peak", polarity="dip", trace=1)
        self.call("marker_function", id=r["id"], threshold={"level": 2.0, "side": "below"})
        before = self.call("get_state")["markers"]["markers"][0]["threshold"]
        self.call("set_mode", mode="sa")
        self.single()
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": -50.0, "side": "above"})
        self.call("set_view", level_unit="dBuV")
        self.call("set_view", ext_db=5.0)
        p = next(x for x in self.call("get_state")["markers"]["markers"] if x["id"] == m["id"])
        self.assertAlmostEqual(p["threshold"]["level"], -50.0 + 106.99 + 5.0, places=2)
        self.call("set_mode", mode="s11")
        self.single()
        after = next(x for x in self.call("get_state")["markers"]["markers"] if x["id"] == r["id"])["threshold"]
        self.assertEqual(after["level"], 2.0)
        # 同じしきい値の交点（掃引ごとの雑音の分だけ揺れる）。ずらされた 113.99 SWR なら表示範囲の端まで交点が無い
        self.assertEqual(after["status"], "ok")
        self.assertAlmostEqual(after["left_hz"], before["left_hz"], delta=2 * MHZ)
        self.assertAlmostEqual(after["right_hz"], before["right_hz"], delta=2 * MHZ)


if __name__ == "__main__":
    unittest.main()
