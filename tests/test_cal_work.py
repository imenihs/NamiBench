"""校正作業の計算（設計K §4.2〜§4.5。core/level/cal_work.py）。機器を使わない計算だけを確かめる。

- 121 周波数の格子・0MHz を 10MHz で測る・band の境目は2行（下の帯の欄を 0.5MHz 下、上の帯の欄を 0.5MHz 上で測り、
  その欄だけに入れる: K §4.2.1）
- 補正値 =（SG のレベル − ケーブルの損失）− 補正なしの測定値。換算に当てると SG のレベルに戻る（符号の取り違えを防ぐ）
- 500MHz を全体のレベルにし、測っていない点の当てる補正は変わらない（やり直した点だけ差し替える）
- RBW の補正・NF の増幅度の向き
- 作業の値: 差が大きい点は確かめ待ち・飛ばす・次の点・保存していない数・保存する中身（前後の値の記録）
- 旧ファイルの取り込みは、校正作業で測った値と比べて大きく違う所に印（設計D §5.6）
"""
from __future__ import annotations

import dataclasses
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

    def test_rows_split_band_edges(self):
        rows = cw.grid_rows()
        self.assertEqual(len(rows), 121 + 11)  # 境目 1000〜11000MHz の 11 か所が2行
        by = {(r.freq_hz, r.side): r for r in rows}
        for b in range(1, 12):
            f = b * 1_000_000_000
            lo, up = by[(f, cw.SIDE_LOWER)], by[(f, cw.SIDE_UPPER)]
            self.assertEqual((lo.cell, lo.measure_hz), ((b - 1, 10), f - 500_000))
            self.assertEqual((up.cell, up.measure_hz), ((b, 0), f + 500_000))
            self.assertEqual(rows.index(lo) + 1, rows.index(up))  # 下の帯・上の帯の順に並ぶ
            self.assertNotIn((f, None), by)
        # 境目でない点・0MHz・12000MHz は1行のまま
        self.assertEqual((by[(0, None)].cell, by[(0, None)].measure_hz), ((0, 0), 10_000_000))
        self.assertEqual((by[(500_000_000, None)].cell, by[(500_000_000, None)].measure_hz), ((0, 5), 500_000_000))
        self.assertEqual((by[(12_000_000_000, None)].cell, by[(12_000_000_000, None)].measure_hz),
                         ((11, 10), 12_000_000_000))
        # 表の欄はちょうど1回ずつ出る
        self.assertEqual(sorted(r.cell for r in rows), [(b, k) for b in range(12) for k in range(11)])
        self.assertEqual(len({r.item for r in rows}), len(rows))
        self.assertEqual(cw.item_cell("freq:1000000000:lower"), (0, 10))
        self.assertEqual(cw.item_cell("freq:1000000000:upper"), (1, 0))

    def test_cells(self):
        # 境目は次の band の値（換算と同じ）。12000MHz は最後の欄
        self.assertEqual(cw.point_cell(1_000_000_000), (1, 0))
        self.assertEqual(cw.point_cell(12_000_000_000), (11, 10))


class CorrectionTest(unittest.TestCase):
    def test_measured_point_reads_sg_level_after_conversion(self):
        # 補正なしで −13.5dBm と読んだ点に、SG −10dBm・ケーブル 1dB で校正すると、換算後は −11dBm（SG − 損失）
        total = cw.correction_from_peak(-10.0, 1.0, -13.5)
        self.assertAlmostEqual(total, 2.5)
        cal = cw.build(None, model="gigast-v5", serial="S", created_at=AT, source=cw_source(),
                       totals={cw.point_cell(1_500_000_000): total})
        self.assertAlmostEqual(-13.5 + applied(cal, 1_500_000_000), -11.0, places=6)

    def test_level_point_and_unmeasured_points_keep_their_correction(self):
        base = legacy_cal()
        before = {f: applied(base, f) for f in (0, 700_000_000, 999_000_000, 1_000_000_000, 5_500_000_000)}
        cal = cw.build(base, model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(),
                       totals={(0, 5): 4.25, (1, 5): -3.0})
        # 500MHz の点が全体のレベル（その点の差は 0）
        self.assertAlmostEqual(cal.level_offset_db, 4.25)
        self.assertEqual(cal.bands[0].values[5], 0.0)
        self.assertAlmostEqual(applied(cal, 500_000_000), 4.25)
        self.assertAlmostEqual(applied(cal, 1_500_000_000), -3.0)
        # 測っていない点（境目の両側を含む）の当てる補正は変わらない
        for f, v in before.items():
            self.assertAlmostEqual(applied(cal, f), v, places=6, msg=f)

    def test_level_stays_when_500mhz_not_measured(self):
        # 全体のレベルは表の外（TG・S11・表の上端より上）にも効く。500MHz を測らない校正（RBW だけ・NF だけ・
        # 周波数特性の一部だけ）で全体のレベルと各欄を書き換えない。旧校正は 500MHz の欄が 0 でないことがある
        src = legacy_cal()
        b0 = list(src.bands[0].values)
        b0[5] = -2.0
        src = dataclasses.replace(src, level_offset_db=1.0,
                                  bands=(dataclasses.replace(src.bands[0], values=tuple(b0)),) + src.bands[1:])
        for kw in ({"rbw": {30_000: 0.4, 15_000: 1.6}}, {"nf_amp": -6.0}, {"totals": {(1, 5): -3.0}}):
            cal = cw.build(src, model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(), **kw)
            self.assertEqual(cal.level_offset_db, 1.0, msg=str(kw))
            measured = [(1, 5)] if "totals" in kw else []
            for i, band in enumerate(cal.bands):
                for k, v in enumerate(band.values):
                    if (i, k) not in measured:
                        self.assertEqual(v, src.bands[i].values[k], msg=f"{kw} {i} {k}")

    def test_boundary_cells_are_written_separately(self):
        base = legacy_cal()
        bg = cw.totals_grid(base)
        cal = cw.build(base, model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(),
                       totals={(0, 10): 7.0})
        tg = cw.totals_grid(cal)
        self.assertAlmostEqual(tg[0, 10], 7.0)
        self.assertAlmostEqual(tg[1, 0], bg[1, 0], places=6)  # 上の帯の欄は前の値のまま

    def test_no_previous_calibration(self):
        cal = cw.build(None, model="gigast-v5", serial="S", created_at=AT, source=cw_source(), totals={(0, 0): 1.0})
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


def row_index(s, freq_hz: int, side=None) -> int:
    """点の一覧の行の番号（境目は2行あるので、周波数と帯の側で探す）。"""
    return next(i for i, p in enumerate(s.points) if p.freq_hz == freq_hz and p.row.side == side)


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
        s.select(len(s.points) - 1)
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
        i15 = row_index(s, 1_500_000_000)
        s.select(i15)
        self.assertFalse(s.point_result(i15, -10.0 - cw.total_at(s.base, 1_500_000_000) + 5.9, AT))

    def test_candidate_records_before_and_after(self):
        base = legacy_cal()
        s = self.session(base=base)
        s.sg_level_dbm, s.cable_loss_db = -20.0, 2.0
        i15 = row_index(s, 1_500_000_000)
        s.select(i15)
        s.point_result(i15, -24.0 - cw.total_at(base, 1_500_000_000) + 0.0 + 2.0, AT)
        s.select(row_index(s, 1_600_000_000))
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
        self.assertEqual(w["skipped"], ["freq:1600000000"])
        self.assertEqual(m["measure_hz"], 1_500_000_000)
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
        a = cw.build(None, model="m", serial="S", created_at=AT, source=cw_source(), totals={(0, 0): 1.0},
                     work={"measured": [{"item": "freq:0", "after": 1.0}, {"item": "rbw:30000", "after": 0.3}]})
        b = cw.build(None, model="m", serial="S", created_at=AT, source=cw_source(), totals={(0, 0): 2.0},
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
        self.assertEqual(len(snap["points"]), 132)
        self.assertEqual([i["item"] for i in snap["items"]], ["level"])
        self.assertEqual(snap["task_id"], "t1")
        self.assertEqual(snap["points"][0]["measure_hz"], 10_000_000)
        self.assertEqual(snap["base"]["source"]["kind"], "legacy_file")
        # RBW・NF は点の一覧を送らない（中身が変わらない）
        s.set_kind(cw.KIND_RBW)
        self.assertEqual(s.snapshot()["points"], [])


class BandEdgeTest(unittest.TestCase):
    """band の境目の2行（K §4.2.1）: 下の帯の欄は境目の 0.5MHz 下、上の帯の欄は 0.5MHz 上で測り、その欄だけに入れる。"""

    SG, LOSS = -10.0, 1.0

    def setUp(self):
        # 本当の当てる補正: band ごとに違う値と、band の中のゆるい傾き。境目の2つの欄は大きく違う
        rng = np.random.default_rng(3)
        totals = {}
        for b in range(12):
            off = float(rng.uniform(-6.0, 6.0))
            for k in range(11):
                totals[(b, k)] = off + 0.05 * k
        self.truth = cw.build(None, model="gigast-v5", serial="SIM00001", created_at=AT, source=cw_source(),
                              totals=totals)
        self.tg = cw.totals_grid(self.truth)

    def reading(self, f: int) -> float:
        """補正なしの読み（機器の代わり）。境目ちょうどのトーンの山は下の帯の側に出る（実機: K §4.2.1）。"""
        b, k = cw.point_cell(f)
        if k == 0 and b > 0 and f == b * 1_000_000_000:
            true = float(self.tg[b - 1, 10])
        else:
            true = applied(self.truth, f)
        return self.SG - self.LOSS - true

    def session(self, base=None):
        s = cw.CalSession(cw.KIND_FREQ, "gigast-v5", "SIM00001", base, "a.json" if base else None)
        s.sg_level_dbm, s.cable_loss_db = self.SG, self.LOSS
        return s

    def test_lower_row_writes_only_lower_cell_and_upper_only_upper(self):
        base = legacy_cal()
        bg = cw.totals_grid(base)
        s = self.session(base)
        lo, up = row_index(s, 2_000_000_000, cw.SIDE_LOWER), row_index(s, 2_000_000_000, cw.SIDE_UPPER)
        self.assertEqual(s.points[lo].measure_hz, 1_999_500_000)
        self.assertEqual(s.points[up].measure_hz, 2_000_500_000)
        # 下の帯の行: 前の値から 2dB（確かめない大きさ）
        s.select(lo)
        self.assertFalse(s.point_result(lo, self.SG - self.LOSS - (bg[1, 10] + 2.0), AT))
        tg = cw.totals_grid(s.candidate(AT))
        self.assertAlmostEqual(tg[1, 10], round(bg[1, 10] + 2.0, 2), places=6)
        self.assertAlmostEqual(tg[2, 0], bg[2, 0], places=6)
        self.assertEqual(s.current, up)  # 次は同じ境目の上の帯の行
        # 上の帯の行: 前の値から −3dB
        self.assertFalse(s.point_result(up, self.SG - self.LOSS - (bg[2, 0] - 3.0), AT))
        tg = cw.totals_grid(s.candidate(AT))
        self.assertAlmostEqual(tg[1, 10], round(bg[1, 10] + 2.0, 2), places=6)
        self.assertAlmostEqual(tg[2, 0], round(bg[2, 0] - 3.0, 2), places=6)
        # ほかの欄と全体のレベルは変わらない
        for b in range(12):
            for k in range(11):
                if (b, k) not in ((1, 10), (2, 0)):
                    self.assertAlmostEqual(tg[b, k], bg[b, k], places=6, msg=(b, k))
        cand = s.candidate(AT)
        self.assertEqual(cand.level_offset_db, base.level_offset_db)
        self.assertEqual([m["item"] for m in cand.work["measured"]],
                         ["freq:2000000000:lower", "freq:2000000000:upper"])
        self.assertEqual([m["measure_hz"] for m in cand.work["measured"]], [1_999_500_000, 2_000_500_000])
        # 一覧の「前」「新」はその行の欄の値
        snap = s.snapshot()
        self.assertEqual(snap["points"][lo]["side"], cw.SIDE_LOWER)
        self.assertAlmostEqual(snap["points"][lo]["before_db"], bg[1, 10])
        self.assertAlmostEqual(snap["points"][up]["before_db"], bg[2, 0])
        self.assertAlmostEqual(snap["points"][up]["after_db"], round(bg[2, 0] - 3.0, 2), places=6)

    def test_old_edge_error_is_gone(self):
        # 前のやり方: 境目ちょうどで1回測り、2つの欄の両方に入れる → 上の帯の欄が下の帯の値になる
        for b in (1, 2, 4):
            f = b * 1_000_000_000
            old = cw.correction_from_peak(self.SG, self.LOSS, self.reading(f))
            self.assertAlmostEqual(old, self.tg[b - 1, 10], places=6)
            self.assertGreater(abs(old - self.tg[b, 0]), 1.0, msg=f)  # 上の帯の欄は大きく外れる
        # いまのやり方: 全部の行を、その行の SG の周波数で測る
        s = self.session()
        for i, p in enumerate(s.points):
            s.select(i)
            self.assertFalse(s.point_result(i, self.reading(p.measure_hz), AT))
        cand = s.candidate(AT)
        tg = cw.totals_grid(cand)
        for b in range(1, 12):
            # 境目の2つの欄が、それぞれ本当の値（0.5MHz ずれた所の読みなので、band の中の傾きの分の 0.01dB 以内）
            self.assertAlmostEqual(tg[b - 1, 10], self.tg[b - 1, 10], delta=0.011, msg=b)
            self.assertAlmostEqual(tg[b, 0], self.tg[b, 0], delta=0.011, msg=b)
            # 換算に当てると、境目のすぐ上・すぐ下で SG のレベルを読み戻す
            for f in (b * 1_000_000_000 - 500_000, b * 1_000_000_000 + 500_000):
                self.assertAlmostEqual(self.reading(f) + applied(cand, f), self.SG - self.LOSS, delta=0.02, msg=f)

    def test_skip_and_confirm_per_row(self):
        base = legacy_cal()
        bg = cw.totals_grid(base)
        s = self.session(base)
        lo, up = row_index(s, 3_000_000_000, cw.SIDE_LOWER), row_index(s, 3_000_000_000, cw.SIDE_UPPER)
        # 下の帯の行が前の値と 7dB 違う → その行だけ確かめ待ち
        s.select(lo)
        self.assertTrue(s.point_result(lo, self.SG - self.LOSS - (bg[2, 10] + 7.0), AT))
        self.assertEqual(s.pending["index"], lo)
        self.assertAlmostEqual(s.pending["before_db"], bg[2, 10])
        self.assertEqual(s.snapshot()["pending"]["index"], lo)
        s.confirm(False)
        self.assertEqual((s.points[lo].status, s.points[up].status), (cw.POINT_NONE, cw.POINT_NONE))
        # 下の帯の行を飛ばす → 次は上の帯の行。上の帯の行は飛ばされていない
        s.skip()
        self.assertEqual((s.points[lo].status, s.points[up].status), (cw.POINT_SKIPPED, cw.POINT_NONE))
        self.assertEqual(s.current, up)
        # 上の帯の行の差は、上の帯の欄の値と比べる（下の帯の欄と比べない）
        self.assertFalse(s.point_result(up, self.SG - self.LOSS - (bg[3, 0] + 5.0), AT))
        s.select(up)
        self.assertTrue(s.point_result(up, self.SG - self.LOSS - (bg[3, 0] + 6.5), AT))
        s.confirm(True)
        self.assertEqual(s.points[up].status, cw.POINT_MEASURED)
        cand = s.candidate(AT)
        self.assertEqual(cand.work["skipped"], ["freq:3000000000:lower"])
        tg = cw.totals_grid(cand)
        self.assertAlmostEqual(tg[2, 10], bg[2, 10], places=6)
        self.assertAlmostEqual(tg[3, 0], round(bg[3, 0] + 6.5, 2), places=6)

    def test_level_point_unchanged(self):
        # 500MHz の行は1行・500MHz で測り、全体のレベルになる。境目を測っても全体のレベルは変わらない
        base = legacy_cal()
        s = self.session(base)
        i = row_index(s, cw.LEVEL_POINT_HZ)
        self.assertEqual((s.points[i].measure_hz, s.points[i].row.cell), (500_000_000, (0, 5)))
        e = row_index(s, 1_000_000_000, cw.SIDE_UPPER)
        s.point_result(e, self.SG - self.LOSS - cw.cell_total(base, (1, 0)), AT)
        self.assertEqual(s.candidate(AT).level_offset_db, base.level_offset_db)
        s.point_result(i, self.SG - self.LOSS - 4.25, AT)
        if s.pending:
            s.confirm(True)  # 前の値と大きく違う: 入れる
        self.assertAlmostEqual(s.candidate(AT).level_offset_db, 4.25)

    def test_saved_table_loads(self):
        s = self.session(legacy_cal())
        for side in (cw.SIDE_LOWER, cw.SIDE_UPPER):
            i = row_index(s, 5_000_000_000, side)
            s.point_result(i, self.reading(s.points[i].measure_hz), AT)
            if s.pending:
                s.confirm(True)
        cand = s.candidate(AT)
        back = Calibration.from_dict(cand.to_dict())
        self.assertEqual(back.bands, cand.bands)
        self.assertEqual(back.work, cand.work)
        self.assertEqual(len(back.bands), 12)
        self.assertTrue(all(len(b.values) == 11 for b in back.bands))
        LevelConverter(load_profile(GIGAST_PROFILE_PATH), back)  # 換算に使える
        # 保存した記録は、境目の行ごとに旧ファイルの比べに使える
        m = cw.measured_items([back])
        self.assertEqual(set(m), {"freq:5000000000:lower", "freq:5000000000:upper"})
        lg = cw.CalSession(cw.KIND_LEGACY, "gigast-v5", "SIM00001", back, "b.json", m)
        lg.set_legacy(legacy_cal(), "gigast.txt", "0" * 64)
        rows = {(p["freq_hz"], p["side"]): p for p in lg.snapshot()["points"]}
        self.assertEqual(rows[(5_000_000_000, cw.SIDE_LOWER)]["measured"], m["freq:5000000000:lower"])
        self.assertEqual(rows[(5_000_000_000, cw.SIDE_UPPER)]["measured"], m["freq:5000000000:upper"])


if __name__ == "__main__":
    unittest.main()
