"""Qt 画面の PlutoSDR の受信の利得の補正（設計Q §7.6）: libiio の偽物（実測の形の利得の段差をまねた）で確かめる。

- メニュー「校正」の「PlutoSDR の利得の補正…」で校正の画面が開き、題は項目の名前（U-24）。つなぎ方の図は TX → 減衰器 → RX
- 測る: 減衰器の値を入れて「始める」→ 進み具合とやめる（U-19）→ 周波数ごとの行 → 確かめる（表とグラフ）→ 保存
- 表が無いときは利得の欄のそばに「利得の補正なし」（理由はツールチップ）。保存すると消え、状態バーの補正に日時
- TG で基準を選んでいる間は、利得の欄と「利得を合わせる」を理由付きで使えない（U-08）
- 部品の大きさとそろいは test_ui_qt_sizes の LayoutPlutoGainTest が数える
"""
from __future__ import annotations

from pygigast.core.devices.plutosdr import driver as pd
from pygigast.core.devices.plutosdr.fake import FakeIioLib
from pygigast.ui_qt import cal_view, texts
from pygigast.ui_qt.cal_dialog import CHECK, MEASURE, SAVED
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests.test_pluto_gain import pluto, tx_stopped
from tests.test_ui_qt_plutosdr import PlutoUiBase
from tests.test_ui_qt_support import UiBase

MHZ = 1_000_000
GRID = [100 * MHZ, 2400 * MHZ, 5000 * MHZ]


class PlutoGainUiBase(PlutoUiBase):
    def setUp(self):
        self.pl = pluto(signals=[(84_700_000, -100.0)])
        self.lib = FakeIioLib(self.pl)
        pd._cache = pd._SerialCache()
        UiBase.setUp(self)
        self.api._inst._pluto_gain_grid = list(GRID)

    def open_cal(self):
        self.menu_action("cal_pluto_gain").trigger()
        self.wait(lambda: self.win.cal_window is not None and self.win.cal_window.v is not None
                  and self.win.cal_window.v["kind"] == "pluto_gain", what="校正の画面")
        return self.win.cal_window

    def measure(self, d):
        d.show_page(MEASURE)
        d.btn_pluto.click()
        self.wait(lambda: d.v["measuring"], what="測り始める")
        self.wait(lambda: not d.v["measuring"] and not self.state()["tasks"], timeout=60, what="測り終わる")
        self.pump(0.2)


class TestPlutoGainCal(PlutoGainUiBase):
    def test_flow_measure_check_save(self):
        self.connect_pluto()
        rx = self.win.pluto_panel
        self.assertTrue(rx.gain_cal.isVisibleTo(rx))
        self.assertEqual(rx.gain_cal.text(), PLUTO_LABELS["gain_cal_none"])
        self.assertEqual(rx.gain_cal.toolTip(), PLUTO_LABELS["gain_cal_none_tip"])
        d = self.open_cal()
        self.assertEqual(d.windowTitle(), texts.MENU_ITEMS["cal_pluto_gain"].rstrip("…"))
        self.assertTrue(d.kind_radios["pluto_gain"].isChecked())
        self.assertEqual(d.diagram.blocks, list(texts.CAL_WIRING["pluto_gain"]))
        self.assertEqual(d.wiring.text(), cal_view.C["wiring_pluto_gain"])
        self.assertTrue(d.btn_next.isEnabled())
        d.btn_next.click()
        self.assertEqual(d.page, MEASURE)
        self.assertEqual(d.f_ext.edit.text(), "40.0 dB")
        self.assertEqual(d.pluto_table.rowCount(), len(GRID))
        d.f_ext.edit.committed.emit("35")
        self.wait(lambda: self.state()["calibration_work"]["inputs"]["ext_atten_db"] == 35.0, what="減衰器の値")
        d.btn_pluto.click()
        self.wait(lambda: d.v["measuring"] and d.pluto_progress.isVisible(), what="進み具合とやめる")
        self.assertFalse(d.btn_pluto.isEnabled())
        self.assertFalse(d.btn_next.isEnabled())
        self.assertFalse(rx.gain.isEnabled())  # 測っている間は利得を変えない
        self.wait(lambda: not d.v["measuring"] and not self.state()["tasks"], timeout=60, what="測り終わる")
        self.pump(0.3)
        self.assertTrue(tx_stopped(self.pl))
        cells = [d.pluto_table.item(0, c).text() for c in range(5)]
        self.assertEqual(cells[0], "100")
        self.assertEqual(cells[1], "−1〜73")
        self.assertEqual(cells[4], cal_view.C["st_measured"])
        self.assertIn("〜", cells[3])
        d.btn_next.click()
        self.assertEqual(d.page, CHECK)
        self.assertTrue(d.check_area.isVisible())
        self.assertEqual(d.items.item(0, 0).text(), cal_view.C["item_pluto_gain_max"])
        self.assertEqual(d.check_points.rowCount(), len(GRID))
        d.btn_next.click()  # 保存
        self.wait(lambda: d.page == SAVED, what="保存")
        self.wait(lambda: not rx.gain_cal.isVisibleTo(rx), what="印が消える")
        self.assertIn(self.state()["plutosdr"]["gain_correction"]["created_at"], texts.calibration_status(self.state()))
        d.close()

    def test_cancel_and_close_asks(self):
        self.connect_pluto()
        self.api._inst._pluto_gain_grid = [100 * MHZ, 2400 * MHZ, 5000 * MHZ, 5500 * MHZ, 5900 * MHZ]
        d = self.open_cal()
        d.show_page(MEASURE)
        d.btn_pluto.click()
        self.wait(lambda: d.v["measuring"] and d.v["unsaved"] >= 1, timeout=30, what="1 周波数を測った")
        d.pluto_progress.cancel.click()
        self.wait(lambda: not d.v["measuring"], timeout=30, what="やめた")
        self.wait(lambda: d.pluto_message.isVisible(), what="やめたことの知らせ")
        self.assertEqual(d.pluto_message.text(), texts.TASK_CANCELLED["cal_pluto_gain"])
        self.wait(lambda: tx_stopped(self.pl), what="送信が止まる")
        n = d.v["unsaved"]
        asked = []
        d.ask_close = lambda: (asked.append(d.v["close_question"]), "discard")[1]
        d.close()
        self.assertEqual(asked, [cal_view.w("lost_freqs", n=n)])
        self.wait(lambda: self.state()["calibration_work"] is None, what="閉じた")

    def test_not_for_other_device(self):
        self.cmd("connect", backend="simulator", serial="SIM00001")
        d = self.open_cal()
        self.assertFalse(d.btn_next.isEnabled())
        self.assertEqual(d.btn_next.toolTip(), texts.ERRORS["calibration_not_for_device"])
        d.close()

    def test_gain_field_locked_while_tg_reference(self):
        self.connect_pluto()
        rx = self.win.pluto_panel
        self.assertTrue(rx.gain.isEnabled())
        self.cmd("set_mode", mode="tg")
        self.cmd("plutosdr_set", tx_atten_db=-10.0)
        self.cmd("reference_take", kind="fine")
        self.wait(lambda: self.state()["plutosdr"]["gain_lock"] is not None, timeout=30, what="基準を選んだ")
        self.pump(0.3)
        self.assertFalse(rx.gain.isEnabled())
        self.assertIn(PLUTO_LABELS["gain_locked_reference"], rx.gain.toolTip())
        self.assertFalse(rx.fit_btn.isEnabled())
        self.assertIn(PLUTO_LABELS["gain_locked_reference"], rx.fit_btn.toolTip())
        self.cmd("set_mode", mode="sa")
        self.wait(lambda: rx.gain.isEnabled(), what="SA では変えられる")
