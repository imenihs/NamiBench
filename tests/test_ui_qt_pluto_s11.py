"""Qt 画面の PlutoSDR のスカラー S11（設計Q §7.8）。libiio の偽物のブリッジ（tests.test_pluto_s11.bridge）を使う。

- S11 のモードのボタンが使える（PlutoSDR のプロファイルが許す）
- 信号源のタブの S11 の範囲はいまの条件だけ（FFT の機器: 欄は理由付きで無効）、「オープンを測る」のツールチップは
  受信の利得を合わせてから測ることを言う
- 「オープンを測る」→ 受信の利得を合わせてからオープンを測る: 進み具合の行は利得を合わせている段を言う・選んだ利得が
  利得の欄に出る・知らせ（状態バー）が選んだ利得を言う
- オープンを測った後は、受信の利得の欄・「利得を合わせる」・送信の減衰の欄が理由付きで使えない（基準を捨てると戻る）
- ほかは GigaSt と同じ（ショートで基準ができ、上段のボタンが次の1段）
"""
from __future__ import annotations

import threading
import time
import unittest

from pygigast.core.devices.plutosdr.fake import FakeIioLib
from pygigast.core.types import SweepDiscarded
from pygigast.ui_qt import screen_pluto, screen_s11, texts
from pygigast.ui_qt.texts_pluto import PLUTO_LABELS
from tests.test_pluto_s11 import bridge
from tests.test_ui_qt_plutosdr import PlutoUiBase

MHZ = 1_000_000


class PlutoS11UiBase(PlutoUiBase):
    def setUp(self):
        super().setUp()
        self.lib = FakeIioLib(bridge())  # 繋ぐときに読む（_PlutoSdrBackend の lib_factory）

    def s11_ready(self):
        self.connect_pluto(single=False)
        self.cmd("set_sweep", start_hz=400 * MHZ, stop_hz=402 * MHZ)
        self.cmd("plutosdr_set", tx_atten_db=-10.0, gain_db=60.0)
        self.cmd("set_mode", mode="s11")
        self.wait(lambda: (self.win.state.get("sweep") or {}).get("mode") == "s11", what="S11 モード")
        self.pump(0.3)


class TestPlutoS11Qt(PlutoS11UiBase):
    def test_mode_button_enabled(self):
        self.connect_pluto(single=False)
        self.wait(lambda: self.win.mode_buttons["s11"].isEnabled(), what="S11 のボタン")
        self.assertTrue(self.win.actions["mode_s11"].isEnabled())

    def test_open_fits_gain_then_locks_with_reasons(self):
        src = self.win.source_tab
        rx, tx = self.win.pluto_panel, self.win.source_tab.pluto_tx
        self.s11_ready()
        # 範囲はいまの条件だけ（理由はツールチップ）。オープンのツールチップは利得を合わせることを言う
        self.assertFalse(src.s11_range.isEnabled())
        self.assertEqual(src.s11_range.currentData(), "fine")
        self.assertIn(texts.ERRORS["reference_wide_fft"], src.s11_range.toolTip())
        self.assertTrue(src.s11_open.isEnabled())
        self.assertIn(texts.LABELS["s11_open_tip_pluto"], src.s11_open.toolTip())
        # オープンの前は欄を変えられる
        self.assertTrue(rx.gain.isEnabled())
        self.assertTrue(tx.atten.isEnabled())
        src.s11_open.click()
        self.wait(lambda: src.s11_short.isEnabled(), timeout=20, what="ショートを測れる")
        st = self.state()
        g = st["plutosdr"]["gain_db"]
        self.assertNotEqual(g, 60.0)
        # 選んだ利得が欄に出て、知らせ（オープンを測り終えた文）が選んだ利得を言う
        self.wait(lambda: rx.gain.value() == g, what="選んだ利得が欄に")
        want = texts.tpl("ref_open_measured_fitted", gain=f"{g:g}", before="60 dB")
        self.wait(lambda: self.win.status.note.text().endswith(want), what="選んだ利得の知らせ（前に時刻）")
        # 利得・「利得を合わせる」・送信の減衰は理由付きで使えない
        why = PLUTO_LABELS["s11_locked"]
        self.wait(lambda: not rx.gain.isEnabled() and not tx.atten.isEnabled(), what="欄が使えない")
        self.assertIn(why, rx.gain.toolTip())
        self.assertIn(why, tx.atten.toolTip())
        self.assertFalse(rx.fit_btn.isEnabled())
        self.assertIn(why, rx.fit_btn.toolTip())
        # ショートで基準ができる（GigaSt と同じ）。基準を使っている間も使えない
        src.s11_short.click()
        self.wait(lambda: (self.state()["normalization"].get("selected") or {}).get("mode") == "s11", timeout=20,
                  what="S11 の基準")
        self.pump(0.3)
        self.assertFalse(tx.atten.isEnabled())
        # 基準を捨てると戻る
        self.cmd("reference_select", id=None)
        self.wait(lambda: rx.gain.isEnabled() and tx.atten.isEnabled(), what="欄が戻る")
        self.assertEqual(tx.atten.toolTip(), PLUTO_LABELS["tx_atten_tip"])

    def test_progress_says_the_fit_step(self):
        self.s11_ready()
        started = threading.Event()

        def slow(sweep, cancel=None, progress=None):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise SweepDiscarded("cancelled", {})

        self.api._inst._device.inner.fit_gain = slow
        self.win.source_tab.s11_open.click()
        self.assertTrue(started.wait(10))
        self.wait(lambda: any(t.get("detail", {}).get("phase") == "fit_gain" for t in self.state()["tasks"]),
                  what="利得を合わせる段")
        t = self.state()["tasks"][0]
        self.assertTrue(texts.task_label(t).startswith(texts.S11_TASK_NAMES["open_fit"]))
        # 合わせている間（オープンを測り始めた時点）から送信の減衰は使えない
        self.wait(lambda: not self.win.source_tab.pluto_tx.atten.isEnabled(), what="送信の減衰が使えない")
        self.cmd("cancel_task", task_id=t["task_id"])
        self.wait(lambda: not self.state()["tasks"], what="やめた")
        self.assertIsNone(self.state()["normalization"]["s11_open"])

    def test_quick_button_takes_the_open_with_the_current_conditions(self):
        q = self.win.ref_quick
        self.s11_ready()
        self.wait(lambda: q.isVisible(), what="上段のボタン")
        self.assertEqual(q.button.text(), texts.S11_QUICK_LABELS["open"])
        self.assertEqual(screen_s11.s11_quick(self.state())["take"]["kind"], "fine")
        self.assertIn(texts.S11_QUICK_LABELS["open_tip_pluto"], q.button.toolTip())
        q.button.click()
        self.wait(lambda: q.button.text() == texts.S11_QUICK_LABELS["short"], timeout=20, what="次はショート")

    def test_panels_show_the_shared_view(self):
        self.s11_ready()
        self.cmd("reference_take", standard="open", kind="fine")
        self.wait(lambda: self.state()["normalization"]["s11_open"] is not None, timeout=20, what="オープン")
        self.pump(0.3)
        v = screen_pluto.pluto_view(self.state())
        self.assertEqual(v["gain_reason"], PLUTO_LABELS["s11_locked"])
        self.assertEqual(v["tx_atten_reason"], PLUTO_LABELS["s11_locked"])
        # TG 高調波の平均除去は S11 でも選べる
        self.assertIsNone(v["tg_harmonic"]["reason"])
        self.assertTrue(self.win.source_tab.pluto_tx.tg_harmonic.isEnabled())


if __name__ == "__main__":
    unittest.main()
