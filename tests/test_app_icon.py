"""アプリのアイコン（Qt の窓と Web の favicon が同じ絵: namibench/resources）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from namibench.resources import ICON_PNG, ICON_SVG  # noqa: E402
from namibench.ui_qt import app as qt_app  # noqa: E402


class AppIconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def test_svg_icon_draws(self):
        icon = qt_app.app_icon()
        self.assertFalse(icon.isNull())
        for size in (16, 32, 256):
            img = icon.pixmap(size, size).toImage()
            self.assertEqual((img.width(), img.height()), (size, size))
            # 角の丸の内側の地の色（生成り #f3ead8）が描けている
            c = img.pixelColor(size // 2, size // 8)
            self.assertEqual((c.alpha(), c.red() > 200, c.blue() > 180), (255, True, True))

    def test_png_fallback_when_svg_cannot_be_read(self):
        from unittest import mock

        with mock.patch.object(qt_app.QImageReader, "supportedImageFormats", lambda: [b"png"]):
            icon = qt_app.app_icon()
        self.assertFalse(icon.isNull())
        self.assertEqual(QImage(str(ICON_PNG)).size().width(), 256)
        self.assertFalse(icon.pixmap(32, 32).isNull())

    def test_files_are_the_same_picture(self):
        """PNG は SVG を 256×256 で描いたもの（絵を直したら両方を作り直す）。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter
        from PySide6.QtSvg import QSvgRenderer

        img = QImage(256, 256, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(str(ICON_SVG)).render(p)
        p.end()
        png = QImage(str(ICON_PNG)).convertToFormat(QImage.Format.Format_ARGB32)
        diff = 0
        for y in range(0, 256, 4):
            for x in range(0, 256, 4):
                a, b = img.pixelColor(x, y), png.pixelColor(x, y)
                diff = max(diff, abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue()),
                           abs(a.alpha() - b.alpha()))
        self.assertLessEqual(diff, 2)

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
