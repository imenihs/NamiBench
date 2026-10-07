"""Qt の校正の画面（設計K §4.1〜§4.5。ui_qt/cal_dialog.py）。

- メニュー「校正」の項目は使える。開いた窓の題は項目の名前（U-24）。何を校正するかを変えると題も変わる
- 繋がっていなければ「次へ」を無効にして理由（入口で止める: U-02・U-08）
- 周波数特性: 次へ → 測る（ボタンと Enter）→ 一覧に値と「測った」→ 飛ばす → 確かめる（表とグラフ）→ 保存
- 差が大きい点は確かめて入れる。測っている間は進み具合とやめる（U-19）。測れなかった理由を手順のそばに（U-25）
- 測った値があるのに閉じる・切り替えるときは聞く。測った点だけ保存して閉じることも選べる（U-18）
- RBW・NF の増幅度・旧校正ファイル（測った値と大きく違う行に、その行の注意: U-25）
- 全部の手順の画面で部品の大きさとそろい（qt_layout_check）
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from pygigast.ui_qt import cal_view, texts
from pygigast.ui_qt.cal_dialog import CHECK, MEASURE, PREPARE, SAVED
from tests.qt_layout_check import problems
from tests.test_level import build_legacy_bytes
from tests.test_ui_qt_support import UiBase

GAIN = ((0, 0.0), (12_000_000_000, -20.0))


class CalUiTest(UiBase):
    def connect_sim(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        self.dev = self.api._inst._device
        self.dev.config.freq_gain_db = GAIN

    def open(self, kind="freq"):
        self.menu_action("cal_" + kind).trigger()
        self.wait(lambda: self.win.cal_window is not None and self.win.cal_window.v is not None
                  and self.win.cal_window.v["kind"] == kind, what="校正の画面")
        return self.win.cal_window

    def wait_idle(self, d):
        self.wait(lambda: d.v is not None and not d.v["measuring"] and not self.state()["tasks"], timeout=30,
                  what="測り終わる")
        self.pump(0.2)

    def test_menu_items_open_the_screen_with_their_names(self):
        for kind in ("freq", "rbw", "nf", "legacy"):
            a = self.menu_action("cal_" + kind)
            self.assertTrue(a.isEnabled(), kind)
        d = self.open("freq")
        self.assertEqual(d.windowTitle(), texts.MENU_ITEMS["cal_freq"].rstrip("…"))
        self.assertTrue(d.kind_radios["freq"].isChecked())
        # 繋がっていないので次へは押せない（理由はツールチップ）
        self.assertFalse(d.btn_next.isEnabled())
        self.assertEqual(d.btn_next.toolTip(), texts.ERRORS["not_connected"])
        self.assertTrue(d.device_reason.isVisible())
        d.kind_radios["rbw"].click()
        self.wait(lambda: d.v["kind"] == "rbw", what="種類を変える")
        self.assertEqual(d.windowTitle(), texts.MENU_ITEMS["cal_rbw"].rstrip("…"))
        d.close()
        self.wait(lambda: self.state()["calibration_work"] is None, what="閉じたら作業も閉じる")

    def test_opens_while_sweeping(self):
        # 掃引中は作業ができる前の状態（作業なし）が次々に届く。それで窓を閉じない（窓は作業ができてから出す）
        self.connect_sim()
        self.cmd("run", mode="continuous")
        for _ in range(5):
            self.menu_action("cal_freq").trigger()
            d = self.win.cal_window
            d.apply_state({**self.win.state, "calibration_work": None})  # 作業ができる前の状態が先に届いた
            self.wait(lambda: d.isVisible() and d.v is not None, what="窓が出る")
            self.pump(0.3)
            self.assertTrue(d.isVisible())
            d.close()
            self.wait(lambda: self.state()["calibration_work"] is None, what="閉じたら作業も閉じる")
        self.cmd("stop")

    def test_frequency_response_flow(self):
        self.connect_sim()
        d = self.open("freq")
        self.assertTrue(d.btn_next.isEnabled())
        self.assertEqual(d.wiring.text(), cal_view.C["wiring_freq"])
        self.assertEqual(d.diagram.blocks, list(texts.CAL_WIRING["freq"]))
        d.btn_next.click()
        self.assertEqual(d.page, MEASURE)
        # 500MHz の点を選んで、ボタンで測る
        d.points.selectRow(5)
        self.wait(lambda: d.v["points"][5]["current"], what="点を選ぶ")
        self.assertIn("500.000MHz", d.instruction.text())
        self.dev.config.signals = [(500_000_000, -10.0)]
        d.btn_measure.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.assertTrue(d.freq_progress.isVisible())
        self.assertFalse(d.btn_measure.isEnabled())
        self.assertFalse(d.btn_back.isEnabled())
        self.wait_idle(d)
        self.assertEqual(d.points.item(5, 3).text(), cal_view.C["st_measured"])
        self.assertTrue(d.points.item(5, 2).text().startswith("+"))
        self.assertTrue(d.v["points"][6]["current"])  # 次の点へ
        # Enter でも測る（欄にいないとき）。信号が無い点は理由を手順のそばに出す
        self.dev.config.signals = []
        d.points.setFocus()
        QTest.keyClick(d, Qt.Key.Key_Return)
        self.wait(lambda: d.v["measuring"] or bool(d.v["message"]), what="Enter で測る")
        self.wait_idle(d)
        self.assertTrue(d.freq_message.isVisible())
        self.assertEqual(d.freq_message.text(), texts.ERRORS["cal_no_signal"])
        d.btn_skip.click()
        self.wait(lambda: d.v["points"][6]["status"] == "skipped", what="飛ばす")
        self.assertEqual(d.points.item(6, 3).text(), cal_view.C["st_skipped"])
        # 確かめる: 表とグラフ
        d.btn_next.click()
        self.assertEqual(d.page, CHECK)
        self.assertEqual(d.btn_next.text(), cal_view.C["save"])
        self.assertTrue(d.check_area.isVisible())
        self.assertEqual(d.check_points.rowCount(), 121)
        self.assertEqual(d.items.item(0, 0).text(), cal_view.C["item_level"])
        self.assertEqual(len(d.curve_after.getData()[0]), 121)
        found = problems(d, "確かめる")
        self.assertEqual(found, [])
        d.btn_next.click()
        self.wait(lambda: d.page == SAVED, what="保存")
        self.assertIn(".json", d.saved.text())
        self.assertEqual(len(self.api._inst._cal_store.history("SIM00001")), 1)
        self.assertEqual(d.v["unsaved"], 0)
        # 続けて校正する → 準備。保存したものが「いまの校正データ」
        d.btn_next.click()
        self.assertEqual(d.page, PREPARE)
        self.assertNotEqual(d.base.text(), cal_view.C["cal_none"])

    def test_close_asks_and_can_save_measured_points(self):
        self.connect_sim()
        d = self.open("freq")
        d.btn_next.click()
        self.dev.config.signals = [(10_000_000, -10.0)]
        d.btn_measure.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.wait_idle(d)
        asked = []
        d.ask_close = lambda: (asked.append(d.v["close_question"]), None)[1]
        d.close()
        self.pump(0.2)
        self.assertTrue(d.isVisible())  # やめる → 閉じない
        self.assertEqual(asked, [cal_view.w("lost_points", n=1)])
        d.ask_close = lambda: "save"
        d.close()
        self.wait(lambda: not d.isVisible() and self.state()["calibration_work"] is None, what="保存して閉じる")
        self.assertEqual(len(self.api._inst._cal_store.history("SIM00001")), 1)

    def test_main_window_close_says_calibration_is_lost(self):
        self.connect_sim()
        d = self.open("freq")
        d.btn_next.click()
        self.dev.config.signals = [(10_000_000, -10.0)]
        d.btn_measure.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.wait_idle(d)
        self.confirm_answer = False
        self.win.close()
        self.assertIn(cal_view.w("lost_main_points", n=1), self.confirm_asked[-1])
        self.assertTrue(self.win.isVisible())  # やめた
        self.confirm_answer = True

    def test_close_without_saving_and_escape(self):
        self.connect_sim()
        d = self.open("freq")
        d.btn_next.click()
        self.dev.config.signals = [(10_000_000, -10.0)]
        d.btn_measure.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.wait_idle(d)
        d.ask_close = lambda: "discard"
        QTest.keyClick(d, Qt.Key.Key_Escape)
        self.wait(lambda: not d.isVisible() and self.state()["calibration_work"] is None, what="保存せずに閉じる")
        self.assertEqual(self.api._inst._cal_store.history("SIM00001"), [])

    def test_switch_kind_asks_when_unsaved(self):
        self.connect_sim()
        d = self.open("freq")
        d.btn_next.click()
        self.dev.config.signals = [(10_000_000, -10.0)]
        d.btn_measure.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.wait_idle(d)
        d._back()
        self.confirm_answer = False
        d.kind_radios["nf"].click()
        self.pump(0.3)
        self.assertEqual(self.confirm_asked[-1], cal_view.w("switch_points", n=1))
        self.assertEqual(d.v["kind"], "freq")
        self.assertTrue(d.kind_radios["freq"].isChecked())
        self.confirm_answer = True
        d.kind_radios["nf"].click()
        self.wait(lambda: d.v["kind"] == "nf", what="切り替える")
        self.assertEqual(d.v["unsaved"], 0)

    def test_big_difference_is_confirmed(self):
        self.connect_sim()
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        self.cmd("import_legacy_calibration", path=str(p), serial="SIM00001")
        d = self.open("freq")
        d.btn_next.click()
        cur = next(p for p in d.v["points"] if p["current"])
        raw = self.state()["calibration_work"]["points"][cur["index"]]
        before, f = raw["before_db"], raw["measure_hz"]
        # 補正が前より 10dB 大きくなる入力（b3=1 は −1dB・受信の利得は GAIN）
        self.dev.config.signals = [(f, -20.0 - before + 1.0 + 20.0 * f / 12e9)]
        self.confirm_answer = True
        d.btn_measure.click()
        self.wait(lambda: d.v["points"][0]["status"] == "measured", timeout=30, what="確かめて入れる")
        self.assertTrue(any(cal_view.C["confirm_big"].split("{")[0] in t for t in self.confirm_asked))

    def test_pending_from_another_screen_is_asked_on_open(self):
        # Web で確かめ待ちまで進めた作業を、Qt の窓を開いたときに聞く（同じ作業を映す: K §4.6）
        self.connect_sim()
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        self.cmd("import_legacy_calibration", path=str(p), serial="SIM00001")
        work = self.cmd("cal_open", kind="freq")["calibration_work"]
        idx = work["current"]
        raw = work["points"][idx]
        before, f = raw["before_db"], raw["measure_hz"]
        self.dev.config.signals = [(f, -20.0 - before + 1.0 + 20.0 * f / 12e9)]
        self.cmd("cal_measure")
        self.wait(lambda: (self.state()["calibration_work"] or {}).get("pending") is not None, timeout=30,
                  what="確かめ待ち")
        self.confirm_answer = True
        n0 = len(self.confirm_asked)
        self.menu_action("cal_freq").trigger()
        self.wait(lambda: len(self.confirm_asked) > n0, what="開いたら聞く")
        self.assertIn(cal_view.C["confirm_big"].split("{")[0], self.confirm_asked[-1])
        self.wait(lambda: self.state()["calibration_work"].get("pending") is None, what="答えた")

    def test_rbw_nf_and_legacy_pages(self):
        self.connect_sim()
        d = self.open("rbw")
        d.btn_next.click()
        self.dev.config.rbw_gain_db = {30_000: -0.5, 15_000: -1.5}
        d.btn_rbw.click()
        self.wait(lambda: d.v["measuring"], what="RBW を測り始める")
        self.assertTrue(d.rbw_progress.isVisible())
        self.wait_idle(d)
        self.assertEqual(d.rbw_table.rowCount(), 1)
        self.assertEqual(d.rbw_table.columnCount(), 6)
        self.assertEqual(problems(d, "RBW 測る"), [])
        d.btn_next.click()
        self.assertFalse(d.check_area.isVisible())  # RBW は表だけ
        self.assertEqual(d.items.rowCount(), 2)
        self.assertEqual(problems(d, "RBW 確かめる"), [])
        self.cmd("cal_save")
        # NF の増幅度
        d.show_page(PREPARE)
        d.kind_radios["nf"].click()
        self.wait(lambda: d.v["kind"] == "nf", what="NF")
        self.assertEqual(d.windowTitle(), texts.MENU_ITEMS["cal_nf"].rstrip("…"))
        d.btn_next.click()
        self.dev.config.nf_hot_db = 4.0
        d.nf_rows[0][1].click()
        self.wait(lambda: d.v["measuring"], what="NF を測り始める")
        self.wait_idle(d)
        self.dev.config.nf_hot_db = 10.5
        d.nf_rows[1][1].click()
        self.wait(lambda: d.v["measuring"], what="NF を測り始める")
        self.wait_idle(d)
        self.assertTrue(d.nf_rows[0][2].text())
        self.assertIn("×", d.nf_result.text())
        self.assertEqual(problems(d, "NF 測る"), [])
        self.cmd("cal_save")
        # 旧校正ファイル
        d.show_page(PREPARE)
        d.kind_radios["legacy"].click()
        self.wait(lambda: d.v["kind"] == "legacy", what="旧校正ファイル")
        self.assertFalse(d.wiring_box.isVisible())  # つなぐ機器は無い
        d.btn_next.click()
        p = self.root / "gigast.txt"
        p.write_bytes(build_legacy_bytes())
        self.open_paths = [str(p)]
        d.btn_file.click()
        self.wait(lambda: d.v.get("legacy_file") == "gigast.txt", what="読み込む")
        d.btn_next.click()
        rows = {d.items.item(r, 0).text(): r for r in range(d.items.rowCount())}
        r30 = rows[cal_view.w("item_rbw", rbw="30 kHz")]
        last = d.items.columnCount() - 1
        # RBW 30kHz は測った値（+0.5dB 程度）と旧ファイル（−2dB）が大きく違う: その行に注意を出す（U-25）
        self.assertTrue(d.items.item(r30, last).text().startswith(cal_view.C["measured_warn"].split("{")[0]))
        self.assertEqual(problems(d, "旧校正ファイル 確かめる"), [])
