"""表示の単位・外部補正・周波数オフセット・配色・プリセット（設計O・J §4.4）のコアの試験。

- 単位の換算（50Ω）と、比・未校正の電力の扱い（core/view_units.py）
- 配色のコントラスト比・色の差・変えた色の数（core/palette.py）
- プリセットの保存・一覧・名前・削除・壊れたファイル（core/presets.py）
- 本体: 単位を換えるとトレース・マーカー・REF の自動・REF レベルが同じだけずれる／元に戻す／周波数オフセットは
  機器へ送る条件を変えない／CSV は表示の単位と換算後の周波数／プリセットの呼び出しと元に戻す／起動で戻る
"""
import csv
import json
import math
import unittest

import numpy as np

from pygigast.core import view_units
from pygigast.core.palette import BASES, MIN_CONTRAST, Palette, contrast_ratio, delta_e, normalize_color
from pygigast.core.presets import PresetError, PresetStore
from tests.instrument_helpers import SERIAL, InstrumentCase


class ViewUnitsTest(unittest.TestCase):
    def test_offsets_50_ohm(self):
        # 0dBm = √(1mW·50Ω) = 0.22361V → 106.99dBμV・46.99dBmV
        self.assertAlmostEqual(view_units.OFFSET_FROM_DBM["dBuV"], 106.9897, places=3)
        self.assertAlmostEqual(view_units.OFFSET_FROM_DBM["dBmV"], 46.9897, places=3)
        self.assertEqual(view_units.level_shift("dBm", {"level_unit": "dBuV", "ext_db": 0}), (
            view_units.OFFSET_FROM_DBM["dBuV"], "dBμV"))

    def test_ext_and_ratio_and_uncalibrated(self):
        opts = {"level_unit": "dBmV", "ext_db": 10.0}
        self.assertAlmostEqual(view_units.level_shift("dBm", opts)[0], 56.9897, places=3)
        # 比（ノーマライズ中の dB）は単位も外部補正も当てない
        self.assertEqual(view_units.level_shift("dB", opts), (0.0, "dB"))
        # 校正していない機器の電力は単位を換えず、外部補正だけ
        self.assertEqual(view_units.level_shift("dB（未校正）", opts), (10.0, "dB（未校正）"))

    def test_check_and_restore(self):
        self.assertIsNone(view_units.check_options(level_unit="dBuV", ext_db=-6.5, freq_offset_hz=-10**9))
        self.assertEqual(view_units.check_options(level_unit="dBW"), "level_unit")
        self.assertEqual(view_units.check_options(ext_db=float("nan")), "ext_db")
        self.assertEqual(view_units.check_options(ext_db=201.0), "ext_db")
        self.assertEqual(view_units.check_options(freq_offset_hz=1.5), "freq_offset_hz")
        self.assertEqual(view_units.check_options(draw_style="dots"), "draw_style")
        r = view_units.restore({"level_unit": "dBW", "ext_db": 3, "freq_offset_hz": "x", "draw_style": "fill"})
        self.assertEqual(r, {**view_units.DEFAULTS, "ext_db": 3.0, "draw_style": "fill"})

    def test_function_lines_option(self):
        """マーカーの機能の線を出すか（G §3.6）: 初期値は出す。真偽だけ受ける。前の版の保存（無い）は初期値。"""
        self.assertIs(view_units.DEFAULTS["function_lines"], True)
        self.assertIsNone(view_units.check_options(function_lines=False))
        for bad in (1, "no", 0.0):
            self.assertEqual(view_units.check_options(function_lines=bad), "function_lines")
        self.assertIs(view_units.restore({"function_lines": False})["function_lines"], False)
        self.assertIs(view_units.restore({"function_lines": "x"})["function_lines"], True)
        self.assertIs(view_units.restore({"level_unit": "dBm"})["function_lines"], True)


class PaletteTest(unittest.TestCase):
    def test_contrast_and_delta_e(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=3)
        self.assertAlmostEqual(contrast_ratio("#777777", "#777777"), 1.0)
        self.assertAlmostEqual(delta_e("#ff0000", "#ff0000"), 0.0)
        self.assertGreater(delta_e("#ff0000", "#00ff00"), 100)
        self.assertEqual(normalize_color("#ABC"), "#aabbcc")
        self.assertIsNone(normalize_color("red"))

    def test_changed_and_warnings(self):
        p = Palette("dark")
        self.assertEqual(p.changed(), [])
        # 初期値はどちらも読みにくい組を作らない（設計J §4.4: 文字と背景のコントラスト比 7 以上）
        self.assertEqual(p.warnings(), [])
        self.assertEqual(Palette("light").warnings(), [])
        p.colors["text_dim"] = "#30353d"
        p.colors["trace2"] = "#ffd04a"
        self.assertEqual(p.changed(), ["text_dim", "trace2"])
        w = p.warnings()
        self.assertIn(("text_dim", "graph_bg"), {(x.get("fg"), x.get("bg")) for x in w})
        sim = [x for x in w if x["code"] == "similar_traces"]
        self.assertEqual([(x["a"], x["b"]) for x in sim], [("trace1", "trace2")])
        for x in w:
            if x["code"] == "low_contrast":
                self.assertLess(x["ratio"], MIN_CONTRAST)

    def test_saved_form(self):
        p = Palette.from_saved({"base": "light", "colors": {"trace1": "#123456", "bogus": "#000000", "text": "x"}})
        self.assertEqual(p.base, "light")
        self.assertEqual(p.colors["trace1"], "#123456")
        self.assertEqual(p.colors["text"], BASES["light"]["text"])
        self.assertNotIn("bogus", p.colors)
        self.assertEqual(Palette.from_saved("broken").to_saved()["base"], "dark")


class PresetStoreTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.store = PresetStore(Path(self.tmp.name) / "presets")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_rename_delete(self):
        self.assertEqual(self.store.list(), ([], 0))
        a = self.store.save("FM 放送帯", {"x": 1}, {"mode": "sa"})
        self.store.save("430MHz フィルタ", {"x": 2})
        rows, broken = self.store.list()
        self.assertEqual([r["name"] for r in rows], ["430MHz フィルタ", "FM 放送帯"])
        self.assertEqual(broken, 0)
        with self.assertRaises(PresetError) as cm:
            self.store.save("FM 放送帯", {})
        self.assertEqual(cm.exception.code, "preset_name_taken")
        with self.assertRaises(PresetError) as cm:
            self.store.save("  ", {})
        self.assertEqual(cm.exception.code, "preset_name_empty")
        self.store.rename(a["id"], "FM")
        self.assertEqual(self.store.get(a["id"])["content"], {"x": 1})
        self.store.save("FM", {"x": 3}, overwrite_id=a["id"])
        self.assertEqual(self.store.get(a["id"])["content"], {"x": 3})
        self.store.delete(a["id"])
        self.assertEqual(len(self.store.list()[0]), 1)
        with self.assertRaises(PresetError):
            self.store.get(a["id"])
        with self.assertRaises(PresetError):
            self.store.get("../x")

    def test_broken_file_is_kept_and_counted(self):
        self.store.save("A", {})
        bad = self.store.folder / "deadbeef.json"
        bad.write_text("{ broken", encoding="utf-8")
        rows, broken = self.store.list()
        self.assertEqual((len(rows), broken), (1, 1))
        self.assertTrue(bad.exists())  # 黙って消さない


class InstrumentViewTest(InstrumentCase):
    def sweep(self):
        self.connect(50_000_000, 2_000_000_000)
        self.single()
        return self.call("get_trace", id=1)

    def test_unit_and_ext_shift_traces_markers_ref(self):
        p0 = self.sweep()
        self.call("set_amplitude", ref_level=-10.0)
        self.call("marker_add", kind="M", trace=1, freq_hz=1_500_000_000)
        m0 = self.call("get_state")["markers"]["markers"][0]["level"]
        auto0 = self.call("get_state")["ref_auto"]["peaks"]["1"]["max"]
        r = self.call("set_view", level_unit="dBuV", ext_db=10.0)
        shift = view_units.OFFSET_FROM_DBM["dBuV"] + 10.0
        self.assertAlmostEqual(r["amplitude"]["ref_level"], round(-10.0 + shift, 2))
        p1 = self.call("get_trace", id=1)
        self.assertEqual(p1["unit"], "dBμV")
        a0, a1 = np.asarray(p0["values"], float), np.asarray(p1["values"], float)
        ok = np.isfinite(a0)
        self.assertTrue(np.allclose(a1[ok] - a0[ok], shift, atol=1e-3))
        st = self.call("get_state")
        m1 = st["markers"]["markers"][0]
        self.assertAlmostEqual(m1["level"] - m0, shift, places=3)
        self.assertEqual(m1["unit"], "dBμV")
        self.assertAlmostEqual(st["ref_auto"]["peaks"]["1"]["max"] - auto0, shift, places=3)
        self.assertEqual(st["ref_auto"]["peaks"]["1"]["unit"], "dBμV")
        self.assertEqual(st["traces"][0]["unit"], "dBμV")
        # 元に戻すと単位も REF も戻る（Ctrl+Z: 設計J §3）
        self.call("undo")
        st = self.call("get_state")
        self.assertEqual(st["view"]["level_unit"], "dBm")
        self.assertAlmostEqual(st["amplitude"]["ref_level"], -10.0, places=2)
        self.assertEqual(self.call("get_trace", id=1)["unit"], "dBm")

    def test_freq_offset_does_not_touch_device_and_csv_follows(self):
        self.sweep()
        before = self.call("get_state")["sweep"]
        self.call("set_view", freq_offset_hz=10_000_000_000, level_unit="dBmV")
        after = self.call("get_state")["sweep"]
        self.assertEqual(before, after)  # 機器へ送る条件は変わらない
        path = self.root / "x.csv"
        r = self.call("export_csv", path=str(path), start_hz=100_000_000, stop_hz=110_000_000)
        self.wait_task(r["task_id"])
        lines = path.read_text(encoding="ascii").splitlines()
        meta = json.loads(lines[0][len("# meta: "):])
        self.assertEqual(meta["display"]["freq_offset_hz"], 10_000_000_000)
        rows = list(csv.reader(lines[1:]))
        self.assertEqual(rows[0], ["frequency_hz", "measured_frequency_hz", "trace1_dBmV"])
        f, mf = int(rows[1][0]), int(rows[1][1])
        self.assertEqual(f - mf, 10_000_000_000)
        self.assertGreaterEqual(mf, 100_000_000)
        p = self.call("get_trace", id=1)
        i = (mf - p["axis"]["start_hz"]) // p["axis"]["step_hz"]
        self.assertAlmostEqual(float(rows[1][2]), float(p["values"][i]), places=3)

    def test_bad_values(self):
        from pygigast.core.instrument import CommandError

        for kw in ({"level_unit": "dBW"}, {"ext_db": "x"}, {"freq_offset_hz": 1.5}, {"draw_style": "dots"},
                   {"function_lines": 1}):
            with self.assertRaises(CommandError):
                self.call("set_view", **kw)
        with self.assertRaises(CommandError) as cm:
            self.call("palette_set", colors={"trace1": "red"})
        self.assertEqual(cm.exception.code, "bad_color")
        with self.assertRaises(CommandError):
            self.call("palette_reset", base="sepia")

    def test_palette_set_reset_undo(self):
        st = self.call("palette_set", colors={"trace1": "#123456"})["palette"]
        self.assertEqual(st["changed"], ["trace1"])
        r = self.call("palette_reset", base="light")
        self.assertEqual(r["lost"], 1)
        self.assertEqual(r["palette"]["base"], "light")
        self.assertEqual(r["palette"]["colors"], BASES["light"])
        self.call("undo")
        p = self.call("get_state")["palette"]
        self.assertEqual((p["base"], p["changed"], p["colors"]["trace1"]), ("dark", ["trace1"], "#123456"))
        self.call("redo")
        self.assertEqual(self.call("get_state")["palette"]["base"], "light")

    def test_preset_save_load_undo(self):
        self.call("set_sweep", start_hz=76_000_000, stop_hz=108_000_000, rbw_hz=30_000)
        self.call("set_trace", number=2, action="max_hold", visible=True)
        self.call("set_amplitude", ref_level=-20.0, db_per_div=5)
        self.call("set_view", level_unit="dBuV", freq_offset_hz=-1_000_000)
        saved = self.call("preset_save", name="FM 放送帯")
        rows = self.call("preset_list")["presets"]
        self.assertEqual([r["name"] for r in rows], ["FM 放送帯"])
        # 中身に校正・ノーマライズの基準・マーカーを入れない（O §1.2）
        content = json.loads((self.root / "data" / "presets" / f"{saved['id']}.json").read_text("utf-8"))["content"]
        self.assertEqual(set(content), {"sweep", "traces", "amplitude", "view"})
        self.call("set_view", level_unit=None, freq_offset_hz=None)
        self.call("set_sweep", start_hz=1_000_000_000, stop_hz=2_000_000_000, rbw_hz=180_000)
        self.call("set_trace", number=2, action="update", visible=False)
        r = self.call("preset_load", id=saved["id"])
        self.assertEqual((r["adopted"]["start_hz"], r["adopted"]["stop_hz"]), (76_000_000, 108_000_000))
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["rbw_preference_hz"], 30_000)
        self.assertEqual(st["traces"][1]["action"], "max_hold")
        # REF は単位を換えたときに同じだけずれた値（−20dBm = 86.99dBμV）のまま残る
        self.assertAlmostEqual(st["amplitude"]["ref_level"], round(-20.0 + view_units.OFFSET_FROM_DBM["dBuV"], 2))
        self.assertEqual(st["amplitude"]["db_per_div"], 5)
        self.assertEqual((st["view"]["level_unit"], st["view"]["freq_offset_hz"]), ("dBuV", -1_000_000))
        # 1回の命令なので1回の元に戻すで前の状態へ
        self.call("undo")
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["start_hz"], 1_000_000_000)
        self.assertEqual(st["traces"][1]["action"], "update")
        self.assertEqual(st["view"]["level_unit"], "dBm")
        self.call("preset_rename", id=saved["id"], name="FM")
        self.call("preset_delete", id=saved["id"])
        self.assertEqual(self.call("preset_list")["presets"], [])

    def test_function_lines_in_preset_and_undo(self):
        """マーカーの機能の線を出すか（G §3.6）は表示の設定: プリセットに入り、元に戻せる。null は初期値（出す）。"""
        self.call("set_view", function_lines=False)
        self.assertIs(self.call("get_state")["view"]["function_lines"], False)
        saved = self.call("preset_save", name="線なし")
        content = json.loads((self.root / "data" / "presets" / f"{saved['id']}.json").read_text("utf-8"))["content"]
        self.assertIs(content["view"]["function_lines"], False)
        self.call("set_view", function_lines=None)
        self.assertIs(self.call("get_state")["view"]["function_lines"], True)
        self.call("undo")
        self.assertIs(self.call("get_state")["view"]["function_lines"], False)
        self.call("set_view", function_lines=True)
        self.call("preset_load", id=saved["id"])
        self.assertIs(self.call("get_state")["view"]["function_lines"], False)

    def test_restored_at_startup(self):
        self.call("set_view", level_unit="dBmV", ext_db=-3.5, freq_offset_hz=123_000_000, draw_style="fill",
                  function_lines=False, zero_span_s=60)
        self.call("palette_set", colors={"accent": "#0055aa"})
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")
        self.assertEqual({k: st["view"][k] for k in view_units.DEFAULTS},
                         {"level_unit": "dBmV", "ext_db": -3.5, "freq_offset_hz": 123_000_000, "draw_style": "fill",
                          "layout": "spectrum", "function_lines": False, "zero_span_s": 60})
        self.assertEqual(st["palette"]["colors"]["accent"], "#0055aa")
        self.assertEqual(st["palette"]["changed"], ["accent"])

    def test_known_devices_and_forget(self):
        self.connect()
        kd = self.call("get_state")["known_devices"]
        self.assertEqual([(d["serial"], d["last"]) for d in kd], [(SERIAL, True)])
        self.call("forget_device", serial=SERIAL)
        self.assertEqual(self.call("get_state")["known_devices"], [])

    def test_ratio_axis_not_shifted(self):
        # 比（TG のノーマライズ中の dB）には単位を当てない: 縦軸の REF はずらさない
        self.connect(100_000_000, 200_000_000)
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="wide")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        self.single()
        self.wait_for(lambda k, p: k == "trace" and p["unit"] == "dB")
        self.assertEqual(self.call("get_trace", id=1)["unit"], "dB")
        self.call("set_amplitude", ref_level=5.0)
        r = self.call("set_view", level_unit="dBuV")
        self.assertEqual(r["amplitude"]["ref_level"], 5.0)
        self.assertEqual(self.call("get_trace", id=1)["unit"], "dB")
        self.assertTrue(math.isfinite(self.call("get_state")["ref_auto"]["peaks"]["1"]["max"]))


if __name__ == "__main__":
    unittest.main()
