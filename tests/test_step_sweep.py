"""SG のステップ掃引（設計L §1.3）の試験。

- 計算（core/step_sweep.py）: 設定の確かめ（SG の単位・範囲・刻み・回数）・段の並び（上り・下り）・段の数の上限・
  重ね方（最大保持・パワー平均・全部重ねる＝段ごとの平均と包絡線）・値の無い点・段ごとの線の間引き
- 本体: 設定は前回終了時の状態に入る・始められない理由（未接続・SG モードでない・段が多すぎる）・全部の段を回して
  結果を選んだトレースに固定で残す・SG の周波数と動作を始める前に戻す・イメージ除去を使わない・最大保持は残し平均は
  捨てる（設計F §8 の 2）・やめる／切断でもそこまでの結果を残し終わり方を知らせる・1回も重ねずに終わったらトレースの
  設定を戻す・間は条件の命令と結果のトレースの動作を受け付けない・保存に設定が残る・閉じたら SG の周波数を戻して残す
- 画面の文（Qt と Web で同じ）: 見積もり・使えない理由・進み具合・終わりの知らせ・凡例・直した知らせ
"""
import threading
import unittest

import numpy as np

from pygigast.core import step_sweep as ss
from pygigast.core.storage import Cancelled
from pygigast.core.storage.h5file import H5Record
from pygigast.ui_qt import screen_model, source_view, texts
from tests.instrument_helpers import InstrumentCase

MHZ = 1_000_000
SG = (1 * MHZ, 4000 * MHZ, 1000)


class CheckTest(unittest.TestCase):
    def test_rounds_and_clamps_with_reasons(self):
        cfg, adj = ss.check(ss.DEFAULTS, {"start_hz": 100_000_400, "stop_hz": 5000 * MHZ, "step_hz": 10,
                                          "per_step": 0}, SG)
        self.assertEqual((cfg["start_hz"], cfg["stop_hz"], cfg["step_hz"], cfg["per_step"]),
                         (100 * MHZ, 4000 * MHZ, 1000, 1))
        self.assertEqual([(a.field, a.reason) for a in adj],
                         [("start_hz", "freq_unit"), ("stop_hz", "sg_range"), ("step_hz", "step_min"),
                          ("per_step", "per_step_min")])
        cfg, adj = ss.check(ss.DEFAULTS, {"per_step": 500}, SG)
        self.assertEqual((cfg["per_step"], adj[0].reason), (ss.PER_STEP_MAX, "per_step_max"))
        cfg, adj = ss.check(ss.DEFAULTS, {"start_hz": 300 * MHZ, "combine": "all", "trace": 4}, SG)
        self.assertEqual((cfg["start_hz"], cfg["combine"], cfg["trace"], adj), (300 * MHZ, "all", 4, []))

    def test_bad_values(self):
        for changes, name in (({"start_hz": 1.5}, "start_hz"), ({"per_step": True}, "per_step"),
                              ({"combine": "min"}, "combine"), ({"trace": 5}, "trace"), ({"step_hz": "1M"}, "step_hz")):
            with self.assertRaises(ValueError) as cm:
                ss.check(ss.DEFAULTS, changes, SG)
            self.assertEqual(str(cm.exception), name)

    def test_restore_reads_what_it_can(self):
        self.assertEqual(ss.restore(None), ss.DEFAULTS)
        got = ss.restore({"start_hz": 50 * MHZ, "combine": "nope", "per_step": 7, "trace": "x"}, SG)
        self.assertEqual(got, {**ss.DEFAULTS, "start_hz": 50 * MHZ, "per_step": 7})

    def test_plan_up_down_and_single(self):
        up = {**ss.DEFAULTS, "start_hz": 100 * MHZ, "stop_hz": 125 * MHZ, "step_hz": 10 * MHZ}
        self.assertEqual(ss.plan(up), [100 * MHZ, 110 * MHZ, 120 * MHZ])  # 終了を越えない
        down = {**up, "start_hz": 125 * MHZ, "stop_hz": 100 * MHZ}
        self.assertEqual(ss.plan(down), [125 * MHZ, 115 * MHZ, 105 * MHZ])
        one = {**up, "step_hz": 1000 * MHZ}
        self.assertEqual((ss.count(one), ss.plan(one)), (1, [100 * MHZ]))
        self.assertEqual(ss.count(ss.DEFAULTS), 41)

    def test_steps_limits(self):
        # 境界値 N−1 / N / N+1（段の数の上限・全部重ねるの上限）
        base = {**ss.DEFAULTS, "start_hz": 1 * MHZ, "step_hz": 1000}
        for n, want in ((ss.STEPS_MAX - 1, None), (ss.STEPS_MAX, None), (ss.STEPS_MAX + 1, "too_many_steps")):
            cfg = {**base, "stop_hz": 1 * MHZ + (n - 1) * 1000}
            self.assertEqual((ss.count(cfg), ss.start_problem(cfg)), (n, want))
        base["combine"] = ss.ALL
        for n, want in ((ss.ALL_STEPS_MAX, None), (ss.ALL_STEPS_MAX + 1, "too_many_steps_all")):
            cfg = {**base, "stop_hz": 1 * MHZ + (n - 1) * 1000}
            self.assertEqual(ss.start_problem(cfg), want)


class CombinerTest(unittest.TestCase):
    def test_max_hold(self):
        c = ss.Combiner(ss.MAX, 3)
        c.add(0, [1.0, np.nan, 3.0], {"transient": [True, False, False]})
        c.add(1, [2.0, 5.0, np.nan], {"transient": [False, False, True]})
        np.testing.assert_array_equal(c.result(), [2.0, 5.0, 3.0])
        np.testing.assert_array_equal(c.flags["transient"], [True, False, True])  # 印は和
        self.assertIsNone(c.step_values())

    def test_power_average(self):
        c = ss.Combiner(ss.AVERAGE, 2)
        for v in ([1.0, np.nan], [3.0, np.nan], [8.0, 4.0]):
            c.add(0, v)
        np.testing.assert_allclose(c.result(), [4.0, 4.0])  # W のまま平均・値の無い点は数えない
        self.assertEqual(c.sweeps, 3)

    def test_all_keeps_step_means_and_envelope(self):
        c = ss.Combiner(ss.ALL, 3)
        c.add(0, [1.0, 1.0, 1.0])
        c.add(0, [3.0, 1.0, np.nan])
        c.add(1, [0.5, 9.0, np.nan])
        rows = c.step_values()
        np.testing.assert_allclose(rows, [[2.0, 1.0, 1.0], [0.5, 9.0, np.nan]])
        np.testing.assert_allclose(c.result(), [2.0, 9.0, 1.0])
        self.assertIsNone(ss.Combiner(ss.ALL, 3).result())
        with self.assertRaises(ValueError):
            c.add(2, [1.0, 2.0])

    def test_decimate_rows(self):
        rows = np.arange(10.0).reshape(1, 10)
        same, k = ss.decimate_rows(rows, 10)
        self.assertEqual(k, 1)
        np.testing.assert_array_equal(same, rows)
        dec, k = ss.decimate_rows(rows, 4)  # 3 点ずつの最大（端は残りの点）
        self.assertEqual(k, 3)
        np.testing.assert_array_equal(dec, [[2, 5, 8, 9]])
        dec, _k = ss.decimate_rows(np.array([[np.nan, np.nan, np.nan, 1.0]]), 2)
        self.assertTrue(np.isnan(dec[0, 0]))
        self.assertEqual(dec[0, 1], 1.0)


class InstrumentStepSweepTest(InstrumentCase):
    SG0 = 500 * MHZ

    def setUp(self):
        super().setUp()
        self.connect(300 * MHZ, 800 * MHZ)
        self.call("set_mode", mode="sg", sg_hz=self.SG0)

    def on_control(self, fn):
        done = threading.Event()
        box = {}

        def run():
            box["r"] = fn()
            done.set()

        self.inst._q.put(("call", run))
        self.assertTrue(done.wait(10))
        return box.get("r")

    def run_steps(self, **cfg):
        self.call("set_step_sweep", **cfg)
        self.clear_events()
        r = self.call("step_sweep_start")
        return r, self.wait_task(r["task_id"])

    def test_settings_are_saved_and_checked(self):
        r = self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=600 * MHZ, step_hz=50 * MHZ, per_step=3,
                      combine="average", trace=3)
        self.assertEqual(r["adjusted"], [])
        s = r["step_sweep"]
        self.assertEqual((s["steps"], s["combine"], s["trace"], s["reason"]), (5, "average", 3, None))
        self.assertGreater(s["sweep_s"], 0)
        r = self.call("set_step_sweep", start_hz=500, per_step=0)
        self.assertEqual([(a["field"], a["adopted"], a["reason"]) for a in r["adjusted"]],
                         [("start_hz", 1 * MHZ, "sg_range"), ("per_step", 1, "per_step_min")])
        with self.assertRaises(Exception) as cm:
            self.call("set_step_sweep", combine="min")
        self.assertEqual((cm.exception.code, cm.exception.field), ("bad_value", "combine"))
        with self.assertRaises(Exception) as cm:
            self.call("set_step_sweep", step_hz=1.5e6)
        self.assertEqual(cm.exception.code, "not_integer")
        self.inst.shutdown()
        self.inst = self.make_instrument()
        s = self.call("get_state")["step_sweep"]
        self.assertEqual((s["start_hz"], s["stop_hz"], s["per_step"], s["combine"], s["trace"]),
                         (1 * MHZ, 600 * MHZ, 1, "average", 3))

    def test_start_reasons(self):
        self.call("set_mode", mode="sa")
        self.assertEqual(self.call("get_state")["step_sweep"]["reason"], "step_sweep_needs_sg")
        with self.assertRaises(Exception) as cm:
            self.call("step_sweep_start")
        self.assertEqual(cm.exception.code, "step_sweep_needs_sg")
        self.call("set_mode", mode="sg")
        self.call("set_step_sweep", start_hz=1 * MHZ, stop_hz=4000 * MHZ, step_hz=1 * MHZ, combine="all")
        with self.assertRaises(Exception) as cm:
            self.call("step_sweep_start")
        self.assertEqual((cm.exception.code, cm.exception.detail["max"]), ("too_many_steps_all", ss.ALL_STEPS_MAX))
        self.call("disconnect")
        self.assertEqual(self.call("get_state")["step_sweep"]["reason"], "not_connected")

    def test_full_run_max_hold(self):
        self.call("set_trace", number=3, action="max_hold")
        self.call("set_trace", number=4, action="average", count=4)
        self.single()
        self.call("run", mode="continuous")
        self.assertEqual(self.call("get_state")["sweep"]["image_reject_used"], "fast")
        seen_sg = set()
        self.inst.subscribe(lambda k, p: seen_sg.add(p["sweep"]["sg_hz"]) if k == "state" else None)
        r, end = self.run_steps(start_hz=400 * MHZ, stop_hz=600 * MHZ, step_hz=50 * MHZ, per_step=2, combine="max",
                                trace=2)
        self.assertEqual(r["steps"], 5)
        self.assertEqual(end["code"], "task_done")
        self.assertEqual(end["detail"]["result"], {"steps_done": 5, "steps": 5, "trace": 2, "kept": True,
                                                   "combine": "max"})
        self.assertTrue({400 * MHZ, 450 * MHZ, 500 * MHZ, 550 * MHZ, 600 * MHZ} <= seen_sg)  # SG の欄も段の周波数
        st = self.call("get_state")
        self.assertEqual(st["sweep"]["sg_hz"], self.SG0)  # 始める前の周波数に戻す
        self.assertEqual(st["sweep"]["image_reject"], "fast")  # 選択は変えない
        self.assertEqual(st["run"]["state"], "continuous")  # 始める前の動作に戻る
        self.assertFalse(st["step_sweep"]["running"])
        t2 = st["traces"][1]
        self.assertEqual((t2["action"], t2["visible"], t2["has_values"]), ("fixed", True, True))
        self.assertEqual(t2["step_sweep"]["steps_done"], 5)
        self.assertEqual(st["traces"][2]["action"], "max_hold")
        # 段ごとの SG の山（400〜600MHz）が全部、最大保持の結果に残る
        tp = self.call("get_trace", id=2)
        f = tp["axis"]["start_hz"] + tp["axis"]["step_hz"] * np.arange(tp["axis"]["points"])
        for sg in (400, 450, 500, 550, 600):
            near = np.abs(f - sg * MHZ) <= 2 * MHZ
            self.assertGreater(np.nanmax(tp["values"][near]), -40.0, sg)
        self.assertNotIn("steps", tp)  # 段ごとの線は全部重ねるときだけ
        # 段を動かしても最大保持は残り（F §8 の 2）、平均は捨てる
        resets = [p["detail"] for k, p in self.events if k == "notice" and p["code"] == "trace_reset"]
        self.assertTrue(resets)
        self.assertFalse([d for d in resets if d["cause"] == "sg_freq" and 3 in d["traces"]])
        self.assertTrue([d for d in resets if d["cause"] == "sg_freq" and 4 in d["traces"]])
        # 始めと終わりのイメージ除去の切り替えでも最大保持は残る（積み上げた分が終わった瞬間に消えない）
        self.assertFalse([d for d in resets if 3 in d["traces"]])
        self.assertTrue([d for d in resets if d["cause"] == "image_reject" and 4 in d["traces"]])
        self.assertTrue(st["traces"][2]["has_values"])
        t3 = self.call("get_trace", id=3)
        f3 = t3["axis"]["start_hz"] + t3["axis"]["step_hz"] * np.arange(t3["axis"]["points"])
        for sg in (400, 600):
            self.assertGreater(np.nanmax(t3["values"][np.abs(f3 - sg * MHZ) <= 2 * MHZ]), -40.0, sg)

    def test_image_reject_not_used_and_reason(self):
        sent = []
        orig = self.inst._step_push_job

        def spy():
            job = self.inst._step_job
            if job is not None and job.adopted is not None:
                sent.append(job.adopted.image_reject_used)
            return orig()

        self.inst._step_push_job = spy
        states = []
        self.inst.subscribe(lambda k, p: states.append(p) if k == "state" and p["step_sweep"]["running"] else None)
        self.run_steps(start_hz=400 * MHZ, stop_hz=450 * MHZ, step_hz=50 * MHZ, per_step=1, combine="max", trace=2)
        self.assertTrue(sent)
        self.assertEqual(set(sent), {"off"})
        self.assertTrue(states)
        st = states[-1]
        self.assertEqual(st["run"]["state"], "step_sweep")
        self.assertEqual(screen_model.image_reject_view(st)["reason"], texts.REASON_TEXTS["image_reject_step"])
        self.assertEqual(texts.source_text(st), texts.tpl("source_sg", freq=texts.format_freq(st["sweep"]["sg_hz"])))

    def test_all_keeps_every_step(self):
        traces = []
        self.inst.subscribe(lambda k, p: traces.append(p) if k == "trace" and p["trace"] == 2 else None)
        _r, end = self.run_steps(start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=100 * MHZ, per_step=2, combine="all",
                                 trace=2)
        self.assertEqual(end["code"], "task_done")
        tp = self.call("get_trace", id=2)
        rows = np.asarray(tp["steps"]["values"])
        self.assertEqual(rows.shape, (4, tp["axis"]["points"]))
        self.assertEqual((tp["steps"]["x0"], tp["steps"]["dx"]), (float(tp["axis"]["start_hz"]),
                                                                  float(tp["axis"]["step_hz"])))
        # 主の値は段ごとの値の包絡線
        np.testing.assert_allclose(np.nanmax(rows, axis=0), tp["values"], atol=1e-3)
        # 掃引ごとに入れ直して画面へ送る（段の数が増えていく）
        counts = [len(p["steps"]["values"]) for p in traces if "steps" in p]
        self.assertEqual(counts[0], 1)
        self.assertEqual(counts[-1], 4)
        self.assertEqual(tp["step_sweep"]["combine"], "all")

    def test_cancel_keeps_partial_result(self):
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=10 * MHZ, per_step=1, combine="max",
                  trace=2)
        self.clear_events()
        r = self.call("step_sweep_start")
        self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        self.call("cancel_task", task_id=r["task_id"])
        end = self.wait_task(r["task_id"])
        self.assertEqual(end["code"], "task_cancelled")
        d = end["detail"]
        self.assertTrue(d["kept"])
        self.assertGreaterEqual(d["steps_done"], 1)
        self.assertLess(d["steps_done"], 31)
        st = self.call("get_state")
        self.assertEqual((st["traces"][1]["action"], st["traces"][1]["has_values"]), ("fixed", True))
        self.assertEqual(st["sweep"]["sg_hz"], self.SG0)
        self.assertEqual(st["run"]["state"], "stopped")
        self.assertIn("そこまでの結果はトレース 2", texts.task_end_text("task_cancelled", d)[0])

    def test_top_stop_ends_like_cancel_and_stops_sweeping(self):
        """上段の「停止」（命令 stop）でもステップ掃引をやめる（2026-10-09 ユーザー決定）。終わり方は「やめる」と同じ
        （そこまでの結果を残し task_cancelled で知らせる）。始める前が連続でも、その後の掃引は止まったまま。"""
        self.call("run", mode="continuous")
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=10 * MHZ, per_step=1, combine="max",
                  trace=2)
        self.clear_events()
        r = self.call("step_sweep_start")
        self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        res = self.call("stop")
        self.assertEqual(res["run"], "stopped")
        end = self.wait_task(r["task_id"])
        self.assertEqual(end["code"], "task_cancelled")
        d = end["detail"]
        self.assertTrue(d["kept"])
        self.assertLess(d["steps_done"], 31)
        st = self.call("get_state")
        self.assertEqual((st["traces"][1]["action"], st["traces"][1]["has_values"]), ("fixed", True))
        self.assertEqual(st["sweep"]["sg_hz"], self.SG0)
        self.assertEqual((st["run"]["state"], st["run"]["last_stop_reason"]), ("stopped", "user"))
        self.assertEqual(st["tasks"], [])
        self.assertIn("そこまでの結果はトレース 2", texts.task_end_text("task_cancelled", d)[0])
        # 連続に戻らない（機器スレッドへ次の掃引を渡していない）
        self.assertIsNone(self.on_control(lambda: self.inst._worker._job))

    def test_cancel_before_any_sweep_restores_trace(self):
        self.call("set_trace", number=2, action="average", count=8, visible=False)
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=500 * MHZ, step_hz=50 * MHZ, combine="max", trace=2)
        undo_before = len(self.inst._undo)

        def start_and_cancel():
            r = self.inst._cmd_step_sweep_start()
            self.inst._cmd_cancel_task(r["task_id"])
            return r

        r = self.on_control(start_and_cancel)
        end = self.wait_task(r["task_id"])
        self.assertEqual(end["detail"]["kept"], False)
        self.assertEqual(texts.task_end_text("task_cancelled", end["detail"])[0], texts.TEMPLATES["step_cancelled_empty"])
        t2 = self.call("get_state")["traces"][1]
        self.assertEqual((t2["action"], t2["count"], t2["visible"]), ("average", 8, False))
        self.assertEqual(len(self.inst._undo), undo_before)

    def test_disconnect_fails_and_keeps_result(self):
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=10 * MHZ, combine="max", trace=2)
        self.clear_events()
        r = self.call("step_sweep_start")
        self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        self.call("disconnect")
        end = self.wait_task(r["task_id"])
        self.assertEqual((end["code"], end["detail"]["error"]["code"], end["detail"]["kept"]),
                         ("task_failed", "disconnected", True))
        text = texts.task_end_text("task_failed", end["detail"])[0]
        self.assertIn(texts.ERRORS["disconnected"], text)  # 「機器が切れました」（コードのまま出さない）
        self.assertEqual(self.call("get_state")["traces"][1]["action"], "fixed")

    def test_commands_refused_while_running(self):
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=5 * MHZ, combine="max", trace=2)
        r = self.call("step_sweep_start")
        try:
            for name, kw in (("set_sweep", {"start_hz": 350 * MHZ}), ("set_mode", {"mode": "sa"}),
                             ("run", {"mode": "single"}), ("set_step_sweep", {"per_step": 2}), ("step_sweep_start", {})):
                with self.assertRaises(Exception) as cm:
                    self.call(name, **kw)
                self.assertEqual(cm.exception.code, "step_sweep_in_progress", name)
            with self.assertRaises(Exception) as cm:
                self.call("set_trace", number=2, action="update")
            self.assertEqual(cm.exception.code, "step_sweep_trace")
            self.call("set_trace", number=2, line="thick")  # 線・表示するかは変えてよい
            self.call("set_trace", number=1, action="max_hold")  # ほかのトレースは変えてよい
        finally:
            self.call("cancel_task", task_id=r["task_id"])
            self.wait_task(r["task_id"])

    def test_saved_file_has_step_settings(self):
        self.run_steps(start_hz=400 * MHZ, stop_hz=500 * MHZ, step_hz=50 * MHZ, combine="max", trace=2)
        path = self.root / "s.h5"
        r = self.call("save", path=str(path))
        self.wait_task(r["task_id"])
        with H5Record(str(path)) as rec:
            infos = {t.number: t.info for t in rec.traces()}
        self.assertEqual(infos[2]["action"], "fixed")
        self.assertEqual(infos[2]["step_sweep"]["steps"], 3)
        self.assertNotIn("step_sweep", infos[1])

    def test_shutdown_while_running_restores_sg(self):
        self.call("set_step_sweep", start_hz=1000 * MHZ, stop_hz=1500 * MHZ, step_hz=1 * MHZ, combine="max", trace=2)
        self.call("step_sweep_start")
        self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        self.inst.shutdown()
        self.inst = self.make_instrument()
        st = self.call("get_state")
        self.assertEqual((st["sweep"]["sg_hz"], st["sweep"]["mode"]), (self.SG0, "sg"))

    def test_internal_stop_hook(self):
        # 外から止める口（送れない条件・終了と同じ道）: そこまでの結果は残り、止まった理由を添えて知らせる
        self.call("set_step_sweep", start_hz=400 * MHZ, stop_hz=700 * MHZ, step_hz=10 * MHZ, combine="max", trace=2)
        r = self.call("step_sweep_start")
        self.wait_for(lambda k, p: k == "trace" and p["trace"] == 2)
        self.on_control(lambda: self.inst._step_stop(Cancelled()))
        end = self.wait_task(r["task_id"])
        self.assertEqual(end["code"], "task_cancelled")


class StepTextsTest(unittest.TestCase):
    def state(self, **step):
        s = {**ss.DEFAULTS, "steps": 41, "sweep_s": 0.612, "reason": None, "running": False, **step}
        return {"step_sweep": s, "sweep": {"step_hz": 1000}}

    def test_view(self):
        v = source_view.step_view(self.state())
        self.assertEqual(v["estimate"], "41 段 × 約 0.61 秒 × 1 回")
        self.assertEqual([c["text"] for c in v["combines"]], ["最大保持", "平均", "全部重ねる"])
        self.assertEqual([t["value"] for t in v["traces"]], [1, 2, 3, 4])
        self.assertIsNone(v["reason"])
        v = source_view.step_view(self.state(sweep_s=12.34, per_step=4, reason="step_sweep_needs_sg"))
        self.assertEqual((v["estimate"], v["reason"]), ("41 段 × 約 12.3 秒 × 4 回", "SG モードで使います"))
        v = source_view.step_view(self.state(running=True, reason="step_sweep_in_progress"))
        self.assertEqual((v["field_reason"], v["reason"]), (texts.REASON_TEXTS["step_running"],
                                                            texts.ERRORS["step_sweep_in_progress"]))
        self.assertEqual(source_view.step_view(self.state(sweep_s=None))["estimate"], "41 段 × 1 回")
        self.assertIn(str(ss.ALL_STEPS_MAX), texts.ERRORS["too_many_steps_all"])
        self.assertIn(str(ss.STEPS_MAX), texts.ERRORS["too_many_steps"])

    def test_progress_end_and_legend(self):
        t = {"task": "step_sweep", "done": 2, "total": 40, "elapsed_s": 12.7}
        self.assertEqual(texts.task_label(t), "ステップ掃引  3/40 段  経過 12 秒")
        self.assertEqual(texts.task_label({**t, "done": 40}), "ステップ掃引  40/40 段  経過 12 秒")
        done = {"task": "step_sweep", "result": {"steps_done": 40, "steps": 40, "trace": 2, "kept": True}}
        self.assertEqual(texts.task_end_text("task_done", done),
                         ("ステップ掃引が終わりました（40/40 段）。結果はトレース 2（固定）", None))
        failed = {"task": "step_sweep", "error": {"code": "disconnected"}, "steps_done": 3, "steps": 40, "trace": 2,
                  "kept": True}
        self.assertIn("3/40 段", texts.task_end_text("task_failed", failed)[0])
        self.assertIn("結果はありません", texts.task_end_text("task_failed", {**failed, "kept": False})[0])
        info = {"combine": "all", "steps_done": 12, "steps": 40}
        parts = texts.legend_parts({"trace": 2, "action": "fixed", "step_sweep": info})
        self.assertEqual(parts["label"], "ステップ掃引・全部重ねる 12/40 段")
        parts = texts.legend_parts({"trace": 2, "action": "fixed", "step_sweep": info, "axis_differs": True})
        self.assertEqual(parts["label"], "ステップ掃引・全部重ねる 12/40 段 " + texts.TEMPLATES["axis_differs"])
        self.assertEqual(texts.RUN_STATES["step_sweep"], "ステップ掃引")

    def test_adjusted_notes(self):
        r = {"adjusted": [{"field": "start_hz", "requested": 500, "adopted": 1_000_000, "reason": "sg_range"},
                          {"field": "per_step", "requested": 0, "adopted": 1, "reason": "per_step_min"}]}
        self.assertEqual(source_view.adjusted_notes(r),
                         [{"field": "step_start", "text": "1 MHz に合わせました（SG の範囲）"},
                          {"field": "step_per_step", "text": "1 回 に合わせました（回数の下限）"}])


if __name__ == "__main__":
    unittest.main()
