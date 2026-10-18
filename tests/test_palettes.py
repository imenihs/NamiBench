"""独自の配色の保存と読み込み（設計J §4.4.1）のコアの試験。

- 一覧（PaletteStore: プリセットと同じ仕組み）: 保存・名前・消す・壊れたファイル・同じ名前は作らない
- 本体の命令: 保存（いまの配色をその配色から始めた扱いにする）・読み込み（いまの配色を置き換える・元に戻す）・
  未保存の変更の数（読み込む前の確認に使う）・名前の変更と削除で始めた配色を追う・書き出しと取り込み
  （中身の文字でやりとりし、パスを受け取らない）・取り込んだ配色の読みやすさの知らせ・起動で戻る
"""
import json
import tempfile
import unittest
from pathlib import Path

from namibench.core.palette import BASES, PALETTE_FORMAT, Palette, PaletteStore
from namibench.core.presets import PresetError
from namibench.core.instrument_common import CommandError
from tests.instrument_helpers import InstrumentCase


class PaletteStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PaletteStore(Path(self.tmp.name) / "palettes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_and_errors_use_palette_codes(self):
        p = Palette("light", {"trace1": "#123456"})
        a = self.store.save("夜の観測", p.content())
        rows, broken = self.store.list(with_content=True)
        self.assertEqual(([r["name"] for r in rows], broken), (["夜の観測"], 0))
        self.assertEqual(PaletteStore.palette_of(self.store.get(a["id"])).colors["trace1"], "#123456")
        with self.assertRaises(PresetError) as cm:
            self.store.save("夜の観測", p.content())
        self.assertEqual(cm.exception.code, "palette_name_taken")
        with self.assertRaises(PresetError) as cm:
            self.store.save(" ", p.content())
        self.assertEqual(cm.exception.code, "palette_name_empty")
        with self.assertRaises(PresetError) as cm:
            self.store.get("nothere")
        self.assertEqual(cm.exception.code, "unknown_palette")
        # 同じ名前の取り込みは「名前 (2)」
        self.assertEqual(self.store.free_name("夜の観測"), "夜の観測 (2)")
        self.store.save("夜の観測 (2)", p.content())
        self.assertEqual(self.store.free_name("夜の観測"), "夜の観測 (3)")

    def test_preset_files_are_not_palettes(self):
        # 別の形のファイル（プリセット）は配色として読まない。読めないファイルは数えて残す
        self.store.folder.mkdir(parents=True)
        bad = self.store.folder / "abc123.json"
        bad.write_text(json.dumps({"format": "pygigast-preset", "version": 1, "name": "x", "content": {}}),
                       encoding="utf-8")
        self.assertEqual(self.store.list(), ([], 1))
        self.assertTrue(bad.exists())

    def test_file_name(self):
        self.assertEqual(PaletteStore.file_name('a/b:c*?"<>|'), "a_b_c______.json")
        self.assertEqual(PaletteStore.file_name(" . "), "palette.json")


class PaletteOriginTest(unittest.TestCase):
    def test_unsaved_counts_from_origin(self):
        p = Palette("dark")
        p.colors["trace1"] = "#010101"
        self.assertEqual(p.unsaved(), ["trace1"])  # 始めた配色が無ければ初期値から変えた色
        p.set_origin("abc", "夜")
        self.assertEqual(p.unsaved(), [])
        p.colors["trace2"] = "#020202"
        self.assertEqual(p.unsaved(), ["trace2"])
        self.assertEqual(p.changed(), ["trace1", "trace2"])
        # 設定に書いた形から戻る（始めた配色も）
        q = Palette.from_saved(p.to_saved())
        self.assertEqual((q.origin["id"], q.origin["name"], q.unsaved()), ("abc", "夜", ["trace2"]))
        self.assertEqual(q.state()["origin"], {"id": "abc", "name": "夜"})
        self.assertNotIn("origin", p.content())


class PaletteCommandsTest(InstrumentCase):
    def state(self):
        return self.call("get_state")["palette"]

    def test_save_load_unsaved_and_undo(self):
        self.call("palette_set", colors={"trace1": "#112233"})
        self.assertEqual(self.state()["unsaved"], ["trace1"])
        a = self.call("palette_save", name="A")
        st = self.state()
        self.assertEqual((st["origin"], st["unsaved"]), ({"id": a["id"], "name": "A"}, []))
        # 変えると保存していない変更になる
        self.call("palette_set", colors={"trace2": "#445566", "trace3": "#778899"})
        self.assertEqual(self.state()["unsaved"], ["trace2", "trace3"])
        self.call("palette_reset", base="light")
        st = self.state()
        self.assertIsNone(st["origin"])
        self.assertEqual(st["base"], "light")
        # 読み込むと A の色に置き換わり、元に戻すで戻る
        r = self.call("palette_load", id=a["id"])
        self.assertEqual(r["name"], "A")
        st = self.state()
        self.assertEqual((st["base"], st["colors"]["trace1"], st["unsaved"]), ("dark", "#112233", []))
        self.assertEqual(st["colors"]["trace2"], BASES["dark"]["trace2"])
        self.call("undo")
        st = self.state()
        self.assertEqual((st["base"], st["origin"]), ("light", None))
        self.call("redo")
        self.assertEqual(self.state()["origin"]["id"], a["id"])

    def test_list_rename_delete_follow_origin(self):
        a = self.call("palette_save", name="A")
        self.call("palette_set", colors={"text": "#202020"})  # 読みにくい（コントラスト不足）
        b = self.call("palette_save", name="B")
        r = self.call("palette_list")
        self.assertEqual([x["name"] for x in r["palettes"]], ["A", "B"])
        self.assertEqual(r["origin_id"], b["id"])
        warn = {x["name"]: x["warnings"] for x in r["palettes"]}
        self.assertEqual(warn["A"], [])
        self.assertTrue(any(w["code"] == "low_contrast" and w["fg"] == "text" for w in warn["B"]))
        with self.assertRaises(CommandError) as cm:
            self.call("palette_rename", id=a["id"], name="B")
        self.assertEqual((cm.exception.code, cm.exception.field), ("palette_name_taken", "name"))
        self.call("palette_rename", id=b["id"], name="B2")
        self.assertEqual(self.state()["origin"]["name"], "B2")
        # 上書き: いまの配色で置き換え、始めた配色はそのまま
        self.call("palette_set", colors={"text": "#f0f0f0"})
        self.call("palette_save", name="B2", overwrite_id=b["id"])
        self.assertEqual(self.state()["unsaved"], [])
        # 始めた配色を消すと、初期値から変えた色が保存していない変更になる
        self.call("palette_delete", id=b["id"])
        st = self.state()
        self.assertIsNone(st["origin"])
        self.assertEqual(st["unsaved"], ["text"])
        with self.assertRaises(CommandError) as cm:
            self.call("palette_load", id=b["id"])
        self.assertEqual(cm.exception.code, "unknown_palette")

    def test_export_and_import(self):
        self.call("palette_set", colors={"trace1": "#123456"})
        a = self.call("palette_save", name="夜/観測")
        out = self.call("palette_export", id=a["id"])
        self.assertEqual(out["file_name"], "夜_観測.json")
        d = json.loads(out["text"])
        self.assertEqual((d["format"], d["name"], d["content"]["colors"]["trace1"]), (PALETTE_FORMAT, "夜/観測", "#123456"))
        # 取り込み: 同じ名前があるので「(2)」。いまの配色は変えない
        before = self.state()
        r = self.call("palette_import", text=out["text"], file_name="x.json")
        self.assertEqual((r["name"], r["warnings"]), ("夜/観測 (2)", []))
        self.assertEqual(self.state()["colors"], before["colors"])
        self.assertEqual(len(self.call("palette_list")["palettes"]), 2)
        # 読みにくい配色は取り込んだときに知らせる
        d["content"]["colors"]["text_dim"] = d["content"]["colors"]["panel_bg"]
        d["name"] = ""
        r = self.call("palette_import", text=json.dumps(d), file_name="渡された配色.json")
        self.assertEqual(r["name"], "渡された配色")  # 名前が無ければファイル名
        self.assertTrue(any(w["code"] == "low_contrast" for w in r["warnings"]))
        # 配色のファイルでないものは取り込まない
        for bad in ("{ broken", json.dumps({"format": "pygigast-preset", "version": 1, "name": "x", "content": {}}),
                    json.dumps({"format": PALETTE_FORMAT, "version": 99, "name": "x", "content": {"colors": {}}}),
                    "x" * 300_000):
            with self.assertRaises(CommandError) as cm:
                self.call("palette_import", text=bad)
            self.assertEqual(cm.exception.code, "palette_file_unreadable")
        self.assertEqual(len(self.call("palette_list")["palettes"]), 3)

    def test_origin_restored_at_startup(self):
        self.call("palette_set", colors={"trace1": "#112233"})
        a = self.call("palette_save", name="A")
        self.call("palette_set", colors={"trace2": "#445566"})
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.state()
        self.assertEqual((st["origin"]["id"], st["unsaved"]), (a["id"], ["trace2"]))
