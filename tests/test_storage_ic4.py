"""イメージキャンセルの4回の形（raw_layout "image_cancel_4": 設計F §6 の 3・設計H §7.1）の保存・記録・読み戻し。

前の2回の形と1回の形のファイルが今までどおり読めることは test_storage.py が確かめる。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from pygigast.core.storage.h5file import (
    LAYOUT_IMAGE_CANCEL,
    LAYOUT_IMAGE_CANCEL_4,
    H5Record,
    MemorySegment,
    extract_h5,
    save_snapshot,
    segment_meta_for,
    write_h5,
)
from pygigast.core.storage.recording import Recorder, RecordingReader, convert_to_h5, new_recording_path
from pygigast.core.types import RawSweep

from tests.test_storage import MHZ, adopted, header, pattern

T0 = 1_780_000_000.0


def raw4(i: int, a, t0: float = T0) -> RawSweep:
    """4回（b3=0・1・0・1）の生の値と、合わせた値（試験では決まった値: 合わせ方は機器の層の試験で確かめる）。"""
    n = a.axis.points
    passes = np.stack([pattern(i + 1000 * k, n) for k in range(4)])
    combined = pattern(i + 5000, n)
    return RawSweep(a, combined, "gigast_ad_u16", t0 + i, 0.1, raw_passes=passes,
                    extra={"passes": 4, "pass_b3": [0, 1, 0, 1]})


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


class TestSnapshot(Base):
    def test_round_trip_keeps_all_four_passes(self):
        a = adopted(image_cancel=True)
        r = raw4(3, a)
        p = save_snapshot(self.dir / "ic4.h5", header(), r)
        with h5py.File(p, "r") as f:
            g = f["segments/0000"]
            self.assertEqual(g["raw_passes"].shape, (1, 4, 11))
            self.assertEqual(g["raw_passes"].dtype, np.uint16)
            self.assertNotIn("raw_b3", g)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_CANCEL_4)
            b = rec.read(0)
            np.testing.assert_array_equal(b.raw[0], r.raw)
            np.testing.assert_array_equal(b.raw2[0], r.raw_passes)
            np.testing.assert_array_equal(b.adopted()[0], r.raw)  # 採用した値は合わせた値そのもの
            back = rec.raw_sweep(0, 0)
            np.testing.assert_array_equal(back.raw, r.raw)
            np.testing.assert_array_equal(back.raw_passes, r.raw_passes)
            self.assertIsNone(back.raw_b3_0)
            self.assertEqual(back.extra["pass_b3"], [0, 1, 0, 1])
            # 点の一部だけ読む
            part = rec.read(0, points=(2, 5))
            np.testing.assert_array_equal(part.raw2[0], r.raw_passes[:, 2:5])

    def test_cut_at_stop_cuts_every_pass(self):
        a = adopted(points=11, stop=100 * MHZ + 7 * MHZ, image_cancel=True)
        r = raw4(0, a)
        p = save_snapshot(self.dir / "cut.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].points, 8)
            np.testing.assert_array_equal(rec.read(0).raw2[0], r.raw_passes[:, :8])

    def test_old_two_pass_layout_still_written_and_read(self):
        # 前の2回の形の RawSweep（2回の形のファイルを読み戻したもの）は前の形のまま
        a = adopted(image_cancel=True)
        b0, b1 = pattern(1, 11), pattern(2, 11)
        r = RawSweep(a, np.minimum(b0, b1), "gigast_ad_u16", T0, 0.1, raw_b3_0=b0, raw_b3_1=b1)
        p = save_snapshot(self.dir / "ic2.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_CANCEL)
            back = rec.raw_sweep(0, 0)
            self.assertIsNone(back.raw_passes)
            np.testing.assert_array_equal(back.raw, np.minimum(b0, b1))


class TestLarge(Base):
    def test_many_sweeps_overview_and_extract(self):
        a = adopted(points=600, step=100_000, image_cancel=True)
        n = 1100  # BLOCK_ROWS（1024）をまたぐ
        sweeps = [raw4(i, a) for i in range(n)]
        meta = segment_meta_for(sweeps[0])
        t = np.array([s.started_at for s in sweeps])
        raw = np.stack([s.raw for s in sweeps])
        passes = np.stack([s.raw_passes for s in sweeps])
        p = write_h5(self.dir / "big.h5", header(), [MemorySegment(meta, t, raw, passes)])
        with H5Record(p) as rec:
            ov = rec.overview(0, 4, (10, 20))
            np.testing.assert_array_equal(ov.values[0], raw[0:4, 10:20].max(axis=0))
            b = rec.read(0, (1020, 1030), (590, 600))
            np.testing.assert_array_equal(b.raw2, passes[1020:1030, :, 590:600])
        out = extract_h5(p, self.dir / "cut.h5", t_range=(T0 + 1000, T0 + 1050), f_range_hz=(None, None))
        with H5Record(out) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_CANCEL_4)
            np.testing.assert_array_equal(rec.read(0).raw2, passes[1000:1051])


class TestRecording(Base):
    def test_record_and_convert(self):
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        a4 = adopted(points=11, image_cancel=True)
        a1 = adopted(points=11)  # イメージキャンセルを OFF にした → 新しい区間
        for i in range(6):
            rec.append(raw4(i, a4))
        for i in range(6, 9):
            rec.append(RawSweep(a1, pattern(i, 11), "gigast_ad_u16", T0 + i, 0.1))
        stats = rec.stop().result(10)
        self.assertIsNone(stats.last_error)
        self.assertEqual(stats.segments, 2)
        with RecordingReader(path) as r:
            self.assertEqual(r.segments[0].raw_layout, LAYOUT_IMAGE_CANCEL_4)
            b = r.read(0, points=(1, 4))
            self.assertEqual(b.raw2.shape, (6, 4, 3))
            np.testing.assert_array_equal(b.raw2[2], raw4(2, a4).raw_passes[:, 1:4])
            np.testing.assert_array_equal(r.overview(0, 4).values[0],
                                          np.stack([raw4(i, a4).raw for i in range(4)]).max(axis=0))
        out = convert_to_h5(path, self.dir / "rec.h5")
        with H5Record(out) as h:
            self.assertEqual([s.raw_layout for s in h.segments], [LAYOUT_IMAGE_CANCEL_4, "single"])
            np.testing.assert_array_equal(h.raw_sweep(0, 5).raw_passes, raw4(5, a4).raw_passes)
            np.testing.assert_array_equal(h.raw_sweep(0, 5).raw, raw4(5, a4).raw)


if __name__ == "__main__":
    unittest.main()
