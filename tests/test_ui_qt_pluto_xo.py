"""Qt 画面の PlutoSDR の基準の補正（設計Q §7.10）: 設定の画面の PlutoSDR のページの欄。libiio の偽物で確かめる。

欄（ppm、小数 3 桁、範囲は機器の申告から）・機器から読み戻した基準の Hz・送り先（plutosdr_xo_set）・断られたときの
欄の下の知らせ・使えないときの理由（ツールチップ）・繋いでいないときは次に書く機器。中身は Web と同じ screen_pluto.xo_view。
"""
import unittest

from pygigast.core.devices.plutosdr.fake import FAKE_SERIAL
from pygigast.ui_qt import texts
from pygigast.ui_qt.fields import DecimalField, range_note
from pygigast.ui_qt.screen_pluto import xo_view
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests.test_ui_qt_plutosdr import PlutoUiBase


class TestPlutoXoSettings(PlutoUiBase):
    def open_page(self):
        self.win._settings_dialog("plutosdr")
        self.pump(0.2)
        return self.win._settings

    def test_field_shows_value_range_and_readback(self):
        d = self.open_page()
        # PlutoSDR に繋いだことが無い: どの機器の値か決まらないので使えない（理由はツールチップ）
        self.assertIsInstance(d.pl_xo, DecimalField)
        self.assertFalse(d.pl_xo.isEnabled())
        self.assertIn(PLUTO_LABELS["not_pluto"], d.pl_xo.toolTip())
        self.assertFalse(d.pl_xo_hz.isVisibleTo(d))
        self.connect_pluto()
        self.wait(lambda: d.pl_xo.isEnabled(), what="欄が使える")
        self.assertEqual(d.pl_xo.edit.text(), "0.000 ppm")
        self.assertEqual((d.pl_xo.lo, d.pl_xo.hi, d.pl_xo.decimals), (-200.0, 200.0, 3))
        self.assertEqual(d.pl_xo_hz.text(), "機器の基準 40,000,000 Hz")
        self.assertTrue(d.pl_xo_hz.isVisibleTo(d))
        sent = []
        orig = self.win.call
        self.win.call = lambda name, *a, **kw: (sent.append((name, kw.get("ppm"), kw.get("field"))), orig(name, *a, **kw))
        try:
            d.pl_xo.edit.setText("1.2344")
            d.pl_xo.edit.committed.emit("1.2344")
            self.assertEqual(sent, [("plutosdr_xo_set", 1.234, d.pl_xo)])  # 小数 3 桁に丸めて送る
            self.wait(lambda: self.lib.pluto.xo_hz == 40_000_049, what="機器に書いた")
            self.wait(lambda: d.pl_xo_hz.text() == "機器の基準 40,000,049 Hz", what="読み戻した値")
            self.wait(lambda: d.pl_xo.edit.text() == "1.234 ppm", what="欄の値")
            # 範囲の外は送らず、欄の下に範囲を出す
            sent.clear()
            d.pl_xo.edit.setText("250")
            d.pl_xo.edit.committed.emit("250")
            self.assertEqual(sent, [])
            self.assertEqual(d.pl_xo.note.text(), range_note(-200.0, 200.0, "ppm"))
            # 同じ値は送り直さない（変えるとトレースの蓄積を捨てる）
            d.pl_xo.edit.committed.emit("1.234")
            self.assertEqual(sent, [])
            # 「初期値に戻す」は 0
            d.pl_xo_reset.click()
            self.assertEqual(sent, [("plutosdr_xo_set", 0.0, d.pl_xo)])
            self.wait(lambda: d.pl_xo.edit.text() == "0.000 ppm", what="0 に戻る")
        finally:
            self.win.call = orig

    def test_refused_while_sweeping_and_reason(self):
        d = self.open_page()
        self.connect_pluto(single=False)
        self.cmd("run", mode="continuous")
        busy = texts.ERRORS["plutosdr_xo_busy"]
        self.wait(lambda: not d.pl_xo.isEnabled(), what="測っている間は使えない")
        self.assertIn(busy, d.pl_xo.toolTip())
        self.assertFalse(d.pl_xo_reset.isEnabled())
        # 画面の外から届いた命令（Web など）が断られた知らせも、欄の下に出る
        self.win.call("plutosdr_xo_set", field=d.pl_xo, ppm=2.0)
        self.wait(lambda: d.pl_xo.note.text() == busy, what="断りの知らせ")
        self.cmd("stop")
        self.wait(lambda: d.pl_xo.isEnabled(), what="止まったら使える")

    def test_not_connected_shows_the_device_it_will_write(self):
        d = self.open_page()
        self.connect_pluto(single=False)
        self.cmd("disconnect")
        want = texts.tpl("pluto_xo_next", serial=FAKE_SERIAL)
        self.wait(lambda: d.pl_xo_hz.text() == want, what="次に書く機器")
        self.assertTrue(d.pl_xo.isEnabled())
        d.pl_xo.edit.setText("-3")
        d.pl_xo.edit.committed.emit("-3")
        self.wait(lambda: self.state()["plutosdr"]["xo"]["ppm"] == -3.0, what="覚えた")
        self.assertEqual(self.lib.pluto.xo_hz, 40_000_000)  # 繋いでいないので書かない
        self.assertEqual(xo_view(self.state())["hz_text"], want)
        self.connect_pluto(single=False)
        self.wait(lambda: d.pl_xo_hz.text() == "機器の基準 39,999,880 Hz", what="繋いだら書いた")

    def test_refused_on_connect_shows_warning(self):
        d = self.open_page()
        self.cmd("plutosdr_xo_set", ppm=150.0, serial=FAKE_SERIAL)
        self.lib.pluto._set_raw("ad9361-phy", "", False, "xo_correction_available", "[39996000 1 40004000]")
        self.connect_pluto(single=False)
        want = texts.tpl("pluto_xo_refused", ppm="150.000", min="−100.000", max="100.000")
        self.wait(lambda: d.pl_xo_warn.text() == want, what="範囲の外で書かなかった")
        self.assertTrue(d.pl_xo_warn.isVisibleTo(d))
        self.assertEqual((d.pl_xo.lo, d.pl_xo.hi), (-100.0, 100.0))


if __name__ == "__main__":
    unittest.main()
