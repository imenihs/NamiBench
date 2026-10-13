"""GigaSt の32バイトの要求・見積もり・上限時間（設計B §7.2・設計C §5.5〜§5.7）。"""
import struct
import unittest

from namibench.core.devices.gigast import protocol
from namibench.core.types import InvalidSweep, Mode

from tests.gigast_helpers import adopted

P = protocol.default_profile()


def fields(req: bytes):
    return struct.unpack("<BIHHHHBBI", req[:19])


class TestSample(unittest.TestCase):
    def test_formula_and_even_rounding(self):
        self.assertEqual(protocol.sample_for(1_000_000, 180_000), 11)  # 10000/900 = 11.1
        self.assertEqual(protocol.sample_for(20_000, 180_000), 0)  # 200/900
        self.assertEqual(protocol.sample_for(2_000, 30_000), 0)  # 20/150
        self.assertEqual(protocol.sample_for(0, 180_000), 0)  # ゼロスパン
        # 偶数丸め: 225/150 = 1.5 → 2、375/150 = 2.5 → 2
        self.assertEqual(protocol.sample_for(22_500, 30_000), 2)
        self.assertEqual(protocol.sample_for(37_500, 30_000), 2)

    def test_undefined_rbw(self):
        with self.assertRaises(InvalidSweep):
            protocol.sample_for(20_000, 100_000)


class TestTimingAndLimits(unittest.TestCase):
    def test_timing_matches_design_examples(self):
        # 設計B §7.2: Plot 500・Sample 0 → 92ms、Plot 12000・Sample 11 → 8.51s
        f, p = protocol.timing(P, 20_000, 180_000)
        self.assertAlmostEqual(f + 500 * p, 0.092, places=6)
        f, p = protocol.timing(P, 1_000_000, 180_000)
        self.assertAlmostEqual(f + 12000 * p, 8.511, places=3)

    def test_limits(self):
        lim = protocol.limits(P, 1_000_000, 180_000, 12000, passes=2)
        f, p = protocol.timing(P, 1_000_000, 180_000)
        self.assertAlmostEqual(lim.first_byte_s, f * 2 + 0.5)
        self.assertEqual(lim.silence_s, 1.0)
        self.assertAlmostEqual(lim.total_s, (f + 12000 * p) * 2 + 1.0)
        self.assertAlmostEqual(lim.estimated_s, (f + 12000 * p) * 2)

    def test_timing_fn(self):
        fn = protocol.timing_fn(P)
        self.assertEqual(fn(20_000, 180_000), protocol.timing(P, 20_000, 180_000))


class TestPacket(unittest.TestCase):
    def test_documented_example(self):
        # Center 500 / Span 1MHz / SA / SG 1500MHz（調査資料 §3.1 の例。MODE はそのまま 0）
        ds = protocol.make_device_sweep(P, 499_500_000, 2_000, 500, 180_000, 0, 1_500_000_000)
        self.assertEqual(
            protocol.pack(ds).hex(),
            "55b8374c001400f401000084030000c0e1e400" + "00" * 13,
        )
        self.assertEqual(len(protocol.pack(ds)), 32)

    def test_preroll_and_fields(self):
        a = adopted(start_hz=495_000_000, step_hz=20_000, points=500, preroll=215)
        req = protocol.build_request(a, b3=1)
        h, start, step, plot, sample, inc, rbw, mode, sg = fields(req)
        self.assertEqual(h, 0x55)
        self.assertEqual(start, (495_000_000 - 215 * 20_000) // 100)
        self.assertEqual(step, 200)
        self.assertEqual(plot, 715)
        self.assertEqual(sample, 0)
        self.assertEqual(inc, 900)
        self.assertEqual(rbw, 0)
        self.assertEqual(mode, 0x08)
        self.assertEqual(sg, 0)
        self.assertEqual(req[19:], bytes(13))

    def test_negative_start_31bit_mask(self):
        a = adopted(start_hz=-100_000_000, step_hz=200_000, points=500)
        _, start, *_ = fields(protocol.build_request(a, b3=1))
        self.assertEqual(start, 0x7FF0BDC0)  # T4 の送信値
        self.assertEqual(protocol.unpack(protocol.build_request(a, b3=1)).start_hz, -100_000_000)

    def test_mode_bits(self):
        cases = [
            (Mode.SA, None, 0, 0, 0x00), (Mode.SA, None, 1, 0, 0x08),
            (Mode.TG, None, 1, 0, 0x09), (Mode.SG, 500_000_000, 0, 0, 0x02),
            (Mode.NF, None, 1, 1, 0x0C), (Mode.NF, None, 1, 0, 0x08),
        ]
        for mode, sg, b3, b2, want in cases:
            a = adopted(mode=mode, sg_hz=sg)
            self.assertEqual(fields(protocol.build_request(a, b3, b2))[7], want, (mode, b3, b2))

    def test_rbw_codes(self):
        for rbw, code in ((180_000, 0), (30_000, 1), (15_000, 2)):
            a = adopted(rbw_hz=rbw, step_hz=2_000, points=500)
            f = fields(protocol.build_request(a, 1))
            self.assertEqual(f[6], code)
            self.assertEqual(f[5], 5 * rbw // 1000)


class TestValidation(unittest.TestCase):
    """範囲外は送らずに InvalidSweep（設計B §2 の5）。境界は N−1 / N / N+1（設計C §5.9）。"""

    def ok(self, **kw):
        return protocol.build_request(adopted(**kw), 1)

    def bad(self, b3=1, b2=0, **kw):
        with self.assertRaises(InvalidSweep):
            protocol.build_request(adopted(**kw), b3, b2)

    def test_start_lower_bound(self):
        self.ok(start_hz=-100_000_000, step_hz=100, points=10)
        self.ok(start_hz=-100_000_000 + 100, step_hz=100, points=10)
        self.bad(start_hz=-100_000_000 - 100, step_hz=100, points=10)
        # 前置きで下限を下回るのも止める
        self.bad(start_hz=-100_000_000, step_hz=100, points=10, preroll=1)

    def test_last_point_upper_bound(self):
        self.ok(start_hz=12_100_000_000 - 499 * 100, step_hz=100, points=500)
        self.bad(start_hz=12_100_000_000 - 498 * 100, step_hz=100, points=500)
        self.ok(start_hz=12_100_000_000, step_hz=0, points=10)
        self.bad(start_hz=12_100_000_100, step_hz=0, points=10)

    def test_points(self):
        self.ok(points=12500, step_hz=100, preroll=0)
        self.bad(points=12501, step_hz=100, preroll=0)
        self.bad(points=12000, step_hz=100, preroll=501)

    def test_step(self):
        self.ok(start_hz=0, step_hz=65535 * 100, points=10)
        self.bad(start_hz=0, step_hz=65536 * 100, points=10)

    def test_not_multiple_of_100(self):
        self.bad(start_hz=495_000_050)
        self.bad(step_hz=20_050)

    def test_undefined_rbw(self):
        self.bad(rbw_hz=100_000)

    def test_rbw_fixed_rules(self):
        self.bad(mode=Mode.TG, rbw_hz=30_000, step_hz=2_000)
        self.ok(rbw_hz=30_000, step_hz=100_000, points=501, start_hz=0, stop_hz=50_000_000)
        self.bad(rbw_hz=30_000, step_hz=100_000, points=502, start_hz=0, stop_hz=50_000_100)

    def test_sg_range(self):
        self.bad(mode=Mode.SG, sg_hz=999_000)
        self.ok(mode=Mode.SG, sg_hz=1_000_000)
        self.ok(mode=Mode.SG, sg_hz=4_000_000_000)
        self.bad(mode=Mode.SG, sg_hz=4_000_001_000)
        self.bad(mode=Mode.SG, sg_hz=500_000_100)  # 1kHz 単位でない
        self.bad(mode=Mode.SG, sg_hz=None)

    def test_b2_only_in_nf(self):
        self.bad(mode=Mode.SA, b2=1)
        self.bad(b3=2)


if __name__ == "__main__":
    unittest.main()
