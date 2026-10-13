"""イメージ除去の区間の形（raw_layout "image_reject_segments": 設計F §7.3・設計H §7.1）の保存・記録・読み戻しと、
前の版のファイル（掃引条件の版 1 の image_cancel）の読み替え。

- raw は区間ごとに回を合わせた値。区間ごとの生の値は raw_segments/<番号> [掃引, 回数, 点数]（属性 point0・points・
  passes・b3）
- 前の形（image_cancel_4・image_cancel・single・nf）は test_storage_ic4.py・test_storage.py が確かめる
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from namibench.core.storage import CONDITIONS_VERSION, StorageError, adopted_from_dict, adopted_to_dict
from namibench.core.storage.h5file import (
    LAYOUT_IMAGE_REJECT,
    H5Record,
    MemorySegment,
    extract_h5,
    save_snapshot,
    segment_meta_for,
    split_raw,
    write_h5,
)
from namibench.core.storage.recording import Recorder, RecordingReader, convert_to_h5, new_recording_path
from namibench.core.types import RawSegment, RawSweep

from tests.test_storage import MHZ, adopted, header, pattern

T0 = 1_780_000_000.0
# 帯の中 4回（点 0〜3）・帯の外 2回（点 4〜10）の2区間（正確で帯の境目をまたいだ形）
LAYOUT = ((0, 4, (0, 1, 0, 1)), (4, 7, (0, 1)))


def raw_rej(i: int, a, layout=LAYOUT, t0: float = T0) -> RawSweep:
    """区間ごとの生の値と、合わせた値（試験では決まった値: 合わせ方は機器の層の試験で確かめる）。"""
    segs = []
    for k, (p0, n, b3) in enumerate(layout):
        vals = np.stack([pattern(i + 1000 * k + 100 * j, n) for j in range(len(b3))])
        segs.append(RawSegment(point0=p0, b3=b3, values=vals))
    combined = pattern(i + 5000, a.axis.points)
    return RawSweep(a, combined, "gigast_ad_u16", t0 + i, 0.1, raw_segments=tuple(segs))


def regions(rec, seg=0):
    return [(r["point0"], r["points"], r["passes"], r["b3"]) for r in rec.segments[seg].raw_segments]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()


class TestSnapshot(Base):
    def test_round_trip_keeps_every_segment(self):
        a = adopted(image_reject="accurate")
        r = raw_rej(3, a)
        p = save_snapshot(self.dir / "rej.h5", header(), r)
        with h5py.File(p, "r") as f:
            g = f["segments/0000"]
            self.assertEqual(g.attrs["raw_layout"], LAYOUT_IMAGE_REJECT)
            d0, d1 = g["raw_segments/00"], g["raw_segments/01"]
            self.assertEqual((d0.shape, d1.shape), ((1, 4, 4), (1, 2, 7)))
            self.assertEqual(d0.dtype, np.uint16)
            self.assertEqual((int(d1.attrs["point0"]), int(d1.attrs["points"]), int(d1.attrs["passes"])), (4, 7, 2))
            self.assertEqual(list(d0.attrs["b3"]), [0, 1, 0, 1])
            self.assertNotIn("raw_passes", g)
            conditions = json.loads(g.attrs["conditions"])
            self.assertEqual((conditions["version"], conditions["image_reject"]), (CONDITIONS_VERSION, "accurate"))
            self.assertNotIn("image_cancel", conditions)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_REJECT)
            self.assertEqual(regions(rec), [(0, 4, 4, [0, 1, 0, 1]), (4, 7, 2, [0, 1])])
            b = rec.read(0)
            np.testing.assert_array_equal(b.raw[0], r.raw)
            np.testing.assert_array_equal(b.adopted()[0], r.raw)  # 採用した値は合わせた値そのもの
            back = rec.raw_sweep(0, 0)
            np.testing.assert_array_equal(back.raw, r.raw)
            self.assertEqual(back.adopted.image_reject, "accurate")
            self.assertEqual([(s.point0, s.b3) for s in back.raw_segments], [(0, (0, 1, 0, 1)), (4, (0, 1))])
            for got, want in zip(back.raw_segments, r.raw_segments):
                np.testing.assert_array_equal(got.values, want.values)

    def test_read_part_of_points_clips_segments(self):
        # 点の一部（2〜6）だけ読む: 区間をまたいで切り、区間の並びも読んだ範囲の先頭からの番号にする
        a = adopted(image_reject="accurate")
        r = raw_rej(1, a)
        p = save_snapshot(self.dir / "part.h5", header(), r)
        with H5Record(p) as rec:
            b = rec.read(0, points=(2, 7))
            self.assertEqual([(x["point0"], x["points"], x["passes"]) for x in b.raw_segments], [(0, 2, 4), (2, 3, 2)])
            self.assertEqual(b.raw2.shape, (1, 2 * 4 + 3 * 2))
            np.testing.assert_array_equal(b.raw2[0, :8].reshape(4, 2), r.raw_segments[0].values[:, 2:4])
            np.testing.assert_array_equal(b.raw2[0, 8:].reshape(2, 3), r.raw_segments[1].values[:, 0:3])

    def test_cut_at_stop_cuts_last_segment_and_drops_empty(self):
        # Stop を超える点は保存しない（設計C §5.3）: 最後の区間を切り、点の無くなった区間は落とす
        a = adopted(points=11, stop=100 * MHZ + 5 * MHZ, image_reject="accurate")
        layout = ((0, 4, (0, 1, 0, 1)), (4, 6, (0, 1)), (10, 1, (0, 1, 0, 1)))
        r = raw_rej(0, a, layout)
        p = save_snapshot(self.dir / "cut.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].points, 6)
            self.assertEqual(regions(rec), [(0, 4, 4, [0, 1, 0, 1]), (4, 2, 2, [0, 1])])
            back = rec.raw_sweep(0, 0)
            np.testing.assert_array_equal(back.raw_segments[1].values, r.raw_segments[1].values[:, :2])

    def test_fast_is_one_segment_of_two(self):
        a = adopted(image_reject="fast")
        r = raw_rej(2, a, ((0, 11, (0, 1)),))
        p = save_snapshot(self.dir / "fast.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(regions(rec), [(0, 11, 2, [0, 1])])
            self.assertEqual(rec.raw_sweep(0, 0).adopted.image_reject, "fast")

    def test_off_is_single(self):
        a = adopted(image_reject="off")
        r = RawSweep(a, pattern(0, 11), "gigast_ad_u16", T0, 0.1)
        p = save_snapshot(self.dir / "off.h5", header(), r)
        with H5Record(p) as rec:
            self.assertEqual(rec.segments[0].raw_layout, "single")
            self.assertIsNone(rec.segments[0].raw_segments)

    def test_segments_must_cover_points(self):
        # 区間が点を順に覆わない RawSweep は保存しない（壊れた形を書かない）
        a = adopted(image_reject="accurate")
        r = raw_rej(0, a, ((0, 4, (0, 1, 0, 1)), (5, 6, (0, 1))))
        with self.assertRaises(ValueError):
            split_raw(r)


class TestLarge(Base):
    def test_many_sweeps_overview_and_extract(self):
        a = adopted(points=600, step=100_000, image_reject="accurate")
        layout = ((0, 250, (0, 1, 0, 1)), (250, 350, (0, 1)))
        n = 1100  # BLOCK_ROWS（1024）をまたぐ
        sweeps = [raw_rej(i, a, layout) for i in range(n)]
        meta = segment_meta_for(sweeps[0])
        t = np.array([s.started_at for s in sweeps])
        raw = np.stack([s.raw for s in sweeps])
        flat = np.stack([split_raw(s)[1] for s in sweeps])
        p = write_h5(self.dir / "big.h5", header(), [MemorySegment(meta, t, raw, flat)])
        seg0 = np.stack([s.raw_segments[0].values for s in sweeps])
        seg1 = np.stack([s.raw_segments[1].values for s in sweeps])
        with H5Record(p) as rec:
            ov = rec.overview(0, 4, (10, 20))
            np.testing.assert_array_equal(ov.values[0], raw[0:4, 10:20].max(axis=0))
            with h5py.File(p, "r") as f:
                np.testing.assert_array_equal(f["segments/0000/raw_segments/00"][1020:1030], seg0[1020:1030])
                np.testing.assert_array_equal(f["segments/0000/raw_segments/01"][1020:1030], seg1[1020:1030])
        # 時間と周波数（点 240〜259: 区間の境目をまたぐ）の範囲を切り出す
        f_lo, f_hi = 100 * MHZ + 240 * 100_000, 100 * MHZ + 259 * 100_000
        out = extract_h5(p, self.dir / "cut.h5", t_range=(T0 + 1000, T0 + 1050), f_range_hz=(f_lo, f_hi))
        with H5Record(out) as rec:
            self.assertEqual(rec.segments[0].raw_layout, LAYOUT_IMAGE_REJECT)
            self.assertEqual(regions(rec), [(0, 10, 4, [0, 1, 0, 1]), (10, 10, 2, [0, 1])])
            back = rec.raw_sweep(0, 3)
            np.testing.assert_array_equal(back.raw_segments[0].values, seg0[1003][:, 240:250])
            np.testing.assert_array_equal(back.raw_segments[1].values, seg1[1003][:, 0:10])


class TestRecording(Base):
    def test_record_and_convert(self):
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        acc = adopted(points=11, image_reject="accurate")
        off = adopted(points=11)  # イメージ除去をなしにした → 新しい区間
        for i in range(6):
            rec.append(raw_rej(i, acc))
        for i in range(6, 9):
            rec.append(RawSweep(off, pattern(i, 11), "gigast_ad_u16", T0 + i, 0.1))
        stats = rec.stop().result(10)
        self.assertIsNone(stats.last_error)
        self.assertEqual(stats.segments, 2)
        with RecordingReader(path) as r:
            self.assertEqual(r.segments[0].raw_layout, LAYOUT_IMAGE_REJECT)
            b = r.read(0, points=(2, 6))
            self.assertEqual([(x["point0"], x["points"], x["passes"]) for x in b.raw_segments], [(0, 2, 4), (2, 2, 2)])
            self.assertEqual(b.raw2.shape, (6, 2 * 4 + 2 * 2))
            want = raw_rej(2, acc)
            np.testing.assert_array_equal(b.raw2[2, :8].reshape(4, 2), want.raw_segments[0].values[:, 2:4])
            np.testing.assert_array_equal(b.raw2[2, 8:].reshape(2, 2), want.raw_segments[1].values[:, 0:2])
        out = convert_to_h5(path, self.dir / "rec.h5")
        with H5Record(out) as h:
            self.assertEqual([s.raw_layout for s in h.segments], [LAYOUT_IMAGE_REJECT, "single"])
            back = h.raw_sweep(0, 5)
            np.testing.assert_array_equal(back.raw, raw_rej(5, acc).raw)
            for got, want in zip(back.raw_segments, raw_rej(5, acc).raw_segments):
                np.testing.assert_array_equal(got.values, want.values)


class TestOldConditions(Base):
    """前の版（掃引条件の版 1）の image_cancel は読むときに置き換える（設計F §7.3）: true → "fast"、false → "off"。"""

    def old_dict(self, image_cancel: bool) -> dict:
        d = adopted_to_dict(adopted(image_reject="accurate"))
        d["version"] = 1
        del d["image_reject"]
        d["image_cancel"] = image_cancel
        return d

    def test_adopted_from_v1_dict(self):
        self.assertEqual(adopted_from_dict(self.old_dict(True)).image_reject, "fast")
        self.assertEqual(adopted_from_dict(self.old_dict(False)).image_reject, "off")

    def test_unknown_version_or_value_refused(self):
        d = adopted_to_dict(adopted())
        d["version"] = 3
        with self.assertRaises(StorageError):
            adopted_from_dict(d)
        d = adopted_to_dict(adopted())
        d["image_reject"] = "slow"
        with self.assertRaises(StorageError):
            adopted_from_dict(d)

    def test_old_file_with_v1_conditions_opens(self):
        # 前の版で書いたファイル（掃引条件の版 1・image_cancel）を開いて読み戻せる
        for ic, want in ((True, "fast"), (False, "off")):
            a = adopted()
            r = RawSweep(a, pattern(0, 11), "gigast_ad_u16", T0, 0.1)
            p = save_snapshot(self.dir / f"old_{ic}.h5", header(), r)
            with h5py.File(p, "r+") as f:
                f["segments/0000"].attrs["conditions"] = json.dumps(self.old_dict(ic))
            with H5Record(p) as rec:
                self.assertEqual(rec.raw_sweep(0, 0).adopted.image_reject, want)


if __name__ == "__main__":
    unittest.main()
