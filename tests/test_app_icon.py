"""アプリのアイコン（Qt の窓と Web の favicon が同じ絵: namibench/resources/icon）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from namibench.resources import FAVICON_SIZE, ICON_SIZES, icon_png  # noqa: E402
from namibench.ui_qt import app as qt_app  # noqa: E402


class AppIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_every_size_is_a_square_png_of_that_size(self):
        for s in ICON_SIZES:
            img = QImage(str(icon_png(s)))
            self.assertFalse(img.isNull(), s)
            self.assertEqual((img.width(), img.height()), (s, s))
        self.assertIn(FAVICON_SIZE, ICON_SIZES)

    def test_icon_picks_the_picture_of_the_size(self):
        icon = qt_app.app_icon()
        self.assertFalse(icon.isNull())
        self.assertEqual(sorted(sz.width() for sz in icon.availableSizes()), sorted(ICON_SIZES))
        for s in (16, 32, 256):
            img = icon.pixmap(s, s).toImage()
            self.assertEqual((img.width(), img.height()), (s, s))
            # 角は丸めてほぼ透明。絵の中に空の淡い寒色と藍の両方がある（どの大きさでも同じ色の絵）
            self.assertLess(img.pixelColor(0, 0).alpha(), 40)
            sky = ink = 0
            for y in range(s):
                for x in range(s):
                    c = img.pixelColor(x, y)
                    if c.alpha() < 255:
                        continue
                    if c.red() > 200 and c.blue() > 220 and c.blue() >= c.red():
                        sky += 1
                    elif c.blue() > c.red() + 40 and c.red() < 90:
                        ink += 1
            self.assertGreater(sky, s * s * 0.10, s)
            self.assertGreater(ink, s * s * 0.10, s)

    def test_app_sets_icon_and_taskbar_id(self):
        from unittest import mock

        class FakeWindow:
            def __init__(self, api, prefs=None, web=None):
                pass

            def resize(self, *a):
                pass

            def show(self):
                pass

        fake_api = mock.Mock()
        with (mock.patch.object(qt_app, "MainWindow", FakeWindow),
              mock.patch.object(qt_app, "create_default", lambda **kw: fake_api),
              mock.patch.object(qt_app, "QApplication") as qa,
              mock.patch.object(qt_app, "_set_app_user_model_id") as app_id,
              mock.patch.object(qt_app.wheel_guard, "install"),
              mock.patch.object(qt_app.UiPrefs, "for_app", classmethod(lambda cls: cls(mock.Mock())))):
            qa.return_value.exec.return_value = 0
            qt_app.main(["x"])
        app_id.assert_called_once()
        icon = qa.return_value.setWindowIcon.call_args.args[0]
        self.assertFalse(icon.isNull())


if __name__ == "__main__":
    unittest.main()
