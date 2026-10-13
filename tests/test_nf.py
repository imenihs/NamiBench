"""NF 測定（設計M）のコア: Y 係数法の式・増幅度の補正・ENR の表・トレースの平均（HOT と COLD をパワー平均してから NF）・
計測器の NF モード（ENR・案内の知らせ・NF の縦軸・保存・書き出し）。"""
from __future__ import annotations

import math
import unittest

import numpy as np

from namibench.core.level import nf as nfc
from namibench.core.storage.h5file import H5Record
from namibench.core.trace import ConditionChange, TraceAction
from namibench.core.trace.ops import to_db
from namibench.core.trace.trace import Trace
from namibench.core.types import FreqAxis, Frame, Quantity, ValueKind
from namibench.ui_qt import nf_view, screen_model
from namibench.ui_qt import texts
from tests.instrument_helpers import InstrumentCase
from tests.test_level import build_legacy_bytes

MHZ = 1_000_000


def w_of_db(db):
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 10.0)


class FormulaTest(unittest.TestCase):
    def test_y_factor_example_of_design(self):
        # 設計M §1 の例: ENR 14.2dB・Y 10dB → Y 係数法 4.66dB（旧アプリの差の式なら 4.2dB）
        nf = nfc.nf_from_y(np.array([10.0]), 14.2)
        self.assertAlmostEqual(float(nf[0]), 14.2 - 10 * math.log10(9.0), places=9)
        self.assertAlmostEqual(float(nf[0]), 4.66, places=2)

    def test_amp_factor_multiplies_the_db_difference(self):
        # 旧 NF_Amp の値 −4 → 倍率 10^(−4/200)。HOT−COLD の dB の差に掛ける（設計D §5.2）
        y = nfc.y_db(w_of_db([13.0]), w_of_db([3.0]), amp_raw=-4)
        self.assertAlmostEqual(float(y[0]), 10.0 * 10 ** (-4 / 200), places=9)
        self.assertAlmostEqual(nfc.amp_factor(None), 1.0)

    def test_y_not_above_one_has_no_value_and_small_is_marked(self):
        parts = nfc.NfParts(hot_w=w_of_db([3.0, 3.4, 3.0, 2.0]), cold_w=w_of_db([3.0, 3.0, 2.0, 3.0]),
                            enr_db=np.full(4, 15.0))
        values, flags = parts.evaluate(parts.hot_w, parts.cold_w)
        db = to_db(values, Quantity.NF)
        self.assertTrue(np.isnan(db[0]))  # Y = 1
        self.assertTrue(np.isfinite(db[1]))  # 差 0.4dB: 値は出す
        self.assertTrue(np.isfinite(db[2]))
        self.assertTrue(np.isnan(db[3]))  # 差が負
        self.assertEqual(flags[nfc.FLAG_SMALL_DIFFERENCE].tolist(), [True, True, False, True])
        self.assertNotIn(nfc.FLAG_ENR_OUTSIDE, flags)

    def test_values_read_as_nf_db(self):
        # トレースの値は 20·log10 で NF の dB に戻る形（比と同じ読み方: マーカー・CSV がそのまま読める）
        nf = np.array([0.5, 4.66, 14.0])
        np.testing.assert_allclose(to_db(nfc.to_values(nf), Quantity.NF), nf)
        np.testing.assert_allclose(nfc.values_to_db(nfc.to_values(nf)), nf)

    def test_no_enr_gives_no_values(self):
        parts = nfc.NfParts(hot_w=w_of_db([13.0]), cold_w=w_of_db([3.0]), enr_db=None)
        values, flags = parts.evaluate(parts.hot_w, parts.cold_w)
        self.assertTrue(np.isnan(values[0]))
        self.assertFalse(flags[nfc.FLAG_SMALL_DIFFERENCE][0])


class EnrTableTest(unittest.TestCase):
    def test_header_unit_and_sorting(self):
        t = nfc.parse_enr_csv("# 346B\nFrequency (GHz),ENR (dB),Unc\n2.0,14.7,0.1\n0.01,15.2,0.1\n1,14.9,0.1\n",
                              "a.csv")
        self.assertEqual(t.freqs_hz, (10 * MHZ, 1000 * MHZ, 2000 * MHZ))
        self.assertEqual(t.enr_db, (15.2, 14.9, 14.7))
        self.assertEqual(t.name, "a.csv")

    def test_plain_numbers_are_mhz_and_units_and_separators(self):
        t = nfc.parse_enr_csv("100\t15.0\n1.5G;14.0\n2000MHz 13.5\n")
        self.assertEqual(t.freqs_hz, (100 * MHZ, 1500 * MHZ, 2000 * MHZ))
        self.assertEqual(t.enr_db, (15.0, 14.0, 13.5))

    def test_errors_say_line_and_reason(self):
        cases = {
            "f,enr\n100,15\n100,14\n": (3, "duplicate"),
            "100,15\nabc,14\n": (2, "frequency"),
            "100,15\n200,x\n": (2, "enr"),
            "100,15\n200,99\n": (2, "enr_range"),
            "100\n": (1, "columns"),
            "f,enr\n# none\n": (0, "empty"),
        }
        for text, (line, reason) in cases.items():
            with self.subTest(text=text):
                with self.assertRaises(nfc.EnrTableError) as cm:
                    nfc.parse_enr_csv(text)
                self.assertEqual((cm.exception.line, cm.exception.reason), (line, reason))

    def test_linear_between_rows_and_edges_outside(self):
        t = nfc.EnrTable((1000 * MHZ, 2000 * MHZ), (15.0, 14.0))
        enr, outside = t.at(np.array([500, 1000, 1250, 2000, 3000], dtype=np.int64) * MHZ)
        np.testing.assert_allclose(enr, [15.0, 15.0, 14.75, 14.0, 14.0])
        self.assertEqual(outside.tolist(), [True, False, False, False, True])
        one = nfc.EnrTable((1000 * MHZ,), (15.5,))
        np.testing.assert_allclose(one.at(np.array([1, 5000 * MHZ], dtype=np.int64))[0], [15.5, 15.5])

    def test_round_trip(self):
        t = nfc.EnrTable((10 * MHZ, 20 * MHZ), (15.0, 14.5), "x.csv")
        self.assertEqual(nfc.EnrTable.from_dict(t.to_dict()), t)


def nf_frame(hot_db, cold_db, enr=15.0, n=4):
    axis = FreqAxis(100 * MHZ, MHZ, n)
    parts = nfc.NfParts(hot_w=w_of_db(np.full(n, hot_db)), cold_w=w_of_db(np.full(n, cold_db)),
                        enr_db=np.full(n, enr))
    values, flags = parts.evaluate(parts.hot_w, parts.cold_w)
    return Frame(axis=axis, values=values, quantity=Quantity.NF, value_kind=ValueKind.REAL, time=0.0, duration_s=0.1,
                 sweep_no=1, display_start_hz=axis.start_hz, display_stop_hz=axis.last_hz, flags=flags, nf=parts)


class TraceAverageTest(unittest.TestCase):
    def test_hot_and_cold_are_power_averaged_before_nf(self):
        t = Trace(1, TraceAction.AVERAGE, count=2, average_type="log")
        t.apply(nf_frame(13.0, 3.0))
        t.apply(nf_frame(3.4, 3.0))
        hot = (w_of_db(13.0) + w_of_db(3.4)) / 2
        cold = w_of_db(3.0)
        want = nfc.nf_from_y(nfc.y_db(np.array([hot]), np.array([cold])), 15.0)[0]
        got = to_db(t.values, Quantity.NF)
        np.testing.assert_allclose(got, np.full(4, want))
        # NF の値の平均（禁じた形）とは違う
        each = [nfc.nf_from_y(np.array([10.0]), 15.0)[0], nfc.nf_from_y(np.array([0.4]), 15.0)[0]]
        self.assertGreater(abs(want - float(np.mean(each))), 1.0)
        # 選んだ対数平均ではなくパワー平均（NF は選択によらない）
        self.assertEqual(t.effective_average_type().value, "power")

    def test_small_difference_mark_from_the_average(self):
        # 1回目は差 0.4dB（印）でも、平均した差が 0.5dB 以上なら印を付けない
        t = Trace(1, TraceAction.AVERAGE, count=2)
        t.apply(nf_frame(3.4, 3.0))
        self.assertTrue(t.flags[nfc.FLAG_SMALL_DIFFERENCE].all())
        t.apply(nf_frame(13.0, 3.0))
        self.assertFalse(t.flags[nfc.FLAG_SMALL_DIFFERENCE].any())

    def test_update_shows_each_pair(self):
        t = Trace(1, TraceAction.UPDATE)
        t.apply(nf_frame(13.0, 3.0))
        np.testing.assert_allclose(to_db(t.values, Quantity.NF), nfc.nf_from_y(np.full(4, 10.0), 15.0))

    def test_enr_change_resets_the_average(self):
        t = Trace(1, TraceAction.AVERAGE, count=4)
        t.apply(nf_frame(13.0, 3.0))
        self.assertIsNotNone(t.on_condition_change(ConditionChange.ENR))
        self.assertEqual(t.current_count, 0)


class InstrumentNfTest(InstrumentCase):
    def nf_mode(self, start=500 * MHZ, stop=600 * MHZ):
        self.connect(start, stop)
        self.inst._device.config.nf_hot_db = 11.3
        self.clear_events()
        self.call("set_mode", mode="nf")

    def notices(self, code):
        return [p for k, p in self.events if k == "notice" and p["code"] == code]

    def trace_db(self, n=1):
        p = self.call("get_trace", id=n)
        return np.asarray(p["values"], dtype=np.float64), p

    def test_mode_enabled_and_guide_once(self):
        st = self.call("get_state")
        self.assertIsNone(screen_model.mode_reasons(st)["nf"])
        self.nf_mode()
        self.assertEqual(len(self.notices("nf_guide")), 1)
        self.call("set_mode", mode="sa")
        self.clear_events()
        self.call("set_mode", mode="nf")
        self.assertEqual(self.notices("nf_guide"), [])
        self.assertTrue(self.call("get_state")["nf"]["guide_shown"])

    def test_no_enr_then_value(self):
        self.nf_mode()
        self.single()
        v, p = self.trace_db()
        self.assertEqual((p["quantity"], p["unit"]), ("nf", "dB"))
        self.assertTrue(np.isnan(v).all())
        self.assertEqual(nf_view.nf_view(self.call("get_state"))["enr_warn"], texts.NF_TEXTS["enr_needed"])
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        v, _p = self.trace_db()
        self.assertTrue(np.isfinite(v).all())
        # 差 11.3dB・ENR 15dB の NF（シミュレータの床の揺れの分だけばらつく）
        self.assertAlmostEqual(float(np.mean(v)), 15.0 - 10 * math.log10(10 ** 1.13 - 1), delta=1.0)

    def test_enr_errors(self):
        self.nf_mode()
        with self.assertRaises(Exception) as cm:
            self.call("nf_set_enr", enr_db=80.0)
        self.assertEqual((cm.exception.code, cm.exception.detail["max"]), ("bad_value", nfc.ENR_RANGE_DB[1]))
        with self.assertRaises(Exception) as cm:
            self.call("nf_load_enr_table", text="100,15\n100,14\n", file_name="b.csv")
        self.assertEqual(cm.exception.code, "enr_table_unreadable")
        self.assertEqual(cm.exception.detail, {"line": 2, "reason": "duplicate"})
        self.assertIn("2 行目", texts.error_text(cm.exception))
        self.call("nf_load_enr_table", text="500,15\n550,14\n", file_name="t.csv")
        with self.assertRaises(Exception) as cm:
            self.call("nf_set_enr", enr_db=14.0)
        self.assertEqual(cm.exception.code, "enr_table_in_use")
        self.call("nf_clear_enr_table")
        with self.assertRaises(Exception) as cm:
            self.call("nf_clear_enr_table")
        self.assertEqual(cm.exception.code, "enr_no_table")

    def test_table_interpolates_and_marks_outside(self):
        self.nf_mode(500 * MHZ, 600 * MHZ)
        self.call("nf_load_enr_table", text="Frequency (MHz),ENR\n500,15\n550,14\n", file_name="t.csv")
        st = self.call("get_state")
        self.assertEqual(st["nf"]["enr"]["kind"], "table")
        self.assertIs(st["nf"]["table_covers"], False)
        v = nf_view.nf_view(st)
        self.assertEqual(v["table_warn"], texts.NF_TEXTS["table_outside"])
        self.assertEqual(v["enr_reason"], texts.NF_TEXTS["enr_table_in_use"])
        self.assertIn("t.csv", v["table_text"])
        self.single()
        _v, p = self.trace_db()
        out = np.asarray(p["flags"]["points"][nfc.FLAG_ENR_OUTSIDE], dtype=bool)
        f = 500 * MHZ + np.arange(out.size) * p["axis"]["step_hz"]
        self.assertEqual(out.tolist(), (f > 550 * MHZ).tolist())

    def test_enr_change_resets_only_in_nf(self):
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("set_trace", number=1, action="average", count=4)
        self.single()
        self.clear_events()
        self.call("nf_set_enr", enr_db=14.0)  # SA モード: 捨てない
        self.assertEqual(self.notices("trace_reset"), [])
        self.call("set_mode", mode="nf")
        self.single()
        self.clear_events()
        self.call("nf_set_enr", enr_db=15.0)
        r = self.notices("trace_reset")
        self.assertEqual((len(r), r[0]["detail"]["cause"]), (1, "enr"))
        self.assertEqual(texts.trace_reset_text(r[0]["detail"]), texts.tpl("trace_reset_cause", cause="ENR"))

    def test_average_counts_pairs_and_is_power(self):
        self.nf_mode()
        self.call("nf_set_enr", enr_db=15.0)
        self.call("set_trace", number=1, action="average", count=4)
        self.call("run", mode="single", count=4)  # 4組（単発は1画面＝1組ずつなので回数を直接決める: 設計F §5.2.1）
        self.wait_for(lambda k, p: k == "state" and p["run"]["last_stop_reason"] == "single_done")
        t1 = next(t for t in self.call("get_state")["traces"] if t["number"] == 1)
        self.assertEqual((t1["current_count"], t1["average_type_effective"], t1["unit"]), (4, "power", "dB"))
        self.assertEqual(nf_view.average_type_reason(self.call("get_state")), texts.NF_TEXTS["average_type_nf"])

    def test_calibration_enr_and_amp(self):
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes(enr="142", nf_amp="-4"))
        self.connect(500 * MHZ, 600 * MHZ)
        self.call("import_legacy_calibration", path=str(p))
        st = self.call("get_state")
        self.assertEqual(st["nf"]["enr"], {"kind": "calibration", "db": 14.2})
        self.assertEqual(st["nf"]["amp_raw"], -4.0)
        v = nf_view.nf_view(st)
        self.assertEqual((v["enr_text"], v["enr_tip"], v["enr_warn"]), ("14.20 dB", texts.NF_TEXTS["enr_from_calibration"], ""))
        self.assertEqual(v["amp_text"], "×0.955")
        self.call("nf_set_enr", enr_db=13.5)
        self.assertEqual(self.call("get_state")["nf"]["enr"], {"kind": "value", "db": 13.5})

    def test_nf_vertical_axis_is_shared(self):
        """縦軸は全部のモードで1つ（2026-10-09 ユーザー決定）: NF に入っても REF・dB/div を変えず、NF で変えた縦軸は
        ほかのモードでもそのまま。次の起動も同じ1つの縦軸。前の版の NF の縦軸（amplitude_nf）は読まない。"""
        self.connect()
        self.call("set_amplitude", ref_level=-30.0, db_per_div=10)
        self.call("set_mode", mode="nf")
        amp = self.call("get_state")["amplitude"]
        self.assertEqual((amp["ref_level"], amp["db_per_div"]), (-30.0, 10))
        self.call("set_amplitude", ref_level=16.0, db_per_div=1)
        self.call("set_mode", mode="sa")
        amp = self.call("get_state")["amplitude"]
        self.assertEqual((amp["ref_level"], amp["db_per_div"]), (16.0, 1))
        self.inst.shutdown()
        self.inst = self.make_instrument()
        amp = self.call("get_state")["amplitude"]
        self.assertEqual((amp["ref_level"], amp["db_per_div"]), (16.0, 1))
        self.assertNotIn("amplitude_nf", self.inst._settings.get("state") or {})

    def test_old_state_nf_axis_is_not_used(self):
        """前の版の状態に NF の縦軸（amplitude_nf）があっても、NF に入ったとき縦軸を変えない。"""
        self.inst.shutdown()
        from namibench.core.settings import Settings

        st = Settings(self.root / "data" / "settings.json")
        state = dict(st.get("state") or {})
        state.update({"amplitude": {"ref_level": -40.0, "db_per_div": 10},
                      "amplitude_nf": {"ref_level": 20.0, "db_per_div": 2}})
        st.set("state", state)
        st.save()
        self.inst = self.make_instrument()
        self.connect()
        self.call("set_mode", mode="nf")
        amp = self.call("get_state")["amplitude"]
        self.assertEqual((amp["ref_level"], amp["db_per_div"]), (-40.0, 10))

    def test_peak_list_counts_from_the_maximum(self):
        """NF の画面のピークの一覧（設計P §9.7）: NF のトレースにもノイズフロアは無いので、基準は表示範囲の最大。
        −ピーク（NF の小さい所）も並べられる（電力のスペクトルのように断らない）。"""
        self.nf_mode()
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        v, p = self.trace_db()
        self.assertEqual(p["quantity"], "nf")
        top = float(np.nanmax(v))
        for pol in ("peak", "dip"):
            r = self.call("peak_list", trace=1, floor_margin_db=0.5, polarity=pol)
            self.assertEqual((r["base"], r["polarity"], r["unit"]), ("max", pol, "dB"), pol)
            self.assertAlmostEqual(r["base_level"], top, places=6)
            for row in r["peaks"]:
                # 差は最大からの差（山は最大 − N dB 以上、谷は最大 − N dB より下）
                self.assertAlmostEqual(row["diff_db"], row["level"] - top, places=6)
                if pol == "peak":
                    self.assertGreaterEqual(row["diff_db"], -0.5 - 1e-9)
                else:
                    self.assertLess(row["diff_db"], -0.5)
        # 画面の欄（Qt・Web 共通の peak_view）も最大からで、谷を探せる
        from namibench.ui_qt import peak_view

        st = self.call("get_state")
        self.assertEqual(st["display_quantity"], "nf")
        for pol in ("peak", "dip"):
            lv = peak_view.list_view(st, pol)
            self.assertEqual((lv["base"], lv["reason"]), ("max", None), pol)

    def test_ref_auto_works_on_nf(self):
        """NF の値（0〜20 dB 付近）が電力の縦軸の REF では見えなくても、REF の自動に使う最大が NF の dB で出る。"""
        self.nf_mode()
        self.call("set_amplitude", ref_level=-30.0, db_per_div=10)
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        self.single()
        st = self.call("get_state")
        self.assertEqual((st["amplitude"]["ref_level"], st["amplitude"]["db_per_div"]), (-30.0, 10))
        peak = st["ref_auto"]["peaks"]["1"]
        self.assertEqual(peak["unit"], "dB")
        self.assertIsNotNone(peak["max"])
        from namibench.ui_qt import screen_model

        v = screen_model.ref_auto_view(st, ["1"], "dB", 10.0, True)
        self.assertIsNone(v["reason"])
        self.assertGreater(v["level"], peak["max"])

    def test_preset_amplitude_is_the_common_axis_and_undo(self):
        self.connect()
        self.call("set_amplitude", ref_level=-30.0, db_per_div=10)
        self.call("set_mode", mode="nf")
        self.call("set_amplitude", ref_level=18.0, db_per_div=5)
        pid = self.call("preset_save", name="NF")["id"]
        self.call("set_amplitude", ref_level=12.0, db_per_div=1)
        self.call("set_mode", mode="sa")
        self.call("preset_load", id=pid)
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["mode"], st["amplitude"]["ref_level"], st["amplitude"]["db_per_div"]),
                         ("nf", 18.0, 5))
        self.call("undo")
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["mode"], st["amplitude"]["ref_level"], st["amplitude"]["db_per_div"]),
                         ("sa", 12.0, 1))

    def test_xy_y_on_nf_axis_follows_only_nf(self):
        # NF の縦軸で置いた XY カーソルの Y は、電力の単位でずらさず、比の形式としても直さない（設計M・設計R §3）
        self.nf_mode()
        self.call("set_xy", y2=7.0)  # 掃引の前（値のあるトレースが無い）でも NF の縦軸
        self.assertEqual(self.inst._markers.xy.y2_kind, "nf")
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        self.call("set_xy", y1=6.0)
        self.assertEqual(self.inst._markers.xy.y1_kind, "nf")
        self.call("set_view", level_unit="dBuV")  # NF の間に単位を替えても動かない
        self.call("set_mode", mode="sa")
        self.single()
        self.call("set_view", level_unit="dBm")  # 電力の縦軸で単位を替えても、NF で置いた Y は動かない
        xy = self.inst._markers.xy
        self.assertEqual((xy.y1, xy.y2, xy.y1_kind), (6.0, 7.0, "nf"))
        self.call("set_xy", y1=-40.0)
        self.call("set_view", level_unit="dBuV")
        self.assertNotEqual(self.inst._markers.xy.y1, -40.0)  # 電力の縦軸で置いた Y はずれる（比べのため）
        self.assertEqual(self.inst._markers.xy.y2, 7.0)

    def test_unit_view_in_nf(self):
        self.nf_mode()
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        st = self.call("get_state")
        self.assertEqual(st["display_quantity"], "nf")
        u = screen_model.unit_view(st)
        self.assertEqual(u["current"], screen_model.NF_UNIT)
        self.assertTrue(all(not c["enabled"] for c in u["choices"]))
        self.assertIn("NF", u["tip"])

    def test_save_keeps_hot_cold_and_enr(self):
        self.nf_mode()
        self.call("nf_load_enr_table", text="500,15\n600,14\n", file_name="t.csv")
        self.single()
        path = self.root / "nf.h5"
        self.wait_task(self.call("save", path=str(path))["task_id"])
        with H5Record(path) as r:
            seg = r.segments[0]
            self.assertEqual((seg.raw_layout, seg.quantity), ("nf", "nf"))
            self.assertEqual(seg.nf["enr"]["kind"], "table")
            self.assertEqual(seg.nf["enr"]["table"]["name"], "t.csv")
            self.assertEqual(seg.nf["small_difference_db"], nfc.SMALL_DIFFERENCE_DB)
            traces = r.traces()
        self.assertEqual(traces[0].quantity, Quantity.NF)
        opened = self.call("open", paths=[str(path)])
        self.wait_task(opened["task_id"])
        p = self.call("get_trace", id="F1.1")
        self.assertEqual((p["quantity"], p["unit"]), ("nf", "dB"))

    def test_csv_export_in_db(self):
        self.nf_mode()
        self.call("nf_set_enr", enr_db=15.0)
        self.single()
        path = self.root / "nf.csv"
        self.wait_task(self.call("export_csv", path=str(path), traces=[1])["task_id"])
        lines = path.read_text(encoding="ascii").splitlines()
        self.assertIn('"nf"', lines[0])
        self.assertIn("dB", lines[1])
        v = float(lines[2].split(",")[1])
        self.assertTrue(0.0 < v < 15.0)


if __name__ == "__main__":
    unittest.main()
