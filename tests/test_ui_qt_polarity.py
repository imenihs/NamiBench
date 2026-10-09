"""Qt の画面: 探すもの「山／谷」（設計G §3.8）とピークの一覧の基準（設計P §9.7）・行の「操作」（G §3.4）。

- マーカー表の上のボタンの並び: 頭に山／谷の切り替え、ピーク・◀ 次のピーク・次のピーク ▶・次に高い・マーカー追加、
  右端に全部消す・表をコピー。谷にするとボタンの場所は同じで名前が変わる。メニューの項目の名前も変わる。
  メニューの「山／谷を切り替える」で切り替わる。選択は画面の覚え書きに残る
- 谷で探すとマーカーは谷の向きになり、ピークの一覧も同じ選択を使う（SA では谷の一覧は理由付きで探せない）
- ピークの一覧の欄の名前（ノイズフロアから／最大から）・差の列・見つけた数の行・谷の「P に登録」
- 行の「操作」の項目は Web と同じ（marker_rows.row_ops）。デルタの基準が無ければ 2点を範囲に は理由付きで無効
"""
from PySide6.QtWidgets import QPushButton

from pygigast.ui_qt import marker_rows, peak_view, texts
from tests.test_marker_dips import bef_in_tg
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
L = texts.LABELS


class PolarityUiBase(UiBase):
    def setUp(self):
        super().setUp()
        # 50MHz〜2GHz: 信号とスプリアスの間の雑音の床に谷がいくつもできる（SA でもマーカーは谷を探せる）
        self.connect_and_sweep()
        self.panel = self.win.marker_panel
        self.tab = self.win.marker_tab

    def markers(self):
        return {m["id"]: m for m in self.state()["markers"]["markers"]}

    def tg_with_bef(self):
        """TG で直結の基準を取り、帯域除去フィルタ（120MHz −30dB・170MHz −20dB のノッチ）を挟んで1回掃引する。"""
        dut = {"on": False}
        with bef_in_tg(lambda: dut["on"]):
            self.cmd("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
            self.cmd("set_mode", mode="tg")
            self.api.call("reference_take", kind="wide", name="direct").result(10)
            self.wait(lambda: not self.state()["tasks"] and (self.state()["normalization"] or {}).get("selected"),
                      timeout=20, what="基準を取り終える")
            dut["on"] = True  # 直結で基準を取った後に帯域除去フィルタを挟む
            n0 = self.state()["run"]["sweep_count"]
            self.cmd("run", mode="single")
            self.wait(lambda: self.win.state.get("display_quantity") == "ratio"
                      and self.win.state.get("run", {}).get("sweep_count", 0) > n0
                      and self.win.state.get("run", {}).get("state") == "stopped", what="比の掃引が画面に届く")
        self.pump(0.2)

    def bar_texts(self):
        """マーカー表の上のボタンの文字を左から（画面の上の並びの順）。"""
        bs = [b for b in self.panel.findChildren(QPushButton) if b.isVisible()]
        bs.sort(key=lambda b: b.mapTo(self.panel, b.rect().topLeft()).x())
        return [b.text() for b in bs]


class ToolbarTest(PolarityUiBase):
    def test_order_and_names_follow_switch(self):
        S = texts.SEARCH_BUTTONS
        peak = [texts.POLARITIES["peak"], texts.POLARITIES["dip"]] + [S["peak"][c] for c in peak_view.SEARCH_CODES] \
            + [L["clear_all"], L["copy_table"]]
        self.assertEqual(self.bar_texts(), peak)
        self.assertEqual(peak[2:7], ["ピーク", "◀ 次のピーク", "次のピーク ▶", "次に高い", "マーカー追加"])
        self.assertTrue(self.panel.pol_buttons["peak"].isChecked())
        self.panel.pol_buttons["dip"].click()
        self.pump(0.1)
        self.assertTrue(self.panel.pol_buttons["dip"].isChecked())
        self.assertFalse(self.panel.pol_buttons["peak"].isChecked())
        # 場所は変えず名前が変わる
        self.assertEqual(self.bar_texts()[2:7], ["ピーク", "◀ 次のピーク", "次のピーク ▶", "次に深い", "マーカー追加"])
        self.assertIn(texts.SEARCH_TIPS["dip"]["marker_peak_lower"], self.panel.btn_lower.toolTip())
        # メニューの項目の名前も変わる（G §3.8）
        a = self.win.actions
        self.assertEqual(a["marker_peak"].text(), texts.DIP_MENU_ITEMS["marker_peak"])
        self.assertEqual(a["marker_add"].text(), texts.DIP_MENU_ITEMS["marker_add"])
        # 画面の覚え書きに残る
        self.assertEqual(self.win.prefs.get(peak_view.PREF_KEY), "dip")
        # メニューの「山／谷を切り替える」で戻る
        a["marker_polarity"].trigger()
        self.pump(0.1)
        self.assertTrue(self.panel.pol_buttons["peak"].isChecked())
        self.assertEqual(a["marker_peak"].text(), texts.MENU_ITEMS["marker_peak"])
        self.assertEqual(self.bar_texts()[2], "ピーク")

    def test_dip_buttons_place_dip_markers(self):
        self.tg_with_bef()
        self.panel.pol_buttons["dip"].click()
        self.panel.btn_peak.click()
        self.wait(lambda: self.win.selected_marker == "M1", what="谷で M1")
        m = self.markers()["M1"]
        self.assertEqual(m["polarity"], "dip")
        self.assertAlmostEqual(m["freq_hz"], 120 * MHZ, delta=MHZ)  # 一番深いノッチ（−30dB）
        # 次に深い = いまより浅い谷のうち最も深いもの（−20dB のノッチ）
        self.panel.btn_lower.click()
        self.wait(lambda: abs(self.markers()["M1"]["freq_hz"] - 170 * MHZ) <= MHZ, what="次に深い谷へ")
        self.assertEqual(self.markers()["M1"]["polarity"], "dip")
        # ◀ 次の谷 で左のノッチへ戻る
        self.panel.btn_left.click()
        self.wait(lambda: abs(self.markers()["M1"]["freq_hz"] - 120 * MHZ) <= MHZ, what="◀ 次の谷")
        # マーカー追加も谷に置く（向きは谷）
        self.panel.btn_add.click()
        self.wait(lambda: "M2" in self.markers(), what="マーカー追加")
        self.assertEqual(self.markers()["M2"]["polarity"], "dip")
        # 表の注意の列に谷と出る
        self.pump(0.2)
        self.assertIn(L["row_dip"], self.win.marker_panel._items["M1"].text(marker_rows.COLUMNS.index("注意")))
        # 山へ戻しても置いたマーカーの向きは変わらない
        self.panel.pol_buttons["peak"].click()
        self.pump(0.1)
        self.assertEqual(self.markers()["M1"]["polarity"], "dip")


class PeakListTest(PolarityUiBase):
    def test_sa_floor_and_no_dips(self):
        self.assertEqual(self.tab.pk_margin_label.text(), L["from_floor"])
        self.assertEqual(self.tab.pk_margin_label.toolTip(), L["floor_tip"])
        self.tab.pk_find.click()
        self.wait(lambda: self.tab.pk_table.rowCount() >= 1, what="ピークの一覧")
        self.assertEqual(self.tab.pk_table.horizontalHeaderItem(2).text(), L["col_height_db"])
        self.assertTrue(self.tab.pk_note.text().startswith(f"{self.tab.pk_table.rowCount()} 個・ノイズフロア "))
        self.assertTrue(self.tab.pk_note.text().endswith(" dBm"))
        # 谷にすると、電力のスペクトルでは一覧の谷を探せない（理由付きで無効）
        self.panel.pol_buttons["dip"].click()
        self.pump(0.1)
        why = texts.REASON_TEXTS["dips_on_power"]
        for b in (self.tab.pk_find, self.tab.top_btn):
            self.assertFalse(b.isEnabled())
            self.assertEqual(b.toolTip(), why)

    def test_tg_max_and_dip_registration(self):
        self.tg_with_bef()
        self.assertEqual(self.tab.pk_margin_label.text(), L["from_max"])
        self.assertEqual(self.tab.pk_margin_label.toolTip(), L["max_tip_peak"])
        self.panel.pol_buttons["dip"].click()
        self.pump(0.1)
        self.assertEqual(self.tab.pk_margin_label.toolTip(), L["max_tip_dip"])
        self.assertTrue(self.tab.pk_find.isEnabled())
        self.tab.pk_find.click()
        self.wait(lambda: self.tab.pk_table.rowCount() == 2, what="谷の一覧（ノッチ 2 つ）")
        self.assertEqual(self.tab.pk_table.horizontalHeaderItem(2).text(), L["col_below_max_db"])
        self.assertTrue(self.tab.pk_note.text().startswith("2 個・最大 "), self.tab.pk_note.text())
        self.assertTrue(self.tab.pk_note.text().endswith(" dB"))
        diffs = [float(self.tab.pk_table.item(i, 2).text()) for i in range(2)]
        self.assertEqual(diffs, sorted(diffs))  # 深い順（−30dB のノッチが先）
        self.assertAlmostEqual(diffs[0], -30.0, delta=1.5)
        self.assertAlmostEqual(float(self.tab.pk_table.item(0, 0).text()), 120.0, delta=1.0)  # 周波数 (MHz)
        # 「P に登録」は谷の P
        self.tab.pk_table.cellWidget(0, 3).findChildren(QPushButton)[0].click()  # 行の「P に登録」（隣は Center に: P §9.6）
        self.wait(lambda: "P1" in self.markers(), what="P に登録")
        self.assertEqual(self.markers()["P1"]["polarity"], "dip")
        # 上位 N 個も谷（深い順に P1・P2）
        self.tab.top_n.setValue(2)
        self.tab.top_btn.click()
        self.wait(lambda: "P2" in self.markers(), what="上位 2 個")
        self.assertEqual({m["polarity"] for k, m in self.markers().items() if k.startswith("P")}, {"dip"})
        self.assertLess(self.markers()["P1"]["level"], self.markers()["P2"]["level"])


class RowOpsTest(PolarityUiBase):
    def test_items_same_as_web_and_span_reason(self):
        self.cmd("marker_add", kind="M", trace=1, freq_hz=100 * MHZ)
        self.pump()
        row = next(r for r in self.panel.rows if r["id"] == "M1")
        menu = self.panel._row_menu(row)
        acts = [a for a in menu.actions()]
        want = marker_rows.row_ops(row, self.win.marker_snap)
        self.assertEqual([a.text() if not a.isSeparator() else None for a in acts],
                         [None if it is None else it["text"] for it in want])
        self.assertEqual([it["text"] for it in want if it],
                         [L["row_center"], L["row_ref"], texts.MENU_ITEMS["marker_span"], "M1 を消す"])
        span = next(a for a in acts if a.objectName() == "row_span")
        self.assertFalse(span.isEnabled())
        self.assertEqual(span.toolTip(), texts.REASON_TEXTS["delta_ref"])
        # 行の「操作」のボタンは開くときに作り直す（基準を選んだら 2点を範囲に が使える）
        self.cmd("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.cmd("marker_set", id="M1", ref="M2")
        self.pump()
        from PySide6.QtWidgets import QToolButton
        ops = self.panel.tree.itemWidget(self.panel._items["M1"], self.panel.tree.columnCount() - 1)
        self.assertIsInstance(ops, QToolButton)
        self.assertEqual(ops.text(), L["row_ops"])
        ops.menu().aboutToShow.emit()
        span = next(a for a in ops.menu().actions() if a.objectName() == "row_span")
        self.assertTrue(span.isEnabled())
        f1 = self.markers()["M1"]["freq_hz"]
        span.trigger()
        self.wait(lambda: self.state()["sweep"]["start_hz"] == f1, what="2点を Start・Stop に")


if __name__ == "__main__":
    import unittest
    unittest.main()
