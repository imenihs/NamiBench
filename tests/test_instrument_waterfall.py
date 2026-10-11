"""設計P の本体の動き（ウォーターフォールの行・過去のトレース・密度・表示の設定・カラーマップ・バンドプラン・記録の再生）。

シミュレータで掃引し、命令とイベントで確かめる（Qt・Web は同じ命令とイベントを使う）。
"""
from __future__ import annotations

import json
import time

import numpy as np

from pygigast.core.instrument import CommandError
from tests.instrument_helpers import InstrumentCase
from tests.test_instrument_s11 import S11Case

MHZ = 1_000_000


def sweep_once(case):
    """単発を1回（イベントを消さない: InstrumentCase.single は消す）。"""
    n0 = case.call("get_state")["run"]["sweep_count"]
    case.call("run", mode="single")
    end = time.monotonic() + 30
    while True:
        st = case.call("get_state")
        if st["run"]["state"] == "stopped" and st["run"]["sweep_count"] > n0:
            return st
        if time.monotonic() > end:
            case.fail("単発が終わらない")
        time.sleep(0.02)


def wait_state(case, pred, timeout=30):
    end = time.monotonic() + timeout
    while True:
        st = case.call("get_state")
        if pred(st):
            return st
        if time.monotonic() > end:
            case.fail("状態にならない")
        time.sleep(0.05)


class WaterfallLiveTest(InstrumentCase):
    def sweeps(self, n=3):
        for _ in range(n):
            sweep_once(self)

    def test_rows_events_and_rows_command(self):
        self.connect()
        self.clear_events()
        self.sweeps(3)
        evs = [p for k, p in self.events if k == "waterfall" and p.get("rows")]
        self.assertEqual(len(evs), 3)  # 1掃引＝1行（P §3）
        r0 = evs[0]["rows"][0]
        self.assertEqual(r0["start_hz"], 50 * MHZ)
        self.assertEqual(r0["values"].dtype, np.float32)
        self.assertEqual(evs[-1]["seq"] - evs[0]["seq"], 2)
        self.assertIn("lo", evs[-1]["range"])
        st = self.call("get_state")
        self.assertEqual(st["waterfall"]["rows"], 3)
        self.assertEqual(st["waterfall"]["limit"], 2000)
        r = self.call("waterfall_rows", max_rows=2)
        self.assertEqual(len(r["rows"]), 2)  # 最新に付いていく表示: 新しい2行を1掃引ずつ
        self.assertEqual(r["rows"][-1]["t"], evs[-1]["rows"][0]["t"])
        r = self.call("waterfall_rows", max_rows=1, t_lo=evs[0]["rows"][0]["t"], t_hi=evs[-1]["rows"][0]["t"])
        self.assertEqual(len(r["rows"]), 1)
        self.assertEqual(r["rows"][0]["sweeps"], 3)  # 範囲を選んだら最大値でまとめる（P §8）
        with self.assertRaises(CommandError):
            self.call("waterfall_rows", max_rows=0)

    def test_pick_shows_past_trace_and_close(self):
        self.connect()
        self.sweeps(2)
        r = self.call("waterfall_rows", max_rows=5)
        t = r["rows"][0]["t"]
        self.clear_events()
        res = self.call("waterfall_pick", t=t)
        self.assertEqual(res["file_id"], "W")
        st = self.call("get_state")
        f = [x for x in st["files"] if x["file_id"] == "W"][0]
        self.assertEqual(f["kind"], "past")
        self.assertEqual(f["t"], t)
        tr = self.wait_for(lambda k, p: k == "trace" and p.get("trace") == "W.1")
        self.assertEqual(tr["source"], "file")
        np.testing.assert_allclose(tr["values"], r["rows"][0]["values"], atol=1e-3)  # 行の値がそのまま過去のトレース
        self.call("close_file", file_id="W")
        self.assertFalse(any(x["file_id"] == "W" for x in self.call("get_state")["files"]))

    def test_trace_choice_and_rows_limit(self):
        self.connect()
        self.call("set_view", wf_trace=2, wf_rows=500)
        st = self.call("get_state")
        self.assertEqual(st["view"]["wf_trace"], 2)
        self.assertEqual(st["waterfall"]["limit"], 500)
        for bad in ({"wf_trace": 5}, {"wf_rows": 123}, {"density_half": 7}, {"layout": "both"}, {"wf_color_auto": 1}):
            with self.assertRaises(CommandError, msg=bad):
                self.call("set_view", **bad)

    def test_manual_range_order_and_unit_shift(self):
        self.connect()
        self.single()
        self.call("set_view", wf_color_auto=False, wf_color_lo=-100.0, wf_color_hi=-40.0)
        with self.assertRaises(CommandError) as cm:
            self.call("set_view", wf_color_lo=-39.5)
        self.assertEqual(cm.exception.code, "wf_range_order")
        self.assertEqual(cm.exception.field, "wf_color_lo")
        self.single()
        self.call("set_view", level_unit="dBuV")
        v = self.call("get_state")["view"]
        # 単位を換えたら手で決めた色の範囲も同じだけずらす（設計O §1.4: 絵は変わらず数字だけ新しい単位）
        self.assertAlmostEqual(v["wf_color_lo"], -100.0 + 106.99, places=1)
        self.assertAlmostEqual(v["wf_color_hi"], -40.0 + 106.99, places=1)
        self.assertFalse(self.call("get_state")["waterfall"]["range"]["auto"])

    def test_colormap_in_palette_and_undo(self):
        r = self.call("palette_set", colormap="magma")
        self.assertEqual(r["palette"]["colormap"], "magma")
        self.assertIn("colormap", r["palette"]["changed"])
        with self.assertRaises(CommandError):
            self.call("palette_set", colormap="rainbow")
        self.call("undo")
        self.assertEqual(self.call("get_state")["palette"]["colormap"], "viridis")
        self.call("palette_set", colormap="gray")
        self.call("palette_reset", base="light")
        self.assertEqual(self.call("get_state")["palette"]["colormap"], "viridis")  # 初期値に戻す（J §4.4）

    def test_density_only_in_density_layout(self):
        self.connect()
        self.clear_events()
        self.single()
        self.assertFalse(any(k == "density" for k, _p in self.events))
        self.call("set_view", layout="density", density_half=5)
        self.clear_events()
        self.single()
        d = self.wait_for(lambda k, p: k == "density")
        self.assertEqual(d["image"].shape, (200, d["cols"]))  # 縦は REF から 10 目盛を 0.5dB 刻み（P §9.3）
        self.assertEqual(d["half"], 5)
        self.assertGreater(int((d["image"] > 0).sum()), 0)
        # 縦軸を変えたら作り直す
        self.clear_events()
        self.call("set_amplitude", db_per_div=5)
        d2 = self.wait_for(lambda k, p: k == "density")
        self.assertEqual(d2["image"].shape[0], 100)
        g = self.call("density_get")
        self.assertEqual(g["image"].shape[0], 100)

    def test_peak_list_has_leak_hook(self):
        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.single()
        r = self.call("peak_list", trace=1, floor_margin_db=10.0)
        for p in r["peaks"]:
            self.assertIn("leak", p)
            self.assertIsNone(p["leak"])  # 印を付けるのは設計L §1.4（いまは口だけ）


class BandplanCommandTest(InstrumentCase):
    def test_load_bookmarks_save(self):
        src = self.root / "my.json"
        src.write_text(json.dumps({"format": "pygigast-bandplan", "version": 1,
                                   "bands": [{"name": "FM", "start_hz": 76 * MHZ, "stop_hz": 95 * MHZ, "color": "#4fa3ff"}],
                                   "bookmarks": []}, ensure_ascii=False), encoding="utf-8")
        self.call("bandplan_load", path=str(src))
        st = self.call("get_state")
        self.assertEqual(st["bandplan"]["bands"][0]["name"], "FM")
        self.assertEqual(st["bandplan"]["source"], "my.json")
        self.call("bookmark_add", name="ビーコン", hz=434_500_000)
        self.call("bookmark_add", name="A", hz=100 * MHZ)
        self.assertEqual([m["hz"] for m in self.call("get_state")["bandplan"]["bookmarks"]], [100 * MHZ, 434_500_000])
        self.call("bookmark_remove", hz=100 * MHZ, name="A")
        with self.assertRaises(CommandError) as cm:
            self.call("bookmark_remove", hz=1)
        self.assertEqual(cm.exception.code, "unknown_bookmark")
        out = self.root / "out.json"
        self.call("bandplan_save", path=str(out))
        d = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(d["bookmarks"], [{"name": "ビーコン", "hz": 434_500_000}])
        # 次の起動でも同じ中身（アプリのデータに写して持つ）
        self.inst.shutdown()
        self.inst = self.make_instrument()
        self.assertEqual(self.call("get_state")["bandplan"]["bookmarks"][0]["name"], "ビーコン")

    def test_bad_file_keeps_current(self):
        bad = self.root / "bad.json"
        bad.write_text('{"format": "pygigast-bandplan", "version": 9}', encoding="utf-8")
        with self.assertRaises(CommandError) as cm:
            self.call("bandplan_load", path=str(bad))
        self.assertEqual(cm.exception.code, "bandplan_unsupported_version")
        with self.assertRaises(CommandError) as cm:
            self.call("bookmark_add", name="  ", hz=1)
        self.assertEqual(cm.exception.code, "bandplan_bad_name")
        self.assertEqual(self.call("get_state")["bandplan"]["bands"], [])


class PlaybackTest(InstrumentCase):
    def record(self, n=6):
        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.call("record_start")
        for _ in range(n):
            sweep_once(self)
        wait_state(self, lambda st: st["recording"].get("sweeps", 0) >= n)
        self.call("record_stop")
        h5 = self.root / "rec.h5"
        r = self.call("record_finish", path=str(h5))
        self.wait_task(r["task_id"])
        return h5

    def open(self, *paths):
        self.clear_events()
        r = self.call("open", paths=[str(p) for p in paths])
        return self.wait_task(r["task_id"])

    def test_open_record_plays_and_back_to_live(self):
        h5 = self.record(6)
        self.call("run", mode="continuous")
        done = self.open(h5)
        res = done["detail"]["result"]
        self.assertEqual(res["playback"]["sweeps"], 6)
        self.assertTrue(res["playback"]["live_stopped"])  # 止めたことを知らせる（黙って変えない）
        self.assertTrue(res["playback"]["layout_changed"])
        st = self.call("get_state")
        pb = st["playback"]
        self.assertTrue(pb["active"])
        self.assertEqual((pb["total"], pb["index"]), (6, 0))
        self.assertEqual(st["view"]["layout"], "waterfall")
        self.assertEqual(st["run"]["state"], "stopped")
        self.assertEqual(pb["step_hz"], st["playback"]["segments"][0]["step_hz"])
        # 再生中はライブの掃引・モードの切り替えを断る（P §9.4）
        for name, kw in (("run", {"mode": "continuous"}), ("set_mode", {"mode": "tg"}),
                         ("set_sweep", {"rbw_hz": 30_000})):
            with self.assertRaises(CommandError) as cm:
                self.call(name, **kw)
            self.assertEqual(cm.exception.code, "playback_active", name)
        # カーソルを動かすとその掃引をトレースに通す
        self.clear_events()
        self.call("playback_seek", index=4)
        self.wait_for(lambda k, p: k == "trace" and p.get("trace") == 1)
        self.assertEqual(self.call("get_state")["playback"]["index"], 4)
        # 記録の行（ウォーターフォールの全体）
        r = self.call("waterfall_rows", max_rows=3)
        self.assertEqual(r["source"], "file")
        self.assertLessEqual(len(r["rows"]), 3)
        self.assertEqual(sum(x["sweeps"] for x in r["rows"]), 6)
        # 時刻で選ぶ（行のクリック）
        t0 = pb["t_first"]
        self.call("waterfall_pick", t=t0)
        self.wait_for(lambda k, p: k == "state" and p["playback"].get("index") == 0)
        # ライブに戻る
        self.clear_events()
        self.call("playback_close")
        st = self.call("get_state")
        self.assertFalse(st["playback"]["active"])
        self.assertTrue(any(k == "notice" and p["code"] == "playback_closed" for k, p in self.events))

    def test_play_to_end_and_speed(self):
        h5 = self.record(5)
        self.open(h5)
        with self.assertRaises(CommandError):
            self.call("playback_speed", speed=3)
        self.call("playback_play", speed=30)
        self.wait_for(lambda k, p: k == "notice" and p["code"] == "playback_end", timeout=30)
        st = self.call("get_state")
        self.assertEqual(st["playback"]["index"], 4)
        self.assertFalse(st["playback"]["playing"])
        # 終わりまで出した後の再生は始めから
        self.call("playback_play")
        self.assertTrue(self.call("get_state")["playback"]["playing"])
        self.call("playback_pause")
        self.assertFalse(self.call("get_state")["playback"]["playing"])

    def test_view_zoom_limited_by_record(self):
        h5 = self.record(3)
        self.open(h5)
        st = self.call("get_state")
        seg = st["playback"]["segments"][0]
        r = self.call("set_sweep", center_hz=100 * MHZ, span_hz=10)
        self.assertTrue(r["playback"])
        span = r["adopted"]["span_hz"]
        self.assertEqual(span, seg["step_hz"] * 10)  # 保存時の Step より細かくしない（H §3.1）
        self.assertEqual(r["adjustments"][0]["reason"], "record_resolution")
        r = self.call("set_sweep", start_hz=10 * MHZ, stop_hz=120 * MHZ)
        self.assertEqual(r["adopted"]["start_hz"], seg["start_hz"])
        self.assertIn("record_range", [a["reason"] for a in r["adjustments"]])
        v = self.call("get_state")["playback"]["view"]
        self.assertEqual(v["start_hz"], seg["start_hz"])
        # ライブの掃引条件は変わらない（測り直さない）
        self.assertEqual(self.call("get_state")["sweep"]["start_hz"], 50 * MHZ)

    def test_save_extract_and_export_during_playback(self):
        from pygigast.core.storage.h5file import H5Record
        from tests.instrument_helpers import SERIAL

        with self.assertRaises(CommandError) as cm:
            self.call("playback_extract", path=str(self.root / "x.h5"))
        self.assertEqual(cm.exception.code, "not_playing")
        h5 = self.record(6)
        self.open(h5)
        st = self.call("get_state")
        t0, t1 = st["playback"]["t_first"], st["playback"]["t_last"]
        # 切り出し（H §3.1）: 時間の前半・いまの表示の周波数の範囲
        self.call("set_sweep", start_hz=80 * MHZ, stop_hz=150 * MHZ)
        cut = self.root / "cut.h5"
        done = self.wait_task(self.call("playback_extract", path=str(cut), t_lo=t0, t_hi=(t0 + t1) / 2)["task_id"])
        self.assertEqual(done["code"], "task_done")
        with H5Record(cut) as h:
            n = sum(s.n_sweeps for s in h.segments)
            self.assertTrue(1 <= n < 6, n)
            s = h.segments[0]
            self.assertGreaterEqual(s.start_hz, 80 * MHZ)
            self.assertLessEqual(s.start_hz + s.step_hz * (s.points - 1), 150 * MHZ)
        # 記録の CSV（H §5・§7.3）: 1行＝1掃引
        out = self.root / "rec.csv"
        done = self.wait_task(self.call("playback_export_csv", path=str(out))["task_id"])
        self.assertEqual(done["code"], "task_done")
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("# meta:"))
        self.assertIn('"source":"rec.h5"', lines[0])
        self.assertEqual(len(lines), 2 + 6)
        # 再生中の保存は記録の機器・掃引の生の値で（M-08）
        snap = self.root / "snap.h5"
        self.wait_task(self.call("save", path=str(snap))["task_id"])
        with H5Record(snap) as h:
            self.assertEqual(h.header.device_serial, SERIAL)
            self.assertEqual(h.segments[0].n_sweeps, 1)

    def test_second_record_refused_and_reference_traces_still_open(self):
        h5 = self.record(3)
        snap = self.root / "snap.h5"
        r = self.call("save", path=str(snap))
        self.wait_task(r["task_id"])
        h5b = self.root / "rec2.h5"
        h5b.write_bytes(h5.read_bytes())
        done = self.open(h5, h5b, snap)
        res = done["detail"]["result"]
        self.assertEqual(res["errors"][0]["code"], "playback_one_at_a_time")
        self.assertEqual(len(res["files"]), 1)  # 1回分のファイルは今までどおり参照トレース
        self.assertTrue(self.call("get_state")["playback"]["active"])

    def test_rows_older_than_history_come_from_recording(self):
        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.call("record_start")
        for _ in range(4):
            sweep_once(self)
        st = wait_state(self, lambda s: s["recording"].get("sweeps", 0) >= 4)
        first = st["waterfall"]["t_first"]
        # 画面に持つ行の上限を超えた古い行（P §3）: 上限を小さくして試す
        self.inst._q.put(("call", lambda: self.inst._wf_history.set_limit(2)))
        sweep_once(self)
        st = self.call("get_state")
        self.assertLess(st["waterfall"]["t_first"] or 0, first + 1e9)
        r = self.call("waterfall_rows", max_rows=50, t_lo=first - 1.0, t_hi=time.time() + 10)
        self.assertGreaterEqual(len(r["rows"]), 4)  # 記録の作業ファイルから読み直した行を足す
        self.assertEqual(r["rows"][0]["t"], first)
        res = self.call("waterfall_pick", t=first)
        self.assertEqual(res["t"], first)
        self.call("record_stop")

    def test_window_older_than_held_rows_ends_at_t_hi(self):
        # 持っている行より古い範囲だけを選んだら、ファイルから読むのは選んだ範囲の終わりまで（P §3・§5）
        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.call("record_start")
        for _ in range(8):
            sweep_once(self)
        wait_state(self, lambda s: s["recording"].get("sweeps", 0) >= 8)
        self.inst._q.put(("call", lambda: self.inst._wf_history.set_limit(2)))
        for _ in range(2):
            sweep_once(self)
        wait_state(self, lambda s: s["recording"].get("sweeps", 0) >= 10)
        st = self.call("get_state")
        allrows = self.call("waterfall_rows", max_rows=50, t_lo=st["waterfall"]["t_first"] - 100,
                            t_hi=time.time() + 10)
        ts = [x["t"] for x in allrows["rows"]]
        self.assertEqual(len(ts), 10)
        r = self.call("waterfall_rows", max_rows=50, t_lo=ts[2] - 1e-3, t_hi=ts[4] + 1e-3)
        self.assertEqual([x["t"] for x in r["rows"]], ts[2:5])
        self.call("record_stop")

    def run_in_control(self, fn):
        """制御スレッドで fn を呼び、結果か例外を返す（命令の形を持たない道を試すため）。"""
        box: list = []
        self.inst._q.put(("call", lambda: box.append(self._try(fn))))
        end = time.monotonic() + 10
        while not box:
            self.assertLess(time.monotonic(), end)
            time.sleep(0.02)
        return box[0]

    @staticmethod
    def _try(fn):
        try:
            return fn()
        except CommandError as e:
            return e

    def test_playback_never_changes_live_conditions(self):
        # 再生中はライブの条件を変えない（設計P §5・H §3.1）。どの道から来ても _apply・_set_adopted で止まる
        from pygigast.core.types import SweepRequest

        h5 = self.record(4)
        self.call("set_mode", mode="tg")
        self.call("preset_save", name="tgp")
        self.call("set_mode", mode="sa")
        live = dict(self.call("get_state")["sweep"])
        self.open(h5)
        self.call("playback_seek", index=1)
        wait_state(self, lambda s: s["playback"]["index"] == 1)
        # マーカーの Center に → 再生の表示の範囲だけ（ライブの範囲は変わらない）
        self.call("set_sweep", start_hz=80 * MHZ, stop_hz=130 * MHZ)
        m = self.call("marker_add", kind="M", trace=1, freq_hz=120 * MHZ)
        r = self.call("marker_to", id=m["id"], target="center")
        self.assertTrue(r.get("playback"))
        st = self.call("get_state")
        view = st["playback"]["view"]
        self.assertEqual(view["start_hz"] + (view["stop_hz"] - view["start_hz"]) // 2,
                         st["markers"]["markers"][0]["freq_hz"])
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (live["start_hz"], live["stop_hz"]))
        # プリセットの呼び出し・条件を戻す元に戻すは理由付きで断る
        pid = self.call("preset_list")["presets"][0]["id"]
        with self.assertRaises(CommandError) as cm:
            self.call("preset_load", id=pid)
        self.assertEqual(cm.exception.code, "playback_active")
        self.assertEqual(st["undo"]["undo_blocked"], "playback_active")  # 画面は元に戻すを理由付きで無効にする
        with self.assertRaises(CommandError) as cm:
            self.call("undo")
        self.assertEqual(cm.exception.code, "playback_active")
        for name, kw in (("set_mode", {"mode": "tg"}), ("set_sweep", {"rbw_hz": 30_000}),
                         ("set_sweep", {"span_lock": True}), ("set_sweep", {"points_auto": True})):
            with self.assertRaises(CommandError, msg=name) as cm:
                self.call(name, **kw)
            self.assertEqual(cm.exception.code, "playback_active")
        # 外のソフト（rtl_tcp）・自動で条件を動かす道（view_in_playback なし）も断る。最後の関所の _set_adopted も
        req = SweepRequest(center_hz=100 * MHZ, edited=("center_hz",))
        self.assertEqual(self.run_in_control(lambda: self.inst._apply(req)).code, "playback_active")
        a = self.inst._adopted
        self.assertEqual(self.run_in_control(lambda: self.inst._set_adopted(a)).code, "playback_active")
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["mode"], st["sweep"]["start_hz"], st["sweep"]["stop_hz"]),
                         (live["mode"], live["start_hz"], live["stop_hz"]))
        # ライブに戻っても前の条件のまま
        self.call("playback_close")
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["mode"], st["sweep"]["start_hz"], st["sweep"]["stop_hz"]),
                         (live["mode"], live["start_hz"], live["stop_hz"]))
        self.assertIsNone(st["undo"]["undo_blocked"])

    def test_marker_delta_to_span_in_playback_keeps_span_lock(self):
        """再生中の「デルタの2点を Start・Stop に」（設計C §5.13）: Span 固定が ON でも OFF でも表示の範囲を2点にし、
        固定（ライブの掃引の条件）は変えず、「Span 固定を外しました」も出さない。"""
        h5 = self.record(3)
        for lock in (True, False):
            self.call("set_sweep", span_lock=lock)
            live = dict(self.call("get_state")["sweep"])
            self.open(h5)
            self.call("marker_add", kind="M", trace=1, freq_hz=80 * MHZ)
            self.call("marker_add", kind="M", trace=1, freq_hz=150 * MHZ)
            self.call("marker_set", id="M2", ref="M1")
            marks = {m["id"]: m["freq_hz"] for m in self.call("get_state")["markers"]["markers"]}
            lo, hi = sorted((marks["M1"], marks["M2"]))
            r = self.call("marker_to", id="M2", target="span")
            self.assertTrue(r.get("playback"), lock)
            self.assertEqual((r["adopted"]["start_hz"], r["adopted"]["stop_hz"]), (lo, hi))
            self.assertFalse([a for a in r["adjustments"] if a["field"] == "span_lock"])
            st = self.call("get_state")
            self.assertEqual((st["playback"]["view"]["start_hz"], st["playback"]["view"]["stop_hz"]), (lo, hi))
            self.assertEqual(st["sweep"]["span_lock_hz"], live["span_lock_hz"], lock)
            self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (live["start_hz"], live["stop_hz"]))
            self.call("marker_clear")
            self.call("playback_close")

    def test_auto_color_range_is_decided_from_rows_shown(self):
        """再生中の色の範囲の「自動」（設計P §9.2）: ウォーターフォールに出している行の全体で決め、カーソルを動かしても
        変えない。時間方向に選んだときはその範囲の行で決め直す。記録の前半（50〜200MHz）と後半（SG の 500MHz を含む
        300〜2100MHz）で最大が違う形にする。"""
        from pygigast.core import waterfall as wf

        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.call("record_start")
        for _ in range(3):
            sweep_once(self)
        self.call("set_sweep", start_hz=300 * MHZ, stop_hz=2100 * MHZ)
        self.call("set_mode", mode="sg", sg_hz=500 * MHZ)
        for _ in range(3):
            sweep_once(self)
        wait_state(self, lambda st: st["recording"].get("sweeps", 0) >= 6)
        self.call("record_stop")
        h5 = self.root / "rec.h5"
        self.wait_task(self.call("record_finish", path=str(h5))["task_id"])
        self.open(h5)
        r = self.call("waterfall_rows", max_rows=100)
        want = wf.range_of(np.concatenate([row["values"] for row in r["rows"]]))
        first = wf.range_of(r["rows"][0]["values"])
        self.assertNotEqual(want, first)  # カーソルの1掃引（始め）で決めた範囲とは違う
        self.assertEqual((r["range"]["lo"], r["range"]["hi"]), want)
        self.assertTrue(r["range"]["auto"])
        for index in (5, 0, 3):
            self.call("playback_seek", index=index)
            st = wait_state(self, lambda st, i=index: st["playback"]["index"] == i)
            self.assertEqual((st["waterfall"]["range"]["lo"], st["waterfall"]["range"]["hi"]), want, index)
        # 時間方向に前半だけを選ぶと、その行で決め直す
        t_hi = r["rows"][2]["t"]
        r2 = self.call("waterfall_rows", max_rows=100, t_lo=r["rows"][0]["t"], t_hi=t_hi)
        self.assertEqual(len(r2["rows"]), 3)
        want2 = wf.range_of(np.concatenate([row["values"] for row in r2["rows"]]))
        self.assertEqual((r2["range"]["lo"], r2["range"]["hi"]), want2)
        st = self.call("get_state")
        self.assertEqual((st["waterfall"]["range"]["lo"], st["waterfall"]["range"]["hi"]), want2)

    def test_density_in_playback_is_read_from_the_recording(self):
        """再生中の密度（設計P §9.3）: 元は記録の中のカーソルの時刻までの直近の掃引（重みが 1/256 になるまで = 半分に
        なる回数の 8 倍）を記録から読む。カーソルを飛ばしても、通過した掃引ではなくカーソルまでの掃引になり、何秒分かは
        記録の時刻から出す。"""
        from pygigast.core import waterfall as wf
        from pygigast.core.storage.record_source import RecordSource

        half = 5
        n = wf.DENSITY_REPLAY_HALVES * half
        self.connect(start_hz=50 * MHZ, stop_hz=200 * MHZ)
        self.call("record_start")
        self.call("run", mode="continuous")
        wait_state(self, lambda st: st["recording"].get("sweeps", 0) >= n + 6, timeout=60)
        self.call("stop")
        self.call("record_stop")
        h5 = self.root / "rec.h5"
        self.wait_task(self.call("record_finish", path=str(h5))["task_id"])
        src = RecordSource(h5)
        times = np.concatenate([src.times(i) for i, sg in enumerate(src.segments) if sg.n_sweeps])
        src.close()
        self.open(h5)
        self.call("set_view", layout="density", density_half=half)

        def density_at(index, sweeps):
            self.call("playback_seek", index=index)
            end = time.monotonic() + 10
            while True:
                d = self.call("density_get")
                if d.get("image") is not None and d.get("sweeps") == sweeps:
                    return d
                self.assertLess(time.monotonic(), end, (index, d.get("sweeps")))
                time.sleep(0.05)

        # 始め（index 0）から終わりへ飛ばす: カーソルまでの直近 n 掃引（通過した2掃引ではない）
        last = len(times) - 1
        d = density_at(last, n)
        self.assertEqual(d["half"], half)
        win = times[last - n + 1:last + 1]
        self.assertAlmostEqual(d["interval_s"], wf.sweep_interval_s(list(win)))
        self.assertAlmostEqual(d["seconds"], half * wf.sweep_interval_s(list(win)))
        # 始めの方へ戻す: カーソルまでの掃引だけ（4 掃引）
        d = density_at(3, 4)
        self.assertAlmostEqual(d["interval_s"], wf.sweep_interval_s(list(times[:4])))

    def test_trace_only_undo_works_during_playback(self):
        # トレースの設定だけの変更は、再生中も元に戻せる（条件を変えないので断らない: 設計P §5）
        h5 = self.record(3)
        self.open(h5)
        live = dict(self.call("get_state")["sweep"])
        self.call("set_trace", number=1, line="dash")
        st = self.call("get_state")
        self.assertIsNone(st["undo"]["undo_blocked"])
        self.call("undo")
        st = self.call("get_state")
        self.assertNotEqual(st["traces"][0]["line"], "dash")
        self.assertIsNone(st["undo"]["redo_blocked"])
        self.call("redo")
        st = self.call("get_state")
        self.assertEqual(st["traces"][0]["line"], "dash")
        self.assertEqual((st["sweep"]["start_hz"], st["sweep"]["stop_hz"]), (live["start_hz"], live["stop_hz"]))

    def test_session_changes_with_each_recording(self):
        # 再生中に別の記録を開くと記録の通し番号が変わる（画面は記録ごとの見方を捨てる: P §5）
        h5 = self.record(3)
        h5b = self.root / "rec2.h5"
        h5b.write_bytes(h5.read_bytes())
        self.open(h5)
        s1 = self.call("get_state")["playback"]["session"]
        self.open(h5b)
        st = self.call("get_state")
        self.assertEqual(st["playback"]["name"], "rec2.h5")
        self.assertGreater(st["playback"]["session"], s1)


class ReferenceSegmentTest(InstrumentCase):
    def test_reference_take_is_a_new_segment_with_its_own_unit(self):
        # 基準を取ると量（電力 → 比）と単位が変わる: 新しい並び・境目の条件に単位と基準・行ごとの単位・行を取り直す（P §3・§9.8）
        self.connect(100 * MHZ, 200 * MHZ)
        self.call("set_mode", mode="tg")
        for _ in range(3):
            sweep_once(self)
        ep = self.call("get_state")["waterfall"]["epoch"]
        r = self.call("reference_take", kind="fine", name="ケーブルA")
        self.assertEqual(self.wait_task(r["task_id"])["code"], "task_done")
        for _ in range(3):
            sweep_once(self)
        st = self.call("get_state")
        self.assertGreater(st["waterfall"]["epoch"], ep)
        rows = self.call("waterfall_rows", max_rows=10)["rows"]
        old, new = rows[0], rows[-1]
        self.assertNotEqual(old["seg"], new["seg"])
        self.assertEqual((old["unit"], new["unit"]), ("dBm", "dB"))
        self.assertEqual(old["cond"]["unit"], "dBm")
        self.assertEqual((new["cond"]["unit"], new["cond"]["ref"]), ("dB", "ケーブルA"))
        self.assertNotEqual(old["cond"], new["cond"])  # 画面は境目の線と条件を出す
        from pygigast.ui_qt import display_view

        label = display_view.boundary_label(new["cond"])
        self.assertIn("dB", label)
        self.assertIn("ケーブルA", label)
        # 基準の名前を変えても境目にしない（並びと境目は基準の id。文はいまの名前）
        rid = new["cond"]["ref_id"]
        self.call("reference_rename", id=rid, name="ケーブルB")
        for _ in range(2):
            sweep_once(self)
        rows = [x for x in self.call("waterfall_rows", max_rows=20)["rows"] if x["unit"] == "dB"]
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(len({x["seg"] for x in rows}), 1)
        self.assertEqual({x["cond"]["ref"] for x in rows}, {"ケーブルB"})
        self.assertEqual(len({str(x["cond"]) for x in rows}), 1)  # 画面は境目を描かない


class ZeroSpanAndFormatTest(S11Case):
    """ほかの設計と組んだときの動き: ゼロスパン（横軸が時間: 設計N §1）と |Γ|・SWR（設計R §3）。"""

    def test_zero_span_adds_no_rows_and_refuses_playback(self):
        self.connect(50 * MHZ, 200 * MHZ)
        self.call("record_start")
        sweep_once(self)
        sweep_once(self)
        wait_state(self, lambda s: s["recording"].get("sweeps", 0) >= 2)
        self.call("record_stop")
        h5 = self.root / "rec.h5"
        self.wait_task(self.call("record_finish", path=str(h5))["task_id"])
        n = self.call("get_state")["waterfall"]["rows"]
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=0)
        self.clear_events()
        sweep_once(self)
        self.assertEqual(self.call("get_state")["waterfall"]["rows"], n)  # ゼロスパンの掃引は行にしない
        self.assertFalse([p for k, p in self.events if k == "waterfall" and p.get("rows")])
        # ゼロスパンの間は記録を再生しない（マーカーとトレースの横軸が時間になっているため）
        done = self.wait_task(self.call("open", paths=[str(h5)])["task_id"])
        res = done["detail"]["result"]
        self.assertIsNone(res["playback"])
        self.assertEqual(res["errors"][0]["code"], "playback_zero_span_live")
        self.assertFalse(self.call("get_state")["playback"]["active"])

    def test_zero_span_record_is_not_played(self):
        self.connect(50 * MHZ, 200 * MHZ)
        self.call("set_sweep", center_hz=100 * MHZ, span_hz=0)
        self.call("record_start")
        sweep_once(self)
        sweep_once(self)
        wait_state(self, lambda s: s["recording"].get("sweeps", 0) >= 2)
        self.call("record_stop")
        h5 = self.root / "zs.h5"
        self.wait_task(self.call("record_finish", path=str(h5))["task_id"])
        self.call("set_sweep", start_hz=50 * MHZ, stop_hz=200 * MHZ)
        done = self.wait_task(self.call("open", paths=[str(h5)])["task_id"])
        self.assertEqual(done["detail"]["result"]["errors"][0]["code"], "playback_zero_span")

    def test_swr_rows_range_and_density_use_display_values(self):
        from pygigast.core.trace import formats

        self.s11_reference()
        self.call("set_view", layout="density")
        self.call("set_view", s11_format="swr")
        self.call("set_amplitude", swr_per_div=0.5)
        self.clear_events()
        sweep_once(self)
        t = self.call("get_trace", id=1)
        evs = [p for k, p in self.events if k == "waterfall" and p.get("rows")]
        row = evs[-1]["rows"][0]
        # 行の値はトレースと同じ SWR の値（dB ではない）
        np.testing.assert_allclose(row["values"], np.asarray(t["values"], dtype=np.float32)[:len(row["values"])],
                                   rtol=1e-5)
        st = self.call("get_state")
        self.assertEqual(st["waterfall"]["unit"], formats.UNIT_SWR)
        # 色の範囲は縦軸と同じ（下端 1・1目盛 0.5 で 10 目盛）
        self.assertEqual(st["waterfall"]["range"], {"lo": 1.0, "hi": 6.0, "auto": False, "axis": True})
        d = [p for k, p in self.events if k == "density"][-1]
        self.assertEqual((d["level_lo"], d["level_hi"], d["unit"]), (1.0, 6.0, formats.UNIT_SWR))
        self.assertAlmostEqual(d["bin_db"], 0.5 / 20)
        self.assertEqual(d["bins"], 200)
        # 密度に入れた値は SWR（縦の升目の位置で確かめる: 一番下の升目は SWR 1〜1.025）
        img = np.asarray(d["image"])[::-1]
        swr = np.asarray(row["values"], dtype=np.float64)
        col = int(np.nanargmin(swr)) * d["cols"] // len(swr)
        b = int(np.floor((swr[int(np.nanargmin(swr))] - 1.0) / d["bin_db"]))
        self.assertGreater(img[min(b, d["bins"] - 1), col], 0)
        # 1目盛を変えたら色の範囲と密度の縦も変わる
        self.clear_events()
        self.call("set_amplitude", swr_per_div=1.0)
        st = self.call("get_state")
        self.assertEqual((st["waterfall"]["range"]["lo"], st["waterfall"]["range"]["hi"]), (1.0, 11.0))
        d = [p for k, p in self.events if k == "density"][-1]
        self.assertEqual(d["level_hi"], 11.0)
        # 形式を dB に戻すと行を取り直す（持っている行も新しい形式で出す）
        ep = st["waterfall"]["epoch"]
        self.call("set_view", s11_format="db")
        st = self.call("get_state")
        self.assertGreater(st["waterfall"]["epoch"], ep)
        r = self.call("waterfall_rows", max_rows=5)
        self.assertEqual(r["unit"], "dB")
        self.assertLess(float(np.nanmax(r["rows"][-1]["values"])), 0.5)  # リターンロスの dB（0dB 以下）
