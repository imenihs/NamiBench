"""Qt の画面: 設定を黙って変えない（設計J §4.10 の 2〜4）と、SG の周波数を変えたときの保持の選択（設計F §8）。

- 単位の欄は実際に使っている単位を出す。基準との比の間は「dB（基準との比）」で、電力の単位は理由付きで選べない
- 電力 → 比に変わったら窓で一度知らせる。比 → 比では出さない。「次から出さない」で次から出さず、設定の画面で戻せる
- 単位の設定がほかの口（直接の命令）から変わっても、単位の欄の下と状態バーに一度知らせる。起動では出さない
- 信号源のタブの「周波数を変えたら最大・最小保持を消す」はコアの設定を映し、変える

シミュレータの機器で通す（tests/test_ui_qt_support.py）。窓は open() で出るので試験を止めない。
"""
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QMessageBox

from pygigast.ui_qt import texts
from pygigast.ui_qt.tab_display import PREF_NOTIFY_RATIO
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
V = texts.VIEW_TEXTS


class LevelUnitUiTest(UiBase):
    def setUp(self):
        super().setUp()
        self.tab = self.win.display_tab

    def sweep_once(self):
        n0 = self.state()["run"]["sweep_count"]
        self.cmd("run", mode="single")
        self.wait(lambda: self.state()["run"]["state"] == "stopped" and self.state()["run"]["sweep_count"] > n0,
                  what="単発の掃引")
        self.wait(lambda: self.win.state.get("run", {}).get("sweep_count", 0) > n0, what="画面に状態が届く")
        self.pump(0.1)

    def take_reference(self, name="直結 A"):
        self.cmd("set_mode", mode="tg")
        self.api.call("reference_take", kind="wide", name=name).result(10)  # UiBase.cmd の引数 name とぶつかる
        self.wait(lambda: not self.state()["tasks"] and (self.state()["normalization"] or {}).get("selected"),
                  timeout=20, what="基準を取り終える")

    def item(self, code):
        model = self.tab.unit.model()
        self.assertIsInstance(model, QStandardItemModel)
        return model.item(self.tab.unit.findData(code))

    def close_box(self, dont_show):
        box = self.tab.ratio_box
        box.checkBox().setChecked(dont_show)
        box.button(QMessageBox.StandardButton.Close).click()
        self.pump(0.1)

    def test_unit_field_shows_unit_in_use(self):
        self.connect_and_sweep(100 * MHZ, 200 * MHZ)
        self.assertEqual(self.tab.unit.currentData(), "dBm")
        self.assertEqual([self.item(c).isEnabled() for c in ("dBm", "dBuV", "dBmV", "ratio")],
                         [True, True, True, False])
        self.assertEqual(self.item("ratio").toolTip(), V["level_unit_ratio_item_tip"])
        self.take_reference()
        self.sweep_once()
        self.wait(lambda: self.tab.unit.currentData() == "ratio", what="欄が比を出す")
        self.assertEqual(self.tab.unit.currentText(), V["level_unit_ratio_item"])
        reason = V["level_unit_ratio_reason"].replace("{unit}", "dBm")
        for c in ("dBm", "dBuV", "dBmV"):
            self.assertFalse(self.item(c).isEnabled(), c)
            self.assertEqual(self.item(c).toolTip(), reason, c)
        self.assertIn(reason, self.tab.unit.toolTip())
        # 選んでいた電力の単位は覚えている（設定は dBm のまま）。SA に戻すと電力の単位
        self.assertEqual(self.state()["view"]["level_unit"], "dBm")
        self.close_box(False)
        self.cmd("set_mode", mode="sa")
        self.sweep_once()
        self.wait(lambda: self.tab.unit.currentData() == "dBm", what="欄が電力の単位に戻る")
        self.assertTrue(self.item("dBuV").isEnabled())

    def test_ratio_box_once_and_dont_show_again(self):
        self.connect_and_sweep(100 * MHZ, 200 * MHZ)
        self.cmd("set_view", level_unit="dBuV")
        self.assertIsNone(self.tab.ratio_box)
        self.take_reference()
        self.sweep_once()
        self.wait(lambda: self.tab.ratio_box is not None and self.tab.ratio_box.isVisible(), what="窓が出る")
        box = self.tab.ratio_box
        self.assertEqual(box.windowTitle(), V["ratio_title"])
        self.assertEqual(box.text(), V["ratio_text"].replace("{name}", "直結 A").replace("{unit}", "dBμV"))
        self.assertEqual(box.checkBox().text(), V["dont_show_again"])
        self.assertIn(V["ratio_status"].replace("{name}", "直結 A"), self.win.status.note.text())
        # 比 → 比では出さない
        self.close_box(False)
        self.sweep_once()
        self.assertIs(self.tab.ratio_box, box)
        self.assertFalse(box.isVisible())
        # もう一度 電力 → 比: 出す。「次から出さない」で閉じる
        self.cmd("set_mode", mode="sa")
        self.sweep_once()
        self.cmd("set_mode", mode="tg")
        self.sweep_once()
        self.wait(lambda: box.isVisible(), what="窓がまた出る")
        self.assertFalse(box.checkBox().isChecked())
        self.close_box(True)
        self.assertIs(self.win.prefs.get(PREF_NOTIFY_RATIO), False)
        # 次の 電力 → 比 では出さない（状態バーには出る）
        self.cmd("set_mode", mode="sa")
        self.sweep_once()
        self.win.status.note.setText("")
        self.cmd("set_mode", mode="tg")
        self.sweep_once()
        self.wait(lambda: V["ratio_status"].replace("{name}", "直結 A") in self.win.status.note.text(),
                  what="状態バーには出る")
        self.pump(0.2)
        self.assertFalse(box.isVisible())
        # 設定の画面の「表示」で戻せる
        self.win._settings_dialog("display")
        dlg = self.win._settings
        self.assertFalse(dlg.notify_ratio.isChecked())
        self.assertEqual(dlg.notify_ratio.text(), V["notify_ratio"])
        dlg.notify_ratio.click()
        self.assertIs(self.win.prefs.get(PREF_NOTIFY_RATIO), True)
        dlg.close()

    def test_unit_change_from_another_source_is_shown(self):
        self.assertTrue(self.tab.unit_note.isHidden())  # 起動で戻したときは出さない
        self.cmd("connect", backend="simulator", serial="SIM00001")
        # 別の口（Web・プリセット・元に戻すと同じく、この画面の欄を通らない命令）
        self.api.call("set_view", level_unit="dBuV").result(5)
        want = V["level_unit_changed"].replace("{unit}", "dBμV")
        self.wait(lambda: self.tab.unit_note.isVisible() and self.tab.unit_note.text() == want, what="欄の下の知らせ")
        self.assertIn(want, self.win.status.note.text())
        self.assertEqual(self.tab.unit.currentData(), "dBuV")
        # 元に戻す（Ctrl+Z）でも出す
        self.cmd("undo")
        want = V["level_unit_changed"].replace("{unit}", "dBm")
        self.wait(lambda: self.tab.unit_note.text() == want, what="元に戻すの知らせ")
        self.assertEqual(self.tab.unit.currentData(), "dBm")


class SgClearHoldsUiTest(UiBase):
    def test_checkbox_follows_core_setting(self):
        cb = self.win.source_tab.sg_clear_holds
        self.assertEqual(cb.text(), texts.LABELS["sg_clear_holds"])
        self.assertTrue(cb.isChecked())
        cb.click()
        self.wait(lambda: self.state()["trace_options"]["sg_clears_holds"] is False, what="コアの設定が変わる")
        self.cmd("set_trace_options", sg_clears_holds=True)
        self.wait(lambda: cb.isChecked(), what="ほかの口の変更を映す")


if __name__ == "__main__":
    import unittest

    unittest.main()
