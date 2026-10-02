"""イメージキャンセルで雑音の床を読むマーカーに「低く読める」の注意を付けない（T18: 実測で床の真ん中はほぼ動かず、
減るのは暴れだけ。前の版の注意は作り物の雑音での見積もりで誤りだった）。"""
import unittest

from tests.instrument_helpers import InstrumentCase

NOTE = "ic_noise_floor"


class TestNoNoiseFloorNote(InstrumentCase):
    def test_no_note_on_floor_with_image_cancel(self):
        self.connect()
        self.call("set_sweep", image_cancel=True)
        self.single()
        self.call("marker_add", kind="M", trace=1, freq_hz=150_000_000)
        self.assertNotIn(NOTE, self.call("get_state")["markers"]["markers"][0]["notes"])


if __name__ == "__main__":
    unittest.main()
