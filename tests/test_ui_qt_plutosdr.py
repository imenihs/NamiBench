"""Qt 画面の PlutoSDR の欄（設計Q §7）: libiio の偽物の PlutoSDR に繋いで確かめる。

受信の利得（自動と、いまの範囲で使える値）・使う値の行・利得を合わせる・送信の減衰・設定の PlutoSDR のページ・機器の一覧。
"""
import unittest

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
UNCAL = "dB（未校正）"


class PlutoUiBase(UiBase):
    def setUp(self):
        self.lib = FakeIioLib()
        pd._cache = pd._SerialCache()
        super().setUp()

    def make_window(self):
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        pl = _PlutoSdrBackend(lib_factory=lambda: self.lib)
        self.api.close()
        self.api = LocalApi(Instrument({"simulator": sim, "plutosdr": pl}, profile=profile,
                                       data_root=self.root / "d", cal_root=self.root / "c"))
        return super().make_window()

    def connect_pluto(self, single=True):
        self.cmd("connect", backend="plutosdr", port=FAKE_URI)
        self.cmd("set_sweep", start_hz=84 * MHZ, stop_hz=85_500_000)
        if single:
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                      what="単発の掃引")
        self.pump(0.3)


class TestPlutoPanels(PlutoUiBase):
    def test_shown_only_while_connected(self):
        rx, tx = self.win.pluto_panel, self.win.source_tab.pluto_tx
        self.assertTrue(rx.isHidden())
        self.assertTrue(tx.isHidden())
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.pump(0.3)
        self.assertTrue(rx.isHidden())
        self.connect_pluto()
        self.assertFalse(rx.isHidden())
        self.assertFalse(tx.isHidden())
        self.assertTrue(self.win.rtl_panel.isHidden())  # RTL-SDR の欄は出さない
        # 自動 + −1〜73dB（1300MHz 未満の帯）
        self.assertEqual(rx.gain.count(), 1 + 75)
        self.assertEqual(rx.gain.itemText(0), PLUTO_LABELS["auto"])
        self.assertEqual(rx.gain.currentData(), 30.0)
        self.assertIn(UNCAL, rx.unit_text.text())
        self.cmd("disconnect")
        self.wait(lambda: rx.isHidden() and tx.isHidden(), what="欄が消える")

    def test_gain_auto_and_used_value(self):
        self.connect_pluto()
        rx = self.win.pluto_panel
        i = rx.gain.findData(73.0)
        rx.gain.setCurrentIndex(i)
        rx.gain.activated.emit(i)
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 73.0, what="利得")
        self.assertTrue(rx.gain_used.isHidden())
        # 2.4GHz 帯は 71dB まで: 一覧が変わり、使う値の行が出る（U-25）
        self.cmd("set_sweep", start_hz=2_400 * MHZ, stop_hz=2_402 * MHZ)
        self.wait(lambda: rx.gain.count() == 1 + 75 and rx.gain.itemData(1) == -3.0, what="帯の一覧")
        self.wait(lambda: not rx.gain_used.isHidden(), what="使う値の行")
        self.assertEqual(rx.gain_used.text(), texts.tpl("pluto_gain_used", db="71"))
        self.assertEqual(rx.gain.currentData(), 71.0)
        rx.gain.setCurrentIndex(0)
        rx.gain.activated.emit(0)
        self.wait(lambda: self.state()["plutosdr"]["agc"], what="自動")
        self.wait(lambda: not rx.fit_btn.isEnabled(), what="利得を合わせるが無効")
        self.assertIn(PLUTO_LABELS["fit_needs_manual"], rx.fit_btn.toolTip())

    def test_fit_gain_button(self):
        self.connect_pluto()
        rx = self.win.pluto_panel
        rx.fit_btn.click()
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] not in (None, 30.0), what="利得を合わせた")

    def test_tx_atten_field(self):
        self.connect_pluto()
        tx = self.win.source_tab.pluto_tx
        tx.atten.edit.setText("-12.3")
        tx.atten.edit.committed.emit("-12.3")
        self.wait(lambda: self.state()["plutosdr"]["tx_atten_db"] == -12.25, what="送信の減衰（0.25dB 刻み）")
        self.wait(lambda: tx.atten.edit.text().startswith("-12.25"), what="欄の値")
        tx.atten.edit.committed.emit("5")
        self.wait(lambda: tx.atten.edit.text().startswith("-12.25"), what="範囲外は変わらない")

    def test_settings_page_and_device_list(self):
        self.connect_pluto(single=False)
        from pygigast.ui_qt.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.win)
        try:
            self.assertIn("plutosdr", dlg._page_index)
            self.assertEqual(dlg.pl_uri.text(), FAKE_URI)
            self.assertTrue(dlg.pl_dll_state.text())
        finally:
            dlg.close()
        rows = self.cmd("list_devices")["devices"]
        pr = [r for r in rows if r["kind"] == "plutosdr"]
        self.assertEqual([r["serial"] for r in pr], [FAKE_SERIAL])
        self.assertEqual(texts.DEVICE_KINDS["plutosdr"], "PlutoSDR")


if __name__ == "__main__":
    unittest.main()
