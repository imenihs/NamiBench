"""イメージ除去の回の合わせ方（設計F §7.1・設計F §6 の 2）。純粋な計算を確かめる。

- 1回はそのまま・2回は小さい方・4回は同じ b3 の2回を床の条件で合わせてから b3 の両側の小さい方
- 床: RBW 180kHz はその場の床（4回の小さい方を ±10 点の中央値でならした値）、30kHz・15kHz はプロファイルの床
"""
import unittest

import numpy as np

from namibench.core.devices.gigast import image_reject as ir
from namibench.core.devices.gigast import protocol

P = protocol.default_profile()
CPD = ir.counts_per_db(P)  # 1dB あたりの count（1986/80）
FLOOR_30K = ir.noise_floor_ad(P, 30_000)


def ad(db_above_floor, floor=FLOOR_30K):
    return floor + np.asarray(db_above_floor, dtype=np.float64) * CPD


def jitter(rng, level_db, sigma_db, n):
    """1回ごとに揺れる値（RBW 30k・15k・肩: T12）を AD の値で。"""
    return ad(level_db + rng.normal(0.0, sigma_db, n))


class TestProfile(unittest.TestCase):
    def test_floor_per_rbw(self):
        # 180kHz 約 −74dBm・30kHz 約 −77dBm（補正なしの換算 AD×80/1986 − 80）。15kHz も約 −77dBm
        self.assertAlmostEqual(149 / CPD - 80, -74.0, delta=0.1)
        self.assertAlmostEqual(FLOOR_30K / CPD - 80, -77.0, delta=0.1)
        for rbw in protocol.RBW_CODES:
            self.assertIsNotNone(ir.noise_floor_ad(P, rbw))
        self.assertIsNone(ir.noise_floor_ad(P, 12_345))

    def test_profile_values(self):
        # 帯・帯の中の回数・その場の床の RBW とならす幅・初期値（設計F §7.1・§7.3）
        r = P["image_reject"]
        self.assertEqual(r["burst_bands_hz"], [[-100_000_000, 1_100_000_000], [2_950_000_000, 4_050_000_000]])
        self.assertEqual(r["accurate_in_band_passes"], 4)
        self.assertEqual(r["local_floor_rbw_hz"], [180_000])
        self.assertEqual(r["local_floor_half_points"], 10)
        self.assertEqual(r["default"], "fast")
        self.assertEqual(r["basis"], "measured_on_one_unit")
        self.assertNotIn("image_cancel", P)
        self.assertNotIn("image_cancel_default", P)


class TestCombine4(unittest.TestCase):
    def test_burst_on_noise_floor_removed(self):
        # 雑音の床の上の山（床 +10dB）は、同じ b3 のもう1回が床なら低い方を採って消える
        n = 1000
        floor = ad(np.zeros(n))
        burst = ad(np.full(n, 10.0))
        out = ir.combine4([burst, floor, floor, burst], FLOOR_30K, CPD)
        np.testing.assert_allclose(out, np.rint(floor), atol=1)

    def test_image_removed(self):
        # イメージは片方の b3 の2回にだけ出る。両側の min で消える（2回のときと同じ）
        n = 10
        noise = ad(np.zeros(n))
        image = ad(np.full(n, 60.0))
        out = ir.combine4([image, noise, image, noise], FLOOR_30K, CPD)
        np.testing.assert_allclose(out, np.rint(noise), atol=1)

    def test_burst_on_image_position_removed(self):
        # イメージの位置（b3=0 側が全部イメージ）で、b3=1 側の1回に山が乗っても、もう1回の床を採る
        # （2回のときは比べる相手がおらず山が残った: imagecheck の 946MHz）
        n = 10
        image = ad(np.full(n, 60.0))
        floor = ad(np.zeros(n))
        burst = ad(np.full(n, 10.0))
        out = ir.combine4([image, burst, image, floor], FLOOR_30K, CPD)
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
            four = ir.combine4(p, FLOOR_30K, CPD).astype(np.float64)
            two = np.minimum(p[0], p[1])  # 2回の min（b3=0 と b3=1）
            true = ad(np.full(n, 20.0))
            bias4 = np.mean(four - true) / CPD
            bias2 = np.mean(two - true) / CPD
            self.assertGreaterEqual(bias4, bias2, f"σ {sigma}: 4回 {bias4:.2f}dB / 2回 {bias2:.2f}dB")

    def test_strong_signal_is_power_average(self):
        # 床から遠い信号は、同じ b3 の2回の差が 6dB を超えても低い方を採らない（電力の平均）
        hi = ad([40.0])
        lo = ad([32.0])
        side = ir.combine_same_b3(hi, lo, FLOOR_30K, CPD)
        want = 10 * np.log10((10 ** (40 / 10) + 10 ** (32 / 10)) / 2) * CPD + FLOOR_30K
        self.assertAlmostEqual(float(side[0]), want, places=6)

    def test_small_difference_near_floor_is_average(self):
        # 床の近くでも差が 6dB 以下なら平均（雑音どうし）
        a, b = ad([0.0]), ad([5.9])
        side = ir.combine_same_b3(a, b, FLOOR_30K, CPD)
        self.assertGreater(float(side[0]), float(a[0]))
        self.assertLess(float(side[0]), float(b[0]))

    def test_unknown_floor_always_averages(self):
        a, b = ad([0.0]), ad([10.0])
        side = ir.combine_same_b3(a, b, None, CPD)
        self.assertGreater(float(side[0]), float(a[0]) + 6 * CPD)

    def test_needs_four_passes(self):
        with self.assertRaises(ValueError):
            ir.combine4([ad([0.0])] * 2, FLOOR_30K, CPD)


class TestCombineByB3(unittest.TestCase):
    """区間の b3 の並びで合わせ方が決まる（設計F §7.1）。"""

    def test_one_pass_as_is(self):
        v = np.array([[5, 6, 7]], dtype=np.uint16)
        np.testing.assert_array_equal(ir.combine(v, (1,), 180_000, P), [5, 6, 7])

    def test_two_passes_min(self):
        v = np.array([[5, 60, 7], [9, 6, 70]], dtype=np.uint16)
        out = ir.combine(v, (0, 1), 30_000, P)
        np.testing.assert_array_equal(out, [5, 6, 7])
        self.assertEqual(out.dtype, np.uint16)

    def test_four_passes_uses_combine4(self):
        v = np.array([[100, 500], [200, 400], [100, 500], [200, 400]], dtype=np.uint16)
        np.testing.assert_array_equal(ir.combine(v, (0, 1, 0, 1), 30_000, P), [100, 400])

    def test_unknown_b3_order_rejected(self):
        with self.assertRaises(ValueError):
            ir.combine(np.zeros((3, 2), dtype=np.uint16), (0, 1, 0), 180_000, P)
        with self.assertRaises(ValueError):
            ir.combine(np.zeros((2, 2), dtype=np.uint16), (0, 1, 0, 1), 180_000, P)


class TestFloor(unittest.TestCase):
    """床の選び方: 180kHz はその場の床、30kHz・15kHz はプロファイルの床（設計F §7.1）。"""

    def test_local_floor_is_median_of_min(self):
        # 4回の点ごとの小さい方を ±10 点の中央値でならす。1点だけ高い所（信号）は中央値で床に戻る
        low = np.full(50, 100.0)
        low[25] = 900.0
        passes = np.stack([low + 5, low, low + 10, low + 3])
        f = ir.local_floor(passes, 10)
        self.assertEqual(f.shape, (50,))
        np.testing.assert_allclose(f, 100.0)

    def test_local_floor_edges_use_points_inside(self):
        # 区間の両端は、区間の中にある点だけで中央値を取る（外を 0 などで埋めて床を下げない）
        low = np.arange(30, dtype=np.float64)
        f = ir.local_floor(np.stack([low, low]), 10)
        self.assertAlmostEqual(f[0], np.median(low[:11]))
        self.assertAlmostEqual(f[15], np.median(low[5:26]))
        self.assertAlmostEqual(f[-1], np.median(low[-11:]))

    def test_floor_choice_by_rbw(self):
        passes = np.full((4, 30), 400.0)
        np.testing.assert_allclose(ir.floor_for(passes, 180_000, P), 400.0)  # その場の床
        self.assertEqual(ir.floor_for(passes, 30_000, P), ir.noise_floor_ad(P, 30_000))
        self.assertEqual(ir.floor_for(passes, 15_000, P), ir.noise_floor_ad(P, 15_000))

    def test_local_floor_finds_burst_near_signal(self):
        # 信号の近く（位相雑音で床が上がった所: 床 +15dB）で、b3=1 側がイメージで埋まった点に、b3=0 側の1回だけ
        # 山が乗る: プロファイルの床（149）では床の近くと見なされず平均で山が残るが、その場の床なら低い方を採って
        # 消える（T17 のその場の床の理由）
        n = 41
        raised = np.full(n, 149 + 15 * CPD)
        burst = raised.copy()
        burst[20] += 10 * CPD
        image = raised.copy()
        image[20] += 30 * CPD
        passes = np.stack([burst, image, raised, image])
        local = ir.combine(passes.astype(np.uint16), (0, 1, 0, 1), 180_000, P)
        profile_floor = ir.combine4(passes, ir.noise_floor_ad(P, 180_000), CPD)
        self.assertLessEqual(int(local[20]), int(raised[20]) + 1)
        self.assertGreater(int(profile_floor[20]), int(raised[20]) + 5 * CPD)


if __name__ == "__main__":
    unittest.main()
