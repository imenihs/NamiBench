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

from PySide6.QtGui import QFont, QFontMetrics

from pygigast.api.local import LocalApi, _SimulatorBackend
from pygigast.api.web import access as acc
from pygigast.core.devices.base import load_profile
from pygigast.core.instrument import GIGAST_PROFILE_PATH, Instrument
from pygigast.ui_qt import device_menu, sizes, texts
from pygigast.ui_qt.files import UnfinishedDialog
from tests.device_helpers import FakeGigaBackend
from tests.qt_layout_check import problems
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


if __name__ == "__main__":
    unittest.main()


class TopBarOneRowTest(UiBase):
    """上段は SA の 1600 幅・TG（直結を 0dB にするとその基準の文が出る）の 1920 幅で1段に収まる（RBW 以降を2行目へ
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
        self.wait(lambda: self.win.ref_quick.isVisible(), what="直結を 0dB にする")
        self.assertTrue(self.one_row(), self.win.topbar.needed_one_row())

    def test_widest_top_frequency_fits(self):
        e = self.win.f_center.edit
        self.pump(0.2)
        for t in sizes.TOP_FREQ_SAMPLES:
            room = e.width() - 2 - 12 - 2 * sizes.EDIT_INNER
            self.assertLessEqual(e.fontMetrics().horizontalAdvance(t), room, t)
