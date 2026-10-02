"""Qt 画面の試験の共通部分（シミュレータの本体・一時フォルダ・利用者への問いの差し替え）。試験は持たない。"""
import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt" and "QT_QPA_FONTDIR" not in os.environ:
    os.environ["QT_QPA_FONTDIR"] = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pygigast.api.local import LocalApi, _SimulatorBackend  # noqa: E402
from pygigast.core.devices.base import load_profile  # noqa: E402
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument  # noqa: E402
from pygigast.ui_qt.main_window import MainWindow  # noqa: E402
from pygigast.ui_qt.prefs import UiPrefs  # noqa: E402
from pygigast.ui_qt import wheel_guard  # noqa: E402

app = QApplication.instance() or QApplication([])
if os.name == "nt":
    # 画面を出さない試験（offscreen）でも、字は本番の Windows の既定（Yu Gothic UI 9.75pt = 13px）にする。offscreen の
    # 既定の字は別の書体（幅が狭い）で、崩れを数える試験が本番と違う前提で通っていた（設計の PDCA 2巡目・CLAUDE.md §6.6）
    from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402

    QFontDatabase.addApplicationFont(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "YuGothR.ttc"))
    _f = QFont("Yu Gothic UI")
    _f.setPointSizeF(9.75)
    app.setFont(_f)
wheel_guard.install(app)  # 画面の起動（ui_qt/app.py）と同じく、スクロールする場所の欄のホイールの決まりを付ける


def make_api(root: Path, speed: float = 0.05) -> LocalApi:
    profile = load_profile(GIGAST_PROFILE_PATH)
    sim = _SimulatorBackend(profile)
    sim.speed = speed
    return LocalApi(Instrument({"simulator": sim}, profile=profile, data_root=root / "d", cal_root=root / "c"))


class UiBase(unittest.TestCase):
    """シミュレータに繋いだ画面。問い（確認・ファイルの選択）は答えを用意して差し替える。"""

    size = (1600, 900)
    check_unfinished = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.api = make_api(self.root)
        self.win = self.make_window()
        self.win.resize(*self.size)
        self.win.show()
        self.pump()
        # 起動時に繋がっていないので機器の一覧が開く（J §4.7）。画面の試験はそれを閉じて主の窓で行う
        # （一覧そのものは test_ui_qt_devices で確かめる）
        self.close_device_list()

    def close_device_list(self):
        self.wait(lambda: self.win.device_window is not None, what="起動時に機器の一覧が開く")
        self.win.device_window.close()
        self.win.activateWindow()
        self.pump(0.05)

    def make_window(self) -> MainWindow:
        win = MainWindow(self.api, prefs=UiPrefs(), check_unfinished=self.check_unfinished)
        self.confirm_answer = True
        self.confirm_asked: list[str] = []
        self.save_path = None
        self.open_paths: list[str] = []
        win.confirm = lambda title, text, ok=None: (self.confirm_asked.append(text), self.confirm_answer)[1]
        win.ask_save_path = lambda title, default, filt: self.save_path
        win.ask_open_paths = lambda title, folder, filt: self.open_paths
        win.ask_text = lambda title, label, text="": self.text_answer
        self.text_answer = None
        win.prefs.set("png_dir", str(self.root / "png"))
        return win

    def tearDown(self):
        self.confirm_answer = True
        self.win.close()
        self.api.close()
        self.tmp.cleanup()

    def pump(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait(self, cond, timeout=8.0, what=""):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if cond():
                return
            time.sleep(0.02)
        self.fail(f"待ったが成り立たなかった: {what}")

    def state(self):
        return self.api.get_state().result(5)

    def cmd(self, name, **kw):
        r = self.api.call(name, **kw).result(10)
        self.pump(0.1)
        return r

    def connect_and_sweep(self, start=50_000_000, stop=2_000_000_000, single=True):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=start, stop_hz=stop)
        if single:
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発の掃引")
        else:
            self.cmd("run", mode="continuous")
        self.wait(lambda: self.win.traces.live_payload(1) is not None, what="トレース1が描かれる")
        self.pump(0.2)

    def menu_action(self, code):
        return self.win.actions[code]
