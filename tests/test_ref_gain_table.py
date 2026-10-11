"""基準と PlutoSDR の受信の利得の補正の表（設計Q §7.6）。

基準の区間は取ったときに当たっていた補正の表（表の作成日時。無ければ None）を覚え、当てるときはいまの表と同じ区間
だけを使う。残らなければ当てずに理由 gain_correction_mismatch を返す（RBW・イメージ除去の違いと同じ扱い: 設計E §6.3）。
- 計算: 区間に表を覚える・違う表は当てない・同じ表は当てる・上書き合成は表ごと・オープンとショートで表が違えば作れない
- 保存: 表を書いて読む。表を書いていない前のファイルは None（表なし）として読み、表なしの測定に当たる
- 本体（libiio の偽物の PlutoSDR）: 表なしで取った基準 → 表を入れると当てない／表 A で取り直すと当たる／表 B に替えると
  当てない。記録には表を書き、再生でも同じ表で取った区間に当てる。GigaSt は変わらない
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL
from pygigast.core.level import pluto_gain as pg
from pygigast.core.normalize import (
    ReferenceBuildError,
    load_reference,
    new_reference,
    s11_segment_from_frames,
    save_reference,
    segment_from_frames,
)
from pygigast.core.normalize import apply as ap
from pygigast.core.normalize.reference import PROFILE_GAIN_TABLE
from pygigast.core.storage.h5file import H5Record
from pygigast.core.types import Mode
from tests.test_normalize import CONN, MHZ, PROFILE, SERIAL, apply, db_to_w, linear_db, make_frame
from tests.test_pluto_gain import InstCase

A = "2026-10-12T10:00:00+09:00"
B = "2026-10-12T11:00:00+09:00"


def frame_with(table, start=100 * MHZ, step=MHZ, points=5, mode=Mode.TG, **kw):
    f = make_frame(start, step, points, db_to_w(linear_db(np.arange(points) * step + start)), mode=mode, **kw)
    if table is not None:
        f.profile[PROFILE_GAIN_TABLE] = table
    return f


def ref_with(table, **kw):
    ref = new_reference("r", PROFILE, SERIAL)
    ref.merge(segment_from_frames([frame_with(table, **kw)], connected_at=CONN, taken_at=2000.0))
    return ref


def apply_with(frame, ref, table):
    return ap.apply_reference(frame, ref, serial=SERIAL, connected_at=CONN, now=3000.0, gain_table=table)


class TestApplyGainTable(unittest.TestCase):
    def test_segment_remembers_table(self):
        self.assertIsNone(ref_with(None).segments[0].gain_table)
        self.assertEqual(ref_with(A).segments[0].gain_table, A)

    def test_taken_without_table_then_table_set(self):
        ref = ref_with(None)
        r = apply_with(frame_with(A), ref, A)
        self.assertFalse(r.applied)
        self.assertEqual(r.reason, ap.GAIN_CORRECTION_MISMATCH)
        self.assertEqual(r.reason, "gain_correction_mismatch")
        self.assertEqual(r.detail["reference_gain_table"], [""])

    def test_same_table_applies_other_table_does_not(self):
        ref = ref_with(A)
        self.assertTrue(apply_with(frame_with(A), ref, A).applied)
        r = apply_with(frame_with(B), ref, B)
        self.assertFalse(r.applied)
        self.assertEqual(r.reason, ap.GAIN_CORRECTION_MISMATCH)
        self.assertEqual(r.detail["reference_gain_table"], [A])
        # 表を外した（表なし）の測定にも当てない
        self.assertEqual(apply_with(frame_with(None), ref, None).reason, ap.GAIN_CORRECTION_MISMATCH)

    def test_no_table_both_sides_unchanged(self):
        # GigaSt・RTL-SDR（表を持たない機器）は両方 None。前と同じに当たる（呼び方も前のまま）
        ref = ref_with(None)
        self.assertTrue(apply(frame_with(None), ref).applied)
        self.assertTrue(apply_with(frame_with(None), ref, None).applied)

    def test_rbw_mismatch_comes_first(self):
        # RBW も表も違うときは、前からの理由（RBW）のまま
        ref = ref_with(A)
        self.assertEqual(apply_with(frame_with(B, rbw=30_000), ref, B).reason, ap.RBW_MISMATCH)

    def test_merge_keeps_tables_apart(self):
        # 表 B で取り直しても、表 A の区間は表 A の測定のために残る（同じ条件だけを上書きする: 設計E §6.2）
        ref = ref_with(A)
        ref.merge(segment_from_frames([frame_with(B)], connected_at=CONN, taken_at=2100.0))
        self.assertEqual(sorted(s.gain_table for s in ref.segments), [A, B])
        self.assertTrue(apply_with(frame_with(A), ref, A).applied)
        self.assertTrue(apply_with(frame_with(B), ref, B).applied)
        ref.merge(segment_from_frames([frame_with(B)], connected_at=CONN, taken_at=2200.0))
        self.assertEqual(sorted(s.gain_table for s in ref.segments), [A, B])

    def test_frames_with_different_tables_are_not_averaged(self):
        with self.assertRaises(ReferenceBuildError):
            segment_from_frames([frame_with(A), frame_with(B)], connected_at=CONN, taken_at=1.0)

    def test_s11_open_and_short_need_the_same_table(self):
        o, s = frame_with(A, mode=Mode.S11), frame_with(A, mode=Mode.S11)
        seg = s11_segment_from_frames([o], [s], connected_at=CONN, taken_at=1.0)
        self.assertEqual(seg.gain_table, A)
        with self.assertRaises(ReferenceBuildError):
            s11_segment_from_frames([o], [frame_with(B, mode=Mode.S11)], connected_at=CONN, taken_at=1.0)


class TestStoreGainTable(unittest.TestCase):
    def test_round_trip(self):
        ref = ref_with(A)
        ref.merge(segment_from_frames([frame_with(None, start=500 * MHZ)], connected_at=CONN, taken_at=2100.0))
        with tempfile.TemporaryDirectory() as d:
            back = load_reference(save_reference(d, ref))
        self.assertEqual([s.gain_table for s in back.segments], [A, None])
        self.assertTrue(apply_with(frame_with(A), back, A).applied)

    def test_old_file_without_field_reads_as_no_table(self):
        import h5py

        ref = ref_with(A)
        with tempfile.TemporaryDirectory() as d:
            p = save_reference(d, ref)
            with h5py.File(p, "r+") as f:
                del f["segments/0"].attrs["gain_table"]  # 表を覚える前のアプリが書いたファイル
            back = load_reference(p)
        self.assertIsNone(back.segments[0].gain_table)
        self.assertTrue(apply_with(frame_with(None), back, None).applied)
        self.assertEqual(apply_with(frame_with(A), back, A).reason, ap.GAIN_CORRECTION_MISMATCH)


def table(created_at: str, corr_db: float = 3.0) -> pg.GainCorrection:
    rows = tuple(pg.GainRow(f, 30, (0, 73), (corr_db, corr_db)) for f in (100 * MHZ, 3500 * MHZ))
    return pg.GainCorrection("PlutoSDR", FAKE_SERIAL, created_at, {"kind": pg.SOURCE_KIND}, rows)


class TestInstrumentGainTable(InstCase):
    def tg(self):
        self.connect()
        self.call("set_mode", mode="tg")
        self.call("plutosdr_set", tx_atten_db=-10.0)

    def take(self):
        with self.cv:
            self.events.clear()
        self.call("reference_take", kind="fine")
        p = self.task_end("reference_take")
        self.assertEqual(p["code"], "task_done", p)

    def norm(self) -> dict:
        with self.cv:
            self.events.clear()
        self.call("run", mode="single")
        # 前の1回の「止まった」状態を拾わないよう、この掃引のトレースが来てから止まるのを待つ
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        end = time.monotonic() + 30
        while self.call("get_state")["run"]["state"] != "stopped":
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        return self.call("get_state")["normalization"]

    def test_table_changes_stop_the_reference(self):
        self.tg()
        self.take()
        self.assertIsNone(self.inst._ref.segments[-1].gain_table)
        self.assertTrue(self.norm()["applied"])
        # 表を保存した（表なしで取った基準に、表を当てた測定を割らない）
        self.inst._pluto_gain_cal = table(A)
        n = self.norm()
        self.assertFalse(n["applied"])
        self.assertEqual(n["reason"], ap.GAIN_CORRECTION_MISMATCH)
        # 表 A で取り直すと当たる
        self.take()
        self.assertEqual(self.inst._ref.segments[-1].gain_table, A)
        self.assertTrue(self.norm()["applied"])
        # 表 B に替えると当てない
        self.inst._pluto_gain_cal = table(B, 1.0)
        n = self.norm()
        self.assertFalse(n["applied"])
        self.assertEqual(n["reason"], ap.GAIN_CORRECTION_MISMATCH)
        # 保存した基準のファイルにも表が残る
        back = load_reference(self.inst._ref_path(FAKE_SERIAL, self.inst._ref.ref_id))
        self.assertIn(A, [s.gain_table for s in back.segments])

    def test_record_keeps_the_table_and_playback_applies(self):
        self.tg()
        self.inst._pluto_gain_cal = table(A)
        self.take()
        self.call("record_start")
        self.call("run", mode="continuous")
        self.wait_for(lambda k, p: k == "state" and p["recording"].get("sweeps", 0) >= 2)
        self.call("stop")
        path = self.root / "rec.h5"
        tid = self.call("record_finish", path=str(path))["task_id"]
        n = self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                          and p["detail"].get("task_id") == tid)
        self.assertEqual(n["code"], "task_done", n)
        with H5Record(path) as h:
            self.assertEqual(h.segments[0].quantity, "ratio")
            self.assertEqual(h.segments[0].normalization["gain_table"], A)
        # 表を替えた後でも、記録は記録したときの表で取った区間で再生する
        self.inst._pluto_gain_cal = table(B, 1.0)
        opened = self.call("open", paths=[str(path)])
        self.wait_for(lambda k, p: k == "notice" and p["code"].startswith("task_")
                      and p["detail"].get("task_id") == opened["task_id"])
        st = self.call("get_state")
        self.assertTrue(st["playback"]["active"])
        self.assertEqual(st["playback"]["problems"], [])
        with self.cv:
            self.events.clear()
        self.call("playback_seek", index=0)
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        n = self.call("get_state")["normalization"]
        self.assertTrue(n["applied"], n)


class TestGigaStUnchanged(InstCase):
    def test_gigast_reference_has_no_table(self):
        self.call("connect", backend="simulator", serial="SIM00001")
        self.call("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.call("set_mode", mode="tg")
        # PlutoSDR の表を持っていても GigaSt には当てない（両方 None のまま）
        self.inst._pluto_gain_cal = table(A)
        self.call("reference_take", kind="fine")
        p = self.task_end("reference_take")
        self.assertEqual(p["code"], "task_done", p)
        self.assertIsNone(self.inst._ref.segments[-1].gain_table)
        n = TestInstrumentGainTable.norm(self)
        self.assertTrue(n["applied"], n)


if __name__ == "__main__":
    unittest.main()
