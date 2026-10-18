"""Qt 画面の外部 IQ 送信の口（設計S §1 §7）: 状態バー（待ち受け・出力中・［送信を止める］）・信号源のタブの外部の行と減衰の欄
（別の値: O1）・SG/TG/S11 を理由付きで無効・使えない RBW の理由・設定の「外部 IQ 送信」のページ。
偽の PlutoSDR と 127.0.0.1 の口に繋ぐ試験のクライアントを使う。"""
import unittest

from PySide6.QtCore import Qt

from namibench.core.units import format_freq
from namibench.ui_qt import texts
from namibench.ui_qt.texts_exttx import EXT_TX_LABELS
from tests.exttx_helpers import LO, RawClient, free_port
from tests.test_ui_qt_plutosdr import PlutoUiBase

MHZ = 1_000_000
REASON = "外部のアプリケーションが送信中"


class ExtUiBase(PlutoUiBase):
    def setUp(self):
        super().setUp()
        self.port = free_port()
        self.client = None

    def tearDown(self):
        if self.client is not None:
            self.client.close()
        super().tearDown()

    def start_streaming(self, rbws=(48000,)):
        self.connect_pluto(single=False)
        self.cmd("set_sweep", start_hz=1574 * MHZ, stop_hz=1577 * MHZ, rbw_hz=rbws[0])
        for hz in rbws[1:]:
            self.cmd("set_sweep", rbw_hz=hz)
        self.cmd("run", mode="continuous")
        self.cmd("ext_tx_set", enabled=True, port=self.port)
        self.client = RawClient(self.port)
        self.client.wait_type("welcome")
        self.assertEqual(self.client.start()["type"], "ready")
        self.client.feed()
        self.client.wait_type("on")
        self.wait(lambda: ((self.state()["ext_tx"]["session"] or {}).get("state") == "on"), what="出力中")
        self.pump(0.5)


class TestExtTxQt(ExtUiBase):
    def test_streaming_state_and_stop(self):
        sb = self.win.status
        self.cmd("ext_tx_set", enabled=True, port=self.port)
        self.pump(0.3)
        self.assertEqual(sb.labels["exttx"].text(), f"外部 IQ 待機中 :{self.port}")
        self.assertFalse(sb.exttx_stop.isVisible())
        self.start_streaming()
        self.assertEqual(sb.labels["exttx"].text(), "外部 IQ 出力中 " + format_freq(LO))
        self.assertIn("アンダーラン", sb.labels["exttx"].toolTip())
        self.assertTrue(sb.exttx_stop.isVisible())
        self.assertEqual(sb.exttx_stop.text(), EXT_TX_LABELS["stop"])
        # SG・TG・S11 は理由付きで無効（SA は使える）
        for code in ("sg", "tg", "s11"):
            b = self.win.mode_buttons[code]
            self.assertFalse(b.isEnabled(), code)
            self.assertIn(REASON, b.toolTip())
        self.assertTrue(self.win.mode_buttons["sa"].isEnabled())
        # RBW: 48k Hann は理由付きで無効、実際の RBW は 12kHz
        combo = self.win.rbw_combo
        i48 = combo.findData(48000)
        self.assertFalse(combo.model().item(i48).isEnabled())
        self.assertIn("2.6MS/s", combo.itemData(i48, Qt.ItemDataRole.ToolTipRole))
        self.assertEqual(combo.currentData(), 12000)
        # 信号源のタブ: 出力中: 外部 IQ（test）・速さ・中心の周波数・強さ・減衰は外部の値（最小の出力）
        tx = self.win.source_tab.pluto_tx
        self.win.show_part("tab-source")
        self.pump(0.3)
        self.assertEqual(tx.state.text(), "出力中: 外部 IQ（test）")
        self.assertEqual(tx.ext_rate.text(), "2.6MS/s")
        self.assertEqual(tx.ext_lo.text(), format_freq(LO))
        self.assertTrue(tx.ext_power.text().endswith("dBFS"))
        self.assertTrue(tx.ext_stop.isVisible())
        self.assertEqual(tx.atten.edit.text(), "−89.75 dB")
        # 一覧から選ぶと外部の送信の減衰へ（SG・TG の plutosdr_set ではない）
        tx.atten.combo.activated.emit(tx.atten.combo.findText("−30 dB"))
        self.wait(lambda: self.state()["ext_tx"]["atten_db"] == -30.0, what="外部の減衰")
        self.assertNotEqual(self.state()["plutosdr"]["tx_atten_db"], -30.0)  # SG・TG の値は別
        # 状態バーの［送信を止める］
        sb.exttx_stop.click()
        self.assertEqual(self.client.wait_type("stopped")["reason"], "user")
        self.wait(lambda: self.state()["ext_tx"]["session"] is None and self.state()["sweep"]["rbw_hz"] == 48000,
                  what="止まって RBW が戻る")
        self.pump(0.3)
        for code in ("sg", "tg", "s11"):
            self.assertTrue(self.win.mode_buttons[code].isEnabled(), code)
        self.assertFalse(tx.ext_stop.isVisible())
        self.assertEqual(tx.state.text(), "")

    def test_source_tab_stop_button(self):
        self.start_streaming()
        self.win.show_part("tab-source")
        self.pump(0.2)
        self.win.source_tab.pluto_tx.ext_stop.click()
        self.assertEqual(self.client.wait_type("stopped")["reason"], "user")

    def test_undo_during_stream_notes_rbw(self):
        """元に戻すで積んだ RBW（送信の前の 24kHz）が 2.6MS/s で使えないとき、使える RBW にして RBW の欄のそばで知らせる
        （設計S §1 §3.2・設計C §5.14）。"""
        self.start_streaming(rbws=(24000, 1500))
        self.win._undo_redo("undo")
        self.wait(lambda: self.state()["sweep"]["rbw_preference_hz"] == 24000, what="元に戻す")
        self.pump(0.3)
        self.assertEqual(self.win.rbw_combo.currentData(), 12000)
        self.assertIn(texts.REASONS["rbw_sample_rate"], self.win.rbw_field.note.text())

    def test_settings_page(self):
        self.win._settings_dialog()
        dlg = self.win._settings
        dlg.open_page("ext_tx")
        self.pump(0.2)
        self.assertFalse(dlg.ext_enable.isChecked())
        self.assertEqual(dlg.ext_port.value(), 1235)
        self.assertEqual(dlg.ext_state.text(), "停止中")
        dlg.ext_port.setValue(self.port)
        dlg.ext_enable.setChecked(True)
        self.wait(lambda: self.state()["ext_tx"]["listening"], what="待ち受け")
        self.pump(0.2)
        self.assertEqual(dlg.ext_state.text(), f"待機中: ポート {self.port}（この PC のみ）")
        dlg.ext_enable.setChecked(False)
        self.wait(lambda: not self.state()["ext_tx"]["listening"], what="止める")
        dlg.close()


if __name__ == "__main__":
    unittest.main()
