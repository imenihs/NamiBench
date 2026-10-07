"""トレースの表示の形式（設計R §3・§5.1・設計E §6.5）の試験。純粋な計算（core/trace/formats.py）と、マーカーの読み値・
しきい値・N dB 帯域幅が形式によらず同じ点を探すこと（core/marker）。"""
from __future__ import annotations

import math
import unittest

import numpy as np

from pygigast.core.marker import Kind, MarkerTable, ThresholdSetting
from pygigast.core.marker import analysis as an
from pygigast.core.marker.markers import marker_from_def, marker_to_def
from pygigast.core.trace import Trace, TraceAction, formats
from pygigast.core.types import AdoptedSweep, FreqAxis, Frame, Mode, Quantity, ValueKind

MHZ = 1_000_000


def gamma_curve(n: int = 201) -> tuple[FreqAxis, np.ndarray]:
    """100〜300MHz の点に、200MHz で一番深い谷（|Γ| 0.02）を持つ |Γ| の並び（S11 の比のトレース）。"""
    axis = FreqAxis(100 * MHZ, MHZ, n)
    f = axis.freqs().astype(float)
    g = 0.02 + 0.97 * np.clip(np.abs(f - 200 * MHZ) / (60 * MHZ), 0, 1)
    return axis, g


def view(fmt: str) -> an.TraceView:
    axis, g = gamma_curve()
    v = an.TraceView.build(1, axis, g, Quantity.RATIO, axis.start_hz, axis.last_hz)
    if fmt != formats.DB:
        import dataclasses

        v = dataclasses.replace(v, fmt=fmt, unit=formats.FORMAT_UNITS[fmt])
    return v


class ConvertTest(unittest.TestCase):
    def test_db_gamma_swr(self):
        db = np.array([0.0, -20.0, -9.542425, np.nan])
        g = formats.from_db(db, formats.GAMMA)
        np.testing.assert_allclose(g[:3], [1.0, 0.1, 1 / 3], rtol=1e-6)
        self.assertTrue(math.isnan(g[3]))
        s = formats.from_db(db, formats.SWR)
        # |Γ| = 1（全反射）は SWR の上限にそろえる（無限大を作らない）
        self.assertEqual(s[0], formats.SWR_MAX)
        np.testing.assert_allclose(s[1:3], [1.1 / 0.9, 2.0], rtol=1e-5)
        self.assertTrue(math.isnan(s[3]))

    def test_swr_limit_mask_and_over_one(self):
        db = np.array([0.5, -0.05, -6.0])  # |Γ| > 1（ばらつきで基準を超えた）・上限の近く・ふつう
        m = formats.swr_limit_mask(db, formats.SWR)
        self.assertEqual(list(m), [True, True, False])
        self.assertIsNone(formats.swr_limit_mask(db, formats.GAMMA))
        np.testing.assert_allclose(formats.from_db(db, formats.SWR)[:2], [formats.SWR_MAX] * 2)

    def test_to_db_inverse_and_convert(self):
        for fmt, v in ((formats.GAMMA, 0.25), (formats.SWR, 1.5), (formats.DB, -12.0)):
            self.assertAlmostEqual(formats.value_from_db(formats.to_db(v, fmt), fmt), v, places=9)
        self.assertAlmostEqual(formats.convert_value(2.0, formats.SWR, formats.GAMMA), 1 / 3, places=9)
        self.assertAlmostEqual(formats.convert_value(2.0, formats.SWR, formats.DB), -9.5424, places=3)
        # 下端より下は無限大でなく下端の値（SWR 1 は |Γ| 0）
        self.assertEqual(formats.convert_value(1.0, formats.SWR, formats.GAMMA), 0.0)
        self.assertEqual(formats.convert_value(0.0, formats.GAMMA, formats.DB), -300.0)

    def test_effective_needs_s11_ratio(self):
        self.assertEqual(formats.effective(formats.SWR, Mode.S11, Quantity.RATIO), formats.SWR)
        self.assertEqual(formats.effective(formats.SWR, "s11", Quantity.RATIO), formats.SWR)
        self.assertEqual(formats.effective(formats.SWR, Mode.TG, Quantity.RATIO), formats.DB)
        self.assertEqual(formats.effective(formats.SWR, Mode.S11, Quantity.POWER), formats.DB)
        # 位相系は実数の値には当たらない
        self.assertEqual(formats.effective(formats.SMITH, Mode.S11, Quantity.RATIO), formats.DB)
        self.assertEqual(formats.effective("unknown", Mode.S11, Quantity.RATIO), formats.DB)

    def test_cells_and_ticks(self):
        self.assertEqual(formats.value_cell(1.23456, "SWR"), "1.23 SWR")
        self.assertEqual(formats.value_cell(0.12345, "|Γ|"), "0.123 |Γ|")
        self.assertEqual(formats.value_cell(-20.004, "dB"), "-20.00 dB")
        self.assertEqual(formats.delta_cell(0.25, "SWR"), "+0.25 SWR")
        self.assertEqual(formats.delta_cell(-3.0, "SWR", db_offset=True), "-3.00")
        self.assertEqual(formats.delta_cell(1.5, "dBm"), "+1.50")
        self.assertEqual(formats.tick_decimals("SWR", 0.05), 2)
        self.assertEqual(formats.tick_decimals("SWR", 0.2), 1)
        self.assertEqual(formats.tick_decimals("SWR", 1.0), 1)
        self.assertEqual(formats.tick_decimals("dB", 0.05), 1)

    def test_linear_axis(self):
        self.assertEqual(formats.per_div_of("SWR", {}), 0.2)
        self.assertEqual(formats.per_div_of("SWR", {"swr_per_div": 0.5}), 0.5)
        self.assertEqual(formats.per_div_of("SWR", {"swr_per_div": 0.3}), 0.2)  # 選択肢に無い値は初期値
        self.assertAlmostEqual(formats.linear_top("SWR", 0.2, 10), 3.0)
        self.assertAlmostEqual(formats.linear_top("|Γ|", 0.1, 10), 1.0)


def s11_frame(axis: FreqAxis, values: np.ndarray, mode: Mode = Mode.S11) -> Frame:
    adopted = AdoptedSweep(
        start_hz=axis.start_hz, stop_hz=axis.last_hz, axis=axis, rbw_hz=180_000, rbw_preference_hz=180_000,
        mode=mode, sg_hz=None, image_reject="off", preroll_points=0, transient_head_points=0, estimated_sweep_s=0.1)
    return Frame(axis=axis, values=values, quantity=Quantity.RATIO, value_kind=ValueKind.REAL, time=1.0,
                 duration_s=0.1, sweep_no=1, display_start_hz=axis.start_hz, display_stop_hz=axis.last_hz,
                 adopted=adopted, raw=None, flags={}, profile={})


class TraceFormatTest(unittest.TestCase):
    def test_format_does_not_discard_and_follows_mode(self):
        axis, g = gamma_curve()
        t = Trace(1, TraceAction.AVERAGE, count=4)
        t.apply(s11_frame(axis, g))
        t.apply(s11_frame(axis, g))
        self.assertEqual(t.current_count, 2)
        t.configure(fmt=formats.SWR)
        # 表し方だけなので蓄積を捨てない（設計R §3）
        self.assertEqual(t.current_count, 2)
        self.assertEqual(t.effective_format, formats.SWR)
        # TG で測った値には |Γ|・SWR が当たらない（選んだ形式は覚えている）
        t2 = Trace(2, fmt=formats.SWR)
        t2.apply(s11_frame(axis, g, Mode.TG))
        self.assertEqual(t2.format, formats.SWR)
        self.assertEqual(t2.effective_format, formats.DB)

    def test_unknown_format_rejected_without_changing_others(self):
        t = Trace(1)
        with self.assertRaises(ValueError):
            t.configure(line="thick", fmt="nonsense")
        self.assertEqual(t.line.value, "solid")
        self.assertEqual(t.format, formats.DB)


class MarkerFormatTest(unittest.TestCase):
    def test_same_points_in_every_format(self):
        """ピーク・谷・N dB 帯域幅は dB で探すので、形式によらず同じ点（設計R §3）。"""
        vdb, vswr, vg = view(formats.DB), view(formats.SWR), view(formats.GAMMA)
        for v in (vswr, vg):
            self.assertEqual(an.extreme_index(v, an.DIP), an.extreme_index(vdb, an.DIP))
            self.assertEqual(an.extreme_index(v, an.PEAK), an.extreme_index(vdb, an.PEAK))
            i = an.extreme_index(v, an.DIP)
            b0, b1 = an.n_db_bandwidth(vdb, i, 6.0, an.DIP), an.n_db_bandwidth(v, i, 6.0, an.DIP)
            self.assertEqual((b0.left.freq_hz, b0.right.freq_hz), (b1.left.freq_hz, b1.right.freq_hz))
        i = an.extreme_index(vswr, an.DIP)
        self.assertAlmostEqual(vswr.level_at(i), 1.02 / 0.98, places=6)
        self.assertAlmostEqual(vg.level_at(i), 0.02, places=6)

    def test_threshold_in_format_units(self):
        """しきい値は形式の値（SWR 2 以下）で受け、dB に直して同じ範囲を探す。"""
        vdb, vswr, vg = view(formats.DB), view(formats.SWR), view(formats.GAMMA)
        i = an.extreme_index(vdb, an.DIP)
        b_db = an.threshold_band(vdb, i, formats.to_db(2.0, formats.SWR), an.SIDE_BELOW)
        b_swr = an.threshold_band(vswr, i, 2.0, an.SIDE_BELOW)
        b_g = an.threshold_band(vg, i, 1 / 3, an.SIDE_BELOW)
        self.assertEqual(b_db.status, an.OK)
        for b in (b_swr, b_g):
            self.assertEqual((b.left.freq_hz, b.right.freq_hz), (b_db.left.freq_hz, b_db.right.freq_hz))

    def test_table_reads_format_and_converts_threshold(self):
        tbl = MarkerTable()
        vswr = view(formats.SWR)
        r = tbl.peak_search(None, vswr, an.DIP)
        tbl.set_threshold(r.marker_id, ThresholdSetting(2.0, an.SIDE_BELOW, formats.SWR))
        tbl.add_level_offset(r.marker_id, "+", 6.0)
        tbl.set_bandwidth(r.marker_id, (6.0,))
        snap = tbl.snapshot({1: vswr})
        m = snap["markers"][0]
        self.assertEqual(m["unit"], "SWR")
        self.assertAlmostEqual(m["level"], 1.02 / 0.98, places=6)
        self.assertEqual(m["threshold"]["level"], 2.0)
        # レベルのカーソルは dB のずれ（+6dB の |Γ| を SWR で出す）
        g6 = 0.02 * 10 ** (6 / 20)
        self.assertAlmostEqual(m["level_offsets"][0]["level"], (1 + g6) / (1 - g6), places=6)
        # 帯域幅の横線の高さも形式の値（谷から 6dB 上がった |Γ| の SWR）
        self.assertAlmostEqual(m["bandwidth"][0]["line_level"], (1 + g6) / (1 - g6), places=6)
        # |Γ| にしたら、しきい値は同じ反射の大きさの |Γ|（0.33）で探して出す
        snap_g = tbl.snapshot({1: view(formats.GAMMA)})
        th = snap_g["markers"][0]["threshold"]
        self.assertEqual(th["level"], 0.33)
        self.assertEqual(th["left_hz"], m["threshold"]["left_hz"])

    def test_threshold_format_round_trips_in_defs(self):
        tbl = MarkerTable()
        tbl.add(Kind.MANUAL, 1, 200 * MHZ)
        mid = tbl.markers()[0].id
        tbl.set_threshold(mid, ThresholdSetting(2.0, an.SIDE_BELOW, formats.SWR))
        d = marker_to_def(tbl.get(mid))
        self.assertEqual(d["threshold"], {"level": 2.0, "side": "below", "format": "swr", "power": False})
        back = marker_from_def(d).threshold
        self.assertEqual((back.fmt, back.power), (formats.SWR, False))
        # 形式・電力の印の無い前の版のしきい値は dB の電力（前の版は電力の単位を替えるとずらしていた）
        del d["threshold"]["format"]
        del d["threshold"]["power"]
        back = marker_from_def(d).threshold
        self.assertEqual((back.fmt, back.power), (formats.DB, True))


if __name__ == "__main__":
    unittest.main()
