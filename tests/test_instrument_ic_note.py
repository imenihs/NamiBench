"""イメージキャンセル（4回）で雑音の床の近くを読むマーカーに注意を添える（設計F §6 の 2・設計G §3.3）。"""
import unittest

from tests.instrument_helpers import InstrumentCase

NOTE = "ic_noise_floor"


class TestIcNoiseFloorNote(InstrumentCase):
    def marker_notes(self, i=0):
        return self.call("get_state")["markers"]["markers"][i]["notes"]

    def test_note_on_floor_not_on_signal(self):
        # シミュレータは 100MHz に −30dBm の信号、ほかは雑音（AD 90〜130。180kHz の床 AD 149 の近く）
        self.connect()
        self.call("set_sweep", image_cancel=True)
        self.single()
        self.call("marker_add", kind="M", trace=1, freq_hz=150_000_000)
        self.call("marker_add", kind="M", trace=1, freq_hz=100_000_000)
        self.assertIn(NOTE, self.marker_notes(0))
        self.assertNotIn(NOTE, self.marker_notes(1))

    def test_no_note_with_image_cancel_off(self):
        self.connect()
        self.call("set_sweep", image_cancel=False)
        self.single()
        self.call("marker_add", kind="M", trace=1, freq_hz=150_000_000)
        self.assertNotIn(NOTE, self.marker_notes(0))


if __name__ == "__main__":
    unittest.main()
