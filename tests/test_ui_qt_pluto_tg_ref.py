"""Qt 画面の PlutoSDR の TG の基準（設計Q §7.9）。libiio の偽物（TX → 損失 → RX の直結）を使う。

- 信号源のタブの「いまの条件で細かく取る」と上段の「基準を取る」のツールチップは、受信の利得を合わせてから取ることを言う
- 「基準を取る」→ 受信の利得を合わせてから基準を取る: 進み具合の行は利得を合わせている段を言う・選んだ利得が利得の欄に
  出る・知らせ（状態バー）が選んだ利得を言う
- 取り始めてから、受信の利得の欄・「利得を合わせる」・送信の減衰の欄が理由付きで使えない（基準を捨てると戻る）
"""
from __future__ import annotations

import threading
import time
import unittest

from namibench.core.devices.plutosdr.fake import FakeIioLib, FakePluto
from namibench.core.types import SweepDiscarded
from namibench.ui_qt import screen_pluto, texts
from namibench.ui_qt.texts_devices import REF_QUICK_LABELS
from namibench.ui_qt.texts_pluto import PLUTO_LABELS
from tests.test_ui_qt_plutosdr import PlutoUiBase

MHZ = 1_000_000


class PlutoTgUiBase(PlutoUiBase):
    def setUp(self):
        super().setUp()
        self.lib = FakeIioLib(FakePluto(signals=[], loss_db=10.0))  # 繋ぐときに読む（_PlutoSdrBackend の lib_factory）

    def tg_ready(self):
        self.connect_pluto(single=False)
        self.cmd("set_sweep", start_hz=400 * MHZ, stop_hz=402 * MHZ)
        self.cmd("plutosdr_set", tx_atten_db=-10.0, gain_db=60.0)
        self.cmd("set_mode", mode="tg")
        self.wait(lambda: (self.state().get("sweep") or {}).get("mode") == "tg", what="TG モード")
        self.pump(0.3)


class TestPlutoTgRefQt(PlutoTgUiBase):
    def test_take_fits_gain_then_locks_with_reasons(self):
        src = self.win.source_tab
        rx, tx = self.win.pluto_panel, src.pluto_tx
        self.tg_ready()
        # ツールチップは利得を合わせてから取ることを言う（信号源のタブと上段）
        self.assertTrue(src.take_fine.isEnabled())
        self.assertIn(texts.LABELS["take_fine_tip_pluto"], src.take_fine.toolTip())
        self.wait(lambda: self.win.ref_quick.isVisible(), what="上段のボタン")
        self.assertIn(REF_QUICK_LABELS["tip_pluto"], self.win.ref_quick.button.toolTip())
        # 取る前は欄を変えられる
        self.assertTrue(rx.gain.isEnabled())
        self.assertTrue(tx.atten.isEnabled())
        notes = []
        show = self.win.status.show_note
        self.win.status.show_note = lambda text, folder=None: (notes.append(text), show(text, folder))[1]
        src.take_fine.click()
        self.wait(lambda: (self.state()["normalization"].get("selected") or {}).get("mode") == "tg", timeout=20,
                  what="TG の基準")
        st = self.state()
        g = st["plutosdr"]["gain_db"]
        self.assertNotEqual(g, 60.0)
        name = st["normalization"]["selected"]["name"]
        # 選んだ利得が欄に出て、知らせ（基準を取り終えた文）が選んだ利得を言う
        self.wait(lambda: rx.gain.value() == g, what="選んだ利得が欄に")
        want = texts.tpl("ref_taken_fitted", gain=f"{g:g}", before="60 dB", name=name)
        # 状態バーの知らせは、後に続く基準の状態の知らせで置き換わるので、出した知らせを全部見る
        self.wait(lambda: any(want in n for n in notes), what="選んだ利得の知らせ")
        fitted = texts.tpl("tg_ref_gain_fitted", gain=f"{g:g}", before="60 dB")
        self.assertTrue(any(fitted in n for n in notes), notes)
        # 利得・「利得を合わせる」・送信の減衰は理由付きで使えない
        why = PLUTO_LABELS["tg_locked"]
        self.wait(lambda: not rx.gain.isEnabled() and not tx.atten.isEnabled(), what="欄が使えない")
        self.assertIn(why, rx.gain.toolTip())
        self.assertIn(why, tx.atten.toolTip())
        self.assertFalse(rx.fit_btn.isEnabled())
        self.assertIn(why, rx.fit_btn.toolTip())
        # 基準を捨てると戻る
        self.cmd("reference_select", id=None)
        self.wait(lambda: rx.gain.isEnabled() and tx.atten.isEnabled(), what="欄が戻る")
        self.assertEqual(tx.atten.toolTip(), PLUTO_LABELS["tx_atten_tip"])

    def test_progress_says_the_fit_step(self):
        self.tg_ready()
        started = threading.Event()

        def slow(sweep, cancel=None, progress=None):
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            raise SweepDiscarded("cancelled", {})

        self.api._inst._device.inner.fit_gain = slow
        self.win.source_tab.take_fine.click()
        self.assertTrue(started.wait(10))
        self.wait(lambda: any(t.get("detail", {}).get("phase") == "fit_gain" for t in self.state()["tasks"]),
                  what="利得を合わせる段")
        t = self.state()["tasks"][0]
        self.assertTrue(texts.task_label(t).startswith(PLUTO_LABELS["ref_fit_task"]))
        # 合わせている間（基準を取り始めた時点）から送信の減衰は使えない
        self.wait(lambda: not self.win.source_tab.pluto_tx.atten.isEnabled(), what="送信の減衰が使えない")
        self.assertIn(PLUTO_LABELS["tg_locked"], self.win.source_tab.pluto_tx.atten.toolTip())
        self.cmd("cancel_task", task_id=t["task_id"])
        self.wait(lambda: not self.state()["tasks"], what="やめた")
        self.wait(lambda: self.win.source_tab.pluto_tx.atten.isEnabled(), what="やめたら戻る")
        self.assertIsNone(self.state()["normalization"]["selected"])

    def test_panels_show_the_shared_view(self):
        self.tg_ready()
        self.cmd("reference_take", kind="fine")
        self.wait(lambda: self.state()["normalization"].get("selected") is not None, timeout=20, what="基準")
        self.pump(0.3)
        v = screen_pluto.pluto_view(self.state())
        self.assertEqual(v["gain_reason"], PLUTO_LABELS["tg_locked"])
        self.assertEqual(v["tx_atten_reason"], PLUTO_LABELS["tg_locked"])
        self.assertEqual(v["fit_reason"], PLUTO_LABELS["tg_locked"])


if __name__ == "__main__":
    unittest.main()
