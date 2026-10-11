"""部品の大きさと余白の決まり（設計J §4.11・docs/uxui_rule.md S-01〜S-07）の試験。

- Qt（ui_qt/sizes.py）と Web（ui_web/style.css の :root）が同じ値を持つ
- 種類ごとの一番長い値が、その種類の欄の幅に入る
- 全部の画面で、欄・ドロップダウン・ボタンの高さが段の値・同じ行の部品がそろう・文字が切れない（qt_layout_check）
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.api.web import access as acc
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import device_menu, sizes, texts
from pygigast.ui_qt.files import UnfinishedDialog
from tests.device_helpers import FakeGigaBackend
from tests.qt_layout_check import problems
from tests.test_ui_qt_pluto_gain import PlutoGainUiBase
from tests.test_ui_qt_plutosdr import PlutoUiBase
from tests.test_ui_qt_rtlsdr import RtlUiBase
from tests.test_ui_qt_support import UiBase, app

ROOT = Path(__file__).resolve().parents[1]

# Web の :root の名前 → Qt の値
WEB_VARS = {
    "--h-l": sizes.H_L, "--h-m": sizes.H_M, "--h-s": sizes.H_S, "--row-h": sizes.ROW_H,
    "--font-l-value": sizes.FONT_L_VALUE, "--font-l-button": sizes.FONT_L_BUTTON, "--font-run": sizes.FONT_RUN,
    "--font-m": sizes.FONT_M, "--font-s": sizes.FONT_S, "--run-min-w": sizes.RUN_MIN_W,
    "--btn-pad-x": sizes.BTN_PAD_X, "--btn-min-w": sizes.BTN_MIN_W, "--gap": sizes.GAP, "--label-gap": sizes.LABEL_GAP,
    "--section-gap": sizes.SECTION_GAP, "--inset": sizes.INSET, "--window-margin": sizes.WINDOW_MARGIN,
    "--indicator": sizes.INDICATOR,
    **{f"--w-{k}": sizes.width_px(k) for k in sizes.WIDTH_EM},
}


class SizeValuesTest(unittest.TestCase):
    def test_web_has_the_same_values(self):
        css = (ROOT / "pygigast" / "ui_web" / "style.css").read_text(encoding="utf-8")
        root = re.search(r":root\s*{(.*?)}", css, re.S).group(1)
        have = {m.group(1): int(m.group(2)) for m in re.finditer(r"(--[\w-]+):\s*(\d+)px", root)}
        self.assertEqual({k: have.get(k) for k in WEB_VARS}, WEB_VARS)

    def test_widest_values_fit(self):
        f = QFont(app.font())
        f.setPixelSize(sizes.FONT_M)
        fm = QFontMetrics(f)
        for kind, samples in sizes.WIDTH_SAMPLES.items():
            # 欄の左右の余白（6px）と枠（1px）。打てるドロップダウンは ▼ の分も
            room = sizes.width_px(kind) - 2 * 6 - 2 - (sizes.CHOICE_ARROW if kind == "value" else 0)
            for s in samples:
                self.assertLessEqual(fm.horizontalAdvance(s), room, f"{kind}: {s}")


class LayoutTest(UiBase):
    """主画面の全部のタブ・設定の全部のページ・別の窓（プリセット・機器の一覧・CSV に書き出す・まとめ終わっていない
    記録・ブラウザからの接続のしかた・つながっている相手）を、決めた解像度で開いて数える。"""

    size = (1280, 720)

    def make_window(self):
        # 機器の一覧に行が出るように、偽の GigaSt を1台挿しておく（実機のポートは開かない）
        profile = load_profile(GIGAST_PROFILE_PATH)
        sim = _SimulatorBackend(profile)
        sim.speed = 0.05
        giga = FakeGigaBackend(profile)
        giga.plug("COM7", "FT00AB12")
        self.api.close()
        self.api = LocalApi(Instrument({"gigast": giga, "simulator": sim}, profile=profile,
                                       data_root=self.root / "d", cal_root=self.root / "c"))
        return super().make_window()

    def check(self, root, where):
        self.pump(0.2)
        return problems(root, where)

    def test_main_window_and_dialogs(self):
        found = []
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("run", mode="single").result(5)
        self.pump(1.0)
        self.wait(lambda: (self.win.state.get("run") or {}).get("state") == "stopped" and self.win.traces.live, what="1回の掃引")
        r = self.api.call("marker_add", kind="M", trace=1, freq_hz=500_000_000).result(5)
        self.pump(0.2)
        # マーカーを選び、子の行（オフセットのカーソル・機能の結果）と XY カーソルも出す
        self.win.select_marker(r["id"])
        self.api.call("marker_offset_add", id=r["id"], type="freq", sign="+", delta=1_000_000).result(5)
        self.api.call("marker_function", id=r["id"], bandwidth_db=[3.0], threshold={"level": -20.0, "side": "above"}).result(5)
        self.api.call("set_xy", x1_hz=400_000_000, y1=-50.0).result(5)
        for key in ("tab-trace", "tab-marker", "tab-view", "tab-source"):
            self.win.show_part(key)
            found += self.check(self.win, f"主画面 {key}")
        # 探すもの「谷」（G §3.8）: マーカー表の上のボタンの名前が変わる（場所は同じ）。ピークの一覧は探せない理由付き
        self.win.marker_panel.set_polarity("dip")
        self.win.show_part("tab-marker")
        found += self.check(self.win, "主画面 tab-marker（谷）")
        self.win.marker_panel.set_polarity("peak")
        # 保存した配色（J §4.4.1）: いまの配色と、読みにくい組のある取り込んだ配色（一覧の行の知らせも数える）
        pal = self.api.call("palette_save", name="夜の観測").result(5)
        d = json.loads(self.api.call("palette_export", id=pal["id"]).result(5)["text"])
        d["content"]["colors"]["text_dim"] = d["content"]["colors"]["panel_bg"]
        d["name"] = "読みにくい配色"
        self.api.call("palette_import", text=json.dumps(d)).result(5)
        self.win._settings_dialog()
        dlg = self.win._settings
        for key in list(dlg._page_index):
            dlg.open_page(key)
            if key == "display":
                self.wait(lambda: len(dlg.palette_page.saved.rows) == 2, what="保存した配色の一覧")
            found += self.check(dlg, f"設定 {key}")
        dlg.close()
        self.win.open_presets()
        found += self.check(self.win._presets, "プリセット")
        self.win._presets.close()
        # 長い処理の進み具合の行（やめるのボタン）と、別の窓に出したグラフ・ピークの一覧の札（ここに戻す）
        self.win.task_panel.on_progress({"task_id": "t1", "task": "reference_take", "done": 3, "total": 10,
                                         "elapsed_s": 2.0})
        found += self.check(self.win, "主画面（進み具合）")
        self.win.task_panel.apply_tasks([])
        self.win.parts.detach("graph")
        self.win.parts.detach("peaks")
        found += self.check(self.win, "主画面（グラフとピークの一覧を別の窓へ）")
        found += self.check(self.win.parts.graph.window, "グラフの別の窓")
        self.win.parts.graph.attach()
        self.win.parts.peaks.attach()
        found += self.dialogs()
        found += self.cal_pages()
        self.assertEqual(sorted(set(found)), [])

    def test_zero_span_tabs(self):
        """ゼロスパン（設計N §1）: 時間の欄（マーカー・X1・X2）・区間の行・表示する長さの欄も同じ決まりで数える。"""
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("set_view", zero_span_s=10).result(5)
        self.api.call("set_sweep", center_hz=100_000_000, span_hz=0).result(5)
        self.api.call("run", mode="single").result(5)
        self.wait(lambda: (self.win.state.get("run") or {}).get("state") == "stopped"
                  and (self.win.traces.live_payload(1) or {}).get("x_kind") == "time", what="ゼロスパンの1回の掃引")
        r = self.api.call("marker_add", kind="M", trace=1).result(5)
        self.pump(0.2)
        self.win.select_marker(r["id"])
        self.api.call("set_xy", x1_hz=-8_000_000, x2_hz=-1_000_000).result(5)
        found = []
        for key in ("tab-trace", "tab-marker", "tab-view"):
            self.win.show_part(key)
            found += self.check(self.win, f"ゼロスパン {key}")
        self.assertEqual(sorted(set(found)), [])

    def test_s11_screens(self):
        """S11（設計E §6.5・設計R §3）: オープンを測った後の信号源のタブと上段（次はショート）、基準を取って縦軸を SWR に
        したトレースの表（単位が違う固定のトレースの「表示」は無効）・表示のタブ（S11 の表示の欄）・マーカーのタブ（SWR の
        しきい値）・|Γ|・SWR の縦軸の上段。"""
        found = []
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000).result(5)
        self.api.call("set_mode", mode="s11").result(5)
        self.api.call("reference_take", kind="fine", standard="open").result(5)
        self.wait(lambda: self.win.source_tab.s11_state.isVisible(), timeout=20, what="オープンを測った")
        self.win.show_part("tab-source")
        found += self.check(self.win, "主画面 S11 オープンの後 tab-source")
        # 基準を取る前の電力（dBm）でトレース2を固定しておく（縦軸と単位が違うので描かない固定のトレース）
        self.api.call("set_trace", number=2, visible=True, action="update").result(5)
        self.api.call("run", mode="single").result(5)
        self.wait(lambda: (self.win.traces.live_payload(2) or {}).get("unit") == "dBm", timeout=20, what="トレース2")
        self.api.call("set_trace", number=2, action="fixed").result(5)
        self.api.call("reference_take", standard="short").result(5)
        self.wait(lambda: (self.win.state.get("display_quantity") == "ratio"), timeout=20, what="基準との比")
        self.api.call("set_view", s11_format="swr").result(5)
        r = self.api.call("marker_peak", polarity="dip", trace=1).result(5)
        self.api.call("marker_function", id=r["id"], threshold={"level": 2.0, "side": "below"}).result(5)
        self.wait(lambda: self.win.display_unit() == "SWR", what="縦軸が SWR")
        self.win.select_marker(r["id"])
        self.wait(lambda: not self.win.trace_tab.rows[2].visible.isEnabled(), what="描かないトレースの表示が無効")
        for key in ("tab-trace", "tab-marker", "tab-view", "tab-source"):
            self.win.show_part(key)
            found += self.check(self.win, f"主画面 S11（SWR） {key}")
        self.assertEqual(sorted(set(found)), [])

    def test_rbw_warning_keeps_layout(self):
        """RBW の欄の注意（U-25: 欄を注意の色にして文はツールチップ）が出ても出なくても、崩れは 0 件で、欄の大きさと
        場所は変わらない（上段の下に行を足さないので、グラフの位置も動かない）。"""
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("set_sweep", start_hz=495_000_000, stop_hz=505_000_000, rbw_hz=180_000).result(5)
        self.api.call("run", mode="single").result(5)
        self.wait(lambda: (self.win.state.get("run") or {}).get("state") == "stopped" and self.win.traces.live,
                  what="1回の掃引")
        combo = self.win.rbw_combo
        found = []
        boxes = []
        for rbw, warn in ((180_000, False), (15_000, True), (180_000, False)):
            self.api.call("set_sweep", rbw_hz=rbw).result(5)
            self.wait(lambda: bool(combo.property("warn")) == warn, what=f"RBW {rbw} の注意 {warn}")
            for key in ("tab-trace", "tab-marker", "tab-view", "tab-source"):
                self.win.show_part(key)
                found += self.check(self.win, f"主画面 RBW {rbw} {key}")
            boxes.append((combo.mapTo(self.win, combo.rect().topLeft()), combo.size(),
                          self.win.plot.mapTo(self.win, self.win.plot.rect().topLeft())))
        self.assertEqual(sorted(set(found)), [])
        self.assertEqual(boxes[0], boxes[1])
        self.assertEqual(boxes[1], boxes[2])

    def _single(self):
        n0 = self.api.call("get_state").result(5)["run"]["sweep_count"]
        self.api.call("run", mode="single").result(5)
        self.wait(lambda: (self.win.state.get("run") or {}).get("state") == "stopped"
                  and self.win.state["run"]["sweep_count"] > n0, what="単発")

    def add_marker_rows(self, freq_hz: int) -> None:
        """マーカー表に行を出す（マーカー・オフセットのカーソル・機能の結果・XY カーソル: Web の App と同じ形）。
        表の行が無いと、表が横にはみ出すか・列が切れるかを数えられない。"""
        r = self.api.call("marker_add", kind="M", trace=1, freq_hz=freq_hz).result(5)
        self.win.select_marker(r["id"])
        self.api.call("marker_offset_add", id=r["id"], type="freq", sign="+", delta=1_000_000).result(5)
        self.api.call("marker_function", id=r["id"], bandwidth_db=[3.0], threshold={"level": -20.0, "side": "above"}).result(5)
        self.api.call("set_xy", x1_hz=freq_hz - 10_000_000, y1=-50.0).result(5)
        self.wait(lambda: len(self.win.marker_panel.rows) >= 6, what="マーカー表の行")

    def test_marker_table_in_every_layout(self):
        """どの表示の組み合わせ（スペクトルのみ・＋ウォーターフォール・＋密度: 設計P §2）でも、行のあるマーカー表が横に
        はみ出さず、右端の「操作」まで見える。機能の結果が長い文（交点が見つかった N dB 帯域幅）でも、注意の列で折り返す。"""
        found = []
        call = lambda n, **kw: self.api.call(n, **kw).result(10)  # noqa: E731
        call("connect", backend="simulator", serial="SIM00001")
        self.api._inst._device.config.signals = [(120_000_000, -30.0)]
        try:
            call("set_sweep", start_hz=100_000_000, stop_hz=140_000_000)
            self._single()
            self.add_marker_rows(120_000_000)
            self.wait(lambda: any(r.get("kind") == "function" and r.get("status") == "ok"
                                  for r in self.win.marker_panel.rows), what="N dB 帯域幅の結果")
            for layout in ("spectrum", "waterfall", "density"):
                call("set_view", layout=layout)
                self._single()
                for key in ("tab-trace", "tab-view"):
                    self.win.show_part(key)
                    found += self.check(self.win, f"主画面 {layout} {key}")
        finally:
            self.api._inst._device.config.signals = []
            call("set_view", layout="spectrum")
        self.assertEqual(sorted(set(found)), [])

    def test_display_parts(self):
        """設計P の部分（ウォーターフォール・密度表示・再生の帯・表示のタブの欄とブックマークの一覧・別の窓）も数える。"""
        found = []
        call = lambda n, **kw: self.api.call(n, **kw).result(10)  # noqa: E731
        call("connect", backend="simulator", serial="SIM00001")
        call("set_sweep", start_hz=50_000_000, stop_hz=200_000_000)
        call("bookmark_add", name="ビーコン", hz=120_000_000)
        call("bookmark_add", name="FM", hz=80_000_000)
        call("record_start")
        call("set_view", layout="waterfall")
        for _ in range(3):
            self._single()
        self.win.show_part("tab-view")
        self.wait(lambda: len(self.win.display.waterfall.shown_rows) >= 3, what="ウォーターフォールの行")
        found += self.check(self.win, "主画面 ＋ウォーターフォール")
        call("set_view", layout="density")
        self._single()
        found += self.check(self.win, "主画面 ＋密度")
        call("record_stop")
        h5 = self.root / "layout_rec.h5"
        self.wait_task_done(call("record_finish", path=str(h5)))
        call("open", paths=[str(h5)])
        self.wait(lambda: self.win.display.bar.isVisible(), what="再生の帯")
        found += self.check(self.win, "主画面 再生")
        self.win.parts.detach("waterfall")
        self.wait(lambda: self.win.parts.lower["waterfall"].detached, what="ウォーターフォールを別の窓へ")
        found += self.check(self.win.parts.lower["waterfall"].window, "ウォーターフォールの別の窓")
        found += self.check(self.win, "主画面（ウォーターフォールを別の窓へ）")
        self.win.parts.lower["waterfall"].attach()
        call("playback_close")
        self.assertEqual(sorted(set(found)), [])

    def wait_task_done(self, r):
        tid = r["task_id"]
        self.wait(lambda: not any(t.get("task_id") == tid for t in self.api.call("get_state").result(5)["tasks"]),
                  what="長い処理の終わり")

    def test_reference_value_screens(self):
        """基準の値（設計E §6.7）: 値を付けて上段で取った基準を一覧で選んだ信号源のタブ（基準の値の欄・行の値・
        「基準の値…」）と上段の基準の文（値付き）、S11 で欄を使えなくした信号源のタブ。"""
        found = []
        src = self.win.source_tab
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("set_sweep", start_hz=100_000_000, stop_hz=200_000_000).result(5)
        self.api.call("set_mode", mode="tg").result(5)
        self.wait(lambda: self.win.ref_quick.isVisible(), what="基準を取る")
        src.take_value.set_value(-40.0)
        self.win.ref_quick.take()
        self.wait(lambda: ((self.win.state.get("normalization") or {}).get("selected") or {}).get("value_db") == -40.0
                  and not self.win.state.get("tasks"), timeout=30, what="値を付けた基準")
        self.wait(lambda: src.ref_list.count() == 1, what="基準の一覧")
        src.ref_list.setCurrentRow(0)
        self.win.show_part("tab-source")
        self.assertTrue(src.btn_value.isEnabled())
        found += self.check(self.win, "主画面 基準の値 tab-source")
        self.api.call("set_mode", mode="s11").result(5)
        self.wait(lambda: not src.take_value.isEnabled(), what="S11 では使えない")
        found += self.check(self.win, "主画面 基準の値 S11 tab-source")
        self.assertEqual(sorted(set(found)), [])

    def test_checker_finds_broken_layout(self):
        # 検査そのものが働くこと（本物の画面をわざと崩して見つかるか）
        self.win.show_part("tab-trace")
        self.pump(0.3)
        self.assertEqual(problems(self.win, "x"), [])
        r = self.win.trace_tab.rows[2]
        width = r.avg.width()
        r.avg.setFixedWidth(width + 10)  # 同じ列の欄の幅がそろわない
        r.clear.setFixedWidth(200)  # ボタンが文字の幅より広い
        self.win.trace_tab.points_spin.setFixedWidth(100)  # 入力欄の幅が値の種類の幅でない
        self.pump(0.2)
        found = problems(self.win, "x")
        self.assertTrue(any("並びの 2 列目" in p for p in found), found)
        self.assertTrue(any("クリア" in p and "伸びている" in p for p in found), found)
        self.assertTrue(any("100px が値の種類の幅" in p for p in found), found)

    def test_checker_finds_broken_table(self):
        """表の検査そのものが働くこと: 本物のマーカー表をわざと横にはみ出させ（注意の列を広げる）、列の文字を切る
        （値の列を狭める）と、はみ出し・右端の「操作」の見切れ・切れた文字が見つかる。"""
        from PySide6.QtWidgets import QHeaderView

        from pygigast.ui_qt.marker_table import COL_NOTE, COL_VALUE

        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self._single()
        self.add_marker_rows(500_000_000)
        self.win.show_part("tab-trace")
        self.assertEqual(self.check(self.win, "x"), [])
        hdr = self.win.marker_panel.tree.header()
        hdr.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_NOTE, 3000)
        hdr.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(COL_VALUE, 20)
        found = self.check(self.win, "x")
        self.assertTrue(any("横にはみ出している" in p for p in found), found)
        self.assertTrue(any("列「操作」" in p and "切れている" in p for p in found), found)
        self.assertTrue(any("dBm」の文字が切れている" in p for p in found), found)

    def test_marker_table_wraps_long_notes(self):
        """表が狭いとき（マーカー表を別の窓にして狭くした）も横にはみ出さない: 注意の列は残りの幅で折り返し、折り返した
        行は文が入る高さになり、その行の「操作」は段の高さのまま行の縦の真ん中にある。"""
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self._single()
        self.add_marker_rows(500_000_000)
        panel = self.win.marker_panel
        tree = panel.tree
        self.win.parts.docks["markers"].setFloating(True)
        self.win.parts.docks["markers"].resize(900, 320)
        self.pump(0.5)
        try:
            self.assertEqual(problems(self.win.parts.docks["markers"], "狭い表"), [])
            heights = {}
            from PySide6.QtWidgets import QTreeWidgetItemIterator
            it = QTreeWidgetItemIterator(tree)
            while it.value():
                i = it.value()
                heights[i.data(0, Qt.ItemDataRole.UserRole)] = tree.visualItemRect(i).height()
                it += 1
            # しきい値の行の注意（長い文）は折り返して2行以上、注意の無い行は1行の高さ
            self.assertGreater(heights["M1.th"], heights["Y1"], heights)
            from PySide6.QtWidgets import QToolButton

            ops = tree.itemWidget(panel._items["M1.th"], len(texts.MARKER_COLUMNS)).findChild(QToolButton)
            row = tree.visualItemRect(panel._items["M1.th"])
            self.assertEqual(ops.height(), sizes.H_M)
            center = ops.mapTo(tree.viewport(), ops.rect().center()).y()
            self.assertLessEqual(abs(center - row.center().y()), 1)
        finally:
            self.win.parts.docks["markers"].setFloating(False)

    def cal_pages(self) -> list[str]:
        """校正の画面（設計K）の手順: 準備・測る（周波数特性・RBW・NF の増幅度）・確かめる（周波数特性・旧校正ファイル）。
        測った点と結果がある形で数える（Web の test_web_layout.cal_views と同じ手順）。"""
        from pygigast.ui_qt.cal_dialog import CHECK, MEASURE
        from tests.test_level import build_legacy_bytes

        found = []
        dev = self.api._inst._device
        self.win.open_calibration("freq")
        d = self.win.cal_window
        self.wait(lambda: d.v is not None, what="校正の画面")
        self.assertEqual(d.windowTitle(), texts.MENU_ITEMS["cal_freq"].rstrip("…"))
        found += self.check(d, "校正 準備")

        def measure(**kw):
            self.api.call("cal_measure", **kw).result(10)
            self.wait(lambda: not self.api.get_state().result(5)["tasks"], timeout=30, what="校正の測定")
            self.pump(0.3)

        dev.config.signals = [(10_000_000, -10.0)]
        measure()
        d.show_page(MEASURE)
        found += self.check(d, "校正 測る（周波数特性）")
        d.show_page(CHECK)
        found += self.check(d, "校正 確かめる（周波数特性）")
        self.api.call("cal_set_kind", kind="rbw", discard=True).result(5)
        measure()
        d.show_page(MEASURE)
        found += self.check(d, "校正 測る（RBW）")
        self.api.call("cal_set_kind", kind="nf", discard=True).result(5)
        self.pump(0.3)
        d.show_page(MEASURE)
        found += self.check(d, "校正 測る（NF の増幅度）")
        self.api.call("cal_set_kind", kind="legacy", discard=True).result(5)
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        self.api.call("cal_legacy_load", path=str(p)).result(10)
        self.pump(0.3)
        d.show_page(CHECK)
        found += self.check(d, "校正 確かめる（旧校正ファイル）")
        d.ask_close = lambda: "discard"
        d.close()
        dev.config.signals = []
        return found

    def dialogs(self) -> list[str]:
        found = []
        dw = device_menu.open_list(self.win)
        self.wait(lambda: len(dw.row_widgets()) == 2, what="機器の一覧の行")
        # 窓の題は、その窓を開くメニューの項目の名前（「…」を除く: U-24）
        self.assertEqual(dw.windowTitle(), texts.MENU_ITEMS["connect"].rstrip("…"))
        found += self.check(dw, "機器の一覧")
        dw.row_widgets()[0].more.click()
        found += self.check(dw, "機器の一覧（詳細）")
        dw.close()
        self.win.files.export()
        ex = self.win.files.export_dialog
        self.assertEqual(ex.windowTitle(), texts.MENU_ITEMS["export"].rstrip("…"))
        found += self.check(ex, "CSV に書き出す")
        ex.r_custom.setChecked(True)
        found += self.check(ex, "CSV に書き出す（数値で指定）")
        ex.reject()
        ud = UnfinishedDialog(self.win.files, [
            {"path": "a", "started_at": "2026-10-03 10:00:00", "n_sweeps": 120, "duration_s": 600,
             "device_model": "GigaSt v5"},
            {"path": "b", "started_at": "2026-10-03 11:00:00", "n_sweeps": 3, "error": "x"}])
        ud.show()
        found += self.check(ud, "まとめ終わっていない記録")
        ud.close()
        web = self.win.web
        with mock.patch.object(acc, "lan_addresses", return_value=["192.168.1.10"]):
            web.port = 0  # 空いているポート
            web.set_enabled(True)
            web.set_lan(True)
            self.wait(lambda: web.connect_window is not None and web.connect_window.isVisible(), what="接続のしかた")
            self.assertEqual(web.connect_window.windowTitle(), texts.MENU_ITEMS["web_connect"].rstrip("…"))
            found += self.check(web.connect_window, "ブラウザからの接続のしかた")
            web.connect_window.close()
            web.show_clients()
            found += self.check(web.clients_window, "つながっている相手")
            found += self.check(self.win, "主画面（状態バーにブラウザからの接続）")
            web.clients_window.close()
            web.set_enabled(False)
        return found


class LayoutWideTest(LayoutTest):
    size = (1920, 1080)


class LayoutRtlTest(RtlUiBase):
    """RTL-SDR に繋いだときだけ出る欄（受信・聞く）を、決めた解像度で開いて数える。"""

    size = (1280, 720)

    def test_receiver_and_listen_panels(self):
        found = []
        self.connect_rtl()
        r = self.cmd("marker_add", kind="M", trace=1, freq_hz=84_700_000)
        self.win.select_marker(r["id"])
        self.pump(0.3)
        self.assertTrue(self.win.rtl_panel.isVisible() or self.win.rtl_panel.isVisibleTo(self.win.trace_tab))
        for key in ("tab-trace", "tab-marker"):
            self.win.show_part(key)
            self.pump(0.2)
            found += problems(self.win, f"主画面 RTL-SDR {key}")
        self.assertTrue(self.win.listen.panel.isVisible())
        self.assertEqual(sorted(set(found)), [])


class LayoutRtlWideTest(LayoutRtlTest):
    size = (1920, 1080)


class LayoutPlutoTest(PlutoUiBase):
    """PlutoSDR に繋いだときだけ出る欄（受信・送信）と設定の PlutoSDR のページを、決めた解像度で開いて数える。"""

    size = (1280, 720)

    def test_receiver_and_transmitter_panels(self):
        found = []
        self.connect_pluto()
        # 設定の利得がいまの範囲で使えない形（使う値の行が出る）
        self.cmd("plutosdr_set", gain_db=73.0)
        self.cmd("set_sweep", start_hz=2_400_000_000, stop_hz=2_402_000_000)
        self.cmd("set_mode", mode="sg", sg_hz=2_401_000_000)
        self.pump(0.4)
        self.assertTrue(self.win.pluto_panel.gain_used.isVisibleTo(self.win.pluto_panel))
        for key in ("tab-trace", "tab-source"):
            self.win.show_part(key)
            self.pump(0.2)
            found += problems(self.win, f"主画面 PlutoSDR {key}")
        self.assertTrue(self.win.source_tab.pluto_tx.isVisible())
        self.assertEqual(sorted(set(found)), [])


class LayoutPlutoWideTest(LayoutPlutoTest):
    size = (1920, 1080)


class LayoutPlutoGainTest(PlutoGainUiBase):
    """校正の画面の PlutoSDR の利得の補正の手順（準備・測った後の測る・確かめる）と、印の出た受信の欄を数える。"""

    size = (1280, 720)

    def test_pages(self):
        from pygigast.ui_qt.cal_dialog import CHECK, MEASURE

        self.connect_pluto()
        found = []
        self.win.show_part("tab-trace")
        self.pump(0.2)
        self.assertTrue(self.win.pluto_panel.gain_cal.isVisible())
        found += problems(self.win, "主画面 PlutoSDR 利得の補正なし")
        d = self.open_cal()
        self.pump(0.2)
        found += problems(d, "PlutoSDR の利得の補正 準備")
        self.measure(d)
        d.show_page(MEASURE)
        self.pump(0.2)
        found += problems(d, "PlutoSDR の利得の補正 測る")
        d.show_page(CHECK)
        self.pump(0.2)
        found += problems(d, "PlutoSDR の利得の補正 確かめる")
        d.ask_close = lambda: "discard"
        d.close()
        self.assertEqual(sorted(set(found)), [])


class LayoutPlutoGainWideTest(LayoutPlutoGainTest):
    size = (1920, 1080)


if __name__ == "__main__":
    unittest.main()


class TopBarOneRowTest(UiBase):
    """上段は SA の 1600 幅・TG（基準を取るとその基準の文が出る）の 1920 幅で1段に収まる（RBW 以降を2行目へ
    追い出さない: 2026-10-03 ユーザー指摘・設計の PDCA 2巡目）。上段の周波数の欄は一番長い値でも切れない。"""

    def one_row(self) -> bool:
        self.pump(0.3)
        return not self.win.topbar._two_rows and abs(
            self.win.rbw_combo.mapToGlobal(self.win.rbw_combo.rect().topLeft()).y()
            - self.win.f_center.edit.mapToGlobal(self.win.f_center.edit.rect().topLeft()).y()) <= 1

    def test_sa_1600(self):
        self.win.resize(1600, 900)
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.assertTrue(self.one_row(), self.win.topbar.needed_one_row())

    def test_tg_1920(self):
        self.win.resize(1920, 1080)
        self.api.call("connect", backend="simulator", serial="SIM00001").result(10)
        self.api.call("set_mode", mode="tg").result(5)
        self.wait(lambda: self.win.ref_quick.isVisible(), what="基準を取る")
        self.assertTrue(self.one_row(), self.win.topbar.needed_one_row())

    def test_widest_top_frequency_fits(self):
        e = self.win.f_center.edit
        self.pump(0.2)
        for t in sizes.TOP_FREQ_SAMPLES:
            room = e.width() - 2 - 12 - 2 * sizes.EDIT_INNER
            self.assertLessEqual(e.fontMetrics().horizontalAdvance(t), room, t)
