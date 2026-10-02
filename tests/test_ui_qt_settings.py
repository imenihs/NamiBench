"""Qt の設定の画面・配色・キーの割り当て・プリセット・表示の単位と周波数オフセット・切り離し（設計O・J §4.3.2・§4.4・§4.6）。"""
import unittest

from PySide6.QtWidgets import QApplication, QPushButton

from pygigast.core import report_layout as rl
from pygigast.core.palette import BASES
from pygigast.ui_qt import keymap, texts
from tests.test_ui_qt_support import UiBase


class KeymapTest(unittest.TestCase):
    def test_defaults_and_shift(self):
        km = keymap.effective({})
        self.assertEqual(km["run_cont"], "F5")
        self.assertEqual(km["mode_nf"], "Ctrl+5")
        self.assertEqual(keymap.sequences("marker_left", "Left"), ("Left", "Shift+Left"))
        self.assertEqual(keymap.sequences("run_cont", "F5"), ("F5",))
        self.assertIn("presets", keymap.commands())
        self.assertIn("mode_sa", keymap.commands())

    def test_conflict_assign_reserved(self):
        km = keymap.effective({})
        self.assertEqual(keymap.conflict(km, "run_single", "F5"), "run_cont")
        # Shift 付きのキーも同じ操作として数える（マーカーを動かすキー）
        self.assertEqual(keymap.conflict(km, "run_single", "Shift+Left"), "marker_left")
        ov = keymap.assign({}, "run_single", "F5", take_from="run_cont")
        self.assertEqual(ov, {"run_single": "F5", "run_cont": ""})
        self.assertEqual(sorted(keymap.changed(ov)), ["run_cont", "run_single"])
        # 初期値に戻した分は持たない
        self.assertEqual(keymap.assign(ov, "run_single", "F6"), {"run_cont": ""})
        self.assertIsNotNone(keymap.reserved_reason("Tab"))
        self.assertIsNotNone(keymap.reserved_reason("Alt+F"))
        self.assertIsNone(keymap.reserved_reason("F9"))
        self.assertEqual(keymap.load_overrides('{"run_cont": "F9", "nope": "X", "stop": 3}'), {"run_cont": "F9"})
        self.assertEqual(keymap.load_overrides("broken"), {})

    def test_web_reserved_table(self):
        for k in ("Ctrl+W", "Ctrl+T", "F5", "Ctrl+1", "Ctrl+Shift+C"):
            self.assertIn(texts.WEB_RESERVED_KEYS[k], texts.KEY_RESERVED_REASONS)
        # Web の初期値はブラウザが先に取るキーを使わない（J §4.6）
        for code, k in texts.WEB_KEY_DEFAULTS.items():
            self.assertNotIn(k, texts.WEB_RESERVED_KEYS, code)


class ReportLayoutOffsetTest(unittest.TestCase):
    def test_band_and_axis_follow_offset_and_ext(self):
        data = {"conditions": {"start_hz": 1_000_000_000, "stop_hz": 2_000_000_000, "freq_offset_hz": 10_000_000_000,
                               "ext_db": 10.0, "unit": "dBμV", "ref_level": 100.0, "db_per_div": 10.0},
                "markers": [{"name": "M1", "freq_hz": 1_500_000_000, "value": 90.0}], "measured_at": None}
        lay = rl.layout(data, (1600, 1000))
        band = [x for _c, items in lay["band"] for x in items]
        self.assertIn("Start 11 GHz", band)
        self.assertTrue(any(x.startswith("周波数オフセット +10 GHz") for x in band), band)
        self.assertIn("外部補正 +10 dB", band)
        self.assertEqual(lay["x_labels"][0], "11000")
        self.assertIn("換算後", lay["x_title"])
        self.assertEqual(lay["table"]["rows"][0][2], "11500")

    def test_level_labels_like_ref_field(self):
        """単位を換えた REF の端数（106.99）を目盛・帯に出さない。REF の欄と同じ 0.1 dB の形（負号「−」）。"""
        data = {"conditions": {"unit": "dBμV", "ref_level": 116.99, "db_per_div": 10.0}, "markers": [],
                "measured_at": None}
        lay = rl.layout(data, (1600, 1000))
        self.assertEqual(lay["y_labels"][:3], ["117.0", "107.0", "97.0"])
        self.assertEqual(lay["y_labels"][-1], "17.0")
        self.assertIn("REF 117.0 dBμV", [x for _c, items in lay["band"] for x in items])
        self.assertEqual(rl.y_labels(5.0, 1.0)[-2:], ["−4.0", "−5.0"])
        self.assertEqual(rl.y_labels(0.04, 0.1)[-1], "−1.0")
        self.assertEqual(rl.y_labels(-0.01, 1.0)[0], "0.0")  # −0.0 と書かない


class SettingsDialogTest(UiBase):
    size = (1280, 720)

    def open_settings(self, page=None):
        self.win._settings_dialog(page)
        self.pump(0.2)
        return self.win._settings

    def test_pages_and_palette_editor(self):
        dlg = self.open_settings("display")
        pages = [dlg.pages.tabText(i) for i in range(dlg.pages.count())]
        self.assertEqual(pages, list(texts.SETTINGS_PAGES.values()))
        # 「表示」のページは配色の編集と、比の表示の窓を出すか（J §4.10）
        self.assertTrue(dlg.pages.currentWidget().isAncestorOf(dlg.palette_page))
        self.assertTrue(dlg.pages.currentWidget().isAncestorOf(dlg.notify_ratio))
        page = dlg.palette_page
        # 色を変えたら、その場で画面の配色と印が変わる（J §4.4）
        self.win.ask_color = lambda title, cur: "#00ff00"
        page._pick("trace1")
        self.wait(lambda: self.win.pal["trace1"] == "#00ff00", what="画面の色")
        self.wait(lambda: page._rows["trace1"]["mark"].text() == "●", what="変えた印")
        self.assertEqual(self.win.traces.live[1]["curve"].opts["pen"].color().name(), "#00ff00")
        self.assertTrue(page._rows["trace1"]["reset"].isEnabled())
        # 見分けにくいトレースの知らせ（止めはしない）
        page._set("trace2", "#00f000")
        self.wait(lambda: "見分けにくい" in page.warn.text(), what="見分けにくさの知らせ")
        # ライトの初期値: 変えた色の数を言ってから聞く。断れば変えない
        self.confirm_answer = False
        dlg.palette_page.light.click()
        self.pump(0.2)
        self.assertIn("変えた色 2 個", self.confirm_asked[-1])
        self.assertEqual(self.state()["palette"]["base"], "dark")
        self.confirm_answer = True
        dlg.palette_page.light.click()
        self.wait(lambda: self.win.pal == BASES["light"], what="ライトの初期値")
        # 元に戻す（Ctrl+Z）で戻せる
        self.win.actions["undo"].trigger()
        self.wait(lambda: self.win.pal["trace1"] == "#00ff00", what="元に戻す")
        # 変えた色が無ければ聞かずにすぐ切り替える
        self.cmd("palette_reset", base="dark")
        n = len(self.confirm_asked)
        self.win.reset_palette("light")
        self.wait(lambda: self.state()["palette"]["base"] == "light", what="すぐ切り替える")
        self.assertEqual(len(self.confirm_asked), n)

    def test_key_rebinding_updates_menu_and_tooltips(self):
        dlg = self.open_settings("keys")
        kp = dlg.keys_page
        # ぶつかるキーは聞く。断れば変えない
        self.confirm_answer = False
        self.assertFalse(kp.set_key("run_single", "F5"))
        self.assertIn("「連続」", self.confirm_asked[-1])
        self.assertEqual(self.win.actions["run_single"].shortcut().toString(), "F6")
        # 付け替える: 連続のキーは外れる。メニューの項目とボタンのツールチップも変わる
        self.confirm_answer = True
        self.assertTrue(kp.set_key("run_single", "F5"))
        self.assertEqual(self.win.actions["run_single"].shortcut().toString(), "F5")
        self.assertEqual(self.win.actions["run_cont"].shortcut().toString(), "")
        self.assertIn("F5", self.win.btn_single.toolTip())
        self.assertNotIn("F5", self.win.btn_cont.toolTip())
        self.assertIn("F5", self.win.actions["run_single"].toolTip())
        # 割り当てられないキーは理由を出して変えない
        self.assertFalse(kp.set_key("marker_here", "Tab"))
        self.assertIn("Tab", kp.status.text())
        # マーカーを動かすキーは Shift 付きも付く
        kp.set_key("marker_left", "J")
        self.assertEqual([s.toString() for s in self.win.actions["marker_left"].shortcuts()], ["J", "Shift+J"])
        # 全部を初期値に戻す: 変えた数を言ってから聞く
        kp.reset_all()
        self.assertIn("3 個", self.confirm_asked[-1])
        self.assertEqual(self.win.actions["run_cont"].shortcut().toString(), "F5")
        self.assertEqual(self.win.prefs.get("keys"), "{}")

    def test_save_page_prefs_and_reset(self):
        dlg = self.open_settings("save")
        dlg.png_size.setCurrentIndex(dlg.png_size.findText("1920×1080"))
        dlg.png_size.activated.emit(dlg.png_size.currentIndex())
        self.assertEqual(self.win.display_tab.png_size_choice(), (1920, 1080))
        dlg._set_pref("png_size", "1600×1000")
        self.assertEqual(self.win.display_tab.png_size_choice(), (1600, 1000))
        # PNG の配色は ユーザー指定・ライト・ダーク の3つ。既定はユーザー指定（I §3.6）
        items = [(dlg.png_palette.itemData(i), dlg.png_palette.itemText(i)) for i in range(dlg.png_palette.count())]
        self.assertEqual(items, [("screen", "ユーザー指定（画面と同じ色）"), ("light", "ライト"), ("dark", "ダーク")])
        self.assertEqual(dlg.png_palette.currentData(), "screen")
        dlg.png_palette.setCurrentIndex(dlg.png_palette.findData("dark"))
        dlg.png_palette.activated.emit(dlg.png_palette.currentIndex())
        self.assertEqual(self.win.display_tab.png_palette_choice(), "dark")
        # 「初期値に戻す」（保存のページで最後に作るもの = PNG の配色の行）
        resets = [b for b in dlg.png_palette.parentWidget().findChildren(QPushButton)
                  if b.text() == texts.SETTINGS_TEXTS["reset"]]
        resets[-1].click()
        self.assertEqual(self.win.display_tab.png_palette_choice(), "screen")
        self.assertEqual(dlg.png_palette.currentData(), "screen")

    def test_known_device_forget_and_manual_port(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        dlg = self.open_settings("connection")
        self.wait(lambda: dlg.known.rowCount() == 1, what="覚えた機器")
        dlg._forget("SIM00001", "SIM00001")
        self.wait(lambda: self.state()["known_devices"] == [], what="忘れる")
        self.assertIn("SIM00001", self.confirm_asked[-1])
        dlg.manual_port.setText("abc")
        dlg._connect_manual()
        self.assertIn("COM", self.win.st_note.text())


class ViewOptionsTest(UiBase):
    def test_unit_ext_offset_on_screen(self):
        self.connect_and_sweep()
        d = self.win.display_tab
        d.unit.setCurrentIndex(d.unit.findData("dBuV"))
        d.unit.activated.emit(d.unit.currentIndex())
        self.wait(lambda: self.win.display_unit() == "dBμV", what="縦軸の単位")
        self.win.trace_tab.freq_offset._commit_text("10G")
        self.wait(lambda: self.state()["view"]["freq_offset_hz"] == 10_000_000_000, what="周波数オフセット")
        self.pump(0.3)
        # 欄は換算後の周波数、機器の条件は変わらない
        self.assertIn("10050.000 MHz", self.win.f_start.edit.text())
        self.assertEqual(self.state()["sweep"]["start_hz"], 50_000_000)
        # 欄に打った値はオフセットを引いて送る
        self.win.f_start._commit_text("10100M")
        self.wait(lambda: self.state()["sweep"]["start_hz"] == 100_000_000, what="打った値の換算")
        # 換算中であることを軸の名前・上段の注意・状態バーで示す（設計O）
        self.assertIn("換算後", self.win.plot.getPlotItem().getAxis("bottom").labelText)
        self.assertIn("換算中", self.win.cond_label.text())
        self.assertTrue(self.win.status.labels["offset"].isVisible())
        d.ext._commit_text("10")
        self.wait(lambda: self.state()["view"]["ext_db"] == 10.0, what="外部補正")
        self.wait(lambda: "外部補正 +10 dB" in self.win.status.labels["ext"].text(), what="状態バーの外部補正")
        # PNG の帯にも出る
        data = self.win.png_data()
        self.assertEqual(data["conditions"]["freq_offset_hz"], 10_000_000_000)
        self.assertEqual(data["conditions"]["ext_db"], 10.0)

    def test_level_ticks_match_ref_field_after_unit_change(self):
        """dBμV に換えると REF は 106.99 dB 上がる。目盛の数字は REF の欄と同じ 0.1 dB の形にする。"""
        self.connect_and_sweep()
        self.cmd("set_amplitude", ref_level=10.0, db_per_div=10)
        self.pump()
        d = self.win.display_tab
        d.unit.setCurrentIndex(d.unit.findData("dBuV"))
        d.unit.activated.emit(d.unit.currentIndex())
        self.wait(lambda: self.win.display_unit() == "dBμV", what="縦軸の単位")
        self.pump(0.2)
        labels = [s for _v, s in self.win.plot.getPlotItem().getAxis("left")._tickLevels[0]]
        self.assertEqual(len(labels), 11)
        self.assertEqual(self.win.f_ref.edit.text(), labels[0] + " dBμV")
        self.assertEqual(labels[:2], ["117.0", "107.0"])
        for s in labels:
            self.assertRegex(s, r"^−?\d+\.\d$")


class DockLayoutTest(UiBase):
    def test_detach_and_reattach_parts(self):
        parts = self.win.parts
        parts.detach("graph")
        self.pump(0.2)
        self.assertTrue(parts.graph.detached)
        self.assertTrue(parts.graph.placeholder.isVisible())
        # 別の窓でもキーが効くよう、窓のキーの項目が付いている
        self.assertIn(self.win.actions["run_cont"], parts.graph.window.actions())
        # 別の窓を閉じたら元の位置に戻る（なくならない）
        parts.graph.window.close()
        self.pump(0.2)
        self.assertFalse(parts.graph.detached)
        self.assertIs(self.win.plot.parentWidget(), parts.graph.holder)
        # タブは1つずつ切り離せる
        parts.detach("tab-view")
        parts.detach("markers")
        self.pump(0.2)
        self.assertTrue(parts.docks["tab-view"].isFloating())
        self.assertTrue(parts.docks["markers"].isFloating())
        self.assertFalse(parts.docks["tab-trace"].isFloating())
        # 切り離した部品を閉じても元に戻る
        parts.docks["tab-view"].close()
        self.assertFalse(parts.docks["tab-view"].isFloating())
        # 並びを残して、次の起動で戻す
        parts.detach("peaks")
        self.pump(0.2)
        saved = parts.save()
        self.assertIsNotNone(saved["peaks"])
        # 「並びを初期に戻す」
        self.win.actions["layout_reset"].trigger()
        self.pump(0.2)
        self.assertFalse(parts.docks["markers"].isFloating())
        self.assertFalse(parts.peaks.detached)
        self.assertIn(texts.LAYOUT_TEXTS["layout_reset_done"], self.win.st_note.text())
        # 残した並びを読むと、切り離した部分も戻る
        parts.restore(saved)
        self.pump(0.2)
        self.assertTrue(parts.peaks.detached)
        self.assertTrue(parts.docks["markers"].isFloating())

    def test_layout_saved_on_close(self):
        self.win.parts.detach("tab-source")
        self.pump(0.2)
        prefs = self.win.prefs
        self.win.close()
        self.assertTrue(isinstance(prefs.get("layout"), dict))
        self.assertTrue(prefs.get("layout")["docks"])


class PresetsDialogTest(UiBase):
    def test_save_load_rename_delete(self):
        self.cmd("set_sweep", start_hz=76_000_000, stop_hz=108_000_000)
        self.text_answer = "FM 放送帯"
        self.win.actions["preset_save"].trigger()
        self.wait(lambda: "保存しました" in self.win.st_note.text(), what="保存の知らせ")
        self.win.actions["presets"].trigger()
        dlg = self.win._presets
        self.wait(lambda: len(dlg.rows) == 1, what="一覧")
        self.assertIn("76 MHz", dlg.rows[0]["summary"])
        self.cmd("set_sweep", start_hz=1_000_000_000, stop_hz=2_000_000_000)
        dlg.load()
        self.wait(lambda: self.state()["sweep"]["start_hz"] == 76_000_000, what="呼び出し")
        self.wait(lambda: "呼び出しました" in self.win.st_note.text(), what="呼び出しの知らせ")
        self.win.actions["undo"].trigger()
        self.wait(lambda: self.state()["sweep"]["start_hz"] == 1_000_000_000, what="元に戻す")
        self.text_answer = "FM"
        dlg.rename()
        self.wait(lambda: dlg.rows and dlg.rows[0]["name"] == "FM", what="名前")
        dlg.delete()
        self.wait(lambda: not dlg.rows, what="消す")
        self.assertIn("FM", self.confirm_asked[-1])
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
