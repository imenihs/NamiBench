"""IQ からスペクトル・継ぎ合わせ（設計Q §6.4）の試験。合成した IQ で確かめる。"""
from __future__ import annotations

import math
import unittest

import numpy as np

from namibench.core.devices.rtlsdr import spectrum as sp

FS = 2_048_000
NS = (128, 256, 512, 1024, 2048, 4096, 8192, 16384)
K = 8


def tone(offset_hz: float, amp: float, n: int, phase: float = 0.3) -> np.ndarray:
    t = np.arange(n)
    return amp * np.exp(1j * (2 * np.pi * offset_hz * t / FS + phase))


def to_u8(iq: np.ndarray) -> np.ndarray:
    """複素数（振り切れ 1.0）を 8bit の符号なし（I, Q の順）へ。"""
    i = np.clip(np.round(iq.real * sp.FULL_SCALE + sp.FULL_SCALE), 0, 255)
    q = np.clip(np.round(iq.imag * sp.FULL_SCALE + sp.FULL_SCALE), 0, 255)
    out = np.empty(2 * iq.size, dtype=np.uint8)
    out[0::2] = i
    out[1::2] = q
    return out


def db(x):
    return 10 * np.log10(x)


class TestWindows(unittest.TestCase):
    def test_enbw(self):
        for n in NS:
            self.assertAlmostEqual(sp.enbw_bins("hann", n), 1.5, places=9)
            self.assertAlmostEqual(sp.enbw_bins("flattop", n), 3.7702, places=3)

    def test_unknown_window(self):
        with self.assertRaises(ValueError):
            sp.window("blackman", 128)


class TestLevel(unittest.TestCase):
    def test_cw_same_level_for_every_window_and_n_at_bin_centre(self):
        # 48kHz はどの N でも格子の上（fs/128 = 16kHz の倍数）
        amp = 0.5
        want = db(amp ** 2)  # −6.02dBFS
        for win in sp.WINDOWS:
            for n in NS:
                p = sp.averaged_power(tone(48_000, amp, n * K), n, win, K)
                k = n // 2 + 48_000 // (FS // n)
                self.assertAlmostEqual(db(p[k]), want, delta=0.1, msg=f"{win} N={n}")
                self.assertEqual(int(np.argmax(p)), k)

    def test_full_scale_is_0dbfs_through_u8(self):
        n = 2048
        for win in sp.WINDOWS:
            iq = sp.iq_from_u8(to_u8(tone(64_000, 1.0, n * K)))
            p = sp.averaged_power(iq, n, win, K)
            self.assertAlmostEqual(db(p.max()), 0.0, delta=0.1, msg=win)

    def test_cw_level_through_u8(self):
        for win in sp.WINDOWS:
            for n in (256, 4096):
                iq = sp.iq_from_u8(to_u8(tone(-96_000, 0.25, n * K)))
                p = sp.averaged_power(iq, n, win, K)
                self.assertAlmostEqual(db(p.max()), db(0.25 ** 2), delta=0.1, msg=f"{win} N={n}")

    def test_scalloping_off_bin(self):
        # 格子の真ん中: Hann は最大約 1.42dB 低い（既知の上限）。Flat top はほぼ 0
        amp = 0.5
        for n in (512, 4096):
            g = FS // n
            for frac in (0.1, 0.25, 0.5):
                off = 10 * g + frac * g
                hann = db(sp.averaged_power(tone(off, amp, n * K), n, "hann", K).max()) - db(amp ** 2)
                flat = db(sp.averaged_power(tone(off, amp, n * K), n, "flattop", K).max()) - db(amp ** 2)
                self.assertLessEqual(hann, 0.0 + 1e-9)
                self.assertGreaterEqual(hann, -1.43, msg=f"N={n} frac={frac}")
                self.assertLess(abs(flat), 0.02, msg=f"N={n} frac={frac}")

    def test_noise_scales_with_enbw(self):
        # 白色雑音の格子の電力 = σ² × ENBW / N（スペアナで RBW に比例するのと同じ）
        rng = np.random.default_rng(1)
        sigma2 = 0.01
        for win in sp.WINDOWS:
            n = 1024
            k = 400
            z = (rng.normal(size=n * k) + 1j * rng.normal(size=n * k)) * math.sqrt(sigma2 / 2)
            p = sp.averaged_power(z, n, win, k)
            want = sigma2 * sp.enbw_bins(win, n) / n
            self.assertAlmostEqual(float(p.mean()) / want, 1.0, delta=0.05, msg=win)

    def test_short_input(self):
        with self.assertRaises(ValueError):
            sp.averaged_power(np.zeros(100, complex), 128, "hann", 1)

    def test_overload_ratio(self):
        b = np.full(20000, 128, dtype=np.uint8)
        self.assertEqual(sp.overload_ratio(b), 0.0)
        b[0:20:2] = 255  # 10 標本の I が端
        self.assertAlmostEqual(sp.overload_ratio(b), 10 / 10000)
        b[1] = 0  # 同じ標本の Q も端: 数は増えない
        self.assertAlmostEqual(sp.overload_ratio(b), 10 / 10000)


def axis_for(start: int, stop: int, grid: int) -> tuple[int, int]:
    """掃引条件モデルと同じ点: 点数 = ceil(Span/Step)+1。(points, last_hz)。"""
    points = -(-(stop - start) // grid) + 1
    return points, start + grid * (points - 1)


class TestPlan(unittest.TestCase):
    FMIN = 24_000_000
    FMAX = 1_766_000_000

    def params(self, n):
        grid = FS // n
        return grid, sp.half_usable_hz(FS, 0.8, grid), sp.dc_delta_hz(grid, 2, 5000)

    def test_half_and_delta(self):
        self.assertEqual(self.params(16384), (125, 819_125, 5000))
        self.assertEqual(self.params(128), (16000, 816_000, 32000))
        self.assertEqual(self.params(2048), (1000, 819_000, 5000))

    def test_realtime_center_below_start(self):
        for n in NS:
            grid, half, delta = self.params(n)
            start = 84_200_000
            points, last = axis_for(start, start + half - delta - grid, grid)
            pl = sp.plan(start, last, grid, half, delta, self.FMIN, self.FMAX)
            self.assertTrue(pl.realtime)
            self.assertEqual(pl.centers, (start - delta,))
            self.assertLessEqual(last - pl.centers[0], half)

    def test_realtime_at_lower_limit_uses_stop_side(self):
        grid, half, delta = self.params(2048)
        start = self.FMIN
        points, last = axis_for(start, start + 500_000, grid)
        pl = sp.plan(start, last, grid, half, delta, self.FMIN, self.FMAX)
        self.assertEqual(pl.centers, (last + delta,))
        # N−1 / N / N+1: 下限 + δ の前後
        for s, want in ((self.FMIN + delta - grid, "stop"), (self.FMIN + delta, "start"),
                        (self.FMIN + delta + grid, "start")):
            _, last = axis_for(s, s + 500_000, grid)
            pl = sp.plan(s, last, grid, half, delta, self.FMIN, self.FMAX)
            self.assertEqual(pl.centers[0], s - delta if want == "start" else last + delta)

    def test_realtime_boundary(self):
        grid, half, delta = self.params(1024)
        start = 100_000_000
        for extent, rt in ((half - delta - grid, True), (half - delta, True), (half - delta + grid, False)):
            pl = sp.plan(start, start + extent, grid, half, delta, self.FMIN, self.FMAX)
            self.assertEqual(pl.realtime, rt, extent)

    def check_cover(self, start, stop, n):
        grid, half, delta = self.params(n)
        points, last = axis_for(start, stop, grid)
        pl = sp.plan(start, last, grid, half, delta, self.FMIN, self.FMAX)
        c = np.array(pl.centers)
        self.assertTrue(np.all((c - start) % grid == 0))
        self.assertGreaterEqual(c.min(), start if not pl.realtime else self.FMIN)
        self.assertLessEqual(c.max(), self.FMAX)
        if not pl.realtime:
            self.assertEqual(c[0], start)
            self.assertEqual(c[-1], last)
            gaps = np.diff(c)
            self.assertLessEqual(gaps.max(), half)
            self.assertGreaterEqual(gaps.min(), 2 * delta)
        asm = sp.Assembler(start, grid, points, half, delta)
        for x in pl.centers:
            asm.add(x, np.ones(n))
        self.assertFalse(asm.no_value.any(), f"{start}-{stop} N={n}: 穴 {np.flatnonzero(asm.no_value)[:5]}")
        # どの点も、採った位置の中心から δ 以上 h 以下
        f = start + grid * np.arange(points)
        d = np.abs(f - c[asm.source])
        self.assertGreaterEqual(d.min(), delta)
        self.assertLessEqual(d.max(), half)
        return pl

    def test_stitch_coverage_many_spans(self):
        rng = np.random.default_rng(7)
        for n in NS:
            grid, half, delta = self.params(n)
            spans = [half - delta, half - delta + 1, half, 2 * half, 2 * half + 1, 1_640_000, 6_000_000]
            spans += list(rng.integers(half - delta, 6_000_000, 20))
            for span in spans:
                start = int(rng.integers(self.FMIN, 1_000_000_000))
                self.check_cover(start, start + int(span), n)

    def test_fm_band_and_full_range(self):
        pl = self.check_cover(76_000_000, 95_000_000, 2048)
        self.assertEqual(len(pl.centers), 25)
        self.check_cover(self.FMIN, self.FMAX - 7000, 128)

    def test_fixed_center_leaves_dc_without_value(self):
        grid, half, delta = self.params(4096)
        start = 84_600_000
        points, last = axis_for(start, 84_800_000, grid)
        c = 84_700_000
        pl = sp.plan(start, last, grid, half, delta, self.FMIN, self.FMAX, fixed_center_hz=c)
        asm = sp.Assembler(start, grid, points, half, delta)
        asm.add(pl.centers[0], np.ones(4096))
        f = start + grid * np.arange(points)
        np.testing.assert_array_equal(asm.no_value, np.abs(f - c) < delta)

    def test_dc_excluded_and_nearer_centre_wins(self):
        grid, half, delta = 1000, 819_000, 5000
        asm = sp.Assembler(0, grid, 3001, half, delta)
        n = 2048
        asm.add(1_000_000, np.full(n, 1.0))
        asm.add(1_800_000, np.full(n, 2.0))
        f = grid * np.arange(3001)
        v = asm.values
        # DC ±δ 未満は使わない
        self.assertTrue(np.all(v[np.abs(f - 1_000_000) < delta] == 2.0))
        # 1.4MHz より下は 1.0MHz の位置（近い方）、上は 1.8MHz の位置
        self.assertEqual(v[1_300], 1.0)
        self.assertEqual(v[1_500], 2.0)
        self.assertEqual(v[1_401], 2.0)
        # 使える幅の外は値なし
        self.assertTrue(np.isnan(v[180]))


class TestPassband(unittest.TestCase):
    """チューナーの通過帯域の形の表と、継ぎ合わせでの割り戻し（設計Q §6.9）。"""

    TABLE = {"offset_hz": [0, 400_000, 800_000], "upper_db": [0.0, -1.0, -3.0], "lower_db": [0.0, 1.0, -2.0]}

    def test_interpolation_sides_and_clamp(self):
        pb = sp.Passband(**self.TABLE)
        np.testing.assert_allclose(pb.db([0, 200_000, 600_000, -200_000, -600_000, 900_000, -900_000]),
                                   [0.0, -0.5, -2.0, 0.5, -0.5, -3.0, -2.0])

    def test_invalid_table(self):
        for t in ({"offset_hz": [0], "upper_db": [0], "lower_db": [0]},
                  {"offset_hz": [10, 20], "upper_db": [0, 0], "lower_db": [0, 0]},
                  {"offset_hz": [0, 20, 20], "upper_db": [0, 0, 0], "lower_db": [0, 0, 0]},
                  {"offset_hz": [0, 20], "upper_db": [0], "lower_db": [0, 0]}):
            with self.assertRaises(ValueError, msg=t):
                sp.Passband(**t)

    def test_from_profile(self):
        self.assertIsNone(sp.Passband.from_profile({"fft": {}}))
        pb = sp.Passband.from_profile({"fft": {"passband": self.TABLE}})
        self.assertAlmostEqual(float(pb.db(400_000)), -1.0)

    def test_synthetic_rolloff_is_divided_out(self):
        # 合成の落ち込みを掛けた平らな電力を、2つの位置から継ぎ合わせる → どの点も 1.0 に戻る
        grid, half, delta, n = 1000, 819_000, 5000, 2048
        pb = sp.Passband(**self.TABLE)
        k = np.arange(-(n // 2), n - n // 2)
        rolled = 10 ** (pb.db(k * grid) / 10)
        asm = sp.Assembler(0, grid, 2601, half, delta, pb)
        asm.add(1_000_000, rolled)
        asm.add(1_800_000, rolled)
        ok = ~asm.no_value
        self.assertGreater(int(ok.sum()), 2000)
        np.testing.assert_allclose(asm.values[ok], 1.0, rtol=1e-12)
        # 割り戻さないと、中心の近く（δ 未満）は隣の位置の端（800kHz 離れ、上側 −3dB）の値になる
        plain = sp.Assembler(0, grid, 2601, half, delta)
        plain.add(1_000_000, rolled)
        plain.add(1_800_000, rolled)
        self.assertAlmostEqual(db(plain.values[1_800]), -3.0, places=6)  # 1.0MHz の位置から +800kHz（上側）
        self.assertAlmostEqual(db(plain.values[1_000]), -2.0, places=6)  # 1.8MHz の位置から −800kHz（下側）


if __name__ == "__main__":
    unittest.main()
