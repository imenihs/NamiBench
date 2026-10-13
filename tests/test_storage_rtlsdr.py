"""RTL-SDR の生の値（rtlsdr_power_w: リニアの電力の float32、値の無い点は NaN）の記録・保存・読み戻し
（設計Q §6.1・設計H §7.1・§7.2）と、今までの uint16 のファイルが読めること。すべて一時フォルダの中で行う。
"""
from __future__ import annotations

import json
import sqlite3
import unittest

import h5py
import numpy as np

from namibench.core.storage.h5file import OVERVIEW_LEVELS, H5Record, save_snapshot
from namibench.core.storage.recording import Recorder, RecordingReader, convert_to_h5, new_recording_path
from namibench.core.types import RawSweep
from tests.test_storage import MHZ, TmpCase, adopted, header, pattern, raw_sweep

ENC = "rtlsdr_power_w"
T0 = 1_780_000_000.0


def rtl_extra(gain_db=29.7, window="hann") -> dict:
    """機器の層（devices/rtlsdr/driver.py）が返す extra と同じ項目。"""
    return {
        "gain_db": gain_db, "gain_mode": "manual", "ppm": 59, "window": window, "fft_n": 2048, "averages": 8,
        "sample_rate_hz": 2_048_000, "dc_delta_hz": 5000, "passband_corrected": True, "half_usable_hz": 819_000,
        "realtime": False, "positions": 3, "retunes": 3, "centers_hz": [1, 2, 3], "fixed_center": False,
        "overload": False, "overload_ratio_max": 0.0, "flags": {"no_value": np.zeros(11, dtype=bool)},
    }


def power(i: int, n: int) -> np.ndarray:
    """掃引 i のリニアの電力（float64。機器の層と同じ型）。点 2 は値が無い（NaN）、点 5 は掃引 0 だけ NaN。"""
    v = (np.arange(n, dtype=np.float64) + 1.0) * 1e-6 * (1.0 + 0.1 * i)
    v[2] = np.nan
    if i == 0:
        v[5] = np.nan
    return v


def rtl_sweep(i: int, a, gain_db=29.7) -> RawSweep:
    return RawSweep(a, power(i, a.axis.points), ENC, T0 + i, 0.05, extra=rtl_extra(gain_db))


class TestRtlSdrH5(TmpCase):
    def test_snapshot_round_trip(self):
        a = adopted(points=11)
        out = save_snapshot(self.dir / "s.h5", header(), rtl_sweep(0, a))
        with h5py.File(out, "r") as f:
            g = f["segments/0000"]
            self.assertEqual(g["raw"].dtype, np.float32)
            self.assertEqual(g.attrs["raw_encoding"], ENC)
            ex = json.loads(g.attrs["raw_extra"])
            self.assertEqual(ex["gain_db"], 29.7)
            # 掃引ごとに変わる項目・点ごとの印は区間の属性に持たない
            for k in ("retunes", "overload", "overload_ratio_max", "centers_hz", "flags", "positions"):
                self.assertNotIn(k, ex)
        with H5Record(out) as h:
            r = h.raw_sweep(0, 0)
            self.assertEqual(r.encoding, ENC)
            self.assertEqual(r.raw.dtype, np.float64)
            np.testing.assert_allclose(r.raw, power(0, 11).astype(np.float32), equal_nan=True)
            self.assertEqual(r.extra["gain_db"], 29.7)
            self.assertEqual(r.extra["window"], "hann")
            np.testing.assert_array_equal(r.extra["flags"]["no_value"], np.isnan(power(0, 11)))

    def test_integer_raw_refused_for_float_encoding(self):
        a = adopted(points=11)
        bad = RawSweep(a, pattern(0, 11), ENC, T0, 0.05, extra=rtl_extra())
        with self.assertRaises(TypeError):
            save_snapshot(self.dir / "s.h5", header(), bad)
        self.assertFalse((self.dir / "s.h5").exists())


class TestRtlSdrRecording(TmpCase):
    def test_record_convert_overview_ignores_nan(self):
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        a = adopted(points=11)
        for i in range(8):
            rec.append(rtl_sweep(i, a))
        for i in range(8, 12):  # 利得を変えた → 値の意味が変わるので新しい区間（設計Q §6.5・設計H §3）
            rec.append(rtl_sweep(i, a, gain_db=49.6))
        stats = rec.stop().result(10)
        self.assertIsNone(stats.last_error)
        self.assertEqual(stats.segments, 2)
        with RecordingReader(path) as r:
            self.assertEqual([s.n_sweeps for s in r.segments], [8, 4])
            self.assertEqual(r.segments[1].raw_extra["gain_db"], 49.6)
            b = r.read(0)
            self.assertEqual(b.raw.dtype, np.float32)
            np.testing.assert_allclose(b.raw[3], power(3, 11).astype(np.float32), equal_nan=True)
            ov = r.overview(0, 4)
            want = np.fmax.reduce(np.stack([power(i, 11) for i in range(4)]).astype(np.float32), axis=0)
            np.testing.assert_allclose(ov.values[0], want, equal_nan=True)
            self.assertFalse(np.isnan(ov.values[0][5]))  # 掃引 0 だけ NaN の点は、残りの最大
            self.assertTrue(np.isnan(ov.values[0][2]))  # 全部 NaN の点は NaN のまま

        out = convert_to_h5(path, self.dir / "rec.h5")
        self.assertFalse(path.exists())
        with H5Record(out) as h:
            self.assertEqual([s.n_sweeps for s in h.segments], [8, 4])
            self.assertEqual([s.raw_extra["gain_db"] for s in h.segments], [29.7, 49.6])
            for k in OVERVIEW_LEVELS:
                self.assertEqual(h._group(0)["overview"][f"max_{k}"].dtype, np.float32)
            ov = h.overview(0, 4)
            np.testing.assert_allclose(ov.values[0], want, equal_nan=True)
            self.assertFalse(np.isnan(ov.values[0][5]))
            self.assertTrue(np.isnan(ov.values[0][2]))
            r = h.raw_sweep(1, 2)
            self.assertEqual(r.extra["gain_db"], 49.6)
            np.testing.assert_allclose(r.raw, power(10, 11).astype(np.float32), equal_nan=True)

    def test_gigast_and_rtlsdr_in_one_recording(self):
        """記録中に機器をつなぎ替えても、区間が分かれて両方の形を持つ。"""
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        a = adopted(points=11)
        for i in range(3):
            rec.append(raw_sweep(i, a))
        for i in range(3, 6):
            rec.append(rtl_sweep(i, a))
        self.assertIsNone(rec.stop().result(10).last_error)
        out = convert_to_h5(path, self.dir / "mix.h5")
        with H5Record(out) as h:
            self.assertEqual([s.raw_encoding for s in h.segments], ["gigast_ad_u16", ENC])
            self.assertEqual(h.read(0).raw.dtype, np.uint16)
            self.assertEqual(h.read(1).raw.dtype, np.float32)
            np.testing.assert_array_equal(h.raw_sweep(0, 1).raw, pattern(1, 11))


class TestOldUint16Files(TmpCase):
    """raw_extra の無い今までのファイル（GigaSt の AD 値だけ）が読めること。"""

    def test_gigast_h5_layout_unchanged_and_reads(self):
        a = adopted(points=11)
        out = save_snapshot(self.dir / "g.h5", header(), raw_sweep(0, a))
        with h5py.File(out, "r") as f:
            g = f["segments/0000"]
            self.assertNotIn("raw_extra", g.attrs)  # GigaSt では属性を作らない（今までと同じ中身）
            self.assertEqual(g["raw"].dtype, np.uint16)
            self.assertEqual(g["overview/max_4"].dtype, np.uint16)
        with H5Record(out) as h:
            self.assertIsNone(h.segments[0].raw_extra)
            r = h.raw_sweep(0, 0)
            self.assertEqual(r.raw.dtype, np.uint16)
            self.assertEqual(r.extra, {})
            np.testing.assert_array_equal(r.raw, pattern(0, 11))

    def test_old_work_file_without_raw_extra(self):
        """区間の JSON に raw_extra が無い作業ファイル（この変更の前に書いたもの）もまとめられる。"""
        path = new_recording_path(self.dir)
        rec = Recorder(path, header())
        rec.wait_opened(5)
        a = adopted(start=200 * MHZ, points=11)
        for i in range(5):
            rec.append(raw_sweep(i, a))
        rec.stop().result(10)
        conn = sqlite3.connect(str(path))
        with conn:
            for sid, meta in conn.execute("SELECT id, meta FROM segments").fetchall():
                d = json.loads(meta)
                d.pop("raw_extra")
                conn.execute("UPDATE segments SET meta = ? WHERE id = ?", (json.dumps(d), sid))
        conn.close()
        out = convert_to_h5(path, self.dir / "old.h5")
        with H5Record(out) as h:
            self.assertEqual(h.segments[0].n_sweeps, 5)
            np.testing.assert_array_equal(h.read(0).raw[4], pattern(4, 11))
            np.testing.assert_array_equal(h.overview(0, 4).values[0], np.stack([pattern(i, 11) for i in range(4)]).max(axis=0))


if __name__ == "__main__":
    unittest.main()
