"""Qt の機器の一覧（設計J §4.7）・REF レベルの欄（J §4.1）・上段の「基準を取る」（J §4.2）。

偽の GigaSt の種類（tests/device_helpers.py）とシミュレータで確かめる。実機のポートは開かない。
"""
import threading
import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QProgressBar, QPushButton

from namibench.api.local import LocalApi, _SimulatorBackend
from namibench.core.devices.base import load_profile
from namibench.core.instrument import GIGAST_PROFILE_PATH, Instrument
from tests.device_helpers import FakeGigaBackend
from tests.test_ui_qt_support import UiBase, app

SERIAL = "FT00AB12"


class DevUiBase(UiBase):
    """偽の GigaSt の種類を持つ画面。起動時に開く機器の一覧は閉じない（それを試す）。"""

    plugged = ()

    def make_window(self):
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        self.giga = FakeGigaBackend(profile)
        for port, serial in self.plugged:
            self.giga.plug(port, serial)
        self.api.close()
        self.api = LocalApi(Instrument({"gigast": self.giga, "simulator": sim}, profile=profile,
                                       data_root=self.root / "d", cal_root=self.root / "c"))
        win = super().make_window()
        win.ask_folder = lambda title: None
        return win

    def close_device_list(self):
        self.wait(lambda: self.win.device_window is not None and self.win.device_window.isVisible(),
                  what="起動時に機器の一覧が開く")

    @property
    def dw(self):
        return self.win.device_window

    def row_texts(self):
        return [(r.name.text(), r.state.text()) for r in self.dw.row_widgets()]


class TestEmptyAndSimulator(DevUiBase):
    def test_empty_state_then_try_simulator(self):
        self.wait(lambda: self.dw.empty.isVisible(), what="空のときの案内")
        self.assertEqual(self.dw.empty_label.text(), "GigaSt・RTL-SDR・PlutoSDR を USB に挿すと、ここに出ます")
        self.assertEqual(self.dw.row_widgets(), [])
        # 未接続の状態バーは選び方を出す（J §4.7 の 7）
        self.assertEqual(self.win.st_conn.text(), "● 未接続（機器 → 機器に接続… で選べます）")
        self.dw.try_sim.click()
        self.wait(lambda: self.state()["connection"]["state"] == "connected", what="シミュレータに繋がる")
        self.wait(lambda: not self.dw.isVisible(), what="繋いだら一覧を閉じる")
        self.wait(lambda: "シミュレータ" in self.win.st_conn.text(), what="状態バーに機器の名前")

    def test_plug_updates_list_automatically(self):
        self.wait(lambda: self.dw.empty.isVisible(), what="空")
        self.giga.plug("COM7", SERIAL)
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="挿した機器が出る")
        self.pump(0.1)
        self.assertFalse(self.dw.empty.isVisible())
        name, state = self.row_texts()[0]
        self.assertEqual(name, "GigaSt v5 AB12")
        self.assertEqual(state, "初めての機器")  # 名前に説明を付けない（J §4.12・U-24）
        # 詳細（COM 番号・USB ID）はたたんである
        row = self.dw.row_widgets()[0]
        self.assertFalse(row.details.isVisible())
        row.more.click()
        self.assertTrue(row.details.isVisible())
        self.assertIn("ポート COM7", row.details.text())
        self.assertIn("USB ID 0403:6001", row.details.text())
        self.giga.unplug("COM7")
        self.wait(lambda: self.dw.empty.isVisible(), what="抜いたら消える")


class TestConnectFromList(DevUiBase):
    plugged = (("COM7", SERIAL),)

    def test_connecting_then_success_closes(self):
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="行")
        self.giga.gate = threading.Event()
        self.dw.row_widgets()[0].action.click()
        self.wait(lambda: self.dw.status.isVisible(), what="繋いでいます")
        self.assertEqual(self.dw.status.text(), "繋いでいます…（GigaSt v5 AB12）")
        self.assertFalse(self.dw.row_widgets()[0].action.isEnabled())
        self.giga.gate.set()
        self.wait(lambda: not self.dw.isVisible(), what="繋いだら閉じる")
        self.assertEqual(self.state()["connection"]["identity"]["port"], "COM7")
        # メニューの「機器に接続…」でいつでも開ける。いま使っている機器は「使用中」で押せない
        self.menu_action("connect").trigger()
        self.wait(lambda: self.dw.isVisible() and self.row_texts() and self.row_texts()[0][1] == "使用中",
                  what="使用中")
        self.assertFalse(self.dw.row_widgets()[0].action.isEnabled())

    def test_port_in_use_shows_reason_and_next_step(self):
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="行")
        self.giga.busy.add("COM7")
        self.dw.row_widgets()[0].action.click()
        self.wait(lambda: self.dw.status.objectName() == "warn", what="失敗の理由")
        self.assertIn("GigaSt v5 AB12 に繋げませんでした", self.dw.status.text())
        self.assertIn("GS5.exe など別のアプリケーションが使っていないか", self.dw.status.text())
        self.wait(lambda: self.row_texts()[0][1] == "ほかのアプリケーションが使っています", what="行の状態")
        self.assertTrue(self.dw.isVisible())

    def test_probe_failed_next_step(self):
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="行")
        self.giga.probe_result = False
        self.dw.row_widgets()[0].action.click()
        self.wait(lambda: self.dw.status.objectName() == "warn", what="失敗の理由")
        self.assertIn("GigaSt の応答が返りませんでした", self.dw.status.text())
        self.assertIn("USB を挿し直してから", self.dw.status.text())

    def test_rename(self):
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="行")
        self.text_answer = "測定台の GigaSt"
        self.dw.row_widgets()[0].rename.click()
        self.wait(lambda: self.row_texts()[0][0] == "測定台の GigaSt", what="名前が変わる")


class TestWhenListOpens(DevUiBase):
    plugged = (("COM7", SERIAL),)

    def test_user_disconnect_opens_unplug_does_not(self):
        self.wait(lambda: len(self.dw.row_widgets()) == 2, what="行")
        self.dw.row_widgets()[0].action.click()
        self.wait(lambda: not self.dw.isVisible(), what="繋いで閉じる")
        # 抜けた（切断・再接続待ち）ときは開かない: 同じ機器が戻るのを待つ（B §7.3）
        self.giga.unplug("COM7")
        self.cmd("run", mode="continuous")
        self.api._inst._device.unplug()
        self.wait(lambda: self.state()["connection"]["state"] == "reconnecting", what="再接続待ち")
        self.pump(0.8)
        self.assertFalse(self.dw.isVisible())
        self.giga.plug("COM7", SERIAL)
        self.wait(lambda: self.state()["connection"]["state"] == "connected", what="戻る")
        # 利用者が切断したら開く
        self.menu_action("disconnect").trigger()
        self.wait(lambda: self.dw.isVisible(), what="切断したら一覧")


class TestAutoConnectNotice(DevUiBase):
    plugged = (("COM7", SERIAL),)

    def make_window(self):
        # 前回この機器に繋いでいた設定を作ってから画面を開く。先に作られた本体は閉じておく（閉じるときに
        # 設定を書くので、後で閉じると前回の機器の覚えを上書きする）
        self.api.close()
        self.api = type("Closed", (), {"close": lambda self: None})()
        profile = load_profile(GIGAST_PROFILE_PATH)
        giga = FakeGigaBackend(profile)
        giga.plug("COM7", SERIAL)
        api = LocalApi(Instrument({"gigast": giga}, profile=profile, data_root=self.root / "d",
                                  cal_root=self.root / "c"))
        api.call("connect", backend="gigast", port="COM7").result(10)
        api.close()
        return super().make_window()

    def close_device_list(self):
        pass

    def test_notice_and_no_list(self):
        self.wait(lambda: "前回の GigaSt v5 AB12 に繋ぎました" in self.win.st_note.text(), what="自動接続の知らせ")
        self.assertIsNone(self.win.device_window)


class TestDllMissing(DevUiBase):
    def make_window(self):
        from namibench.api.local import _RtlSdrBackend
        from namibench.core.devices.rtlsdr import dll

        def missing():
            raise dll.DllNotFound("無い")

        win = super().make_window()
        self.api._inst._backends["rtlsdr"] = _RtlSdrBackend(lib_factory=missing)
        return win

    def test_dll_row_and_choose_folder(self):
        self.wait(lambda: any(r.d["state"] == "dll_not_found" for r in self.dw.row_widgets()), what="DLL の行")
        row = next(r for r in self.dw.row_widgets() if r.d["state"] == "dll_not_found")
        self.assertEqual(row.state.text(), "DLL が見つかりません")
        self.assertEqual(row.action.text(), "DLL のフォルダを選ぶ…")
        # 挿した機器が無いので空の案内も出す（DLL の行と一緒に）
        self.assertTrue(self.dw.empty.isVisible())
        folder = str(self.root / "rtl")
        self.win.ask_folder = lambda title: folder
        row.action.click()
        self.wait(lambda: self.state()["rtlsdr"]["dll_folder"] == folder, what="DLL のフォルダを設定へ")


class TestRefField(UiBase):
    def test_dropdown_wheel_and_typing(self):
        f = self.win.f_ref
        texts_ = [f.combo.itemText(i) for i in range(f.combo.count())]
        self.assertEqual(len(texts_), 15)
        self.assertEqual((texts_[0], texts_[2], texts_[-1]), ("+20 dBm", "0 dBm", "−120 dBm"))
        f.combo.activated.emit(texts_.index("−40 dBm"))
        self.wait(lambda: self.state()["amplitude"]["ref_level"] == -40.0, what="一覧から −40")
        self.assertEqual(f.edit.text(), "−40.0 dBm")
        # 欄の上のホイールは、カーソルの桁によらず 1dB
        f.edit.setCursorPosition(1)
        ev = QWheelEvent(QPointF(5, 5), QPointF(f.edit.mapToGlobal(QPoint(5, 5))), QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(f.edit, ev)
        self.wait(lambda: self.state()["amplitude"]["ref_level"] == -39.0, what="ホイールで +1dB")
        app.sendEvent(f.combo, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                           Qt.ScrollPhase.NoScrollPhase, False))
        self.wait(lambda: self.state()["amplitude"]["ref_level"] == -40.0, what="一覧の矢印の上でも 1dB")
        # 直接入力は「-」でも「−」でも
        for typed, want in (("-37.5", -37.5), ("−12", -12.0)):
            f.edit.setFocus()
            f.edit.selectAll()
            f.edit.insert(typed)  # QTest.keyClicks は Latin-1 の外の字（−）を送れないので、打った文字として入れる
            QTest.keyClick(f.edit, Qt.Key.Key_Return)
            self.wait(lambda w=want: self.state()["amplitude"]["ref_level"] == w, what=f"打った {typed}")


class TestRefQuick(UiBase):
    def test_hidden_in_sa_and_takes_in_tg(self):
        q = self.win.ref_quick
        self.assertFalse(q.isVisible())
        self.connect_and_sweep(start=100_000_000, stop=200_000_000)
        self.assertFalse(q.isVisible())
        self.cmd("set_mode", mode="tg")
        self.cmd("set_amplitude", ref_level=-60.0)
        self.wait(lambda: q.isVisible(), what="TG で出る")
        self.assertEqual(q.status.text(), "基準なし（dBm で表示）")
        self.assertTrue(q.button.isEnabled())
        q.button.click()
        self.wait(lambda: self.state()["normalization"].get("selected") is not None, timeout=20, what="基準が取れる")
        sel = self.state()["normalization"]["selected"]
        self.assertTrue(sel["name"].startswith("TG "))
        self.wait(lambda: self.state()["run"]["last_stop_reason"] == "single_done", timeout=20, what="すぐ1回測る")
        self.wait(lambda: q.status.text().startswith("基準: TG "), what="いまの基準")
        # 0dB が見えるよう REF レベルを動かし、知らせる
        self.wait(lambda: self.win.ref_level() == 10.0, what="REF が +10")
        self.assertEqual(self.win.display_unit(), "dB")

    def test_taking_adds_nothing_to_top_bar(self):
        """基準を取っている間も上段は「基準を取る」と基準の文だけ（ちらつく棒・二つ目の「やめる」・進み具合の文を
        置かない。進み具合は掃引の進み具合の棒と処理の帯）。部品の位置と大きさは取る前と同じ。"""
        q = self.win.ref_quick
        self.connect_and_sweep(start=100_000_000, stop=200_000_000)
        self.cmd("set_mode", mode="tg")
        self.wait(lambda: q.isVisible(), what="TG で出る")
        self.assertFalse(hasattr(q, "bar") or hasattr(q, "cancel"))
        self.assertEqual([c.text() for c in q.findChildren(QPushButton)], [q.button.text()])
        self.assertEqual(q.findChildren(QProgressBar), [])
        rects = lambda: [(w.geometry().getRect()) for w in (q, q.button, q.status)] + [self.win.topbar.height()]
        before = rects()
        text = q.status.text()
        st = dict(self.state())
        st["tasks"] = [{"task_id": "t1", "kind": "reference_take", "done": 0, "total": 1, "elapsed_s": 0.0,
                        "cancelling": False, "detail": {}}]
        q.apply(st)
        self.pump(0.2)
        self.assertFalse(q.button.isEnabled())
        self.assertEqual(q.status.text(), text)
        self.assertEqual(q.findChildren(QProgressBar), [])
        self.assertEqual(rects(), before)


if __name__ == "__main__":
    unittest.main()
