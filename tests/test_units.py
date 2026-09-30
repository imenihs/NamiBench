import unittest

from pygigast.core.units import ParseError, format_freq, parse_db, parse_freq_hz


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


if __name__ == "__main__":
    unittest.main()
