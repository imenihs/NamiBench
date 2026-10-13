"""利用者のデータの場所と、名前を変える前の場所からのコピー（core/paths.py）。

- 場所: Windows は %APPDATA%\\NamiBench、それ以外は ~/.namibench
- 新しい場所が無く古い場所（pyGigaSt・.pygigast）があれば丸ごとコピーし、古い場所は変えない。1回だけ知らせる
- コピーが途中で失敗したら作りかけを消し、古い場所のまま使い、理由を知らせる
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from namibench.core import paths


def tree(root: Path) -> dict:
    """フォルダの中身（相対パス → 中身）。"""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.env = {"APPDATA": str(self.base)}
        self.new = self.base / "NamiBench"
        self.old = self.base / "pyGigaSt"

    def tearDown(self):
        self.tmp.cleanup()

    def make_old(self) -> dict:
        (self.old / "calibration" / "TEST0001").mkdir(parents=True)
        (self.old / "settings.json").write_text('{"format": "pygigast-settings", "version": 1}', encoding="utf-8")
        (self.old / "calibration" / "TEST0001" / "current.json").write_text("{}", encoding="utf-8")
        (self.old / "presets").mkdir()
        return tree(self.old)

    def test_places(self):
        self.assertEqual(paths.data_root(self.env), self.base / "NamiBench")
        self.assertEqual(paths.legacy_data_root(self.env), self.base / "pyGigaSt")
        self.assertEqual(paths.data_root({}), Path.home() / ".namibench")
        self.assertEqual(paths.legacy_data_root({}), Path.home() / ".pygigast")

    def test_only_old_place_is_copied(self):
        before = self.make_old()
        r = paths.prepare_data_root(self.env)
        self.assertEqual(r.path, self.new)
        self.assertEqual(tree(self.new), before)
        self.assertTrue((self.new / "presets").is_dir())  # 空のフォルダも移る
        self.assertEqual(tree(self.old), before)  # 古い場所は変えない
        self.assertEqual(r.notice, ("data_moved", {"from": str(self.old), "to": str(self.new)}))
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["NamiBench", "pyGigaSt"])  # 作業用は残らない
        # 2回目の起動: もう新しい場所があるのでコピーしない・知らせない
        (self.old / "settings.json").write_text("changed", encoding="utf-8")
        r2 = paths.prepare_data_root(self.env)
        self.assertEqual(r2, paths.PreparedRoot(self.new))
        self.assertEqual(tree(self.new), before)

    def test_new_place_already_there(self):
        self.make_old()
        self.new.mkdir()
        (self.new / "settings.json").write_text("new", encoding="utf-8")
        r = paths.prepare_data_root(self.env)
        self.assertEqual(r, paths.PreparedRoot(self.new))
        self.assertEqual(tree(self.new), {"settings.json": b"new"})

    def test_neither_place(self):
        r = paths.prepare_data_root(self.env)
        self.assertEqual(r, paths.PreparedRoot(self.new))
        self.assertFalse(self.new.exists())  # 作るのは書くときの各ストア（いままでと同じ）

    def test_copy_failure_keeps_old_place(self):
        before = self.make_old()

        def broken_copy(src, dst):
            # 途中まで書いてから失敗する（ディスクが一杯など）
            shutil.copytree(src / "calibration", Path(dst) / "calibration")
            raise OSError(28, "No space left on device")

        r = paths.prepare_data_root(self.env, copytree=broken_copy)
        self.assertEqual(r.path, self.old)
        code, d = r.notice
        self.assertEqual(code, "data_move_failed")
        self.assertEqual((d["from"], d["to"]), (str(self.old), str(self.new)))
        self.assertIn("No space left on device", d["reason"])
        self.assertFalse(self.new.exists())
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["pyGigaSt"])  # 作りかけを消した
        self.assertEqual(tree(self.old), before)
        # 次の起動でもう一度コピーする
        r2 = paths.prepare_data_root(self.env)
        self.assertEqual(r2.path, self.new)
        self.assertEqual(tree(self.new), before)

    def test_other_process_finished_first(self):
        """同時に起動したもう一方が先に移し終えたら、作業用を消して新しい場所を使う（失敗と言わない）。"""
        before = self.make_old()

        def racing_copy(src, dst):
            shutil.copytree(src, dst)
            shutil.copytree(src, self.new)  # もう一方のプロセスが先に名前を付けた

        r = paths.prepare_data_root(self.env, copytree=racing_copy)
        self.assertEqual(r, paths.PreparedRoot(self.new))
        self.assertEqual(tree(self.new), before)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["NamiBench", "pyGigaSt"])


class InstrumentStartTest(unittest.TestCase):
    """起動の口（data_root を渡さない Instrument）がコピーし、購読した相手ごとに1回知らせる。"""

    def test_moved_notice_reaches_each_subscriber(self):
        from namibench.api.local import _SimulatorBackend
        from namibench.core.devices.base import load_profile
        from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
        from namibench.ui_qt import texts

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            old = base / "pyGigaSt"
            old.mkdir()
            (old / "settings.json").write_text(json.dumps({"format": "pygigast-settings", "version": 1,
                                                           "rtltcp": {"enabled": False, "port": 1240,
                                                                      "bind": "local"}}), encoding="utf-8")
            profile = load_profile(GIGAST_PROFILE_PATH)
            with mock.patch.dict(os.environ, {"APPDATA": str(base)}):
                inst = Instrument({"simulator": _SimulatorBackend(profile)}, profile=profile)
            try:
                self.assertEqual(inst._root, base / "NamiBench")
                self.assertEqual(inst._cal_store.root, base / "NamiBench" / "calibration")
                self.assertTrue((base / "NamiBench" / "settings.json").is_file())
                self.assertEqual(inst._settings.get("rtltcp")["port"], 1240)  # コピーした設定で起動した
                got: list[list] = [[], []]
                done = threading.Event()

                def sub(i):
                    def cb(kind, payload):
                        if kind == "notice" and payload["code"] == "data_moved":
                            got[i].append(payload)
                            if all(got):
                                done.set()
                    return cb

                inst.subscribe(sub(0))
                inst.subscribe(sub(1))
                self.assertTrue(done.wait(10))
                inst.submit("get_state").result(10)  # 後から余計に届かないこと
                self.assertEqual([len(g) for g in got], [1, 1])
                n = got[0][0]
                self.assertTrue(n["startup"])
                self.assertEqual(texts.notice_text(n),
                                 f"データの置き場所を {old} から {base / 'NamiBench'} へコピーしました（前の場所は残しています）")
                failed = {"code": "data_move_failed", "detail": {"from": "A", "to": "B", "reason": "disk full"}}
                self.assertEqual(texts.notice_text(failed),
                                 "データを B へコピーできなかったので、前の場所 A のまま使います（disk full）")
            finally:
                inst.shutdown()

    def test_given_root_does_not_touch_user_place(self):
        """試験などで場所を渡したときは、利用者のデータの場所を見ない・コピーしない。"""
        from namibench.api.local import _SimulatorBackend
        from namibench.core.devices.base import load_profile
        from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "pyGigaSt").mkdir()
            profile = load_profile(GIGAST_PROFILE_PATH)
            with mock.patch.dict(os.environ, {"APPDATA": str(base)}):
                inst = Instrument({"simulator": _SimulatorBackend(profile)}, profile=profile, data_root=base / "d")
            try:
                self.assertFalse((base / "NamiBench").exists())
                self.assertEqual(inst._cal_store.root, base / "d" / "calibration")
            finally:
                inst.shutdown()


class WebStartupNoticeTest(unittest.TestCase):
    def test_each_later_screen_gets_the_moved_notice_once(self):
        """Web の口は起動の後に開き、画面はさらに後から繋ぐ。どの画面にも移した知らせが1回だけ届く。"""
        import time

        from websockets.sync.client import connect as ws_connect

        from namibench.api.local import LocalApi, _SimulatorBackend
        from namibench.core.devices.base import load_profile
        from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
        from tests.test_web_server import _Server

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "pyGigaSt").mkdir()
            profile = load_profile(GIGAST_PROFILE_PATH)
            with mock.patch.dict(os.environ, {"APPDATA": str(base)}):
                api = LocalApi(Instrument({"simulator": _SimulatorBackend(profile)}, profile=profile))
            srv = _Server(api)
            try:
                for _ in range(2):
                    with ws_connect(f"ws://127.0.0.1:{srv.port}/api/v1/events", open_timeout=10, max_size=None) as ws:
                        moved = []
                        end = time.monotonic() + 3
                        while time.monotonic() < end:
                            try:
                                m = ws.recv(timeout=0.3)
                            except TimeoutError:
                                continue
                            if isinstance(m, str) and json.loads(m)["kind"] == "notice":
                                p = json.loads(m)["payload"]
                                if p["code"] == "data_moved":
                                    moved.append(p["view"]["text"])
                        self.assertEqual(len(moved), 1)
                        self.assertIn(f"{base / 'NamiBench'} へコピーしました", moved[0])
            finally:
                srv.stop()
                api.close()


class QtPrefsCopyTest(unittest.TestCase):
    def test_copy_legacy_prefs(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QSettings

        from namibench.ui_qt.prefs import UiPrefs, copy_legacy

        with tempfile.TemporaryDirectory() as td:
            def ini(name):
                return QSettings(str(Path(td) / name), QSettings.Format.IniFormat)

            old = UiPrefs(ini("old.ini"))
            old.set("web_port", 8123)
            old.set("layout", {"docks": "AAA="})
            self.assertTrue(copy_legacy(ini("new.ini"), ini("old.ini")))
            new = UiPrefs(ini("new.ini"))
            self.assertEqual((new.get("web_port"), new.get("layout")), (8123, {"docks": "AAA="}))
            self.assertEqual(old.get("web_port"), 8123)  # 古いほうは残る
            # 新しいほうに何かあればコピーしない
            new.set("web_port", 9000)
            self.assertFalse(copy_legacy(ini("new.ini"), ini("old.ini")))
            self.assertEqual(UiPrefs(ini("new.ini")).get("web_port"), 9000)
            # 古いほうが空ならコピーしない
            self.assertFalse(copy_legacy(ini("empty_new.ini"), ini("missing.ini")))


if __name__ == "__main__":
    unittest.main()
