"""Qt 画面の PlutoSDR の欄（設計Q §7）: libiio の偽物の PlutoSDR に繋いで確かめる。

受信の利得（dB の打てるドロップダウン: 範囲の 1dB 刻みを打てて、一覧は自動と 10dB 刻み）・使う値の行・利得を合わせる・
送信の減衰・設定の PlutoSDR のページ・機器の一覧。
"""
import unittest

from pygigast.api.local import LocalApi, _PlutoSdrBackend, _SimulatorBackend
from pygigast.core.devices.base import load_profile
from pygigast.core.units import minus_sign
from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL, FAKE_URI, FakeIioLib
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import texts
from pygigast.ui_qt.fields import DbChoiceField, range_note
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
        # REF の欄と同じ部品。一覧は自動 + 範囲（1300MHz 未満の帯は −1〜73dB）の中の 10dB 刻み
        self.assertIsInstance(rx.gain, DbChoiceField)
        self.assertEqual(rx.gain.choices(), [PLUTO_LABELS["auto"]] + [f"{g} dB" for g in range(0, 71, 10)])
        self.assertEqual((rx.gain.lo, rx.gain.hi), (-1.0, 73.0))
        self.assertEqual(rx.gain.edit.text(), "30 dB")
        self.assertEqual(rx.gain.value(), 30.0)
        self.assertIn(UNCAL, rx.unit_text.text())
        self.cmd("disconnect")
        self.wait(lambda: rx.isHidden() and tx.isHidden(), what="欄が消える")

    def test_points_field_shows_more_than_12000(self):
        """FFT の機器は点数が RBW と Span で決まり、12000 を超えることがある。欄は実際の点数を出す（切り詰めない）。"""
        self.connect_pluto()
        self.cmd("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ, rbw_hz=6000)
        n = self.state()["sweep"]["points"]
        self.assertGreater(n, 12000)
        self.wait(lambda: self.win.trace_tab.points_spin.value() == n, what="点数の欄が実際の点数")

    def test_gain_auto_and_used_value(self):
        self.connect_pluto()
        rx = self.win.pluto_panel
        # 一覧に無い値を打つ（1dB 刻み。小数は整数に丸める）
        rx.gain.edit.committed.emit("73")
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 73.0, what="打った利得")
        self.wait(lambda: rx.gain.edit.text() == "73 dB", what="欄の値")
        self.assertTrue(rx.gain_used.isHidden())
        rx.gain.edit.committed.emit("42.4")
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 42.0, what="整数に丸める")
        rx.gain.edit.committed.emit("80")  # 範囲の外は送らず、欄の下に範囲を出す
        self.pump(0.3)
        self.assertEqual(self.state()["plutosdr"]["gain_db"], 42.0)
        self.assertEqual(rx.gain.edit.text(), "42 dB")
        # 一覧から選ぶ
        i = rx.gain.combo.findText("70 dB")
        rx.gain.combo.setCurrentIndex(i)
        rx.gain.combo.activated.emit(i)
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 70.0, what="一覧の利得")
        self.wait(lambda: rx.gain.value() == 70.0, what="欄が映す")
        # ホイールは 1dB
        rx.gain.edit.stepped.emit(0, +1)
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 71.0, what="1dB 上げる")
        rx.gain.edit.committed.emit("73")
        self.wait(lambda: self.state()["plutosdr"]["gain_db"] == 73.0, what="利得")
        # 2.4GHz 帯は −3〜71dB: 範囲が変わり、使う値の行が出る（U-25）
        self.cmd("set_sweep", start_hz=2_400 * MHZ, stop_hz=2_402 * MHZ)
        self.wait(lambda: (rx.gain.lo, rx.gain.hi) == (-3.0, 71.0), what="帯の範囲")
        self.wait(lambda: not rx.gain_used.isHidden(), what="使う値の行")
        self.assertEqual(rx.gain_used.text(), texts.tpl("pluto_gain_used", db="71"))
        self.assertEqual(rx.gain.edit.text(), "71 dB")
        rx.gain.edit.committed.emit("72")
        self.pump(0.3)
        self.assertEqual(self.state()["plutosdr"]["gain_db"], 73.0)
        # 自動は一覧の先頭の行か、その文字を打つ
        rx.gain.combo.setCurrentIndex(0)
        rx.gain.combo.activated.emit(0)
        self.wait(lambda: self.state()["plutosdr"]["agc"], what="自動")
        self.wait(lambda: rx.gain.edit.text() == PLUTO_LABELS["auto"], what="欄が自動")
        self.wait(lambda: not rx.fit_btn.isEnabled(), what="利得を合わせるが無効")
        self.assertIn(PLUTO_LABELS["fit_needs_manual"], rx.fit_btn.toolTip())
        self.assertEqual(rx.gain.value(), 71.0)
        # 自動の間のホイールは使う値から 1dB 送って手動にする
        rx.gain.edit.stepped.emit(0, -1)
        self.wait(lambda: not self.state()["plutosdr"]["agc"] and self.state()["plutosdr"]["gain_db"] == 70.0,
                  what="自動から手動へ")
        self.wait(lambda: rx.gain.edit.text() == "70 dB", what="欄が手動の値")
        rx.gain.edit.committed.emit(PLUTO_LABELS["auto"])
        self.wait(lambda: self.state()["plutosdr"]["agc"], what="打って自動")

    def test_gain_same_value_is_not_sent_again(self):
        # 利得を送るとトレースの蓄積を捨てるので、同じ値（欄を離れたときに一覧の同じ文字の行が選ばれたときも）は送らない
        self.connect_pluto()
        rx = self.win.pluto_panel
        sent = []
        orig = self.win.call
        self.win.call = lambda name, *a, **kw: (sent.append((name, kw.get("gain_db"), kw.get("agc"))), orig(name, *a, **kw))
        try:
            i = rx.gain.combo.findText("30 dB")
            rx.gain.combo.activated.emit(i)
            rx.gain.edit.committed.emit("30")
            self.assertEqual(sent, [])
            rx.gain.edit.committed.emit("31")
            self.assertEqual(sent, [("plutosdr_set", 31.0, False)])
        finally:
            self.win.call = orig

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
        self.wait(lambda: tx.atten.edit.text().startswith("−12.25"), what="欄の値")
        tx.atten.edit.committed.emit("5")
        self.wait(lambda: tx.atten.edit.text().startswith("−12.25"), what="範囲外は変わらない")

    def test_tx_atten_choices_and_range(self):
        # 送信の減衰は受信の利得と同じ打てるドロップダウン。一覧は 0〜−80dB の 10dB 刻みと最小の −89.75dB
        # （−90 は機器の範囲の外）。中身は Web と同じ screen_pluto.pluto_view の tx_atten_choices
        self.connect_pluto()
        tx = self.win.source_tab.pluto_tx
        self.assertIsInstance(tx.atten, DbChoiceField)
        self.assertEqual(tx.atten.choices(), ["0 dB"] + [f"−{a} dB" for a in range(10, 81, 10)] + ["−89.75 dB"])
        self.assertEqual((tx.atten.lo, tx.atten.hi, tx.atten.decimals), (-89.75, 0.0, 2))
        self.assertEqual(tx.atten.edit.text(), "−40.00 dB")
        sent = []
        orig = self.win.call
        self.win.call = lambda name, *a, **kw: (sent.append((name, kw.get("tx_atten_db"), kw.get("field"))),
                                                orig(name, *a, **kw))
        try:
            # SG・TG: 一覧から選ぶと plutosdr_set の tx_atten_db（知らせは欄の下）
            for mode, extra, pick, want in (("sg", {"sg_hz": 85 * MHZ}, "−30 dB", -30.0), ("tg", {}, "−89.75 dB", -89.75)):
                self.cmd("set_mode", mode=mode, **extra)
                self.pump(0.2)
                sent.clear()
                tx.atten.combo.activated.emit(tx.atten.combo.findText(pick))
                self.assertEqual(sent, [("plutosdr_set", want, tx.atten)], mode)
                self.wait(lambda: self.state()["plutosdr"]["tx_atten_db"] == want, what=f"{mode} の送信の減衰")
                self.wait(lambda: tx.atten.edit.text() == minus_sign(f"{want:.2f} dB"), what="欄の値（小数2桁）")
            # 範囲の外を打つと、欄の下に範囲を知らせて元に戻す（送らない）
            sent.clear()
            for typed in ("5", "-90"):
                tx.atten.edit.setText(typed)
                tx.atten.edit.committed.emit(typed)
                self.assertEqual(sent, [], typed)
                self.assertEqual(tx.atten.note.text(), range_note(-89.75, 0.0, "dB"))
                self.assertEqual(tx.atten.edit.text(), "−89.75 dB")
            # 範囲の中を打てば送る（0.25dB 刻みへの丸めはコア）
            tx.atten.edit.committed.emit("−89.7")
            self.assertEqual(sent, [("plutosdr_set", -89.7, tx.atten)])
            self.wait(lambda: self.state()["plutosdr"]["tx_atten_db"] == -89.75, what="丸めはコア")
        finally:
            self.win.call = orig

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
