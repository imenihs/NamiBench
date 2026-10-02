"""設定を黙って変えない（設計J §4.10 の 2〜4）のコアと画面の中身（Qt・Web で共有する screen_model・texts）の試験。

- 単位の設定が変わったら、どこから変わっても（命令・プリセット・元に戻す）知らせ level_unit_changed を一度出す。
  同じ単位では出さない。起動で戻すときは出さない
- いま出している量（display_quantity）: 掃引が無ければ None、電力 → 基準との比に変わったら知らせ ratio_display
  （基準の名前と覚えている電力の単位）。比 → 比では出さない。起動して最初の掃引が比でも出さない
- 単位の欄の中身（screen_model.unit_view）と知らせの出し方（texts.notice_view の field・modal）
"""
import time
import unittest

from pygigast.ui_qt import screen_model, texts
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000


def unit_codes(events):
    return [p["detail"]["unit"] for k, p in events if k == "notice" and p["code"] == "level_unit_changed"]


class LevelUnitNoticeTest(InstrumentCase):
    def test_every_source_notifies_once(self):
        self.connect()
        self.clear_events()
        self.call("set_view", level_unit="dBuV")
        self.call("set_view", level_unit="dBuV")  # 同じ単位: 出さない
        self.call("set_view", ext_db=3.0)  # 単位以外: 出さない
        self.assertEqual(unit_codes(self.events), ["dBuV"])
        # プリセット（呼び出しで単位が変わる）
        pid = self.call("preset_save", name="p")["id"]
        self.call("set_view", level_unit="dBmV")
        self.clear_events()
        self.call("preset_load", id=pid)
        self.assertEqual(unit_codes(self.events), ["dBuV"])
        # 元に戻す（Ctrl+Z）・やり直す
        self.clear_events()
        self.call("undo")
        self.call("redo")
        self.assertEqual(unit_codes(self.events), ["dBmV", "dBuV"])
        # 「単位と外部補正を初期値に戻す」
        self.clear_events()
        self.call("set_view", level_unit=None, ext_db=None)
        self.assertEqual(unit_codes(self.events), ["dBm"])

    def test_not_at_startup_restore(self):
        self.call("set_view", level_unit="dBmV")
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")
        self.assertEqual(st["view"]["level_unit"], "dBmV")
        self.assertEqual(unit_codes(self.events), [])


class RatioDisplayTest(InstrumentCase):
    def single(self):
        # 単発の終わりを掃引の回数で待つ（基準を取り終えた後の状態の知らせを、終わりと取り違えない）
        n0 = self.call("get_state")["run"]["sweep_count"]
        self.clear_events()
        self.call("run", mode="single")
        return self.wait_for(lambda k, p: k == "state" and p["run"]["state"] == "stopped"
                             and p["run"]["sweep_count"] > n0)

    def take_reference(self, name="直結 A"):
        self.call("set_mode", mode="tg")
        r = self.call("reference_take", kind="wide", name=name)
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")

    def ratio_notices(self):
        return [p["detail"] for k, p in self.events if k == "notice" and p["code"] == "ratio_display"]

    def test_power_to_ratio_notifies_once(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.assertIsNone(self.call("get_state")["display_quantity"])
        self.call("set_view", level_unit="dBuV")
        self.single()
        self.assertEqual(self.call("get_state")["display_quantity"], "power")
        self.take_reference()
        self.clear_events()
        self.single()
        self.assertEqual(self.ratio_notices(), [{"name": "直結 A", "unit": "dBuV"}])
        self.assertEqual(self.call("get_state")["display_quantity"], "ratio")
        # 比 → 比（続けて掃引）では出さない
        self.clear_events()
        self.single()
        self.assertEqual(self.ratio_notices(), [])
        # SA に戻ると電力。もう一度 TG（基準を選んだまま）に入ると出す
        self.call("set_mode", mode="sa")
        self.single()
        self.assertEqual(self.call("get_state")["display_quantity"], "power")
        self.call("set_mode", mode="tg")
        self.clear_events()
        self.single()
        self.assertEqual(len(self.ratio_notices()), 1)

    def test_not_at_startup(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.take_reference()
        self.single()
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.connect(100 * MHZ, 200 * MHZ)
        end = time.monotonic() + 10
        while not (self.call("get_state")["normalization"] or {}).get("selected"):
            self.assertLess(time.monotonic(), end, "前回の基準が戻らない")
            time.sleep(0.05)
        self.single()
        self.assertEqual(self.call("get_state")["display_quantity"], "ratio")
        self.assertEqual(self.ratio_notices(), [])


class UnitViewTest(unittest.TestCase):
    """単位の欄の中身（Qt の tab_display と Web の index.html が同じものを出す）。"""

    UNITS = {"dBm": "dBm", "dBuV": "dBμV", "dBmV": "dBmV"}

    def st(self, quantity, unit="dBuV"):
        return {"view": {"level_unit": unit, "level_units": self.UNITS}, "display_quantity": quantity}

    def test_power(self):
        for q in (None, "power"):
            u = screen_model.unit_view(self.st(q))
            self.assertEqual((u["ratio"], u["current"], u["label"]), (False, "dBuV", "dBμV"))
            self.assertEqual([(c["code"], c["enabled"]) for c in u["choices"]],
                             [("dBm", True), ("dBuV", True), ("dBmV", True), ("ratio", False)])
            self.assertEqual(u["choices"][-1]["tip"], texts.VIEW_TEXTS["level_unit_ratio_item_tip"])
            self.assertEqual(u["tip"], texts.VIEW_TEXTS["level_unit_tip"])

    def test_ratio_shows_ratio_and_disables_power_units(self):
        u = screen_model.unit_view(self.st("ratio"))
        self.assertEqual((u["ratio"], u["current"], u["unit"]), (True, "ratio", "dBuV"))
        reason = texts.VIEW_TEXTS["level_unit_ratio_reason"].replace("{unit}", "dBμV")
        self.assertIn("dBμV", reason)
        self.assertEqual([(c["code"], c["enabled"], c["tip"]) for c in u["choices"][:3]],
                         [(c, False, reason) for c in self.UNITS])
        self.assertEqual(u["choices"][-1]["text"], texts.VIEW_TEXTS["level_unit_ratio_item"])
        self.assertIn(reason, u["tip"])
        # Web へは状態の ui.level で届く
        self.assertEqual(screen_model.ui_view(self.st("ratio"))["level"], u)

    def test_notice_views(self):
        v = texts.notice_view({"code": "level_unit_changed", "detail": {"unit": "dBmV", "previous": "dBm"}})
        self.assertEqual((v["text"], v["field"], v["modal"]), ("単位を dBmV にしました", "unit", None))
        v = texts.notice_view({"code": "ratio_display", "detail": {"name": "直結 12:24", "unit": "dBuV"}})
        self.assertIsNone(v["field"])
        m = v["modal"]
        self.assertEqual((m["key"], m["title"], m["check"]),
                         ("ratio_display", texts.VIEW_TEXTS["ratio_title"], texts.VIEW_TEXTS["dont_show_again"]))
        self.assertIn("基準「直結 12:24」との比（dB）", m["text"])
        self.assertEqual(m["text"].count("dBμV"), 2)  # 電力で見る単位・SA・SG に戻したときの単位
        self.assertIn("直結 12:24", v["text"])
        # ほかの知らせは欄にも窓にも出さない
        v = texts.notice_view({"code": "sweep_discarded", "detail": {}})
        self.assertEqual((v["field"], v["modal"]), (None, None))


if __name__ == "__main__":
    unittest.main()
