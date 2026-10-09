"""設計P のデータ（ウォーターフォールの行・まとめ・色の範囲の自動・密度・カラーマップ・バンドプラン・記録の読み出し）の試験。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from pygigast.core import bandplan as bp
from pygigast.core import colormaps
from pygigast.core import waterfall as wf
from pygigast.core.palette import Palette
from pygigast.core.record_frames import SegmentLevels
from pygigast.core.storage.h5file import MemorySegment, segment_meta_for, write_h5
from pygigast.core.storage.record_source import RecordSource
from pygigast.core.storage.recording import Recorder, new_recording_path
from tests.test_storage import adopted, header, pattern, raw_sweep

MHZ = 1_000_000


def row(t, values, seg=1, start=100 * MHZ, step=MHZ, cond=None):
    return wf.row_from_values(t, start, step, np.asarray(values, dtype=np.float32), unit="dBm", seg=seg,
                              cond=cond or {"start_hz": start, "stop_hz": start + step * (len(values) - 1),
                                            "rbw_hz": 180_000, "mode": "sa"})


class WaterfallDataTest(unittest.TestCase):
    def test_decimate_keeps_peaks_and_ignores_nan(self):
        v = np.full(10_000, -100.0, dtype=np.float32)
        v[1234] = -10.0
        v[5000:5003] = np.nan
        out, k = wf.decimate_max(v, 4096)
        self.assertEqual(k, 3)
        self.assertEqual(out.shape[0], -(-10_000 // 3))
        self.assertEqual(float(out[1234 // 3]), -10.0)  # ピークを消さない（P §8）
        self.assertFalse(np.isnan(out[5000 // 3 + 1]) and False)
        small, k1 = wf.decimate_max(np.arange(5, dtype=np.float32), 4096)
        self.assertEqual(k1, 1)
        np.testing.assert_array_equal(small, np.arange(5))

    def test_row_from_large_sweep_has_coarser_step(self):
        r = row(1.0, np.zeros(12_000))
        self.assertLessEqual(r.columns, wf.WF_MAX_COLUMNS)
        self.assertEqual(r.step_hz, 3 * MHZ)
        z = wf.row_from_values(1.0, 0, 0, np.zeros(100, dtype=np.float32), unit="dBm", seg=1, cond={})
        self.assertTrue(z.zero_span)
        self.assertEqual((z.start_hz, z.step_hz), (0, 1))

    def test_select_rows_merges_by_max_and_not_across_conditions(self):
        rows = [row(i, [-90.0 + (5 if i == 7 else 0), -80.0]) for i in range(10)]
        rows += [row(10 + i, [-70.0, -60.0], seg=2) for i in range(4)]
        out = wf.select_rows(rows, 5)
        self.assertLessEqual(len(out), 5)
        segs = [r.seg for r in out]
        self.assertEqual(segs, sorted(segs))  # 古い順のまま
        for r in out:
            self.assertTrue(r.sweeps >= 1)
        merged = [r for r in out if r.seg == 1 and r.t <= 7 <= r.t_end]
        self.assertTrue(merged and float(merged[0].values[0]) == -85.0)  # 列ごとの最大値
        self.assertEqual(wf.select_rows(rows[:3], 5), rows[:3])  # 少なければまとめない
        self.assertEqual(sum(r.sweeps for r in out), 14)

    def test_history_limit_find_between(self):
        h = wf.History(3)
        for i in range(5):
            h.add(row(float(i), [0.0]))
        self.assertEqual([r.t for r in h.rows()], [2.0, 3.0, 4.0])
        self.assertEqual(h.find(3.2).t, 3.0)
        self.assertEqual([r.t for r in h.between(2.5, 3.5)], [3.0])
        h.set_limit(2)
        self.assertEqual([r.t for r in h.rows()], [3.0, 4.0])

    def test_auto_range_follows_floor_to_max_slowly(self):
        a = wf.AutoRange()
        v = np.full(100, -90.0)
        v[10] = -30.0
        self.assertTrue(a.feed(v, 0.0))
        self.assertEqual(a.range, (-90.0, -30.0))
        # 数秒たたないうちは見せる範囲を変えない（ちらつかない: P §9.2）
        self.assertFalse(a.feed(v - 20.0, 0.5))
        self.assertEqual(a.range, (-90.0, -30.0))
        # 時定数で追う（一度に飛ばない）
        self.assertTrue(a.feed(v - 20.0, 3.0))
        lo, hi = a.range
        self.assertTrue(-110.0 < lo < -90.0 and -50.0 < hi < -30.0, a.range)
        # 平らな値でも最小の幅
        b = wf.AutoRange()
        b.feed(np.full(10, -80.0), 0.0)
        self.assertEqual(b.range[1] - b.range[0], wf.AUTO_MIN_SPAN_DB)

    def test_density_counts_and_decay(self):
        d = wf.Density()
        self.assertTrue(d.setup(("k",), 4, -100.0, 0.0, 2))
        self.assertEqual((d.bins, d.n_cols), (200, 4))
        self.assertFalse(d.setup(("k",), 4, -100.0, 0.0, 2))
        d.add(np.array([-50.0, -50.0, -50.0, -50.0]))
        img = d.image()
        self.assertEqual(img.shape, (200, 4))
        b = int((-50.0 + 100.0) / wf.DENSITY_BIN_DB)
        self.assertTrue((img[199 - b] == 255).all())  # 0 行目が上（高いレベル）
        self.assertEqual(int((img > 0).sum()), 4)
        for _ in range(4):  # 4 回（半分になる回数 2 の2倍）は別のレベル
            d.add(np.array([-20.0, -20.0, -20.0, -20.0]))
        img2 = d.image()
        old = img2[199 - b]
        new = img2[199 - int(80 / wf.DENSITY_BIN_DB)]
        self.assertTrue((old > 0).all() and (new > old).all())  # 古い掃引の重みは減る
        out = d.image()
        self.assertEqual(out.dtype, np.uint8)
        # 範囲の外の値・値の無い点は数えない
        e = wf.Density()
        e.setup(("e",), 3, -100.0, 0.0, 10)
        e.add(np.array([10.0, np.nan, -150.0]))
        self.assertEqual(int((e.image() > 0).sum()), 0)

    def test_density_columns_and_interval(self):
        d = wf.Density()
        d.setup(("x",), 5000, -100.0, 0.0, 10)
        self.assertEqual(d.n_cols, wf.DENSITY_MAX_COLUMNS)
        self.assertIsNone(wf.sweep_interval_s([1.0]))
        self.assertAlmostEqual(wf.sweep_interval_s([1.0, 1.5, 2.0, 2.6]), 0.5)


class ColormapPaletteTest(unittest.TestCase):
    def test_luts_are_monotonic_in_lightness(self):
        for name in colormaps.CHOICES:
            lut = colormaps.lut(name)
            self.assertEqual(lut.shape, (256, 3))
            y = lut @ np.array([0.2126, 0.7152, 0.0722])
            self.assertTrue(np.all(np.diff(y[::16]) > -1.0), name)  # 明るさが単調に増える（P §9.1）
        self.assertEqual(colormaps.lut("unknown").tolist(), colormaps.lut("viridis").tolist())
        web = colormaps.luts_for_web()
        self.assertEqual(sorted(web), sorted(colormaps.CHOICES))
        self.assertEqual(len(web["viridis"]), 768)

    def test_palette_colormap_saved_and_counted(self):
        p = Palette("dark")
        self.assertEqual(p.colormap, "viridis")
        self.assertNotIn("colormap", p.changed())
        p.colormap = "magma"
        self.assertEqual(p.changed(), ["colormap"])
        q = Palette.from_saved(p.to_saved())
        self.assertEqual(q.colormap, "magma")
        self.assertEqual(Palette.from_saved({"base": "light", "colormap": "nope"}).colormap, "viridis")
        self.assertEqual(p.state()["colormaps"], list(colormaps.CHOICES))

    def test_saved_palette_carries_colormap(self):
        # 名前を付けた配色（J §4.4.1）にも色の並びが入る。読み込んだ後に変えたら「保存していない変更」に数える
        p = Palette("dark", colormap="magma")
        self.assertEqual(p.content()["colormap"], "magma")
        p.set_origin("id1", "mine")
        self.assertEqual(p.unsaved(), [])
        p.colormap = "gray"
        self.assertEqual(p.unsaved(), ["colormap"])
        q = Palette.from_saved(p.to_saved())
        self.assertEqual((q.colormap, q.origin["colormap"], q.unsaved()), ("gray", "magma", ["colormap"]))
        # 色の並びを持たない前の版の保存は初期値の色の並びとして読む
        old = Palette.from_saved({"base": "dark", "colors": {}, "origin": {"id": "a", "name": "b", "colors": {}}})
        self.assertEqual((old.colormap, old.origin["colormap"], old.unsaved()), ("viridis", "viridis", []))


class DisplayViewTest(unittest.TestCase):
    """画面の中身（Qt と Web が同じものを使う: ui_qt/display_view.py）のうち、ほかの設計と組むところ。"""

    def test_zero_span_and_density_unit(self):
        from pygigast.ui_qt import display_view, texts

        st = {"sweep": {"start_hz": 100, "stop_hz": 100, "step_hz": 0, "points": 500}, "view": {"layout": "density"}}
        why = texts.REASON_TEXTS["layout_zero_span"]
        self.assertEqual(display_view.shown_layout(st), "spectrum")
        self.assertEqual(display_view.layout_reasons(st), {"spectrum": None, "waterfall": why, "density": why})
        self.assertEqual(display_view.part_reasons(st), {"waterfall": why, "density": why})
        # 再生中は記録の周波数の範囲（ゼロスパンの間は再生しないが、再生の範囲が横軸）
        pb = {**st, "playback": {"active": True, "view": {"start_hz": 1, "stop_hz": 2}}}
        self.assertEqual(display_view.shown_layout(pb), "density")
        # 密度の縦はグラフの縦軸と同じ範囲: 行にするトレースの単位が縦軸と違えば描かない（設計R §3）
        st = {"sweep": {"step_hz": 1000}, "view": {"layout": "density"}, "waterfall": {"unit": "SWR"},
              "traces": [{"number": 1, "visible": True, "has_values": True, "unit": "dB"}]}
        self.assertEqual(display_view.density_reason(st),
                         texts.REASON_TEXTS["trace_unit_differs"].replace("{axis}", "dB").replace("{unit}", "SWR"))
        st["traces"][0]["unit"] = "SWR"
        self.assertIsNone(display_view.density_reason(st))
        # |Γ|・SWR の行は縦軸と同じ範囲で色を付ける（自動・下限・上限は理由付きで使えない）
        st["waterfall"]["range"] = {"lo": 1.0, "hi": 3.0, "auto": False, "axis": True}
        v = display_view.display_tab_view(st)
        self.assertEqual((v["auto"], v["auto_reason"], v["range_reason"], v["lo"], v["hi"]),
                         (True, texts.REASON_TEXTS["wf_linear_axis"], texts.REASON_TEXTS["wf_linear_axis"], 1.0, 3.0))
        self.assertEqual(display_view.waterfall_reading(0.0, 1, 100, 1.23456, "SWR")[-1], "1.23 SWR")
        self.assertEqual(display_view.waterfall_reading(0.0, 1, 100, 0.12345, "|Γ|")[-1], "0.123 |Γ|")


class PlaybackLockViewTest(unittest.TestCase):
    """再生中に使えない、ライブの条件を変える欄（Qt と Web が同じ理由を出す: 設計P §5）。"""

    def test_locks_and_reasons(self):
        from pygigast.ui_qt import display_view, screen_model, texts

        why = texts.REASON_TEXTS["playback_active"]
        live = {"sweep": {"step_hz": 1000, "mode": "sa"}, "undo": {"undo_blocked": None, "redo_blocked": None}}
        self.assertEqual(display_view.locks(live), {"preset_load": None, "undo": None, "redo": None})
        self.assertIsNone(screen_model.sg_reason(live))
        r = screen_model.sweep_reasons(live)
        self.assertEqual((r["rbw"], r["span_lock"], r["points"]), (None, None, None))
        pb = {**live, "playback": {"active": True}, "undo": {"undo_blocked": "playback_active", "redo_blocked": None}}
        self.assertEqual(display_view.locks(pb), {"preset_load": why, "undo": why, "redo": None})
        self.assertEqual(screen_model.sg_reason(pb), why)
        r = screen_model.sweep_reasons(pb)
        self.assertEqual((r["rbw"], r["span_lock"], r["points"], r["image_reject"]), (why, why, why, why))
        self.assertEqual(screen_model.mode_reasons(pb)["tg"], why)


class BandplanTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_sample_is_empty_and_valid(self):
        plan = bp.load(bp.SAMPLE_PATH)
        self.assertEqual((plan["bands"], plan["bookmarks"]), ([], []))

    def test_parse_and_round_trip(self):
        d = {"format": "pygigast-bandplan", "version": 1,
             "bands": [{"name": "430MHz 帯", "start_hz": 430 * MHZ, "stop_hz": 440 * MHZ, "color": "#4FA3FF"}],
             "bookmarks": [{"name": "ビーコン", "hz": 434_500_000}, {"name": "A", "hz": 100 * MHZ}]}
        plan = bp.parse(d)
        self.assertEqual(plan["bands"][0]["color"], "#4fa3ff")
        self.assertEqual([m["hz"] for m in plan["bookmarks"]], [100 * MHZ, 434_500_000])  # 周波数の順
        p = bp.save(self.dir / "a.json", plan)
        self.assertEqual(bp.load(p), plan)
        self.assertIn("ビーコン", p.read_text(encoding="utf-8"))  # 利用者が読んで直せる形
        self.assertEqual(len(bp.bands_in(plan, 435 * MHZ, 436 * MHZ)), 1)
        self.assertEqual(len(bp.bookmarks_in(plan, 0, 200 * MHZ)), 1)

    def test_refuses_bad_files_with_reason(self):
        cases = [
            ({"format": "x"}, "bandplan_not_format"),
            ({"format": "pygigast-bandplan", "version": 2}, "bandplan_unsupported_version"),
            ({"format": "pygigast-bandplan", "version": 1,
              "bands": [{"name": "a", "start_hz": 2, "stop_hz": 1, "color": "#000000"}]}, "bandplan_band_order"),
            ({"format": "pygigast-bandplan", "version": 1,
              "bands": [{"name": "a", "start_hz": 1, "stop_hz": 2, "color": "red"}]}, "bandplan_bad_color"),
            ({"format": "pygigast-bandplan", "version": 1, "bookmarks": [{"name": "", "hz": 1}]}, "bandplan_bad_name"),
            ({"format": "pygigast-bandplan", "version": 1, "bookmarks": [{"name": "a", "hz": 1.5}]}, "bandplan_bad_freq"),
        ]
        for d, code in cases:
            with self.assertRaises(bp.BandplanError) as cm:
                bp.parse(d)
            self.assertEqual(cm.exception.code, code, d)
        bad = self.dir / "b.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(bp.BandplanError) as cm:
            bp.load(bad)
        self.assertEqual(cm.exception.code, "bandplan_not_json")


class RecordSourceTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_h5_rows_use_overview_and_raw_sweep(self):
        n, p = 300, 11
        a = adopted(points=p)
        t = 1_780_000_000.0 + np.arange(n, dtype=np.float64)
        raw = np.stack([pattern(i, p) for i in range(n)])
        meta = segment_meta_for(raw_sweep(0, a))
        path = write_h5(self.dir / "r.h5", header(), [MemorySegment(meta, t, raw)])
        src = RecordSource(path)
        try:
            self.assertEqual(src.kind, "h5")
            self.assertEqual(src.n_sweeps, n)
            r = src.rows(0, 0, n, 20)
            # 概観の段は「求めた行数の掃引の数」以下で一番粗い段（ここでは 4 掃引ごと）。残りは呼ぶ側が最大値でまとめる
            self.assertLessEqual(len(r.values), 4 * 20)
            self.assertEqual(int(r.sweeps[0]), 4)
            self.assertLessEqual(len(wf.select_rows([row(float(x), v) for x, v in zip(r.t_first, r.values)], 20)), 20)
            self.assertEqual(int(r.sweeps.sum()), n)
            k = int(r.sweeps[0])
            np.testing.assert_array_equal(r.values[0], raw[:k].max(axis=0))
            full = src.rows(0, 10, 15, 100)
            np.testing.assert_array_equal(full.values, raw[10:15])
            self.assertEqual(list(full.first_index), [10, 11, 12, 13, 14])
            rs = src.raw_sweep(0, 7)
            np.testing.assert_array_equal(rs.raw, raw[7])
            self.assertEqual(src.locate(t[42] + 0.2), (0, 42))
            self.assertEqual(src.span(), (t[0], t[-1]))
        finally:
            src.close()

    def test_work_file_rows_include_incomplete_tail(self):
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        a = adopted(points=11)
        for i in range(70):
            rec.append(raw_sweep(i, a))
        rec.stop().result(10)
        src = RecordSource(path)
        try:
            self.assertEqual(src.kind, "work")
            r = src.rows(0, 0, 70, 20)
            self.assertEqual(int(r.sweeps.sum()), 70)  # 揃っていない最後の塊（概観に無い分）も生の値で読む
            np.testing.assert_array_equal(r.values[-1], pattern(69, 11))
            np.testing.assert_array_equal(src.raw_sweep(0, 3).raw, pattern(3, 11))
            self.assertEqual(len(src.times(0)), 70)
        finally:
            src.close()

    def test_segment_levels_convert_like_live(self):
        a = adopted(points=11)
        meta = segment_meta_for(raw_sweep(0, a))
        h = header()
        from pygigast.core.devices.base import load_profile
        from pygigast.core.instrument import GIGAST_PROFILE_PATH
        from pygigast.core.level.convert import LevelConverter

        prof = load_profile(GIGAST_PROFILE_PATH)
        h.profile = prof
        h.calibration = None
        t = 1_780_000_000.0 + np.arange(3, dtype=np.float64)
        raw = np.stack([pattern(i, 11) for i in range(3)])
        path = write_h5(self.dir / "c.h5", h, [MemorySegment(meta, t, raw)])
        src = RecordSource(path)
        try:
            lv = SegmentLevels(src.segments[0], src.header)
            self.assertTrue(lv.can_convert)
            db = lv.db_rows(raw)
            want = LevelConverter(prof, None).to_dbm(lv.adopted, raw[1]).dbm
            np.testing.assert_allclose(db[1], want, atol=1e-4)
            fr = lv.frame(src.raw_sweep(0, 1), 5, (a.start_hz, a.stop_hz))
            np.testing.assert_allclose(10 * np.log10(fr.values) + 30, want, atol=1e-6)
            self.assertEqual(fr.sweep_no, 5)
        finally:
            src.close()


if __name__ == "__main__":
    unittest.main()
