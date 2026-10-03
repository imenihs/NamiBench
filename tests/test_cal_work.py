"""校正作業の計算（設計K §4.2〜§4.5。core/level/cal_work.py）。機器を使わない計算だけを確かめる。

- 121 点の格子・0MHz を 10MHz で測る・band の境目は2つの欄
- 補正値 =（SG のレベル − ケーブルの損失）− 補正なしの測定値。換算に当てると SG のレベルに戻る（符号の取り違えを防ぐ）
- 500MHz を全体のレベルにし、測っていない点の当てる補正は変わらない（やり直した点だけ差し替える）
- RBW の補正・NF の増幅度の向き
- 作業の値: 差が大きい点は確かめ待ち・飛ばす・次の点・保存していない数・保存する中身（前後の値の記録）
- 旧ファイルの取り込みは、校正作業で測った値と比べて大きく違う所に印（設計D §5.6）
"""
from __future__ import annotations

import math
import unittest

import numpy as np

from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH
from pygigast.core.level import cal_work as cw
from pygigast.core.level.calibration import Calibration
from pygigast.core.level.convert import LevelConverter
from pygigast.core.level.legacy_cal import parse_legacy_cal
from tests.test_level import build_legacy_bytes

AT = "2026-10-03T12:00:00+09:00"


def legacy_cal(serial: str = "SIM00001") -> Calibration:
    return Calibration.from_legacy(parse_legacy_cal(build_legacy_bytes()), model="gigast-v5", serial=serial,
                                   file_name="gigast.txt", sha256="0" * 64, created_at=AT)


def applied(cal: Calibration, f: int) -> float:
    """換算が当てる補正（全体のレベル＋周波数特性。RBW 180kHz）。"""
    conv = LevelConverter(load_profile(GIGAST_PROFILE_PATH), cal)
    c, _flag = conv.corr(np.array([f], dtype=np.int64))
    return float(cal.level_offset_db + c[0])


class GridTest(unittest.TestCase):
    def test_grid(self):
        g = cw.grid_freqs()
        self.assertEqual(len(g), 121)
        self.assertEqual((g[0], g[-1]), (0, 12_000_000_000))
        self.assertEqual(cw.measure_freq(0), 10_000_000)
        self.assertEqual(cw.measure_freq(1_500_000_000), 1_500_000_000)

    def test_cells(self):
        self.assertEqual(cw.cells_of(1_000_000_000), [(0, 10), (1, 0)])
        self.assertEqual(cw.cells_of(12_000_000_000), [(11, 10)])
        self.assertEqual(cw.cells_of(500_000_000), [(0, 5)])
        # 境目は次の band の値（換算と同じ）。12000MHz は最後の欄
        self.assertEqual(cw.point_cell(1_000_000_000), (1, 0))
        self.assertEqual(cw.point_cell(12_000_000_000), (11, 10))


class CorrectionTest(unittest.TestCase):
    def test_measured_point_reads_sg_level_after_conversion(self):
        # 補正なしで −13.5dBm と読んだ点に、SG −10dBm・ケーブル 1dB で校正すると、換算後は −11dBm（SG − 損失）
        total = cw.correction_from_peak(-10.0, 1.0, -13.5)
        self.assertAlmostEqual(total, 2.5)
        cal = cw.build(None, model="gigast-v5", serial="S", created_at=AT, source=cw_source(),
                       totals={1_500_000_000: total})
        self.assertAlmostEqual(-13.5 + applied(cal, 1_500_000_000), -11.0, places=6)

    def test_level_point_and_unmeasured_points_keep_their_correction(self):
        base = legacy_cal()
        before = {f: applied(base, f) for f in (0, 700_000_000, 999_000_000, 1_000_000_000, 5_500_000_000)}
        cal = cw.build(base, model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(),
                       totals={500_000_000: 4.25, 1_500_000_000: -3.0})
        # 500MHz の点が全体のレベル（その点の差は 0）
        self.assertAlmostEqual(cal.level_offset_db, 4.25)
        self.assertEqual(cal.bands[0].values[5], 0.0)
        self.assertAlmostEqual(applied(cal, 500_000_000), 4.25)
        self.assertAlmostEqual(applied(cal, 1_500_000_000), -3.0)
        # 測っていない点（境目の両側を含む）の当てる補正は変わらない
        for f, v in before.items():
            self.assertAlmostEqual(applied(cal, f), v, places=6, msg=f)

    def test_boundary_point_sets_both_cells(self):
        cal = cw.build(legacy_cal(), model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(),
                       totals={1_000_000_000: 7.0})
        tg = cw.totals_grid(cal)
        self.assertAlmostEqual(tg[0, 10], 7.0)
        self.assertAlmostEqual(tg[1, 0], 7.0)

    def test_no_previous_calibration(self):
        cal = cw.build(None, model="gigast-v5", serial="S", created_at=AT, source=cw_source(), totals={0: 1.0})
        self.assertEqual(cal.level_offset_db, 0.0)  # 500MHz を測っていない → 前の値（補正なし）のまま
        self.assertEqual(cal.rbw_offset_db, {180_000: 0.0})  # 測っていない RBW の補正は作らない
        self.assertIsNone(cal.nf)
        self.assertEqual(cal.b3_basis, 1)

    def test_rbw_offset_direction(self):
        peaks = {180_000: -14.2, 30_000: -14.7, 15_000: -15.9}
        off = cw.rbw_offsets(peaks)
        for r in (30_000, 15_000):
            self.assertAlmostEqual(peaks[r] + off[r], peaks[180_000])  # 足すと 180kHz の読みになる

    def test_nf_amp(self):
        raw = cw.nf_amp_raw(6.5)
        self.assertAlmostEqual(raw, 200 * math.log10(6.0 / 6.5))
        self.assertAlmostEqual(6.5 * 10 ** (raw / 200), 6.0)  # 倍率を掛けると差が 6.0dB（旧アプリと同じ意味）
        self.assertAlmostEqual(cw.nf_amp_raw(6.0), 0.0)
        with self.assertRaises(ValueError):
            cw.nf_amp_raw(0.0)


def cw_source():
    from pygigast.core.level.calibration import CalibrationSource

    return CalibrationSource(kind=cw.SOURCE_KIND)


class SessionTest(unittest.TestCase):
    def session(self, base=None, kind=cw.KIND_FREQ, measured=None):
        return cw.CalSession(kind, "gigast-v5", "SIM00001", base, "a.json" if base else None, measured)

    def test_point_flow_and_advance(self):
        s = self.session()
        s.select(5)
        self.assertFalse(s.point_result(5, -11.8, AT))  # 前の校正が無いので確かめない
        self.assertEqual(s.points[5].status, cw.POINT_MEASURED)
        self.assertAlmostEqual(s.points[5].total_db, 1.8)
        self.assertEqual(s.current, 6)  # 次の点へ
        s.skip()
        self.assertEqual(s.points[6].status, cw.POINT_SKIPPED)
        self.assertEqual(s.current, 7)
        s.select(120)
        s.skip()
        self.assertEqual(s.current, 0)  # 後ろに無ければ前から（測った・飛ばした点は飛ばす）
        self.assertEqual(s.unsaved(), 1)
        self.assertTrue(s.savable())

    def test_big_difference_waits_for_confirmation(self):
        s = self.session(base=legacy_cal())
        before = cw.total_at(s.base, 500_000_000)
        s.select(5)
        self.assertTrue(s.point_result(5, -10.0 - before - 7.0, AT))  # 補正が前より 7dB 大きい
        self.assertIsNotNone(s.pending)
        self.assertAlmostEqual(s.pending["diff_db"], 7.0)
        self.assertEqual(s.points[5].status, cw.POINT_NONE)
        self.assertFalse(s.savable())
        s.confirm(False)  # 入れない: 前の値のまま
        self.assertIsNone(s.pending)
        self.assertEqual(s.points[5].status, cw.POINT_NONE)
        self.assertEqual(s.current, 5)
        self.assertTrue(s.point_result(5, -10.0 - before - 7.0, AT))
        s.confirm(True)
        self.assertEqual(s.points[5].status, cw.POINT_MEASURED)
        # 6dB 以内なら確かめない
        s.select(15)
        self.assertFalse(s.point_result(15, -10.0 - cw.total_at(s.base, 1_500_000_000) + 5.9, AT))

    def test_candidate_records_before_and_after(self):
        base = legacy_cal()
        s = self.session(base=base)
        s.sg_level_dbm, s.cable_loss_db = -20.0, 2.0
        s.select(15)
        s.point_result(15, -24.0 - cw.total_at(base, 1_500_000_000) + 0.0 + 2.0, AT)
        s.select(16)
        s.skip()
        cand = s.candidate(AT)
        self.assertEqual(cand.source.kind, cw.SOURCE_KIND)
        w = cand.work
        self.assertEqual(w["kind"], cw.KIND_FREQ)
        self.assertEqual(w["previous"], {"file": "a.json", "created_at": AT, "source": "legacy_file"})
        self.assertEqual([m["item"] for m in w["measured"]], ["freq:1500000000"])
        m = w["measured"][0]
        self.assertAlmostEqual(m["before"], round(cw.total_at(base, 1_500_000_000), 2))
        self.assertEqual(m["sg_level_dbm"], -20.0)
        self.assertEqual(m["cable_loss_db"], 2.0)
        self.assertEqual(w["skipped"], [1_600_000_000])
        # JSON で保存して読み戻せる（work を含む）
        back = Calibration.from_dict(cand.to_dict())
        self.assertEqual(back.work, cand.work)
        s.after_save(cand, "b.json")
        self.assertEqual(s.unsaved(), 0)
        self.assertIs(s.base, cand)
        self.assertEqual(s.saved["file"], "b.json")

    def test_rbw_and_nf(self):
        s = self.session(kind=cw.KIND_RBW)
        self.assertFalse(s.savable())
        s.set_rbw([{"freq_hz": 500_000_000, "peaks_dbm": {180_000: -14.0, 30_000: -14.5, 15_000: -15.0}},
                   {"freq_hz": 1_500_000_000, "peaks_dbm": {180_000: -9.0, 30_000: -9.6, 15_000: -9.9}}])
        cand = s.candidate(AT)
        self.assertEqual(cand.rbw_offset_db, {180_000: 0.0, 30_000: 0.5, 15_000: 1.0})  # 500MHz で決める
        s = self.session(kind=cw.KIND_NF, base=legacy_cal())
        s.set_nf(cw.NF_WITH_ATT, 4.0)
        self.assertEqual(s.unsaved(), 1)
        self.assertFalse(s.savable())
        s.set_nf(cw.NF_WITHOUT_ATT, 10.5)
        cand = s.candidate(AT)
        self.assertAlmostEqual(cand.nf.amp_raw, round(200 * math.log10(6.0 / 6.5), 2))
        self.assertEqual(cand.nf.enr_db, 14.2)  # ENR は前の校正の値のまま（測らない）
        s.set_nf(cw.NF_WITHOUT_ATT, 3.0)  # 入れ替えて測った: 差が負
        self.assertIsNone(s.nf_amp())
        self.assertFalse(s.savable())

    def test_nf_without_previous_has_no_enr(self):
        s = self.session(kind=cw.KIND_NF)
        s.set_nf(cw.NF_WITH_ATT, 4.0)
        s.set_nf(cw.NF_WITHOUT_ATT, 10.0)
        cand = s.candidate(AT)
        self.assertIsNone(cand.nf.enr_db)
        self.assertEqual(Calibration.from_dict(cand.to_dict()).nf, cand.nf)

    def test_legacy_warns_against_measured_values(self):
        s = self.session(kind=cw.KIND_LEGACY, measured={"rbw:30000": 0.4, "freq:0": 1.0, "nf_amp": -7.5})
        s.set_legacy(legacy_cal(), "gigast.txt", "0" * 64)
        snap = s.snapshot()
        items = {i["item"]: i for i in snap["items"]}
        self.assertTrue(items["rbw:30000"]["warn"])  # −2 と測った 0.4 は 1dB を超えて違う
        self.assertEqual(items["rbw:30000"]["measured"], 0.4)
        self.assertFalse(items["rbw:15000"]["warn"])  # 測った値が無い
        self.assertFalse(items["nf_amp"]["warn"])  # −4 と −7.5: 倍率で 4% の違い
        p0 = snap["points"][0]
        legacy0 = cw.total_at(legacy_cal(), 0)
        self.assertEqual(p0["warn"], abs(legacy0 - 1.0) > 6.0)
        cand = s.candidate(AT)
        self.assertEqual(cand.source.kind, cw.SOURCE_LEGACY)
        self.assertEqual(cand.source.file_name, "gigast.txt")
        self.assertEqual(cand.work["kind"], cw.KIND_LEGACY)

    def test_measured_items_newest_first_and_only_from_work(self):
        a = cw.build(None, model="m", serial="S", created_at=AT, source=cw_source(), totals={0: 1.0},
                     work={"measured": [{"item": "freq:0", "after": 1.0}, {"item": "rbw:30000", "after": 0.3}]})
        b = cw.build(None, model="m", serial="S", created_at=AT, source=cw_source(), totals={0: 2.0},
                     work={"measured": [{"item": "freq:0", "after": 2.0}]})
        lg = legacy_cal()
        self.assertEqual(cw.measured_items([a, b, lg]), {"freq:0": 2.0, "rbw:30000": 0.3})

    def test_set_kind_clears_work(self):
        s = self.session()
        s.point_result(0, -11.0, AT)
        s.set_kind(cw.KIND_RBW)
        self.assertEqual(s.unsaved(), 0)
        self.assertEqual(s.kind, cw.KIND_RBW)
        with self.assertRaises(ValueError):
            s.set_kind("x")

    def test_snapshot_shape(self):
        s = self.session(base=legacy_cal())
        snap = s.snapshot(task_id="t1", device_reason=None)
        self.assertEqual(len(snap["points"]), 121)
        self.assertEqual([i["item"] for i in snap["items"]], ["level"])
        self.assertEqual(snap["task_id"], "t1")
        self.assertEqual(snap["points"][0]["measure_hz"], 10_000_000)
        self.assertEqual(snap["base"]["source"]["kind"], "legacy_file")
        # RBW・NF は点の一覧を送らない（中身が変わらない）
        s.set_kind(cw.KIND_RBW)
        self.assertEqual(s.snapshot()["points"], [])


if __name__ == "__main__":
    unittest.main()
