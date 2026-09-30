"""Qt の欄に打って確定した値が、本体の採用値として画面に戻ることを確かめる（設計J §3・C §5.1）。"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pygigast.api.local import LocalApi, _SimulatorBackend  # noqa: E402
from pygigast.core.devices.base import load_profile  # noqa: E402
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument  # noqa: E402
from pygigast.ui_qt.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])


class MainWindowInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.api = LocalApi(Instrument({"simulator": sim}, profile=profile,
                                       data_root=root / "d", cal_root=root / "c"))
        self.win = MainWindow(self.api)
        self.win.show()
        self.pump()

    def tearDown(self):
        self.win.close()
        self.api.close()
        self.tmp.cleanup()

    def pump(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def type_into(self, field, text):
        e = field.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, text)
        QTest.keyClick(e, Qt.Key.Key_Return)
        self.pump()

    def test_center_span_then_start(self):
        self.type_into(self.win.f_span, "100M")
        self.type_into(self.win.f_center, "1.2G")
        st = self.api.get_state().result(5)["sweep"]
        self.assertEqual((st["start_hz"], st["stop_hz"]), (1_150_000_000, 1_250_000_000))
        self.assertEqual(self.win.f_start.edit.text(), "1150.000 MHz")
        # Start を触ると Stop を守って Center・Span が動く
        self.type_into(self.win.f_start, "1100")
        st = self.api.get_state().result(5)["sweep"]
        self.assertEqual((st["start_hz"], st["stop_hz"]), (1_100_000_000, 1_250_000_000))
        self.assertEqual(self.win.f_center.edit.text(), "1175.000 MHz")

    def test_out_of_range_is_adjusted_and_noted(self):
        self.type_into(self.win.f_stop, "12300")
        self.assertEqual(self.win.f_stop.edit.text(), "12100.000 MHz")
        self.assertIn("12.1 GHz に合わせました", self.win.f_stop.note.text())

    def test_unreadable_text_keeps_value(self):
        before = self.win.f_center.edit.text()
        self.type_into(self.win.f_center, "abc")
        self.assertEqual(self.win.f_center.edit.text(), before)
        self.assertIn("読めない値", self.win.f_center.note.text())

    def test_escape_restores(self):
        before = self.win.f_span.edit.text()
        e = self.win.f_span.edit
        e.setFocus()
        e.selectAll()
        QTest.keyClicks(e, "5")
        QTest.keyClick(e, Qt.Key.Key_Escape)
        self.assertEqual(e.text(), before)

    def test_ref_and_div_do_not_touch_sweep(self):
        v0 = self.api.get_state().result(5)["version"]
        self.type_into(self.win.f_ref, "-20")
        vb = self.win.plot.getPlotItem().getViewBox().viewRange()[1]
        self.assertAlmostEqual(vb[1], -20, delta=0.1)
        self.assertAlmostEqual(vb[0], -120, delta=0.1)
        self.assertEqual(self.api.get_state().result(5)["version"], v0)


if __name__ == "__main__":
    unittest.main()
