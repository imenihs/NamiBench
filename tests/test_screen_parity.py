"""RBW の知らせは RBW の欄に付ける（U-25。2026-10-07 ユーザー決定「tooltip とかで教えるようにしよう」）と、
Qt と Web で同じに出す中身（screen_model・texts・report_layout）の試験。画面を持たない。

- いまの RBW で信用できない条件（コアの sweep.warnings。4つ: texts.WARNINGS）は、上段の下の条件の行に出さず、
  RBW の欄の注意（screen_model.rbw_field_view）とツールチップに出す。PNG の帯（報告）には文で残す
- 基準の範囲外の帯（設計E §6.4）の区間は1か所（report_layout.norm_out_of_range）から画面と PNG へ
- 開いたファイルの見出しのツールチップ・重ねた直後に出すトレース（Qt と Web が同じ関数）
- Web の整数の欄（fields.js の int-field: Qt の単位付き QSpinBox と同じ決まり）の読み方と送り方
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import numpy as np

from pygigast.core import report_layout as rl
from pygigast.core.devices.base import load_profile
from pygigast.core.devices.gigast.driver import timing_fn
from pygigast.core.instrument import GIGAST_PROFILE_PATH
from pygigast.core.instrument_common import adopted_to_dict
from pygigast.core.storage.h5file import FileHeader, TraceData, save_snapshot
from pygigast.core.sweep.model import SweepModel
from pygigast.core.types import FreqAxis, Mode, Quantity, SweepRequest, ValueKind
from pygigast.ui_qt import screen_model, texts

MHZ = 1_000_000
KHZ = 1_000
UI_WEB = Path(__file__).resolve().parent.parent / "pygigast" / "ui_web"


def adopted_state(center, span, rbw, mode=Mode.SA, points=None) -> dict:
    """掃引条件のモデルで決めた採用値を、状態の sweep の形にする（警告・RBW の選択肢はコアと同じ）。"""
    p = load_profile(GIGAST_PROFILE_PATH)
    m = SweepModel(p, timing_fn(p))
    kw = {"points": points} if points else {}
    a = m.apply(SweepRequest(center_hz=center, span_hz=span, edited=("center_hz", "span_hz"), mode=mode,
                             rbw_preference_hz=rbw, **kw), m.default())
    return {"sweep": adopted_to_dict(a)}


def write_file(path, hidden=(2,)):
    """2本のトレースを持つ保存ファイル。hidden の番号は「保存したときに表示していなかった」トレース。"""
    axis = FreqAxis(400 * MHZ, 400_000, 501)
    tds = [TraceData(number=n, values=np.full(501, 1e-9 * n), quantity=Quantity.POWER, value_kind=ValueKind.REAL,
                     axis=axis, info={"action": "clear_write", "visible": n not in hidden})
           for n in (1, 2)]
    save_snapshot(path, FileHeader("gigast-v5", "TEST0001", note="直結"), None, tds)
    return path



class RbwFieldViewTest(unittest.TestCase):
    def test_every_rbw_warning_goes_to_the_field_not_under_the_top_bar(self):
        """コアが出す RBW の知らせ 4 つ（texts.WARNINGS の全部）を、それぞれの条件で作って確かめる。"""
        cases = {
            "rbw180k_narrow_span": adopted_state(500 * MHZ, 200 * KHZ, 180 * KHZ),
            "rbw30k_fine_step": adopted_state(500 * MHZ, 1 * MHZ, 30 * KHZ, points=1001),
            "rbw15k_unstable": adopted_state(500 * MHZ, 10 * MHZ, 15 * KHZ),
            "rbw15k_zero_span": adopted_state(100 * MHZ, 0, 15 * KHZ),
        }
        self.assertEqual(set(cases), set(texts.WARNINGS))  # 4 / 4
        for code, st in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, st["sweep"]["warnings"])
                sentence = texts.WARNINGS[code]
                v = screen_model.rbw_field_view(st)
                self.assertTrue(v["warn"])
                self.assertIn(sentence, v["lines"])
                self.assertTrue(v["tip"].startswith(sentence), v["tip"])  # 知らせが先
                # 上段の下の行には出さない。PNG の帯（報告）には文で残す
                self.assertNotIn(sentence, texts.condition_lines(st))
                self.assertNotIn(sentence, screen_model.ui_view(st)["cond"])
                self.assertIn(sentence, screen_model.png_warnings(st))
                self.assertEqual(screen_model.ui_view(st)["rbw_field"], v)

    def test_no_warning_keeps_the_field_plain(self):
        st = adopted_state(500 * MHZ, 10 * MHZ, 180 * KHZ)
        self.assertEqual(st["sweep"]["warnings"], [])
        v = screen_model.rbw_field_view(st)
        self.assertFalse(v["warn"])
        self.assertEqual((v["lines"], v["tip"]), ([], ""))

    def test_tip_keeps_the_unusable_reason_after_the_warning(self):
        # TG では RBW は 180kHz だけ（設計C §5.14）。狭い Span の知らせと、選べない理由の両方が欄のツールチップに
        st = adopted_state(500 * MHZ, 200 * KHZ, 30 * KHZ, mode=Mode.TG)
        self.assertEqual(st["sweep"]["rbw_hz"], 180 * KHZ)
        v = screen_model.rbw_field_view(st)
        lines = v["tip"].split("\n")
        self.assertEqual(lines[0], texts.WARNINGS["rbw180k_narrow_span"])
        self.assertEqual(lines[1], screen_model.rbw_limit_text(st))
        # 知らせが無ければ理由だけ（今までのツールチップと同じ）
        plain = adopted_state(500 * MHZ, 10 * MHZ, 30 * KHZ, mode=Mode.TG)
        self.assertEqual(screen_model.rbw_field_view(plain)["tip"], screen_model.rbw_limit_text(plain))
        self.assertFalse(screen_model.rbw_field_view(plain)["warn"])

    def test_other_condition_lines_stay_and_png_keeps_the_order(self):
        st = adopted_state(500 * MHZ, 10 * MHZ, 15 * KHZ)
        st["view"] = {"freq_offset_hz": 10_000 * MHZ}
        st["sweep"]["tail_gap"] = True
        off = texts.VIEW_TEXTS["freq_offset_on"].replace("{off}", texts.offset_text(10_000 * MHZ))
        self.assertEqual(texts.condition_lines(st), [off, texts.TEMPLATES["cond_tail_gap"]])
        # PNG の帯: 換算中 → RBW の知らせ → 末尾の欠け（前と同じ並び）
        self.assertEqual(texts.condition_lines(st, with_rbw=True),
                         [off, texts.WARNINGS["rbw15k_unstable"], texts.TEMPLATES["cond_tail_gap"]])
        self.assertEqual(screen_model.png_warnings(st)[:3], texts.condition_lines(st, with_rbw=True))


class NormBandsTest(unittest.TestCase):
    """基準の範囲外の帯（設計E §6.4）: 画面（Qt・Web）と PNG（Qt・Web）が同じ区間を描く。"""

    RANGES = [[490 * MHZ, 494_960_000], [505_040_000, 510 * MHZ]]

    def state(self, used=True, step=40_000):
        return {"sweep": {"mode": "tg", "start_hz": 490 * MHZ, "stop_hz": 510 * MHZ, "step_hz": step},
                "normalization": {"used": used, "applied": True, "out_of_range_hz": self.RANGES,
                                  "selected": {"name": "TG", "taken_at": 0}}}

    def test_ranges_only_while_the_reference_is_used(self):
        self.assertEqual(rl.norm_out_of_range(self.state()), self.RANGES)
        self.assertEqual(rl.norm_out_of_range(self.state(used=False)), [])
        self.assertEqual(rl.norm_out_of_range(self.state(step=0)), [])  # ゼロスパン（横軸が時間）
        self.assertEqual(rl.norm_out_of_range({}), [])
        self.assertEqual(screen_model.ui_view(self.state())["norm_bands"], self.RANGES)

    def test_png_layout_carries_the_bands(self):
        st = self.state()
        data = rl.build_data(st, ref_level=0.0, db_per_div=10.0, unit="dB", mode_name="TG", traces=[], markers=[],
                             warnings=[])
        self.assertEqual(data["normalization"]["out_of_range_hz"], self.RANGES)
        lay = rl.layout(data, (1600, 1000))
        self.assertEqual(lay["norm_bands_hz"], self.RANGES)
        self.assertEqual(lay["meta"]["normalization"]["out_of_range_hz"], self.RANGES)  # tEXt にも残る
        none = rl.build_data(self.state(used=False), ref_level=0.0, db_per_div=10.0, unit="dB", mode_name="TG",
                             traces=[], markers=[], warnings=[])
        self.assertEqual(rl.layout(none, (1600, 1000))["norm_bands_hz"], [])


class FileTextsTest(unittest.TestCase):
    def test_heading_tip(self):
        f = {"path": "data/a.h5", "name": "a.h5", "created_at": "2026-10-07T10:00:00+09:00",
             "device_model": "gigast-v5", "device_serial": "SIM00001", "note": "直結"}
        self.assertEqual(texts.file_heading_tip(f),
                         "data/a.h5\n2026-10-07T10:00:00+09:00  gigast-v5 SIM00001\n直結")
        # 無い行は出さない（空の行を作らない）
        self.assertEqual(texts.file_heading_tip({"path": "b.h5", "name": "b.h5"}), "b.h5")

    def test_shown_at_open(self):
        self.assertTrue(screen_model.file_trace_shown_at_open({"info": {"visible": True}}))
        self.assertFalse(screen_model.file_trace_shown_at_open({"info": {"visible": False}}))
        self.assertTrue(screen_model.file_trace_shown_at_open({"info": {}}))  # 持たないファイルは出す
        self.assertTrue(screen_model.file_trace_shown_at_open({}))


@unittest.skipUnless(shutil.which("node"), "Node が無い")
class IntFieldRulesTest(unittest.TestCase):
    """Web の整数の欄（int-field）: 単位は欄の中。数でない・範囲の外は送らない。↑↓ は 1 ずつで端で止める。"""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.document = { activeElement: null };
require(process.argv[1]);
const F = GSFields;
const out = {
  plain: F.parseIntIn("12", " 回", 1, 64),
  suffix: F.parseIntIn("12 回", " 回", 1, 64),
  nospace: F.parseIntIn("12回", " 回", 1, 64),
  wide: F.parseIntIn("１２ 回", " 回", 1, 64),
  low: F.parseIntIn("0", " 回", 1, 64),
  lowEdge: F.parseIntIn("1", " 回", 1, 64),
  high: F.parseIntIn("65", " 回", 1, 64),
  highEdge: F.parseIntIn("64", " 回", 1, 64),
  word: F.parseIntIn("abc", " 回", 1, 64),
  dec: F.parseIntIn("2.5", " 回", 1, 64),
  empty: F.parseIntIn("", " 回", 1, 64),
  up: F.stepInt(3, 1, 1, 8),
  upEdge: F.stepInt(8, 1, 1, 8),
  down: F.stepInt(3, -1, 1, 8),
  downEdge: F.stepInt(1, -1, 1, 8),
};
console.log(JSON.stringify(out));
"""

    def test_rules(self):
        out = subprocess.run(["node", "-e", self.SCRIPT, str(UI_WEB / "fields.js")], capture_output=True, text=True,
                             timeout=30, check=True, encoding="utf-8").stdout
        got = json.loads(out)
        self.assertEqual((got["plain"], got["suffix"], got["nospace"], got["wide"]), (12, 12, 12, 12))
        # 範囲の境界（N-1 / N / N+1）
        self.assertEqual((got["low"], got["lowEdge"], got["highEdge"], got["high"]), (None, 1, 64, None))
        self.assertEqual((got["word"], got["dec"], got["empty"]), (None, None, None))
        self.assertEqual((got["up"], got["upEdge"], got["down"], got["downEdge"]), (4, 8, 2, 1))


if __name__ == "__main__":
    unittest.main()
