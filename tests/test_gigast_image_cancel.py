"""イメージキャンセルの4回の合わせ方（設計F §6 の 2）と読み値の注意。純粋な計算を確かめる。"""
import unittest

import numpy as np

from pygigast.core.devices.gigast import image_cancel as ic
from pygigast.core.devices.gigast import protocol
from pygigast.core.types import RawSweep

from tests.gigast_helpers import adopted

P = protocol.default_profile()
CPD = ic.counts_per_db(P)  # 1dB あたりの count（1986/80）
FLOOR_30K = ic.noise_floor_ad(P, 30_000)


def ad(db_above_floor, floor=FLOOR_30K):
    return floor + np.asarray(db_above_floor, dtype=np.float64) * CPD


def jitter(rng, level_db, sigma_db, n):
    """1回ごとに揺れる値（RBW 30k・15k・肩: T12）を AD の値で。"""
    return ad(level_db + rng.normal(0.0, sigma_db, n))


class TestProfile(unittest.TestCase):
    def test_floor_per_rbw(self):
        # 180kHz 約 −74dBm・30kHz 約 −77dBm（補正なしの換算 AD×80/1986 − 80）。15kHz は仮の値
        self.assertAlmostEqual(149 / CPD - 80, -74.0, delta=0.1)
        self.assertAlmostEqual(FLOOR_30K / CPD - 80, -77.0, delta=0.1)
        for rbw in protocol.RBW_CODES:
            self.assertIsNotNone(ic.noise_floor_ad(P, rbw))
        self.assertIsNone(ic.noise_floor_ad(P, 12_345))


class TestCombine(unittest.TestCase):
    def test_burst_on_noise_floor_removed(self):
        # 雑音の床の上の山（床 +10dB）は、同じ b3 のもう1回が床なら低い方を採って消える
        n = 1000
        floor = ad(np.zeros(n))
        burst = ad(np.full(n, 10.0))
        out = ic.combine([burst, floor, floor, burst], FLOOR_30K, CPD)
        np.testing.assert_allclose(out, np.rint(floor), atol=1)

    def test_image_removed(self):
        # イメージは片方の b3 の2回にだけ出る。両側の min で消える（2回のときと同じ）
        n = 10
        noise = ad(np.zeros(n))
        image = ad(np.full(n, 60.0))
        out = ic.combine([image, noise, image, noise], FLOOR_30K, CPD)
        np.testing.assert_allclose(out, np.rint(noise), atol=1)

    def test_burst_on_image_position_removed(self):
        # イメージの位置（b3=0 側が全部イメージ）で、b3=1 側の1回に山が乗っても、もう1回の床を採る
        # （2回のときは比べる相手がおらず山が残った: imagecheck の 946MHz）
        n = 10
        image = ad(np.full(n, 60.0))
        floor = ad(np.zeros(n))
        burst = ad(np.full(n, 10.0))
        out = ic.combine([image, burst, image, floor], FLOOR_30K, CPD)
        np.testing.assert_allclose(out, np.rint(floor), atol=1)
        two_pass = np.minimum(image, burst)
        self.assertTrue((out < two_pass - 8 * CPD).all())

    def test_jittery_signal_not_biased_below_two_pass_min(self):
        # 1回ごとに揺れる弱くない信号（床 +20dB・σ 3.35dB / 4.5dB）: 平均で合わせるので、2回の min より
        # 低く偏らない（設計F §6 の 2 の理由: 常に min を取ると4回から最小を選んで低く偏った）
        rng = np.random.default_rng(7)
        n = 200_000
        for sigma in (1.0, 3.35, 4.5):
            p = [jitter(rng, 20.0, sigma, n) for _ in range(4)]
            four = ic.combine(p, FLOOR_30K, CPD).astype(np.float64)
            two = np.minimum(p[0], p[1])  # いまの2回の min（b3=0 と b3=1）
            true = ad(np.full(n, 20.0))
            bias4 = np.mean(four - true) / CPD
            bias2 = np.mean(two - true) / CPD
            self.assertGreaterEqual(bias4, bias2, f"σ {sigma}: 4回 {bias4:.2f}dB / 2回 {bias2:.2f}dB")

    def test_strong_signal_is_power_average(self):
        # 床から遠い信号は、同じ b3 の2回の差が 6dB を超えても低い方を採らない（電力の平均）
        hi = ad([40.0])
        lo = ad([32.0])
        side = ic.combine_same_b3(hi, lo, FLOOR_30K, CPD)
        want = 10 * np.log10((10 ** (40 / 10) + 10 ** (32 / 10)) / 2) * CPD + FLOOR_30K
        self.assertAlmostEqual(float(side[0]), want, places=6)

    def test_small_difference_near_floor_is_average(self):
        # 床の近くでも差が 6dB 以下なら平均（雑音どうし）
        a, b = ad([0.0]), ad([5.9])
        side = ic.combine_same_b3(a, b, FLOOR_30K, CPD)
        self.assertGreater(float(side[0]), float(a[0]))
        self.assertLess(float(side[0]), float(b[0]))

    def test_unknown_floor_always_averages(self):
        a, b = ad([0.0]), ad([10.0])
        side = ic.combine_same_b3(a, b, None, CPD)
        self.assertGreater(float(side[0]), float(a[0]) + 6 * CPD)

    def test_needs_four_passes(self):
        with self.assertRaises(ValueError):
            ic.combine([ad([0.0])] * 2, FLOOR_30K, CPD)


class TestNote(unittest.TestCase):
    def raw(self, values, rbw_hz=30_000, passes=True):
        a = adopted(points=len(values), rbw_hz=rbw_hz, image_cancel=passes, start_hz=495_000_000, step_hz=2_000)
        v = np.asarray(values, dtype=np.uint16)
        return RawSweep(a, v, "gigast_ad_u16", 0.0, 0.1, raw_passes=np.stack([v] * 4) if passes else None)

    def test_near_floor_flag(self):
        near = int(FLOOR_30K + 2.9 * CPD)
        far = int(FLOOR_30K + 3.2 * CPD) + 1
        flags = ic.note_flags(self.raw([int(FLOOR_30K) - 40, near, far, 2000]), P)
        np.testing.assert_array_equal(flags[ic.NOTE_IC_NOISE_FLOOR], [True, True, False, False])

    def test_no_flag_without_four_passes(self):
        self.assertEqual(ic.note_flags(self.raw([50, 60], passes=False), P), {})

    def test_no_flag_for_profile_without_floor(self):
        self.assertEqual(ic.note_flags(self.raw([50, 60]), {"raw": P["raw"]}), {})


if __name__ == "__main__":
    unittest.main()
