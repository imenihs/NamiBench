"""画面の覚え書きが起動し直しても残ることを確かめる（設計O §1.1: 窓の並び・ブラウザから使う設定・キーは画面ごと）。"""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from pygigast.ui_qt.prefs import UiPrefs  # noqa: E402
from tests.test_ui_qt_support import UiBase  # noqa: E402


def ini_prefs(path: Path) -> UiPrefs:
    return UiPrefs(QSettings(str(path), QSettings.Format.IniFormat))


class PrefsFileTest(unittest.TestCase):
    def test_values_keep_their_types(self):
        path = Path(tempfile.mkdtemp()) / "prefs.ini"
        p = ini_prefs(path)
        values = {"web_enabled": True, "web_port": 8123, "layout": {"docks": "AAA=", "graph": None},
                  "keys": {"run_cont": "F9"}, "last_dir": "C:/データ", "png_size": [1280, 720]}
        for k, v in values.items():
            p.set(k, v)
        q = ini_prefs(path)  # 起動し直した
        for k, v in values.items():
            self.assertEqual(q.get(k), v, k)
        self.assertEqual(q.get("missing", 5), 5)

    def test_broken_value_falls_back_to_default(self):
        path = Path(tempfile.mkdtemp()) / "prefs.ini"
        s = QSettings(str(path), QSettings.Format.IniFormat)
        s.setValue("web_port", "{not json")
        s.sync()
        self.assertEqual(ini_prefs(path).get("web_port", 8080), 8080)


class AppStartTest(unittest.TestCase):
    def test_app_gives_window_lasting_prefs(self):
        """アプリの起動の口は、残る覚え書き（QSettings）を画面に渡す（渡さないと閉じたら全部消える）。"""
        from unittest import mock

        from pygigast.ui_qt import app as qt_app

        got = {}

        class FakeWindow:
            def __init__(self, api, prefs=None, web=None):
                got["prefs"] = prefs

            def resize(self, *a):
                pass

            def show(self):
                pass

        fake_api = mock.Mock()
        with (mock.patch.object(qt_app, "MainWindow", FakeWindow),
              mock.patch.object(qt_app, "create_default", lambda **kw: fake_api),
              mock.patch.object(qt_app, "QApplication") as qa,
              mock.patch.object(qt_app.UiPrefs, "for_app", classmethod(lambda cls: cls(mock.Mock())))):
            qa.return_value.exec.return_value = 0
            qt_app.main(["x"])
        self.assertIsNotNone(got["prefs"])
        self.assertIsNotNone(got["prefs"]._s)
        fake_api.close.assert_called_once()


class WebPrefsRestartTest(UiBase):
    def test_web_port_survives_restart(self):
        path = Path(tempfile.mkdtemp()) / "prefs.ini"
        from pygigast.ui_qt.main_window import MainWindow

        win = MainWindow(self.api, prefs=ini_prefs(path), check_unfinished=False)
        try:
            win.web.set_port(8197)
        finally:
            win.close()
            self.pump(0.1)
        win2 = MainWindow(self.api, prefs=ini_prefs(path), check_unfinished=False)
        try:
            self.assertEqual(win2.web.port, 8197)
            self.assertFalse(win2.web.enabled)
        finally:
            win2.close()
            self.pump(0.1)


if __name__ == "__main__":
    unittest.main()


class LanAtStartTest(UiBase):
    def test_lan_is_off_at_start_and_said_so(self):
        """前回 LAN に出していても、起動したときは出さず、そのことを知らせる（黙って切らない）。"""
        from PySide6.QtWidgets import QMessageBox

        from pygigast.ui_qt import texts
        from pygigast.ui_qt.main_window import MainWindow

        path = Path(tempfile.mkdtemp()) / "prefs.ini"
        p = ini_prefs(path)
        p.set("web_lan", True)
        win = MainWindow(self.api, prefs=ini_prefs(path), check_unfinished=False)
        try:
            self.pump(0.2)
            self.assertFalse(win.web.lan)
            boxes = [w for w in win.findChildren(QMessageBox) if w.isVisible()]
            self.assertEqual(len(boxes), 1)
            self.assertIn("起動したときは切っています", boxes[0].text())
            self.assertFalse(ini_prefs(path).get("web_lan"))  # 知らせるのは1回だけ
        finally:
            win.close()
            self.pump(0.1)

    def test_no_message_when_lan_was_off(self):
        from PySide6.QtWidgets import QMessageBox

        from pygigast.ui_qt.main_window import MainWindow

        path = Path(tempfile.mkdtemp()) / "prefs.ini"
        win = MainWindow(self.api, prefs=ini_prefs(path), check_unfinished=False)
        try:
            self.pump(0.2)
            self.assertEqual([w for w in win.findChildren(QMessageBox) if w.isVisible()], [])
        finally:
            win.close()
            self.pump(0.1)
