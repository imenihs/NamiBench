"""Qt の基準の値（設計E §6.7: 表示 = 基準との比 + 基準の値）の画面を確かめる。

- 信号源のタブの「基準の値」の欄（打てるドロップダウン・既定 0.0 dB・範囲の外は欄の下に知らせて戻す）。取るときに送る
- 上段の「基準を取る」も同じ欄の値を使う。上段・信号源のタブ・状態バーの基準の文に値が出る（0 dB では出さない）
- 一覧の行に値が出て、「基準の値…」で測り直さずに直せる（読めない値は知らせる）
- S11 では欄を理由付きで使えなくし、S11 の基準の行では「基準の値…」を理由付きで使えなくする
"""
from __future__ import annotations

import unittest

from namibench.ui_qt import screen_model, texts
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000


class RefValueUiTest(UiBase):
    def tg_ready(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_mode", mode="tg")
        self.wait(lambda: (self.win.state.get("sweep") or {}).get("mode") == "tg", what="TG モード")

    def selected(self):
        return self.state()["normalization"].get("selected") or {}

    def test_field_take_and_texts(self):
        src = self.win.source_tab
        f = src.take_value
        self.assertEqual(f.value(), 0.0)
        self.assertEqual(f.edit.text(), "0.0 dB")
        self.assertEqual(f.choices()[0], "+40 dB")
        self.assertEqual(f.choices()[-1], "−40 dB")
        # 範囲の外は欄の下に範囲を知らせて元の値に戻す（±100 dB）
        f.edit.setText("150")
        f._commit_text("150")
        self.pump(0.1)
        self.assertEqual(f.value(), 0.0)
        self.assertIn("100", f.note.text())
        f._commit_text("−40")
        self.assertEqual(f.value(), -40.0)
        self.tg_ready()
        self.pump(0.2)
        self.assertTrue(f.isEnabled())
        src.take_wide.click()
        self.wait(lambda: self.selected().get("value_db") == -40.0 and not self.state()["tasks"], timeout=30,
                  what="基準の値 −40 dB の基準")
        self.wait(lambda: src.ref_list.count() == 1, what="一覧")
        self.assertTrue(src.ref_list.item(0).text().endswith("  −40.0 dB"), src.ref_list.item(0).text())
        self.wait(lambda: "（−40.0 dB）" in src.ref_state.text(), timeout=20, what="信号源のタブの基準の文")
        self.assertIn("−40.0 dB", self.win.ref_quick.status.text())
        # 上段の「基準を取る」も欄の値を使う
        f._commit_text("12.5")
        self.win.ref_quick.button.click()
        self.wait(lambda: self.selected().get("value_db") == 12.5 and not self.state()["tasks"], timeout=30,
                  what="上段で取った基準")
        self.wait(lambda: "+12.5 dB" in self.win.ref_quick.status.text(), timeout=20, what="上段の基準の文")

    def test_edit_value_from_list(self):
        src = self.win.source_tab
        self.tg_ready()
        self.pump(0.2)
        src.take_wide.click()
        self.wait(lambda: bool(self.selected()) and not self.state()["tasks"], timeout=30, what="基準")
        self.wait(lambda: src.ref_list.count() == 1, what="一覧")
        # 0 dB は行にも状態の文にも出さない（U-15）
        self.assertNotIn("dB", src.ref_list.item(0).text())
        self.assertFalse(src.btn_value.isEnabled())
        self.assertEqual(src.btn_value.toolTip(), texts.REASON_TEXTS["select_reference"])
        src.ref_list.setCurrentRow(0)
        self.assertTrue(src.btn_value.isEnabled())
        self.text_answer = "+6"
        src.btn_value.click()
        self.wait(lambda: self.selected().get("value_db") == 6.0, timeout=10, what="直した基準の値")
        self.wait(lambda: src.ref_list.count() == 1 and src.ref_list.item(0).text().endswith("+6.0 dB"),
                  what="一覧の行の値")
        self.wait(lambda: texts.tpl("ref_value_changed", name=self.selected()["name"], value="+6.0 dB")
                  in self.win.status.note.text(), what="直した知らせ")
        # 読めない値は送らずに知らせる
        self.text_answer = "abc"
        src.btn_value.click()
        self.pump(0.2)
        self.assertIn(texts.LABELS["bad_value"], self.win.status.note.text())
        self.assertEqual(self.selected().get("value_db"), 6.0)
        # 範囲の外はコアが断り、範囲を言う
        self.text_answer = "200"
        src.btn_value.click()
        self.wait(lambda: "100 dB の値を入れてください" in self.win.status.note.text(), what="範囲の外")
        self.assertEqual(self.selected().get("value_db"), 6.0)

    def test_s11_disables_field_and_row_button(self):
        src = self.win.source_tab
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.cmd("set_sweep", start_hz=100 * MHZ, stop_hz=200 * MHZ)
        self.cmd("set_mode", mode="s11")
        self.wait(lambda: not src.take_value.isEnabled(), what="S11 では使えない")
        self.assertIn(texts.REASON_TEXTS["ref_value_s11"], src.take_value.toolTip())
        self.cmd("reference_take", standard="open", kind="fine")
        self.wait(lambda: not self.state()["tasks"], timeout=20, what="オープン")
        self.cmd("reference_take", standard="short")
        self.wait(lambda: src.ref_list.count() == 1, timeout=20, what="S11 の基準")
        src.ref_list.setCurrentRow(0)
        self.assertFalse(src.btn_value.isEnabled())
        self.assertEqual(src.btn_value.toolTip(), texts.REASON_TEXTS["ref_value_s11"])
        # 上段の S11 の手順は基準の値を送らない
        self.assertNotIn("value_db", screen_model.ref_quick_view(self.state())["take"])


if __name__ == "__main__":
    unittest.main()
