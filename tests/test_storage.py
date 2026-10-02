"""設計H（記録・保存・読込・書き出し）の試験。すべて一時フォルダの中で行う。"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np

from pygigast.core.storage import Cancelled, StorageError, WriterThread, adopted_from_dict, adopted_to_dict
from pygigast.core.storage.export import (
    LAYOUT_LONG,
    convert_unit,
    export_record_csv,
    export_touchstone,
    export_traces_csv,
)
from pygigast.core.storage.h5file import (
    LAYOUT_IMAGE_CANCEL,
    LAYOUT_NF,
    OVERVIEW_LEVELS,
    FileHeader,
    H5Record,
    MemorySegment,
    TraceData,
    extract_h5,
    save_snapshot,
    segment_meta_for,
    split_raw,
    write_h5,
)
from pygigast.core.storage.recording import (
    Recorder,
    RecordingReader,
    convert_to_h5,
    find_unfinished,
    new_recording_path,
)
from pygigast.core.types import AdoptedSweep, Adjustment, FreqAxis, Mode, Quantity, RawSweep, ValueKind

ROOT = Path(__file__).resolve().parent.parent
MHZ = 1_000_000


def adopted(start=100 * MHZ, step=1 * MHZ, points=11, stop=None, image_reject="off", mode=Mode.SA) -> AdoptedSweep:
    stop = start + step * (points - 1) if stop is None else stop
    return AdoptedSweep(
        start_hz=start,
        stop_hz=stop,
        axis=FreqAxis(start, step, points),
        rbw_hz=180_000,
        rbw_preference_hz=180_000,
        mode=mode,
        sg_hz=None,
        image_reject=image_reject,
        preroll_points=3,
        transient_head_points=0,
        estimated_sweep_s=0.1,
        adjustments=(Adjustment("start_hz", 100_000_050, 100_000_000, "rounded_to_unit"),),
    )


def pattern(i: int, n: int) -> np.ndarray:
    """掃引 i の決まった生の値（読み戻して比べる）。"""
    return ((np.arange(n, dtype=np.int64) * 7 + i * 13) % 2000 + 100).astype(np.uint16)


def raw_sweep(i: int, a: AdoptedSweep, t0: float = 1_780_000_000.0, legacy_two_pass: bool = False) -> RawSweep:
    """legacy_two_pass は前の2回の形のイメージキャンセル（前の版のファイルから読み戻した RawSweep の形）。"""
    n = a.axis.points
    if legacy_two_pass:
        b1 = pattern(i, n)
        b0 = pattern(i + 1000, n)
        return RawSweep(a, np.minimum(b0, b1), "gigast_ad_u16", t0 + i, 0.1, raw_b3_0=b0, raw_b3_1=b1)
    return RawSweep(a, pattern(i, n), "gigast_ad_u16", t0 + i, 0.1)


def header() -> FileHeader:
    return FileHeader(
        device_model="gigast-v5",
        device_serial="TEST0001",
        profile={"model": "gigast-v5", "value_kind": "real"},
        calibration={"format": "pygigast-calibration", "version": 1, "level_offset_db": 1.0},
        note="メモ",
        created_at="2026-10-01T12:00:00+09:00",
        timezone="+09:00",
    )


def trace(number=1, values=None, kind=ValueKind.REAL, q=Quantity.POWER, axis=None) -> TraceData:
    axis = axis or FreqAxis(100 * MHZ, 1 * MHZ, 11)
    if values is None:
        values = np.logspace(-12, -6, axis.points)
    return TraceData(
        number=number,
        values=np.asarray(values),
        quantity=q,
        value_kind=kind,
        axis=axis,
        info={"behavior": "max_hold", "visible": True},
        flags={"transient": np.zeros(axis.points, dtype=bool)},
    )


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


# ---------------------------------------------------------------------------


class TestConditionsJson(unittest.TestCase):
    def test_round_trip(self):
        a = adopted()
        d = json.loads(json.dumps(adopted_to_dict(a)))
        self.assertEqual(adopted_from_dict(d), a)

    def test_unknown_version_refused(self):
        d = adopted_to_dict(adopted())
        d["version"] = 99
        with self.assertRaises(StorageError) as cm:
            adopted_from_dict(d)
        self.assertEqual(cm.exception.code, "unsupported_version")

    def test_cut_at_stop(self):
        # Stop が格子に乗らないときは最後の点（Stop の先）を保存しない（設計C §5.3）
        a = adopted(points=11, stop=100 * MHZ + 9 * MHZ + 500_000)
        r1, _ = split_raw(raw_sweep(0, a))
        self.assertEqual(len(r1), 10)


class TestH5(TmpCase):
    def test_snapshot_round_trip(self):
        a = adopted()
        r = raw_sweep(5, a)
        markers = [{"id": "M1", "trace": 1, "frequency_hz": 105 * MHZ}]
        tr1 = trace(1)
        tr2 = trace(2, values=np.linspace(0.1, 1.0, 11) * np.exp(1j * np.linspace(0, 3, 11)), kind=ValueKind.COMPLEX,
                    q=Quantity.RATIO)
        p = save_snapshot(self.dir / "a.h5", header(), r, [tr1, tr2], markers)
        self.assertFalse((self.dir / "a.h5.partial").exists())
        with H5Record(p) as rec:
            self.assertEqual(rec.header.device_serial, "TEST0001")
            self.assertEqual(rec.header.note, "メモ")
            self.assertEqual(rec.header.calibration["level_offset_db"], 1.0)
            self.assertEqual(len(rec.segments), 1)
            s = rec.segments[0]
            self.assertEqual((s.start_hz, s.step_hz, s.points, s.n_sweeps), (100 * MHZ, MHZ, 11, 1))
            b = rec.read(0)
            np.testing.assert_array_equal(b.raw[0], pattern(5, 11))
            self.assertEqual(b.freqs_hz.dtype, np.int64)
            back = rec.raw_sweep(0, 0)
            self.assertEqual(back.adopted.stop_hz, a.stop_hz)
            self.assertEqual(back.adopted.adjustments, a.adjustments)
            trs = rec.traces()
            self.assertEqual([t.number for t in trs], [1, 2])
            np.testing.assert_allclose(trs[0].values, tr1.values, rtol=1e-6)
            self.assertEqual(trs[1].value_kind, ValueKind.COMPLEX)
            np.testing.assert_allclose(trs[1].values, tr2.values, rtol=1e-6)
            self.assertEqual(trs[0].info["behavior"], "max_hold")
            self.assertEqual(trs[0].origin["file"], "a.h5")
            self.assertEqual(rec.markers(), markers)
        with h5py.File(p, "r") as f:
            ds = f["segments/0000/raw"]
            self.assertEqual(ds.dtype, np.uint16)
            self.assertEqual(ds.compression, "gzip")
            self.assertEqual(ds.compression_opts, 1)
            self.assertEqual(f["traces/1/values"].dtype, np.float32)
            self.assertEqual(f["traces/2/values"].dtype, np.complex64)
            self.assertEqual(f.attrs["format"], "pygigast-record")

    def test_large_segment_chunks_overview_and_partial_read(self):
        n, p = 1100, 1200
        a = adopted(start=0, step=100_000, points=p)
        t = 1_780_000_000.0 + np.arange(n, dtype=np.float64)
        raw = np.stack([pattern(i, p) for i in range(n)])
        meta = segment_meta_for(raw_sweep(0, a))
        calls = []
        path = write_h5(self.dir / "big.h5", header(), [MemorySegment(meta, t, raw)],
                        progress=lambda d, tot, el: calls.append((d, tot, el)))
        self.assertEqual(calls[0][:2], (0, n))
        self.assertEqual(calls[-1][:2], (n, n))
        self.assertTrue(all(c[2] >= 0 for c in calls))
        with h5py.File(path, "r") as f:
            self.assertEqual(f["segments/0000/raw"].chunks, (256, 512))
            for k in OVERVIEW_LEVELS:
                ov = f[f"segments/0000/overview/max_{k}"][()]
                self.assertEqual(ov.shape, (-(-n // k), p))
                # 最後の半端な塊も含めて、k 掃引ごとの最大値
                for r in (0, ov.shape[0] - 1):
                    np.testing.assert_array_equal(ov[r], raw[r * k : (r + 1) * k].max(axis=0))
        with H5Record(path) as rec:
            i0, i1 = rec.point_range(0, 10_050_000, 20_000_000)  # 10.05MHz は点の間 → 次の点から
            self.assertEqual((i0, i1), (101, 201))
            s0, s1 = rec.sweep_range(0, t[500], t[599])
            self.assertEqual((s0, s1), (500, 600))
            b = rec.read_range(0, (t[500], t[599]), (10_050_000, 20_000_000))
            np.testing.assert_array_equal(b.raw, raw[500:600, 101:201])
            self.assertEqual(int(b.freqs_hz[0]), 10_100_000)
            self.assertEqual(rec.choose_overview(0, 2000), 1)
            self.assertEqual(rec.choose_overview(0, 100), 16)
            ov = rec.overview(0, 16, (0, 5))
            self.assertEqual(len(ov.t_start), ov.values.shape[0])

    def test_legacy_two_pass_keeps_both_passes(self):
        # 前の2回の形（読み戻した RawSweep）を保存し直しても、2回とも残る
        a = adopted(image_reject="fast")
        r = raw_sweep(3, a, legacy_two_pass=True)
        p = save_snapshot(self.dir / "ic.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_CANCEL)
            b = rec.read(0)
            np.testing.assert_array_equal(b.raw[0], r.raw_b3_1)
            np.testing.assert_array_equal(b.raw2[0], r.raw_b3_0)
            np.testing.assert_array_equal(b.adopted()[0], r.raw)
            np.testing.assert_array_equal(rec.raw_sweep(0, 0).raw, r.raw)

    def test_nf_raw_links_to_hot(self):
        a = adopted(mode=Mode.NF)
        hot, cold = pattern(1, 11), pattern(2, 11)
        r = RawSweep(a, hot, "gigast_ad_u16", 1.0, 0.1, raw_hot=hot, raw_cold=cold)
        p = save_snapshot(self.dir / "nf.h5", header(), r)
        with h5py.File(p, "r") as f:
            g = f["segments/0000"]
            self.assertIsInstance(g.get("raw", getlink=True), h5py.SoftLink)
            np.testing.assert_array_equal(g["raw_cold"][0], cold)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_NF)
            np.testing.assert_array_equal(rec.read(0).raw[0], hot)

    def test_cancel_leaves_no_file(self):
        n, p = 3000, 600
        a = adopted(start=0, step=100_000, points=p)
        raw = np.stack([pattern(i, p) for i in range(n)])
        meta = segment_meta_for(raw_sweep(0, a))
        ev = threading.Event()

        def prog(done, total, el):
            if done >= 1024:
                ev.set()

        target = self.dir / "c.h5"
        with self.assertRaises(Cancelled):
            write_h5(target, header(), [MemorySegment(meta, np.arange(n, dtype=float), raw)], progress=prog, cancel=ev)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_unknown_file_refused(self):
        p = self.dir / "x.h5"
        with h5py.File(p, "w") as f:
            f.attrs["format"] = "something-else"
        with self.assertRaises(StorageError) as cm:
            H5Record(p)
        self.assertEqual(cm.exception.code, "not_pygigast_file")
        p2 = save_snapshot(self.dir / "v.h5", header(), raw_sweep(0, adopted()))
        with h5py.File(p2, "a") as f:
            f.attrs["version"] = np.int64(99)
        with self.assertRaises(StorageError) as cm:
            H5Record(p2)
        self.assertEqual(cm.exception.code, "unsupported_version")

    def test_extract(self):
        n, p = 300, 50
        a = adopted(start=0, step=MHZ, points=p)
        t = 1000.0 + np.arange(n, dtype=float)
        raw = np.stack([pattern(i, p) for i in range(n)])
        src = write_h5(self.dir / "s.h5", header(), [MemorySegment(segment_meta_for(raw_sweep(0, a)), t, raw)])
        dst = extract_h5(src, self.dir / "cut.h5", t_range=(1100.0, 1149.0), f_range_hz=(10 * MHZ, 19 * MHZ))
        with H5Record(dst) as rec:
            s = rec.segments[0]
            self.assertEqual((s.start_hz, s.points, s.n_sweeps), (10 * MHZ, 10, 50))
            np.testing.assert_array_equal(rec.read(0).raw, raw[100:150, 10:20])
        with self.assertRaises(StorageError) as cm:
            extract_h5(src, self.dir / "none.h5", t_range=(0.0, 1.0))
        self.assertEqual(cm.exception.code, "range_empty")
        self.assertFalse((self.dir / "none.h5").exists())


class TestRecording(TmpCase):
    def test_public_started_at_and_stopped(self):
        before = time.time()
        rec = Recorder(self.dir / "active_props.sqlite", header())
        rec.wait_opened(5)
        self.assertGreaterEqual(rec.started_at, before)
        self.assertLessEqual(rec.started_at, time.time())
        self.assertFalse(rec.stopped)
        rec.stop().result(10)
        self.assertTrue(rec.stopped)
        with self.assertRaises(AttributeError):
            rec.stopped = False  # 読むだけ

    def test_record_segments_and_convert(self):
        path = new_recording_path(self.dir)
        self.assertTrue(path.name.startswith("active_"))
        committed = []
        rec = Recorder(path, header(), on_committed=lambda s: committed.append(s.sweeps_committed))
        rec.wait_opened(5)
        a1 = adopted(points=11)
        a2 = adopted(start=200 * MHZ, points=21)  # 条件が途中で変わる → 新しい区間
        for i in range(10):
            rec.append(raw_sweep(i, a1))
        for i in range(10, 30):
            rec.append(raw_sweep(i, a2))
        for i in range(30, 35):
            rec.append(raw_sweep(i, a2), normalization={"name": "cable A", "acquired_at": "2026-10-01T10:00:00+09:00"})
        stats = rec.stop().result(10)
        self.assertEqual(stats.sweeps_committed, 35)
        self.assertEqual(stats.segments, 3)
        self.assertEqual(committed[-1], 35)
        self.assertIsNone(stats.last_error)

        with RecordingReader(path) as r:
            self.assertEqual([s.n_sweeps for s in r.segments], [10, 20, 5])
            ov = r.overview(1, 4)
            self.assertEqual(ov.values.shape, (5, 21))  # 20 掃引 → 揃った塊は 5 つ
            np.testing.assert_array_equal(ov.values[0], np.stack([pattern(i, 21) for i in range(10, 14)]).max(axis=0))
            b = r.read(1, t_range=(1_780_000_012.0, 1_780_000_014.0), points=(2, 5))
            np.testing.assert_array_equal(b.raw, np.stack([pattern(i, 21)[2:5] for i in (12, 13, 14)]))

        calls = []
        out = convert_to_h5(path, self.dir / "rec.h5", traces=[trace(1)], progress=lambda d, t, e: calls.append((d, t)))
        self.assertEqual(calls[-1], (35, 35))
        self.assertFalse(path.exists())
        self.assertEqual([p.name for p in self.dir.iterdir()], ["rec.h5"])
        with H5Record(out) as h:
            self.assertEqual([s.n_sweeps for s in h.segments], [10, 20, 5])
            self.assertEqual([s.points for s in h.segments], [11, 21, 21])
            self.assertIsNone(h.segments[1].normalization)
            self.assertEqual(h.segments[2].normalization["name"], "cable A")
            np.testing.assert_array_equal(h.read(1).raw[3], pattern(13, 21))
            self.assertEqual(float(h.t_start(2)[0]), 1_780_000_030.0)
            self.assertEqual(len(h.traces()), 1)

    def test_read_from_other_connection_while_recording(self):
        path = self.dir / "active_live.sqlite"
        ev = threading.Event()
        rec = Recorder(path, header(), on_committed=lambda s: ev.set() if s.sweeps_committed >= 5 else None)
        a = adopted()
        for i in range(5):
            rec.append(raw_sweep(i, a))
        self.assertTrue(ev.wait(10))
        # 記録を止めずに、別の接続から確定済みの掃引が読める（WAL）
        with RecordingReader(path) as r:
            self.assertEqual(r.n_sweeps, 5)
            self.assertIsNone(r.stopped_at)
            np.testing.assert_array_equal(r.read(0).raw[4], pattern(4, 11))
            found = find_unfinished(self.dir, exclude=[path])
            self.assertEqual(found, [])
            rec.append(raw_sweep(5, a))
            rec.stop().result(10)
            r.refresh()
            self.assertEqual(r.n_sweeps, 6)

    def test_abandoned_without_stop_is_rescued(self):
        # 閉じずに（stop を呼ばずに）書き手の接続を捨てる = 落ちたときに近い形
        path = self.dir / "active_abandon.sqlite"
        w = WriterThread()
        rec = Recorder(path, header(), writer=w)
        a = adopted()
        for i in range(7):
            rec.append(raw_sweep(i, a))
        w.submit(lambda: None).result(10)  # 待ち行列を空にする
        found = find_unfinished(self.dir)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].n_sweeps, 7)
        self.assertFalse(found[0].stopped)
        self.assertAlmostEqual(found[0].duration_s, 6.0)
        w.submit(lambda: rec._conn.close()).result(10)  # 接続を閉じないと Windows では消せないので、試験の後片付け
        w.close()

    def test_killed_process_keeps_committed_sweeps(self):
        path = self.dir / "active_killed.sqlite"
        script = self.dir / "rec_child.py"
        script.write_text(textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(ROOT)!r})
            sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
            from test_storage import adopted, raw_sweep, header
            from pygigast.core.storage.recording import Recorder
            def done(s):
                print(s.sweeps_committed, flush=True)
            rec = Recorder({str(path)!r}, header(), on_committed=done)
            a = adopted(points=2000)
            i = 0
            while True:
                rec.append(raw_sweep(i, a))
                i += 1
                time.sleep(0.005)
        """), encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, cwd=str(ROOT))
        last = 0
        try:
            deadline = time.monotonic() + 30
            while last < 20 and time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                last = int(line)
        finally:
            proc.kill()  # 後片付けをさせずに落とす
            proc.wait(10)
            proc.stdout.close()
        self.assertGreaterEqual(last, 20)
        found = find_unfinished(self.dir)
        self.assertEqual(len(found), 1)
        self.assertGreaterEqual(found[0].n_sweeps, last)  # 知らせた掃引までは必ず残る
        with RecordingReader(path) as r:
            b = r.read(0)
            for i in range(len(b.t_start)):
                np.testing.assert_array_equal(b.raw[i], pattern(i, 2000))
        out = convert_to_h5(path, self.dir / "rescued.h5")
        with H5Record(out) as h:
            self.assertEqual(h.segments[0].n_sweeps, found[0].n_sweeps)
        self.assertFalse(path.exists())

    def test_cancel_convert_keeps_work_file(self):
        path = self.dir / "active_c.sqlite"
        rec = Recorder(path, header())
        a = adopted(points=600)
        for i in range(2100):
            rec.append(raw_sweep(i, a))
        rec.stop().result(60)
        ev = threading.Event()
        with self.assertRaises(Cancelled):
            convert_to_h5(path, self.dir / "out.h5", cancel=ev, progress=lambda d, t, e: ev.set() if d >= 1024 else None)
        self.assertTrue(path.exists())
        self.assertFalse((self.dir / "out.h5").exists())
        self.assertFalse((self.dir / "out.h5.partial").exists())

    def test_finish_on_writer_thread(self):
        path = self.dir / "active_f.sqlite"
        rec = Recorder(path, header())
        for i in range(3):
            rec.append(raw_sweep(i, adopted()))
        out = rec.finish(self.dir / "f.h5", markers=[{"id": "P1"}]).result(30)
        self.assertFalse(path.exists())
        with H5Record(out) as h:
            self.assertEqual(h.segments[0].n_sweeps, 3)
            self.assertEqual(h.markers(), [{"id": "P1"}])


class TestExport(TmpCase):
    def _read_csv(self, p: Path):
        with open(p, newline="") as f:
            first = f.readline()
            rows = list(csv.reader(f))
        return first, rows

    def test_traces_csv(self):
        t1 = trace(1, values=np.full(11, 1e-3))  # 0 dBm
        t2 = trace(2, values=np.full(11, 1e-6))  # −30 dBm
        calls = []
        p = self.dir / "t.csv"
        res = export_traces_csv(p, [t1, t2], meta={"note": "日本語"}, f_range_hz=(102 * MHZ, 104 * MHZ),
                                progress=lambda d, t, e: calls.append((d, t)))
        self.assertEqual(res.rows, 3)
        self.assertEqual(calls[-1], (3, 3))
        raw = p.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        raw.decode("ascii")
        first, rows = self._read_csv(p)
        self.assertTrue(first.startswith("# meta: "))
        meta = json.loads(first[len("# meta: "):])
        self.assertEqual(meta["note"], "日本語")
        self.assertEqual(meta["unit"], "dBm")
        self.assertEqual(rows[0], ["frequency_hz", "trace1_dBm", "trace2_dBm"])
        self.assertEqual([int(r[0]) for r in rows[1:]], [102 * MHZ, 103 * MHZ, 104 * MHZ])
        self.assertEqual(rows[1][1:], ["0.0000", "-30.0000"])

    def test_axis_mismatch_refused(self):
        t2 = trace(2, axis=FreqAxis(100 * MHZ, 2 * MHZ, 11))
        with self.assertRaises(StorageError) as cm:
            export_traces_csv(self.dir / "x.csv", [trace(1), t2], meta={})
        self.assertEqual(cm.exception.code, "axis_mismatch")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_record_csv_wide_and_long(self):
        freqs = np.array([1 * MHZ, 2 * MHZ, 3 * MHZ], dtype=np.int64)
        t = np.array([1_780_000_000.0, 1_780_000_001.5])
        v = np.array([[1e-3, 1e-4, 1e-5], [1e-6, 1e-7, 1e-8]])
        p = self.dir / "w.csv"
        res = export_record_csv(p, freqs_hz=freqs, blocks=[(t, v)], total_sweeps=2, quantity=Quantity.POWER, meta={})
        self.assertEqual(res.rows, 2)
        _, rows = self._read_csv(p)
        self.assertEqual(rows[0], ["time_iso", "time_unix", "1000000", "2000000", "3000000"])
        self.assertEqual(rows[2][1], "1780000001.500000")
        self.assertEqual(rows[2][2:], ["-30.0000", "-40.0000", "-50.0000"])
        iso = datetime.fromisoformat(rows[2][0])
        self.assertIsNotNone(iso.tzinfo)  # 時差付き
        self.assertAlmostEqual(iso.timestamp(), 1_780_000_001.5)
        p2 = self.dir / "l.csv"
        res = export_record_csv(p2, freqs_hz=freqs, blocks=[(t, v)], total_sweeps=2, quantity="power", meta={},
                                layout=LAYOUT_LONG)
        self.assertEqual(res.rows, 6)
        _, rows = self._read_csv(p2)
        self.assertEqual(rows[0], ["time_unix", "frequency_hz", "value"])
        self.assertEqual(rows[6], ["1780000001.500000", "3000000", "-50.0000"])

    def test_record_csv_cancel_removes_file(self):
        freqs = np.arange(10, dtype=np.int64)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(Cancelled):
            export_record_csv(self.dir / "c.csv", freqs_hz=freqs, blocks=[(np.zeros(3), np.ones((3, 10)))],
                              total_sweeps=3, quantity=Quantity.POWER, meta={}, cancel=ev)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_touchstone_refuses_scalar(self):
        t = trace(1, values=np.linspace(0.1, 1, 11), q=Quantity.RATIO)
        with self.assertRaises(StorageError) as cm:
            export_touchstone(self.dir / "x.s1p", {"S11": t}, meta={})
        self.assertEqual(cm.exception.code, "touchstone_needs_complex")
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_touchstone_complex(self):
        v = np.linspace(0.1, 1, 11) * np.exp(1j * 0.5)
        t = trace(1, values=v, kind=ValueKind.COMPLEX, q=Quantity.RATIO)
        p = self.dir / "a.s1p"
        export_touchstone(p, {"S11": t}, meta={"device": "x"})
        lines = p.read_text(encoding="ascii").splitlines()
        self.assertTrue(lines[0].startswith("! meta: "))
        self.assertEqual(lines[1], "# HZ S RI R 50")
        f, re_, im = lines[2].split()
        self.assertEqual(int(f), 100 * MHZ)
        self.assertAlmostEqual(float(re_), v[0].real)
        self.assertAlmostEqual(float(im), v[0].imag)
        with self.assertRaises(StorageError) as cm:
            export_touchstone(self.dir / "b.s2p", {"S11": t, "S21": t}, meta={})
        self.assertEqual(cm.exception.code, "touchstone_incomplete_parameters")

    def test_unit_mismatch(self):
        with self.assertRaises(StorageError) as cm:
            convert_unit(np.ones(3), Quantity.RATIO, "dBm")
        self.assertEqual(cm.exception.code, "unit_mismatch")


if __name__ == "__main__":
    unittest.main()
