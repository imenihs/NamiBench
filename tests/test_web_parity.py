"""Web の画面: RBW の知らせは RBW の欄に（U-25。Qt と同じ screen_model.rbw_field_view）と、Qt と同じにそろえた所
（単位付きの整数の欄・基準の範囲外の帯（画面と PNG）・開いたファイルの見出しと重ねた直後のトレース）。
Qt の側は tests/test_ui_qt_parity.py。道具は tests/test_web_layout.py の EdgePage・App・open_app。
"""
from __future__ import annotations

import json
import re
import time
import unittest
from pathlib import Path

from pygigast.ui_qt import texts
from tests.test_screen_parity import write_file
from tests.test_web_layout import App, EdgePage, edge_available, open_app

MHZ = 1_000_000
UI_WEB = Path(__file__).resolve().parent.parent / "pygigast" / "ui_web"
RBW = "document.querySelector('[data-tier=L] .field-select')"


@unittest.skipUnless(edge_available(), "Microsoft Edge が無い")
class WebRbwAndParityTest(unittest.TestCase):
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

    def setUp(self):
        self.app.call("set_mode", mode="sa")
        self.app.call("reference_select", id=None)
        self.app.call("set_sweep", start_hz=495 * MHZ, stop_hz=505 * MHZ, rbw_hz=180_000)
        for f in self.app.call("get_state").get("files") or []:
            self.app.call("close_file", file_id=f["file_id"])

    def wait_idle(self, what):
        end = time.monotonic() + 30
        while self.app.call("get_state").get("tasks"):
            self.assertLess(time.monotonic(), end, what)
            time.sleep(0.05)

    def single(self):
        n0 = self.app.call("get_state")["run"]["sweep_count"]
        self.app.call("run", mode="single")
        end = time.monotonic() + 30
        while True:
            st = self.app.call("get_state")
            if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
                return st
            self.assertLess(time.monotonic(), end, "掃引")
            time.sleep(0.05)

    # ---------------------------------------------------------------- RBW の欄の注意（U-25）

    def rbw_box(self):
        return self.page.ev(f"(() => {{ const r = {RBW}.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }})()")

    def test_rbw_warning_marks_the_field_not_the_line_under_the_top_bar(self):
        open_app(self.page, self.app, 1280, 720)
        self.assertFalse(self.page.ev(f"{RBW}.classList.contains('warn')"))
        before = self.rbw_box()
        self.app.call("set_sweep", rbw_hz=15_000)
        self.page.wait(f"{RBW}.classList.contains('warn')", "RBW の欄が注意の色")
        self.page.settle()
        sentence = texts.WARNINGS["rbw15k_unstable"]
        title = self.page.ev(f"{RBW}.title")
        self.assertTrue(title.startswith(sentence), title)
        # 文字と枠が注意の色（配色の warn）。大きさと場所は変わらない
        warn = self.page.ev("getComputedStyle(document.documentElement).getPropertyValue('--warn').trim()")
        color, border = self.page.ev(f"(() => {{ const cs = getComputedStyle({RBW}); return [cs.color, cs.borderTopColor]; }})()")
        rgb = "rgb({}, {}, {})".format(*(int(warn[i:i + 2], 16) for i in (1, 3, 5)))
        self.assertEqual((color, border), (rgb, rgb))
        self.assertEqual(self.rbw_box(), before)
        # 上段の下の条件の行には出さない
        self.assertNotIn(sentence, self.page.ev("(document.querySelector('.cond') || {}).textContent || ''"))
        self.assertNotIn(sentence, self.page.ev("gsApp.condLines.join('\\n')"))
        # 180kHz に戻すと注意は消える
        self.app.call("set_sweep", rbw_hz=180_000)
        self.page.wait(f"!{RBW}.classList.contains('warn')", "注意が消える")
        self.assertNotIn(sentence, self.page.ev(f"{RBW}.title"))
        self.assertEqual(self.rbw_box(), before)

    def test_zero_span_rbw15k(self):
        open_app(self.page, self.app, 1280, 720)
        self.app.call("set_sweep", center_hz=100 * MHZ, span_hz=0, rbw_hz=15_000)
        try:
            self.page.wait(f"{RBW}.classList.contains('warn')", "ゼロスパンの RBW 15kHz")
            self.assertTrue(self.page.ev(f"{RBW}.title").startswith(texts.WARNINGS["rbw15k_zero_span"]))
        finally:
            self.app.call("set_sweep", start_hz=495 * MHZ, stop_hz=505 * MHZ, rbw_hz=180_000)

    # ---------------------------------------------------------------- 単位付きの整数の欄（Qt の QSpinBox の suffix）

    def field(self, name):
        return f"document.querySelector('[data-field={name}]')"

    def type_in(self, name, text, key="Enter"):
        el = self.field(name)
        self.page.ev(f"(() => {{ const el = {el}; el.focus(); el.value = {json.dumps(text)};"
                     f" el.dispatchEvent(new Event('input')); el.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}'}}));"
                     " return 1; })()")
        self.page.settle()

    def test_count_fields_carry_the_unit_inside(self):
        open_app(self.page, self.app, 1280, 720)
        L = texts.LABELS
        want = {"top_n": "3" + L["count_suffix"], "step_per_step": "1" + L["times_suffix"],
                "ref_avg": "1" + L["times_suffix"]}
        for tab, name in (("marker", "top_n"), ("source", "step_per_step"), ("source", "ref_avg")):
            self.page.ev(f"gsApp.tab = '{tab}'; 1")
            self.page.settle()
            with self.subTest(name=name):
                self.assertEqual(self.page.ev(f"{self.field(name)}.value"), want[name])
                self.assertTrue(self.page.ev(f"{self.field(name)}.classList.contains('w-int')"))
                # 単位を欄の外に並べない（Qt と同じ: 欄の中だけ）
                after = self.page.ev(f"({self.field(name)}.nextElementSibling || {{}}).textContent || ''")
                self.assertNotIn(L["times_suffix"].strip(), after)
                self.assertNotIn(L["count_suffix"].strip(), after)
        # 上位 N 個: 打った値（単位なし）を確定すると欄は「5 個」。範囲の外（1〜8）・数でない文字は送らずに戻す
        self.page.ev("gsApp.tab = 'marker'; 1")
        self.page.settle()
        self.type_in("top_n", "5")
        self.assertEqual(self.page.ev("gsApp.topN"), 5)
        self.assertEqual(self.page.ev(f"{self.field('top_n')}.value"), "5" + L["count_suffix"])
        for bad in ("9", "0", "abc"):
            self.type_in("top_n", bad)
            self.assertEqual(self.page.ev("gsApp.topN"), 5, bad)
            self.assertEqual(self.page.ev(f"{self.field('top_n')}.value"), "5" + L["count_suffix"], bad)
        self.type_in("top_n", "", key="ArrowUp")
        self.assertEqual(self.page.ev("gsApp.topN"), 6)
        # 1段の回数はコアの設定（Qt と同じ set_step_sweep）
        self.page.ev("gsApp.tab = 'source'; 1")
        self.page.settle()
        self.type_in("step_per_step", "3" + L["times_suffix"])
        self.page.wait("gsApp.stepView.per_step === 3", "1段の回数")
        self.page.settle()
        self.assertEqual(self.page.ev(f"{self.field('step_per_step')}.value"), "3" + L["times_suffix"])
        self.type_in("ref_avg", "65")
        self.assertEqual(self.page.ev("gsApp.refAvg"), 1)
        self.type_in("ref_avg", "8")
        self.assertEqual(self.page.ev("gsApp.refAvg"), 8)
        self.assertEqual(self.page.errors, [])

    # ---------------------------------------------------------------- 基準の範囲外の帯（設計E §6.4）

    def test_norm_band_on_screen_and_png(self):
        self.app.call("set_mode", mode="tg")
        self.app.call("reference_take", kind="fine")
        self.wait_idle("基準を取る")
        self.app.call("set_sweep", start_hz=490 * MHZ, stop_hz=510 * MHZ)
        st = self.single()
        ranges = st["normalization"]["out_of_range_hz"]
        self.assertEqual(len(ranges), 2)
        open_app(self.page, self.app, 1280, 720)
        self.page.wait("(gsApp.ui.norm_bands || []).length === 2", "帯")
        self.page.settle()
        bands = self.page.ev("gsApp.$graph.bands.filter((b) => !b.label).map((b) => [b.start_hz, b.stop_hz])")
        self.assertEqual(bands, ranges)
        # PNG: 同じ区間を画面と同じ部品で描く（PNG の描画が作るグラフだけを記録する）
        got = self.page.ev("""(async () => {
          const seen = []; const Orig = window.GSGraph;
          window.GSGraph = class extends Orig {
            setBands(list) { seen.push((list || []).map((b) => [b.start_hz, b.stop_hz, b.fill])); return super.setBands(list); } };
          try { await gsApp.pngBlob(""); } finally { window.GSGraph = Orig; }
          return seen; })()""")
        warn = self.page.ev("gsApp.pngColors().warn")
        self.assertEqual(got, [[[lo, hi, warn] for lo, hi in ranges]])

    # ---------------------------------------------------------------- 開いたファイル

    def open_file(self, path):
        self.app.call("open", paths=[str(path)])
        self.wait_idle("開く")

    def test_file_heading_and_traces_shown_at_save(self):
        root = Path(self.app.tmp.name)
        self.open_file(write_file(root / "a.h5"))
        open_app(self.page, self.app, 1280, 720)
        self.page.ev("gsApp.tab = 'trace'; 1")
        self.page.wait("gsApp.files.length === 1", "開いたファイル")
        self.page.settle()
        f = self.app.call("get_state")["files"][0]
        head = "document.querySelector('section[data-sec=files] .file-head')"
        self.assertEqual(self.page.ev(f"{head}.querySelector('.file-name').textContent.trim()"), f["name"])
        self.assertEqual(self.page.ev(f"{head}.querySelector('.file-name').title"), texts.file_heading_tip(f))
        # 見出しの行に日時・機器を並べない（ツールチップだけ: Qt と同じ）
        self.assertNotIn("TEST0001", self.page.ev(f"{head}.textContent"))
        # 開いたファイルごとに枠で囲む（Qt の fileBox と同じ）: 見出し・［外す］・そのファイルのトレースが枠の中
        box = "document.querySelector('section[data-sec=files] .file')"
        self.assertEqual(self.page.ev(f"getComputedStyle({box}).borderTopWidth"), "1px")
        self.assertEqual(self.page.ev(f"{box}.querySelectorAll('label.inline input').length"), len(f["traces"]))
        # 重ねた直後は、保存したときに表示していたトレースだけ
        t1, t2 = (t["id"] for t in f["traces"])
        self.page.wait(f"gsApp.$traces.has('{t1}') && gsApp.$traces.has('{t2}')", "値が届く")
        checks = "[...document.querySelectorAll('section[data-sec=files] label.inline input')]"
        self.assertEqual(self.page.ev(f"{checks}.map((c) => c.checked)"), [True, False])
        self.assertEqual(self.page.ev("gsApp.shownFiles(gsApp.palette).map((x) => x.id)"), [t1])
        # 利用者が出したものは、別のファイルを開いても戻さない
        self.page.ev(f"{checks}[1].click(); 1")
        self.page.settle()
        self.open_file(write_file(root / "b.h5"))
        self.page.wait("gsApp.files.length === 2", "2つ目のファイル")
        self.page.settle()
        shown = self.page.ev("gsApp.shownFiles(gsApp.palette).map((x) => x.id)")
        self.assertIn(t2, shown)
        f2 = self.app.call("get_state")["files"][1]
        self.assertIn(f2["traces"][0]["id"], shown)
        self.assertNotIn(f2["traces"][1]["id"], shown)
        self.assertEqual(self.page.errors, [])


class CountFieldsListTest(unittest.TestCase):
    def test_same_fields_as_qt(self):
        """Qt で単位を欄の中に持つ整数の欄（QSpinBox の suffix: 上位 N 個・1段の回数・基準の平均の 3 つ）と同じ数の
        int-field。単位を欄の外に置いた形（W('…_suffix').trim()）は残っていない。"""
        html = (UI_WEB / "index.html").read_text(encoding="utf-8")
        fields = re.findall(r'<int-field data-field="(\w+)"', html)
        self.assertEqual(sorted(fields), ["ref_avg", "step_per_step", "top_n"])
        self.assertNotIn("_suffix').trim()", html)
        qt = []
        for name in ("tab_markers.py", "step_panel.py", "tab_source.py"):
            src = (UI_WEB.parent / "ui_qt" / name).read_text(encoding="utf-8")
            qt += re.findall(r"self\.(\w+)\.setSuffix\(", src)
        self.assertEqual(sorted(qt), ["per_step", "take_avg", "top_n"])


if __name__ == "__main__":
    unittest.main()
