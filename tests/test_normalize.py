"""ノーマライズ（設計E §6）の試験。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from namibench.core.normalize import (
    KIND_WIDE,
    ReferenceBuildError,
    ReferenceFileError,
    apply_reference,
    delete_reference,
    list_references,
    load_reference,
    new_reference,
    return_loss_db,
    s11_segment_from_frames,
    save_reference,
    segment_from_frames,
    swr,
    wide_range_hz,
)
from namibench.core.normalize import apply as ap
from namibench.core.types import AdoptedSweep, FreqAxis, Frame, Mode, Quantity, ValueKind

MHZ = 1_000_000
PROFILE = {"model": "test-model", "source": {"band_edges_hz": [1000 * MHZ, 2000 * MHZ, 3000 * MHZ]}}
SERIAL = "SER1"
CONN = 1000.0


def db_to_w(db):
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 10.0)


def make_frame(start, step, points, power_w, *, mode=Mode.TG, image_reject="off", rbw=180_000,
               quantity=Quantity.POWER, flags=None):
    axis = FreqAxis(start, step, points)
    adopted = AdoptedSweep(
        start_hz=start, stop_hz=axis.last_hz, axis=axis, rbw_hz=rbw, rbw_preference_hz=rbw,
        mode=mode, sg_hz=None, image_reject=image_reject, preroll_points=0,
        transient_head_points=0, estimated_sweep_s=0.1,
    )
    return Frame(
        axis=axis, values=np.asarray(power_w, dtype=np.float64), quantity=quantity,
        value_kind=ValueKind.REAL, time=0.0, duration_s=0.1, sweep_no=1,
        display_start_hz=start, display_stop_hz=axis.last_hz, adopted=adopted, flags=flags or {},
    )


def ref_from(start, step, points, db_fn, *, taken_at=2000.0, connected_at=CONN, kind="fine", **kw):
    f = FreqAxis(start, step, points).freqs()
    fr = make_frame(start, step, points, db_to_w(db_fn(f)), **kw)
    return segment_from_frames([fr], connected_at=connected_at, taken_at=taken_at, kind=kind)


def to_db_ratio(values):
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(values)


def apply(frame, ref, *, connected_at=CONN, now=3000.0, serial=SERIAL):
    return apply_reference(frame, ref, serial=serial, connected_at=connected_at, now=now)


def linear_db(f):
    # 周波数に比例する基準（dB）。線形補間で誤差が出ない形
    return -30.0 - (f.astype(np.float64) / MHZ) * 0.01


class TestTakeReference(unittest.TestCase):
    def test_power_average_and_transient_excluded(self):
        p1 = np.array([1e-6, 2e-6, 3e-6])
        p2 = np.array([3e-6, 4e-6, 5e-6])
        f1 = make_frame(100 * MHZ, MHZ, 3, p1, flags={"transient": np.array([True, False, False])})
        f2 = make_frame(100 * MHZ, MHZ, 3, p2)
        seg = segment_from_frames([f1, f2], connected_at=CONN, taken_at=1.0)
        np.testing.assert_allclose(seg.power_w, [3e-6, 3e-6, 4e-6])
        self.assertEqual(seg.averages, 2)

    def test_mismatched_frames_rejected(self):
        a = make_frame(100 * MHZ, MHZ, 3, np.ones(3))
        b = make_frame(100 * MHZ, MHZ, 3, np.ones(3), image_reject="fast")
        with self.assertRaises(ReferenceBuildError):
            segment_from_frames([a, b], connected_at=CONN, taken_at=1.0)
        c = make_frame(100 * MHZ, MHZ, 3, np.ones(3), mode=Mode.SA)
        with self.assertRaises(ReferenceBuildError):
            segment_from_frames([c], connected_at=CONN, taken_at=1.0)

    def test_wide_range_from_profile(self):
        prof = {"source": {"tg_range_hz": [1 * MHZ, 4000 * MHZ]}}
        self.assertEqual(wide_range_hz(prof), (MHZ, 4000 * MHZ, MHZ))
        with self.assertRaises(KeyError):
            wide_range_hz(PROFILE)

    def test_merge_overwrites_same_range_only(self):
        ref = new_reference("A", PROFILE, SERIAL)
        ref.merge(ref_from(100 * MHZ, MHZ, 101, lambda f: np.full(f.shape, -30.0), kind=KIND_WIDE))
        ref.merge(ref_from(150 * MHZ, MHZ, 11, lambda f: np.full(f.shape, -40.0), taken_at=2500.0))
        # 範囲の外（100〜149・161〜200MHz）は残り、150〜160MHz は新しい値
        self.assertEqual(len(ref.segments), 3)
        fr = make_frame(100 * MHZ, MHZ, 101, db_to_w(np.full(101, -30.0)))
        r = apply(fr, ref)
        db = to_db_ratio(r.frame.values)
        np.testing.assert_allclose(db[:50], 0.0, atol=1e-9)
        np.testing.assert_allclose(db[50:61], 10.0, atol=1e-9)
        np.testing.assert_allclose(db[61:], 0.0, atol=1e-9)
        # 違う条件（イメージ除去がスピード）の区間は上書きしない
        ref.merge(ref_from(100 * MHZ, MHZ, 101, lambda f: np.full(f.shape, -31.0), image_reject="fast"))
        self.assertEqual(len(ref.segments), 4)


class TestApply(unittest.TestCase):
    def setUp(self):
        self.ref = new_reference("cable", PROFILE, SERIAL)
        self.ref.merge(ref_from(MHZ, MHZ, 4000, linear_db, kind=KIND_WIDE))

    def test_exact_grid_is_fine_and_ratio_is_amplitude(self):
        f = FreqAxis(100 * MHZ, MHZ, 11).freqs()
        fr = make_frame(100 * MHZ, MHZ, 11, db_to_w(linear_db(f) - 6.0))
        r = apply(fr, self.ref)
        self.assertTrue(r.applied)
        self.assertEqual(r.frame.quantity, Quantity.RATIO)
        np.testing.assert_allclose(to_db_ratio(r.frame.values), -6.0, atol=1e-9)
        np.testing.assert_allclose(r.frame.values, 10 ** (-6.0 / 20.0), rtol=1e-12)
        self.assertTrue(r.frame.flags["ref_fine"].all())
        self.assertEqual(r.accuracy, "fine")
        self.assertEqual(r.out_of_range_hz, [])

    def test_interpolated_is_coarse(self):
        f = FreqAxis(100 * MHZ + 250_000, 20_000, 50).freqs()
        fr = make_frame(int(f[0]), 20_000, 50, db_to_w(linear_db(f)))
        r = apply(fr, self.ref)
        np.testing.assert_allclose(to_db_ratio(r.frame.values), 0.0, atol=1e-9)
        self.assertTrue(r.frame.flags["ref_coarse"].all())
        self.assertFalse(r.frame.flags["ref_fine"].any())
        self.assertEqual(r.accuracy, "coarse")

    def test_exact_hit_with_other_step_is_coarse(self):
        f = FreqAxis(100 * MHZ, 2 * MHZ, 5).freqs()
        r = apply(make_frame(100 * MHZ, 2 * MHZ, 5, db_to_w(linear_db(f))), self.ref)
        self.assertTrue(r.frame.flags["ref_coarse"].all())

    def test_no_interpolation_across_band_edge(self):
        # 999〜1001MHz を 0.25MHz 刻み。境目の上下で基準の段差 20dB
        ref = new_reference("edge", PROFILE, SERIAL)
        ref.merge(ref_from(990 * MHZ, MHZ, 21, lambda f: np.where(f >= 1000 * MHZ, -10.0, -30.0)))
        f = FreqAxis(999 * MHZ, 250_000, 9).freqs()
        meas = np.where(f >= 1000 * MHZ, -10.0, -30.0)
        r = apply(make_frame(999 * MHZ, 250_000, 9, db_to_w(meas)), ref)
        nr = r.frame.flags["no_reference"]
        # 999MHz は基準点、999.25〜999.75 は境目をまたぐので値なし、1000MHz ちょうどは上の帯の基準点
        self.assertEqual(nr.tolist(), [False, True, True, True, False, False, False, False, False])
        self.assertTrue(r.frame.flags["no_reference_band_edge"][1:4].all())
        self.assertTrue(np.isnan(r.frame.values[1:4]).all())
        ok = ~nr
        np.testing.assert_allclose(to_db_ratio(r.frame.values[ok]), 0.0, atol=1e-9)
        self.assertTrue(r.partial)
        self.assertEqual(r.out_of_range_hz, [(999_250_000, 999_750_000)])

    def test_fine_segment_takes_priority_and_seam_bridged(self):
        ref = new_reference("mix", PROFILE, SERIAL)
        ref.merge(ref_from(100 * MHZ, MHZ, 101, lambda f: np.full(f.shape, -30.0), kind=KIND_WIDE))
        # 150.5〜151.5MHz を 100kHz で細かく取り直し（-33dB）
        ref.merge(ref_from(150 * MHZ + 500_000, 100_000, 11, lambda f: np.full(f.shape, -33.0)))
        f = FreqAxis(150 * MHZ, 100_000, 21).freqs()
        r = apply(make_frame(150 * MHZ, 100_000, 21, db_to_w(np.full(21, -30.0))), ref)
        self.assertFalse(r.frame.flags["no_reference"].any())  # つなぎ目に穴が無い
        db = to_db_ratio(r.frame.values)
        np.testing.assert_allclose(db[5:16], 3.0, atol=1e-9)
        self.assertTrue(r.frame.flags["ref_fine"][5:16].all())
        self.assertTrue(r.frame.flags["ref_coarse"][:5].all())
        self.assertEqual(r.accuracy, "mixed")
        self.assertEqual(f.size, 21)

    def test_partial_coverage_gives_no_value_not_power(self):
        ref = new_reference("part", PROFILE, SERIAL)
        ref.merge(ref_from(3500 * MHZ, MHZ, 501, lambda f: np.full(f.shape, -20.0)))
        f = FreqAxis(3900 * MHZ, 10 * MHZ, 21).freqs()  # 3900〜4100MHz
        r = apply(make_frame(3900 * MHZ, 10 * MHZ, 21, db_to_w(np.full(21, -20.0))), ref)
        self.assertTrue(r.applied)
        self.assertEqual(r.frame.quantity, Quantity.RATIO)
        nr = r.frame.flags["no_reference"]
        self.assertEqual(nr.tolist(), (f > 4000 * MHZ).tolist())
        self.assertTrue(np.isnan(r.frame.values[nr]).all())
        self.assertEqual(r.out_of_range_hz, [(4010 * MHZ, 4100 * MHZ)])

    def test_hole_in_reference_is_no_value(self):
        ref = new_reference("hole", PROFILE, SERIAL)
        p = np.full(11, -20.0)
        seg = ref_from(100 * MHZ, MHZ, 11, lambda f: p)
        seg.power_w[5] = np.nan  # 105MHz は使えない点
        ref.merge(seg)
        f = FreqAxis(104 * MHZ, 500_000, 5).freqs()  # 104〜106MHz
        r = apply(make_frame(104 * MHZ, 500_000, 5, db_to_w(np.full(5, -20.0))), ref)
        self.assertEqual(r.frame.flags["no_reference"].tolist(), [False, True, True, True, False])
        self.assertEqual(f.size, 5)

    def test_all_out_of_range_returns_power(self):
        fr = make_frame(5000 * MHZ, MHZ, 10, np.ones(10))
        r = apply(fr, self.ref)
        self.assertFalse(r.applied)
        self.assertEqual(r.reason, ap.OUT_OF_RANGE)
        self.assertIs(r.frame, fr)
        self.assertEqual(r.frame.quantity, Quantity.POWER)

    def test_condition_mismatch_returns_power_with_reason(self):
        cases = [
            (dict(image_reject="fast"), ap.IMAGE_REJECT_MISMATCH),
            (dict(image_reject="accurate"), ap.IMAGE_REJECT_MISMATCH),
            (dict(rbw=30_000), ap.RBW_MISMATCH),
            (dict(mode=Mode.S11), ap.MODE_MISMATCH),
        ]
        for kw, reason in cases:
            fr = make_frame(100 * MHZ, MHZ, 5, np.ones(5), **kw)
            r = apply(fr, self.ref)
            self.assertFalse(r.applied, reason)
            self.assertEqual(r.reason, reason)
            self.assertEqual(r.frame.quantity, Quantity.POWER)
        r = apply(make_frame(100 * MHZ, MHZ, 5, np.ones(5), image_reject="fast"), self.ref)
        self.assertEqual(r.detail["reference_image_reject"], ["off"])
        fr = make_frame(100 * MHZ, MHZ, 5, np.ones(5))
        self.assertEqual(apply(fr, self.ref, serial="OTHER").reason, ap.SERIAL_MISMATCH)
        self.assertEqual(apply(fr, None).reason, ap.NO_REFERENCE_SELECTED)
        ratio = make_frame(100 * MHZ, MHZ, 5, np.ones(5), quantity=Quantity.RATIO)
        self.assertEqual(apply(ratio, self.ref).reason, ap.NOT_POWER)

    def test_image_reject_must_match_three_ways(self):
        # 3つの選択のどれで取った基準も、同じ選択の測定にだけ当たる（設計F §7.3・設計E §6.3）
        for taken in ("accurate", "fast", "off"):
            ref = new_reference(taken, PROFILE, SERIAL)
            ref.merge(ref_from(100 * MHZ, MHZ, 5, linear_db, image_reject=taken))
            for measured in ("accurate", "fast", "off"):
                r = apply(make_frame(100 * MHZ, MHZ, 5, np.ones(5), image_reject=measured), ref)
                self.assertEqual(r.applied, taken == measured, (taken, measured))

    def test_zero_span_matches_off_reference(self):
        # ゼロスパンは選択によらずイメージ除去をしない（設計F §7.1）: なしで取った基準が当たる
        ref = new_reference("zs", PROFILE, SERIAL)
        ref.merge(ref_from(100 * MHZ, MHZ, 5, linear_db, image_reject="off"))
        fr = make_frame(102 * MHZ, 0, 5, np.ones(5), image_reject="fast")
        self.assertTrue(apply(fr, ref).applied)

    def test_power_cycled_flag_and_age(self):
        fr = make_frame(100 * MHZ, MHZ, 5, np.ones(5))
        r = apply(fr, self.ref, connected_at=CONN + 60.0, now=2600.0)
        self.assertTrue(r.applied)  # 当てることはやめない
        self.assertTrue(r.power_cycled)
        self.assertTrue(r.frame.flags["ref_power_cycled"].all())
        self.assertEqual(r.oldest_taken_at, 2000.0)
        self.assertAlmostEqual(r.age_s, 600.0)
        r2 = apply(fr, self.ref)
        self.assertFalse(r2.power_cycled)

    def test_existing_flags_kept(self):
        tr = np.array([True, False, False])
        fr = make_frame(100 * MHZ, MHZ, 3, np.ones(3), flags={"transient": tr})
        r = apply(fr, self.ref)
        self.assertIs(r.frame.flags["transient"], tr)
        self.assertNotIn("no_reference", fr.flags)  # 元のフレームは変えない


class TestS11(unittest.TestCase):
    def test_open_short_average(self):
        po = np.array([4.0, 1.0, 9.0]) * 1e-6
        ps = np.array([1.0, 1.0, 1.0]) * 1e-6
        o = make_frame(100 * MHZ, MHZ, 3, po, mode=Mode.S11)
        s = make_frame(100 * MHZ, MHZ, 3, ps, mode=Mode.S11)
        seg = s11_segment_from_frames([o], [s], connected_at=CONN, taken_at=1.0)
        np.testing.assert_allclose(seg.power_w, np.array([2.25, 1.0, 4.0]) * 1e-6)
        ref = new_reference("bridge", PROFILE, SERIAL, mode=Mode.S11)
        ref.merge(seg)
        # |Γ| = 0.5 相当の負荷（電力で基準の 1/4）
        dut = make_frame(100 * MHZ, MHZ, 3, seg.power_w / 4.0, mode=Mode.S11)
        r = apply(dut, ref)
        np.testing.assert_allclose(r.frame.values, 0.5)
        np.testing.assert_allclose(return_loss_db(r.frame.values), 20 * np.log10(2.0))
        np.testing.assert_allclose(swr(r.frame.values), 3.0)
        # TG の基準は S11 に当てない
        self.assertEqual(apply(make_frame(100 * MHZ, MHZ, 3, po), ref).reason, ap.MODE_MISMATCH)

    def test_swr_edges(self):
        out = swr(np.array([0.0, 1.0, 1.2, np.nan]))
        self.assertEqual(out[0], 1.0)
        self.assertTrue(np.isinf(out[1]) and np.isinf(out[2]))
        self.assertTrue(np.isnan(out[3]))

    def test_s11_requires_s11_mode(self):
        o = make_frame(100 * MHZ, MHZ, 3, np.ones(3))
        with self.assertRaises(ReferenceBuildError):
            s11_segment_from_frames([o], [o], connected_at=CONN, taken_at=1.0)


class TestStore(unittest.TestCase):
    def test_round_trip_and_list(self):
        ref = new_reference("ケーブルA", PROFILE, SERIAL)
        seg = ref_from(MHZ, MHZ, 10, linear_db, kind=KIND_WIDE)
        seg.power_w[3] = np.nan
        ref.merge(seg)
        ref.merge(ref_from(4 * MHZ + 500_000, 100_000, 5, linear_db, taken_at=2100.0, image_reject="accurate"))
        with tempfile.TemporaryDirectory() as d:
            p = save_reference(d, ref)
            self.assertEqual(Path(p).suffix, ".h5")
            import h5py
            with h5py.File(p, "r") as f:
                self.assertEqual(int(f.attrs["version"]), 3)  # 3: 基準の値（設計E §6.7）
                self.assertEqual(f["segments/1"].attrs["image_reject"], "accurate")
                self.assertNotIn("image_cancel", f["segments/1"].attrs)
                self.assertEqual(f["segments/0"].attrs["start_hz"].dtype, np.int64)
                self.assertTrue(np.isnan(f["segments/0/power_w"][3]))
            back = load_reference(p)
            self.assertEqual(back.name, "ケーブルA")
            self.assertEqual(back.band_edges_hz, ref.band_edges_hz)
            self.assertEqual(len(back.segments), 2)
            for a, b in zip(ref.segments, back.segments):
                self.assertEqual(a.axis, b.axis)
                np.testing.assert_array_equal(a.power_w, b.power_w)
                self.assertEqual((a.image_reject, a.rbw_hz, a.connected_at, a.taken_at, a.kind),
                                 (b.image_reject, b.rbw_hz, b.connected_at, b.taken_at, b.kind))
            other = new_reference("B", PROFILE, SERIAL)
            other.merge(ref_from(MHZ, MHZ, 3, linear_db, taken_at=5000.0))
            save_reference(d, other)
            Path(d, "broken.h5").write_bytes(b"not hdf5")
            infos = list_references(d)
            self.assertEqual([i.name for i in infos], ["B", "ケーブルA"])
            self.assertTrue(delete_reference(d, other.ref_id))
            self.assertEqual([i.name for i in list_references(d)], ["ケーブルA"])

    def test_old_version_image_cancel_is_read_as_fast_or_off(self):
        # 前の版（版 1）の基準の区間の image_cancel: true → "fast"、false → "off"（設計F §7.3）
        import h5py

        ref = new_reference("old", PROFILE, SERIAL)
        ref.merge(ref_from(MHZ, MHZ, 3, linear_db, image_reject="fast"))
        ref.merge(ref_from(10 * MHZ, MHZ, 3, linear_db, image_reject="off"))
        with tempfile.TemporaryDirectory() as d:
            p = save_reference(d, ref)
            with h5py.File(p, "r+") as f:
                f.attrs["version"] = 1
                for key, ic in (("0", True), ("1", False)):
                    g = f["segments"][key]
                    del g.attrs["image_reject"]
                    g.attrs["image_cancel"] = ic
            back = load_reference(p)
            self.assertEqual([s.image_reject for s in back.segments], ["fast", "off"])
            # 当てるときも新しい選択で一致を見る
            self.assertTrue(apply(make_frame(MHZ, MHZ, 3, np.ones(3), image_reject="fast"), back).applied)

    def test_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            import h5py
            p = Path(d, "x.h5")
            with h5py.File(p, "w") as f:
                f.attrs["format"] = "pygigast-reference"
                f.attrs["version"] = 99
            with self.assertRaises(ReferenceFileError):
                load_reference(p)
            with h5py.File(p, "w") as f:
                f.attrs["format"] = "pygigast-reference"
                f.attrs["version"] = 1
                f.attrs["id"] = "../x"
                f.create_group("segments")
            with self.assertRaises(ReferenceFileError):
                load_reference(p)
            with self.assertRaises(ReferenceFileError):
                delete_reference(d, "../x")


if __name__ == "__main__":
    unittest.main()
