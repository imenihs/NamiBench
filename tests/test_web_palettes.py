"""Web の画面: 設定の「配色」の上の「保存した配色」（独自の配色の保存と読み込み: 設計J §4.4.1）。

Qt の画面（tests/test_ui_qt_settings.py の test_saved_palettes）と同じ並び・同じ語・同じ動きであることを、ヘッドレスの
Edge で開いた Web の画面で確かめる（道具は tests/test_web_layout.py の EdgePage・App・open_app）。ファイルを選ぶ窓と
ダウンロードはブラウザの窓なので、選んだファイル（chooseFiles）とダウンロード（downloadText）だけを差し替える。
"""
from __future__ import annotations

import json
import unittest

from pygigast.ui_qt import texts
from tests.test_web_layout import App, EdgePage, edge_available, open_app

S = texts.SETTINGS_TEXTS


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebSavedPalettesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App(lan=False)
        try:
            cls.page = EdgePage()
        except Exception:
            cls.app.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.app.close()

    def ev(self, js):
        return self.page.ev(js)

    def buttons(self):
        return self.ev("[...document.querySelectorAll('.pal-saved button')].map((b) => [b.textContent.trim(), b.disabled])")

    def answer_modal(self, value):
        self.page.wait("!!gsApp.modal", "名前の窓")
        self.ev(f"gsApp.modal.fields[0].value = {json.dumps(value)}; gsApp.modalOk(); 1")

    def test_saved_palettes(self):
        a = self.app
        for p in a.call("palette_list")["palettes"]:
            a.call("palette_delete", id=p["id"])
        a.call("palette_reset", base="dark")
        open_app(self.page, a, 1280, 720)
        self.ev("gsApp.openSettings('display'); 1")
        self.page.wait("gsApp.palStore.ready", "一覧")
        self.page.settle()
        # 並びと語は Qt と同じ（選んだ配色への操作の行・新しく作る行）。選んでいなければ操作の行は使えない
        self.assertEqual(self.ev("document.querySelector('.pal-saved legend').textContent.trim()"), S["palettes_group"])
        self.assertEqual(self.ev("document.querySelector('.pal-saved .dim').textContent.trim()"), S["palettes_none"])
        want = [S[k] for k in ("palette_load", "palette_rename", "palette_delete", "palette_overwrite", "palette_export",
                               "palette_save_new", "palette_import")]
        self.assertEqual([b[0] for b in self.buttons()], want)
        self.assertEqual([b[1] for b in self.buttons()], [True] * 5 + [False] * 2)
        # いまの配色に名前を付けて保存
        a.call("palette_set", colors={"trace1": "#123456"})
        self.ev("gsApp.palSaveNew(); 1")
        self.answer_modal("夜の観測")
        self.page.wait("gsApp.palStore.rows.length === 1 && !!gsApp.palSel()", "保存した行")
        self.assertIn("配色「夜の観測」を保存しました", self.ev("gsApp.status"))
        self.assertEqual([b[1] for b in self.buttons()], [False] * 7)
        # 変えてから読み込む: 保存していない変更の数を言ってから聞く（Qt と同じ文）。断れば変えない
        a.call("palette_set", colors={"trace2": "#654321"})
        self.page.wait("gsApp.paletteView.unsaved_n === 1", "保存していない変更")
        self.ev("gsApp.palLoad(); 1")
        self.page.wait("!!gsApp.confirmBox", "読み込みの確かめ")
        self.assertEqual(self.ev("gsApp.confirmBox.text"), S["palette_load_confirm"].replace("{n}", "1").replace(
            "{name}", "夜の観測"))
        self.assertEqual(self.ev("gsApp.confirmBox.ok"), S["palette_load"])
        self.ev("gsApp.answerConfirm(false); 1")
        self.page.settle()
        self.assertEqual(a.call("get_state")["palette"]["colors"]["trace2"], "#654321")
        self.ev("gsApp.palLoad(); 1")
        self.page.wait("!!gsApp.confirmBox", "読み込みの確かめ")
        self.ev("gsApp.answerConfirm(true); 1")
        self.page.wait("gsApp.paletteView.unsaved_n === 0", "読み込み")
        pal = a.call("get_state")["palette"]
        self.assertEqual((pal["colors"]["trace1"], pal["colors"]["trace2"]), ("#123456", pal["defaults"]["dark"]["trace2"]))
        # 書き出し（ブラウザの保存先へ）: 中身はサーバーの書き出しの形・ファイル名は配色の名前
        self.ev("window.__dl = null; gsApp.downloadText = (n, t) => { window.__dl = [n, t]; }; gsApp.palExport(); 1")
        self.page.wait("!!window.__dl", "書き出し")
        name, text = self.ev("window.__dl")
        self.assertEqual(name, "夜の観測.json")
        d = json.loads(text)
        self.assertEqual(d["content"]["colors"]["trace1"], "#123456")
        # 取り込み（読みにくい組はその行と知らせにその場で出す）。同じ名前は「(2)」
        d["content"]["colors"]["text"] = d["content"]["colors"]["graph_bg"]
        self.ev("gsApp.chooseFiles = async () => [new File([" + json.dumps(json.dumps(d)) + "], 'in.json')];"
                " gsApp.palImport(); 1")
        self.page.wait("gsApp.palStore.rows.length === 2", "取り込み")
        self.page.settle()
        self.assertIn("配色「夜の観測 (2)」を取り込みました（⚠ 読みにくい組", self.ev("gsApp.status"))
        self.assertEqual(self.ev("gsApp.palSel().name"), "夜の観測 (2)")
        warn = self.ev("[...document.querySelectorAll('.pal-list-scroll tr')].map((tr) => tr.children[1].textContent.trim())")
        self.assertEqual([bool(w) for w in warn], [False, True])
        self.assertTrue(warn[1].startswith("⚠ 読みにくい組"))
        # 読めないファイルは欄の下に理由
        self.ev("gsApp.chooseFiles = async () => [new File(['{'], 'bad.json')]; gsApp.palImport(); 1")
        self.page.wait("!!gsApp.notes.palettes", "読めないファイル")
        self.assertEqual(self.ev("gsApp.notes.palettes.text"), texts.ERRORS["palette_file_unreadable"])
        # 名前・上書き・消す
        self.ev("gsApp.palRename(); 1")
        self.answer_modal("取り込んだ配色")
        self.page.wait("gsApp.palStore.rows.some((r) => r.name === '取り込んだ配色')", "名前")
        self.ev("gsApp.palOverwrite(); 1")
        self.page.wait("!!gsApp.confirmBox", "上書きの確かめ")
        self.assertEqual(self.ev("gsApp.confirmBox.text"), S["palette_overwrite_confirm"].replace("{name}", "取り込んだ配色"))
        self.ev("gsApp.answerConfirm(true); 1")
        self.page.wait("gsApp.palStore.rows.every((r) => !r.warn)", "上書き")
        self.ev("gsApp.palDelete(); 1")
        self.page.wait("!!gsApp.confirmBox", "消すの確かめ")
        self.ev("gsApp.answerConfirm(true); 1")
        self.page.wait("gsApp.palStore.rows.length === 1", "消す")
        # ほかの画面（Qt）で保存したら、開いている一覧が読み直される（いまの配色を始めた配色が変わる）
        a.call("palette_save", name="ほかの画面で保存")
        self.page.wait("gsApp.palStore.rows.some((r) => r.name === 'ほかの画面で保存')", "読み直し")
