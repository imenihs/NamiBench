"""基準の値（設計E §6.7）を直したときに「その基準の比で表した値だけ」が動くことの試験（確認の1巡目の指摘の直し）。

- S11 のリターンロス（dB）・NF の縦軸で決めた XY カーソルの Y・しきい値は、TG の基準の値を直しても動かない
- ウォーターフォールのライブの行・密度・手で決めた色の範囲・過去のトレース（行をクリックした掃引）は同じだけ動く
  （単位の変更と同じく絵は変えない）
- 記録は測ったときの設定ごと再現する: 再生は記録に書いた基準の値で当て、基準の値を直しても（再生中も・開き直しても）
  変わらない。再生中は REF・しきい値も動かさない。書いていない前の版は 0 dB
"""
from __future__ import annotations

import time
import unittest

import numpy as np

from pygigast.core.instrument_playback import _recorded_value
from pygigast.core.marker.markers import Y_KIND_NF, Y_KIND_TG
from tests.instrument_helpers import InstrumentCase
from tests.test_instrument_waterfall import sweep_once, wait_state

MHZ = 1_000_000


class FollowCase(InstrumentCase):
    def take(self, kind="wide", **kw):
        self.clear_events()
        r = self.call("reference_take", kind=kind, **kw)
        n = self.wait_task(r["task_id"])
        self.assertEqual(n["code"], "task_done", n)
        self.settle()
        return n["detail"]["result"]

    def settle(self):
        """取った後の1回の掃引（すぐ測って見せる）まで終わって止まるのを待つ。"""
        end = time.monotonic() + 30
        while self.call("get_state")["run"]["state"] != "stopped" or self.call("get_state")["tasks"]:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.05)

    def db(self, n=1):
        return np.asarray(self.call("get_trace", id=n)["values"], dtype=float)

    def median(self, n=1):
        return float(np.nanmedian(self.db(n)))

    def threshold_of(self, mid):
        return next(r for r in self.call("get_state")["markers"]["markers"] if r["id"] == mid)["threshold"]["level"]


class KindsTest(FollowCase):
    def test_s11_values_do_not_move(self):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        a = self.take(name="A")["reference_id"]
        # S11 の基準で S11 の比（リターンロス dB）を出し、そこで Y としきい値を決める
        self.call("set_mode", mode="s11")
        r = self.call("reference_take", kind="fine", standard="open")
        self.wait_task(r["task_id"])
        r = self.call("reference_take", standard="short")
        self.wait_task(r["task_id"])
        self.settle()
        sweep_once(self)
        self.call("set_xy", y1=-10.0)
        self.assertEqual((self.inst._markers.xy.y1_kind, self.inst._markers.xy.y1_ref), ("db", None))
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": -10.0, "side": "below"})
        self.assertIsNone(self.inst._markers.get(m["id"]).threshold.ref)
        # TG の基準 A に戻して TG の比を出し、そこで Y2 を置く
        self.call("reference_select", id=a)
        self.call("set_mode", mode="tg")
        sweep_once(self)
        self.call("set_xy", y2=-5.0)
        self.assertEqual((self.inst._markers.xy.y2_kind, self.inst._markers.xy.y2_ref), (Y_KIND_TG, a))
        self.call("set_amplitude", ref_level=10.0)
        self.call("reference_set_value", id=a, value_db=40.0)
        st = self.call("get_state")
        self.assertEqual(st["markers"]["xy"]["y1"], -10.0)  # S11 のリターンロスで置いた Y は動かない
        self.assertEqual(st["markers"]["xy"]["y2"], 35.0)  # A の比で置いた Y は動く
        self.assertEqual(self.threshold_of(m["id"]), -10.0)  # S11 で決めたしきい値は動かない
        self.assertEqual(st["amplitude"]["ref_level"], 50.0)  # 縦軸は A の比

    def test_nf_values_do_not_move(self):
        self.connect(500 * MHZ, 600 * MHZ)
        self.inst._device.config.nf_hot_db = 11.3
        self.call("set_mode", mode="tg")
        a = self.take(name="A")["reference_id"]
        self.call("set_mode", mode="nf")
        self.call("nf_set_enr", enr_db=15.0)
        sweep_once(self)
        self.call("set_xy", y1=8.0)
        self.assertEqual(self.inst._markers.xy.y1_kind, Y_KIND_NF)
        m = self.call("marker_add", kind="M", trace=1, freq_hz=550 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": 3.0, "side": "above"})
        self.call("set_mode", mode="tg")
        sweep_once(self)
        self.call("reference_set_value", id=a, value_db=40.0)
        self.assertEqual(self.threshold_of(m["id"]), 3.0)
        self.assertEqual(self.call("get_state")["markers"]["xy"]["y1"], 8.0)
        self.call("set_mode", mode="nf")
        sweep_once(self)
        self.assertEqual(self.threshold_of(m["id"]), 3.0)

    def test_kinds_persist_in_defs(self):
        """XY の Y の基準の id としきい値の基準の id は、前回終了時の状態（マーカー表の置き方）に残る。"""
        from pygigast.core.marker import MarkerTable, ThresholdSetting
        from pygigast.core.marker.markers import Kind

        t = MarkerTable()
        t.set_xy(y1=20.0, y2=-3.0)
        t.set_y_kind("y1", Y_KIND_TG, "A")
        t.set_y_kind("y2", "db", "A")  # S11 のリターンロスは基準の id を持たない
        r = t.add(Kind.MANUAL, 1, 100 * MHZ, None)
        t.get(r.marker_id).threshold = ThresholdSetting(-3.0, "below", fmt="db", power=False, ref="A")
        back = MarkerTable()
        back.from_defs(t.to_defs())
        self.assertEqual((back.xy.y1_kind, back.xy.y1_ref, back.xy.y2_kind, back.xy.y2_ref), (Y_KIND_TG, "A", "db", None))
        self.assertEqual(back.get(r.marker_id).threshold.ref, "A")
        # 置き直した Y は種類と基準を呼ぶ側が入れ直す（前の値の種類を残さない）
        back.set_xy(y1=1.0)
        self.assertEqual((back.xy.y1_kind, back.xy.y1_ref), (None, None))

    def test_tg_threshold_is_not_converted_to_swr(self):
        """TG の比で決めたしきい値は反射の形式へ直さない（数字のまま。S11 の形式の換算は S11 の比の値だけ）。"""
        from pygigast.core.marker import ThresholdSetting

        th = ThresholdSetting(-3.0, "below", fmt="db", power=False, ref="A")
        self.assertEqual(th.level_in("swr"), -3.0)
        self.assertNotEqual(ThresholdSetting(-3.0, "below", fmt="db").level_in("swr"), -3.0)


class WaterfallFollowTest(FollowCase):
    def tg_rows(self, n=3, layout="waterfall"):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        a = self.take(name="A")["reference_id"]
        self.call("set_view", layout=layout)
        for _ in range(n):
            sweep_once(self)
        return a

    def test_rows_range_and_past_trace_move(self):
        a = self.tg_rows()
        self.call("set_view", wf_color_auto=False, wf_color_lo=-30.0, wf_color_hi=10.0)
        rows0 = self.call("waterfall_rows", max_rows=10)["rows"]
        self.assertGreaterEqual(len(rows0), 3)
        self.call("waterfall_pick", t=rows0[0]["t"])
        past0 = self.db("W.1")
        epoch0 = self.call("get_state")["waterfall"]["epoch"]
        self.call("reference_set_value", id=a, value_db=40.0)
        st = self.call("get_state")
        rows1 = self.call("waterfall_rows", max_rows=10)["rows"]
        for r0, r1 in zip(rows0, rows1):
            np.testing.assert_allclose(np.asarray(r1["values"], float), np.asarray(r0["values"], float) + 40.0,
                                       atol=1e-3)
        self.assertEqual((st["view"]["wf_color_lo"], st["view"]["wf_color_hi"]), (10.0, 50.0))
        self.assertEqual((st["waterfall"]["range"]["lo"], st["waterfall"]["range"]["hi"]), (10.0, 50.0))
        self.assertGreater(st["waterfall"]["epoch"], epoch0)  # 画面は行を取り直す
        np.testing.assert_allclose(self.db("W.1"), past0 + 40.0, atol=1e-3)
        # 行をもう一度クリックしても新しい値
        self.call("waterfall_pick", t=rows0[0]["t"])
        np.testing.assert_allclose(self.db("W.1"), past0 + 40.0, atol=1e-3)

    def test_auto_range_moves(self):
        a = self.tg_rows()
        before = self.call("get_state")["waterfall"]["range"]
        self.assertTrue(before["auto"])
        self.call("reference_set_value", id=a, value_db=-20.0)
        after = self.call("get_state")["waterfall"]["range"]
        self.assertAlmostEqual(after["lo"], before["lo"] - 20.0, places=2)
        self.assertAlmostEqual(after["hi"], before["hi"] - 20.0, places=2)

    def test_density_picture_stays(self):
        a = self.tg_rows(4, layout="density")
        self.call("set_amplitude", ref_level=10.0, db_per_div=5)
        d0 = self.call("density_get")
        self.assertGreater(int((d0["image"] > 0).sum()), 0)
        self.call("reference_set_value", id=a, value_db=40.0)
        d1 = self.call("density_get")
        self.assertEqual((d1["level_lo"], d1["level_hi"]), (d0["level_lo"] + 40.0, d0["level_hi"] + 40.0))
        # REF も行も同じだけ動き、絵は同じ（升目の境目ちょうどの値が float32 の丸めで隣へ移る数点だけは違いうる）
        self.assertEqual(d1["image"].shape, d0["image"].shape)
        self.assertLessEqual(int((d1["image"] != d0["image"]).sum()), d0["image"].size // 1000)


class PlaybackKeepsRecordedValueTest(FollowCase):
    """記録は測ったときの設定ごと再現する（2026-10-08 リードの決定）。"""

    def record(self, a_value=40.0):
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        a = self.take(name="A", value_db=a_value)["reference_id"]
        self.call("record_start")
        for _ in range(3):
            sweep_once(self)
        wait_state(self, lambda st: st["recording"].get("sweeps", 0) >= 3)
        self.call("record_stop")
        h5 = self.root / "rec.h5"
        self.wait_task(self.call("record_finish", path=str(h5))["task_id"])
        return a, h5

    def open_and_seek(self, h5, index=1):
        self.wait_task(self.call("open", paths=[str(h5)])["task_id"])
        self.seek(index)

    def seek(self, index):
        self.clear_events()
        self.call("playback_seek", index=index)
        self.wait_for(lambda k, p: k == "state" and p["playback"].get("index") == index)
        wait_state(self, lambda st: st["playback"].get("index") == index and not st["tasks"])
        time.sleep(0.2)

    def test_change_during_and_after_playback_and_reopen(self):
        a, h5 = self.record(40.0)
        m = self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
        self.call("marker_function", id=m["id"], threshold={"level": 37.0, "side": "below"})
        self.call("set_xy", y1=38.0)
        self.call("set_amplitude", ref_level=50.0)
        self.open_and_seek(h5, 1)
        self.assertLess(abs(self.median() - 40.0), 1.0)
        rows = self.call("waterfall_rows", max_rows=10)["rows"]
        self.assertLess(abs(float(np.nanmedian(np.concatenate([r["values"] for r in rows]))) - 40.0), 1.0)
        # 再生中に基準の値を直す: 再生のトレース・REF・XY・しきい値は動かない
        self.call("reference_set_value", id=a, value_db=10.0)
        self.assertLess(abs(self.median() - 40.0), 1.0)
        st = self.call("get_state")
        self.assertEqual(st["amplitude"]["ref_level"], 50.0)
        self.assertEqual(st["markers"]["xy"]["y1"], 38.0)
        self.assertEqual(self.threshold_of(m["id"]), 37.0)
        self.seek(2)
        self.assertLess(abs(self.median() - 40.0), 1.0)  # 次の掃引も記録したときの値
        rows = self.call("waterfall_rows", max_rows=10)["rows"]
        self.assertLess(abs(float(np.nanmedian(np.concatenate([r["values"] for r in rows]))) - 40.0), 1.0)
        self.call("playback_close")
        # ライブに戻ると、いまの基準の値（+10）
        sweep_once(self)
        self.assertLess(abs(self.median() - 10.0), 1.0)
        # 再生の後に直しても、開き直しても記録したときの値
        self.call("reference_set_value", id=a, value_db=-20.0)
        self.open_and_seek(h5, 1)
        self.assertLess(abs(self.median() - 40.0), 1.0)

    def test_change_before_first_open(self):
        """記録の後、初めて開く前に基準の値を直しても、記録したときの値で再生する（基準のファイルの値を使わない）。"""
        a, h5 = self.record(40.0)
        self.call("reference_set_value", id=a, value_db=10.0)
        self.open_and_seek(h5, 1)
        self.assertLess(abs(self.median() - 40.0), 1.0)
        rows = self.call("waterfall_rows", max_rows=10)["rows"]
        self.assertLess(abs(float(np.nanmedian(np.concatenate([r["values"] for r in rows]))) - 40.0), 1.0)

    def test_recorded_value_of_old_files(self):
        self.assertEqual(_recorded_value({"id": "x"}), 0.0)  # 基準の値を書いていない前の版は 0 dB
        self.assertEqual(_recorded_value({"value_db": 12.5}), 12.5)
        self.assertEqual(_recorded_value({"value_db": "x"}), 0.0)
        self.assertEqual(_recorded_value({"value_db": 500.0}), 0.0)


if __name__ == "__main__":
    unittest.main()
