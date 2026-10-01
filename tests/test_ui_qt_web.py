"""Qt 画面の「Web の口」（設計A §4.3.2・O §1.3）と RTL-SDR の「利得を合わせる」（設計Q §6.5）。

Web の口は空いているポート（0）で 127.0.0.1 / 0.0.0.0 に本当に開く。LAN のアドレスは決まった値に差し替える。
"""
import unittest
from unittest import mock

from pygigast.api.local import LocalApi, _RtlSdrBackend, _SimulatorBackend
from pygigast.api.web import access as acc
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from pygigast.ui_qt.settings_dialog import SettingsDialog
from tests.test_rtlsdr_fit_gain import GainLib, TONE_HZ
from tests.test_ui_qt_rtlsdr import MHZ, RtlUiBase
from tests.test_ui_qt_support import UiBase

ADDR = ["192.168.1.10"]


class TestWebControl(UiBase):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(acc, "lan_addresses", return_value=ADDR)
        self.patch.start()
        self.web = self.win.web
        self.web.port = 0  # 空いているポート

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_default_closed_and_menu_reason(self):
        self.assertFalse(self.web.enabled)
        self.assertFalse(self.win.status.web.isVisible())
        act = self.menu_action("web_connect")
        self.assertFalse(act.isEnabled())
        self.assertIn(texts.WEB_LABELS["lan_off"], act.toolTip())
        dlg = SettingsDialog(self.win)
        self.win._settings = dlg
        dlg.apply_web()
        self.assertFalse(dlg.web_enable.isChecked())
        self.assertFalse(dlg.web_lan.isEnabled())
        self.assertFalse(dlg.web_show.isEnabled())
        self.assertIn("暗号化", texts.WEB_LABELS["note"])
        dlg.close()

    def test_lan_on_shows_connect_window_status_and_clients(self):
        self.web.set_enabled(True)
        self.assertTrue(self.web.enabled)
        self.assertTrue(self.win.prefs.get("web_enabled"))
        self.assertIn("127.0.0.1", self.web.view()["text"])
        self.web.set_lan(True)
        self.pump(0.2)
        self.assertTrue(self.web.lan)
        self.assertEqual(self.web.service.access.lan, True)
        cw = self.web.connect_window
        self.assertIsNotNone(cw)
        self.assertTrue(cw.isVisible())
        code = self.web.service.access.current_code()[0]
        self.assertEqual(cw.code.text().replace(" ", ""), code)
        self.assertIn("あと", cw.left.text())
        self.assertTrue(self.menu_action("web_connect").isEnabled())
        self.assertEqual(self.win.status.web.text(), texts.tpl_web("status", n=0, a=0, b=0))
        self.assertTrue(self.win.status.web.isVisible())
        # 開き直すと新しい番号（前の番号の窓は無効と出す）
        self.web.show_connect()
        self.pump(0.1)
        cw2 = self.web.connect_window
        self.assertEqual(cw2.code.text().replace(" ", ""), self.web.service.access.current_code()[0])
        # つながっている相手（偽の接続を足す）と［切断］
        closed = []
        s = self.web.service.access.login_key(self.web.service.access.keys["view"], "192.168.1.77")
        self.web.service.access.add_conn("view", "192.168.1.77", s.token, False, lambda: closed.append(1))
        self.pump(0.2)
        self.assertEqual(self.win.status.web.text(), texts.tpl_web("status", n=1, a=0, b=1))
        self.win.status.web.click()
        self.pump(0.1)
        cl = self.web.clients_window
        self.assertEqual(cl.table.rowCount(), 1)
        self.assertEqual(cl.table.item(0, 1).text(), "192.168.1.77")
        self.assertEqual(cl.table.item(0, 2).text(), "見るだけ")
        cl.table.cellWidget(0, 3).click()
        self.pump(0.1)
        self.assertEqual(closed, [1])
        self.assertIsNone(self.web.service.access.session(s.token))
        # LAN を OFF: 窓を閉じ、状態バーから消え、自分の PC だけに戻る
        self.web.set_lan(False)
        self.pump(0.2)
        self.assertFalse(self.web.lan)
        self.assertTrue(self.web.enabled)
        self.assertFalse(self.win.status.web.isVisible())
        self.assertFalse(self.menu_action("web_connect").isEnabled())
        # LAN を切ったことを覚える（前回 ON のときだけ、次の起動で「切っています」と知らせる: test_ui_qt_prefs）
        self.assertFalse(self.win.prefs.get("web_lan"))

    def test_regenerate_shows_new_keys(self):
        self.web.set_enabled(True)
        self.web.set_lan(True)
        old = dict(self.web.service.access.keys)
        self.web.regenerate()
        self.pump(0.1)
        self.assertNotEqual(old["operate"], self.web.service.access.keys["operate"])
        self.assertIsNotNone(self.web.connect_window)

    def test_settings_checkboxes_drive_service(self):
        dlg = SettingsDialog(self.win)
        self.win._settings = dlg
        dlg.web_enable.setChecked(True)
        self.pump(0.2)
        self.assertTrue(self.web.enabled)
        self.assertTrue(dlg.web_lan.isEnabled())
        dlg.web_lan.setChecked(True)
        self.pump(0.2)
        self.assertTrue(self.web.lan)
        self.assertIn(texts.WEB_LABELS["state_lan"].split("{")[0], dlg.web_state.text())
        dlg.web_enable.setChecked(False)
        self.pump(0.2)
        self.assertFalse(self.web.enabled)
        self.assertFalse(self.web.service.access.lan)
        dlg.close()


class TestFitGainButton(RtlUiBase):
    def setUp(self):
        super().setUp()

    def make_window(self):
        self.lib = GainLib()
        self.lib.tones = [(TONE_HZ, 0.9)]
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        rtl = _RtlSdrBackend(lib_factory=lambda: self.lib)
        self.api.close()
        self.api = LocalApi(Instrument({"simulator": sim, "rtlsdr": rtl}, profile=profile,
                                       data_root=self.root / "d", cal_root=self.root / "c"))
        return UiBase.make_window(self)

    def test_button_disabled_with_reason_then_fits(self):
        p = self.win.rtl_panel
        self.assertFalse(p.fit_btn.isEnabled())
        self.assertIn(texts.RTL_LABELS["not_rtlsdr"], p.fit_btn.toolTip())
        self.connect_rtl()
        self.cmd("rtlsdr_set", gain_db=49.6)
        self.pump(0.2)
        self.assertTrue(p.fit_btn.isEnabled())
        p.fit_btn.click()
        self.wait(lambda: abs((self.state()["rtlsdr"]["gain_db"] or 0) - 29.7) < 1e-6, what="利得を合わせた")
        self.wait(lambda: "利得を 29.7 dB に合わせました" in self.win.status.note.text(), what="知らせ")
        self.wait(lambda: p.gain.currentData() == 29.7, what="欄が映す")
        self.cmd("rtlsdr_set", agc=True)
        self.pump(0.2)
        self.assertFalse(p.fit_btn.isEnabled())
        self.assertIn(texts.RTL_LABELS["fit_needs_manual"], p.fit_btn.toolTip())


if __name__ == "__main__":
    unittest.main()
