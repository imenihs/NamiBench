"""S11（反射。設計E §6.5・設計R §3・§5.1・設計L §1.1）を本体とシミュレータで通す試験。

- オープン → ショートの2段で基準を取り、被測定物（シミュレータは 145MHz で整合する共振）のリターンロスが出る
- ショートはオープンと同じ範囲（kind を省いたらオープンの kind）。組めなくなったオープンは捨てて知らせる
- シミュレータは基準を取る間、測る標準器を繋いだことにする（実機は利用者が繋ぎ替える）
- 縦軸の表示の形式（|Γ|・SWR。表示の設定 view.s11_format に1つ: 2026-10-09 ユーザー決定）: 読み値・しきい値・縦軸の
  1目盛・CSV・保存して開いた参照トレース・プリセット・元に戻す・前の版の状態とプリセットの読み替え
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
        """形式は縦軸に1つ（set_view の s11_format）。トレースの命令では選べない。位相系は位相を測れる機器だけ。"""
        self.connect(100 * MHZ, 200 * MHZ)
        with self.assertRaises(CommandError) as cm:
            self.call("set_trace", number=1, format="swr")
        self.assertEqual(cm.exception.code, "bad_arguments")
        for f in ("phase", "group_delay", "smith", "polar"):
            with self.assertRaises(CommandError) as cm:
                self.call("set_view", s11_format=f)
            self.assertEqual(cm.exception.code, "format_needs_phase")
        with self.assertRaises(CommandError) as cm:
            self.call("set_view", s11_format="nonsense")
        self.assertEqual(cm.exception.code, "bad_value")
        # S11 を測る前（TG モード）でも選んでおける。S11 の比でないトレースには当たらない
        self.call("set_mode", mode="tg")
        r = self.call("set_view", s11_format="swr")
        self.assertEqual(r["view"]["s11_format"], "swr")
        st = self.call("get_state")
        self.assertNotIn("format", st["traces"][0])
        self.call("set_view", s11_format=None)  # null は初期値（リターンロス dB）
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "db")

    def test_every_s11_trace_follows_the_axis_format(self):
        """S11 の比のトレースは全部が縦軸の形式で出る（測定中の 1・2 と固定した 3）。"""
        self.s11_reference()
        self.call("set_trace", number=2, action="max_hold")
        self.ratio_trace()
        self.call("set_trace", number=3, action="update")
        self.single()
        self.call("set_trace", number=3, action="fixed")
        self.call("set_view", s11_format="gamma")
        for n in (1, 2, 3):
            t = self.call("get_trace", id=n)
            self.assertEqual((t["unit"], t["format"]), ("|Γ|", "gamma"), n)
            self.assertLessEqual(float(np.nanmax(np.asarray(t["values"], dtype=float))), 1.5)
        units = [t["unit"] for t in self.call("get_state")["traces"][:3]]
        self.assertEqual(units, ["|Γ|"] * 3)
        # 元に戻す（Ctrl+Z）で形式も戻る
        self.call("undo")
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "db")
        self.assertEqual(self.call("get_trace", id=3)["unit"], "dB")

    def test_axis_follows_the_live_trace_not_a_lower_fixed_one(self):
        """縦軸（XY カーソルの Y の種類）は測定中のトレースで決まる。番号の小さい固定のトレース（電力）があっても、測定中の
        S11 のトレースの形式（SWR）になる（2026-10-09 ユーザー決定: 単位の違う固定・参照トレースのほうを描かない）。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        self.single()
        self.call("set_trace", number=1, action="fixed")
        self.call("set_trace", number=2, action="update", visible=True)
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.single()
        self.single()  # 基準を当てた掃引を待つ（tg_ratio と同じく2回）
        self.call("set_view", s11_format="swr")
        st = self.call("get_state")
        self.assertEqual([st["traces"][0]["unit"], st["traces"][1]["unit"]], ["dBm", "SWR"])
        self.call("set_xy", y1=2.0)
        self.assertEqual(self.inst._markers.xy.y1_kind, "swr")

    def test_tg_ratio_trace_is_not_converted(self):
        """S21（TG）で測って固定した比のトレースには |Γ|・SWR は当たらない（dB のまま）。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="fine")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.single()
        self.call("set_trace", number=1, action="fixed")
        self.call("set_view", s11_format="swr")
        t = self.call("get_trace", id=1)
        self.assertEqual((t["unit"], t["format"]), ("dB", "db"))

    def test_preset_keeps_the_axis_format(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_view", s11_format="swr")
        pid = self.call("preset_save", name="SWR")["id"]
        self.call("set_view", s11_format="db")
        self.call("preset_load", id=pid)
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "swr")
        self.call("undo")
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "db")

    def test_old_preset_with_trace_formats_is_read_as_the_axis_format(self):
        """前の版のプリセット（形式をトレースごとに持ち、view に s11_format が無い）は、縦軸を決めていたトレース（表示して
        いる一番番号の小さいトレース）の形式を縦軸の形式として読む。"""
        import json

        self.connect(100 * MHZ, 200 * MHZ)
        pid = self.call("preset_save", name="old")["id"]
        path = next((self.root / "data" / "presets").glob("*.json"))
        d = json.loads(path.read_text(encoding="utf-8"))
        d["content"]["view"].pop("s11_format")
        for t in d["content"]["traces"]:
            t["format"] = {1: "db", 2: "gamma", 3: "swr", 4: "db"}[t["number"]]
            t["visible"] = t["number"] != 1
        path.write_text(json.dumps(d), encoding="utf-8")
        self.call("preset_load", id=pid)
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "gamma")

    def test_old_state_with_trace_formats_is_read_as_the_axis_format(self):
        self.inst.shutdown()
        from pygigast.core.settings import Settings

        st = Settings(self.root / "data" / "settings.json")
        state = dict(st.get("state") or {})
        view = dict(state.get("view") or {})
        view.pop("s11_format", None)
        state["view"] = view
        state["traces"] = [{"number": n, "action": "update", "visible": True, "format": f}
                           for n, f in ((1, "swr"), (2, "gamma"), (3, "db"), (4, "db"))]
        st.set("state", state)
        st.save()
        self.inst = self.make_instrument()
        self.assertEqual(self.call("get_state")["view"]["s11_format"], "swr")

    def test_swr_readings_threshold_and_axis(self):
        self.s11_reference()
        self.ratio_trace()
        self.call("set_view", s11_format="swr")
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
        self.call("set_view", s11_format="gamma")
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

    def test_csv_and_saved_file_follow_the_axis_format(self):
        """CSV は画面と同じ形式で書く。保存したファイルを開いた参照トレースは、保存したときの形式（info.format: 前の版の
        トレースごとの形式を含む）でなく、いまの縦軸の形式で出る。"""
        self.s11_reference()
        self.ratio_trace()
        self.call("set_view", s11_format="swr")
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
        # 縦軸の形式を |Γ| にすると、開いたファイルのトレースも |Γ| で出る（保存したときの SWR は使わない）
        self.call("set_view", s11_format="gamma")
        ft = self.call("get_trace", id=tid)
        self.assertEqual((ft["unit"], ft["format"]), ("|Γ|", "gamma"))
        f = self.call("get_state")["files"][0]["traces"][0]
        self.assertEqual((f["unit"], f["info"]["format"]), ("|Γ|", "swr"))
        # 保存したときの形式が SWR のファイルを、リターンロス（dB）の縦軸で開き直しても dB で出る
        self.call("close_file", file_id=res["files"][0]["file_id"])
        self.call("set_view", s11_format="db")
        r = self.call("open", paths=[str(h5)])
        res = self.wait_task(r["task_id"])["detail"]["result"]
        self.assertEqual(res["files"][0]["traces"][0]["unit"], "dB")


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
        self.call("set_view", s11_format="swr")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], db_to_swr(-3.0), places=6)
        self.assertAlmostEqual(xy["y2"], db_to_swr(-10.0), places=6)
        self.assertAlmostEqual(xy["dy"], db_to_swr(-10.0) - db_to_swr(-3.0), places=6)
        self.call("set_view", s11_format="gamma")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], 10 ** (-3.0 / 20), places=6)
        self.call("set_view", s11_format="db")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], -3.0, places=6)
        self.assertAlmostEqual(xy["y2"], -10.0, places=6)
        # 元に戻す（Ctrl+Z）で形式が dB に戻っても、Y カーソルは同じ反射の大きさの dB に戻る
        self.call("set_view", s11_format="swr")
        self.call("undo")
        self.assertAlmostEqual(self.call("get_state")["markers"]["xy"]["y1"], -3.0, places=6)

    def test_show_zero_only_on_a_db_axis(self):
        """縦軸が SWR（基準を取る前に選んでおいた）なら、ショートで基準を取っても REF を動かさず知らせもしない。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="s11")
        self.call("set_view", s11_format="swr")
        self.call("set_amplitude", ref_level=-60.0)
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short", show_zero=True)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        self.assertNotIn("ref_level", n["detail"]["result"])
        self.assertEqual(self.call("get_state")["amplitude"]["ref_level"], -60.0)
        # dB の縦軸なら、0dB が見えるよう REF を動かす（J §4.7 の末尾のまま）
        self.call("set_view", s11_format="db")
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short", show_zero=True)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["detail"]["result"].get("ref_level"), 10.0)

    def test_power_unit_change_does_not_shift_swr_threshold(self):
        """SWR で決めたしきい値は、電力の単位・外部補正を替えてもずらさない。電力で決めたしきい値は今までどおりずらす。"""
        self.s11_reference()
        self.ratio_trace()
        self.call("set_view", s11_format="swr")
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

    def tg_ratio(self):
        """TG モードで基準を取り、縦軸を基準との比にする（掃引2回）。"""
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="wide")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.single()
        self.single()
        self.assertEqual(self.call("get_state")["display_quantity"], "ratio")

    def test_power_threshold_not_shifted_while_the_axis_is_a_ratio(self):
        """電力で決めたしきい値でも、縦軸が比の間は外部補正・電力の単位を替えてもずらさない（設計書 O §1.4）。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.single()
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": -50.0, "side": "above"})
        self.tg_ratio()

        def threshold():
            p = next(x for x in self.call("get_state")["markers"]["markers"] if x["id"] == m["id"])
            return p["unit"], p["threshold"]["level"]

        self.assertEqual(threshold(), ("dB", -50.0))
        self.call("set_view", ext_db=20.0)
        self.assertEqual(threshold(), ("dB", -50.0))
        self.call("set_view", level_unit="dBuV")
        self.assertEqual(threshold(), ("dB", -50.0))

    def test_power_threshold_not_converted_as_a_ratio_format(self):
        """電力で決めたしきい値は、SWR のトレースで読んでも比の形式の換算をしない（電力と比の間では直さない）。"""
        self.connect(100 * MHZ, 200 * MHZ)
        self.single()
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": -50.0, "side": "above"})
        self.call("set_mode", mode="s11")
        for standard in ("open", "short"):
            r = self.call("reference_take", kind="fine", standard=standard)
            self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.call("set_view", s11_format="swr")
        self.single()
        p = next(x for x in self.call("get_state")["markers"]["markers"] if x["id"] == m["id"])
        self.assertEqual((p["unit"], p["threshold"]["level"]), ("SWR", -50.0))

    def test_xy_y_set_on_a_power_axis_is_not_converted_as_a_ratio_format(self):
        """電力の縦軸で置いた Y は、比の縦軸（SWR を覚えていても）に戻っても形式の換算をしない。"""
        self.s11_reference()
        self.call("set_view", s11_format="swr")
        self.ratio_trace()
        self.call("set_xy", y1=2.0)
        self.call("set_mode", mode="sa")
        self.single()
        self.call("set_xy", y1=-40.0, y2=-60.0)
        self.tg_ratio()
        xy = self.call("get_state")["markers"]["xy"]
        self.assertEqual((xy["y1"], xy["y2"]), (-40.0, -60.0))

    def test_xy_y_rows_carry_the_unit_they_were_set_in(self):
        """表の Y の行は置いたときの単位で書く: 電力で置いた −50 を SWR の縦軸で「SWR」と書かない。"""
        from pygigast.ui_qt import marker_rows
        self.s11_reference()
        self.call("set_mode", mode="sa")
        self.single()
        self.call("set_xy", y1=-50.0)
        self.call("set_mode", mode="s11")
        self.call("set_view", s11_format="swr")
        self.ratio_trace()
        self.call("set_xy", y2=2.0)
        snap = self.call("get_state")["markers"]
        rows = {r["id"]: r for r in marker_rows.rows_from_snapshot(snap)}
        self.assertEqual(rows["Y1"]["unit"], "dBm")
        self.assertEqual(rows["Y2"]["unit"], "SWR")
        cells = {r["id"]: c for r, c in zip(marker_rows.rows_from_snapshot(snap),
                                             marker_rows.format_rows(marker_rows.rows_from_snapshot(snap), "SWR"))}
        self.assertIn("dBm", cells["Y1"][3])

    def test_xy_y_set_on_a_swr_axis_is_not_shifted_by_power_units(self):
        """SWR の縦軸で置いた Y は、電力の縦軸で外部補正を替えてもずらさず、SWR の縦軸に戻ればそのまま SWR 2。
        電力の縦軸で置いた Y は今までどおりずらす。"""
        self.s11_reference()
        self.call("set_view", s11_format="swr")
        self.ratio_trace()
        self.call("set_xy", y1=2.0)
        self.call("set_mode", mode="sa")
        self.single()
        self.call("set_xy", y2=-60.0)
        self.call("set_view", ext_db=10.0)
        xy = self.call("get_state")["markers"]["xy"]
        self.assertEqual((xy["y1"], xy["y2"]), (2.0, -50.0))
        self.call("set_mode", mode="s11")
        self.single()
        self.assertEqual(self.call("get_state")["markers"]["xy"]["y1"], 2.0)
        # 縦軸の形式を |Γ| に替えると、SWR で置いた Y だけを同じ反射の大きさに直す（電力で置いた Y は直さない）
        self.call("set_view", s11_format="gamma")
        xy = self.call("get_state")["markers"]["xy"]
        self.assertAlmostEqual(xy["y1"], 1.0 / 3.0, places=6)
        self.assertEqual(xy["y2"], -50.0)


if __name__ == "__main__":
    unittest.main()
