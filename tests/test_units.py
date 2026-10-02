import unittest

from pygigast.core.units import ParseError, drag_ref_level, format_freq, parse_db, parse_freq_hz


class ParseFreqTest(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(parse_freq_hz("1.2G"), 1_200_000_000)
        self.assertEqual(parse_freq_hz("300k"), 300_000)
        self.assertEqual(parse_freq_hz("1500"), 1_500_000_000)  # 既定は MHz
        self.assertEqual(parse_freq_hz("1500", default_exp=0), 1500)
        self.assertEqual(parse_freq_hz("12100M"), 12_100_000_000)
        self.assertEqual(parse_freq_hz("12.1 GHz"), 12_100_000_000)
        self.assertEqual(parse_freq_hz("-100m"), -100_000_000)
        self.assertEqual(parse_freq_hz(".5k"), 500)

    def test_no_float_error(self):
        # float なら 12099.9999 のような桁落ちが出る値
        self.assertEqual(parse_freq_hz("12099.9999001"), 12_099_999_900)
        self.assertEqual(parse_freq_hz("0.1234567891G"), 123_456_789)

    def test_rejects(self):
        for s in ("", "abc", "1.2.3", "1x", "G"):
            with self.assertRaises(ParseError, msg=s):
                parse_freq_hz(s)

    def test_format(self):
        self.assertEqual(format_freq(1_234_567_890), "1234.56789 MHz")
        self.assertEqual(format_freq(12_100_000_000), "12.1 GHz")
        self.assertEqual(format_freq(300_000), "300 kHz")
        self.assertEqual(format_freq(0), "0 Hz")
        self.assertEqual(format_freq(-100_000_000), "-100 MHz")
        self.assertEqual(format_freq(1_000_000_000, 6), "1000 MHz")

    def test_db(self):
        self.assertEqual(parse_db("-10 dBm"), -10.0)
        self.assertEqual(parse_db("5dB"), 5.0)
        with self.assertRaises(ParseError):
            parse_db("x")



class DragRefLevelTest(unittest.TestCase):
    """縦軸のドラッグの REF（設計J §4.1.1）: 1目盛の整数倍の ±0.15 目盛で吸い付き、量は刻まず 0.1 dB に丸める。"""

    def test_snaps_to_whole_divisions(self):
        # 10dB/div・グラフの高さの 10% で 1目盛（10dB）
        self.assertEqual(drag_ref_level(0.0, -0.1, 10), -10.0)
        self.assertEqual(drag_ref_level(0.0, -0.114, 10), -10.0)   # −11.4 → 吸い付く（1.4dB ≦ 1.5dB）
        self.assertEqual(drag_ref_level(0.0, -0.116, 10), -11.6)   # −11.6 → 吸い付かない
        self.assertEqual(drag_ref_level(0.0, 0.2, 5), 10.0)

    def test_not_quantized_and_relative(self):
        self.assertEqual(drag_ref_level(0.0, -0.05, 10), -5.0)     # 半目盛は吸い付かない・刻まない
        self.assertEqual(drag_ref_level(0.0, -0.051, 10), -5.1)
        self.assertEqual(drag_ref_level(-3.7, -0.1, 10), -13.7)    # 動かした量で吸い付く
        self.assertEqual(drag_ref_level(0.0, 0.0, 10), 0.0)


if __name__ == "__main__":
    unittest.main()
