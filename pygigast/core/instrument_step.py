"""計測器の本体のうち、SG のステップ掃引（設計L §1.3）。Instrument に混ぜる（メソッドは制御スレッドから呼ばれる）。

- 設定（開始・終了・刻み・1段の回数・重ね方・結果を入れるトレース）はコアに1つ（Qt と Web が同じものを見る）。前回
  終了時の状態に入る。命令 set_step_sweep は渡した欄だけ変え、直した値は adjusted で返す（M-02: 黙って直さない）
- 命令 step_sweep_start: SG モードで、いまの SP の掃引条件のまま、段ごとに SG の周波数を動かして回数分掃引し、重ね方に
  従って重ねる。長い処理（task "step_sweep"）として段の進み具合を出し、cancel_task か上段の「停止」（命令 stop）でやめられる（U-19）
  - SG の周波数は `_set_adopted(new, step_sweep=True)` で動かす（設計F §8 の 2: 最大・最小保持を残し、平均は捨てる）。
    SG 周波数の欄・状態バーの「SG 出力中」もいまの段の周波数になる。終わったら始める前の周波数に戻す
  - イメージ除去は使わない（旧アプリも SG-SWEEP 中は使わない）。利用者の選択は変えず、機器へ渡す条件だけを「なし」に
    する。選択の欄は間だけ理由付きで選べない（画面: screen_model.sweep_reasons）。始めと終わりのイメージ除去の切り替えで
    平均は捨て、最大・最小保持は残す（SG の周波数を動かすときと同じ扱い）
  - 掃引はいまのトレースにもふつうに入る（トレース1 などで、いまの段の掃引が見える）
  - 結果は選んだトレースを固定にして入れる（掃引ごとに入れ直す: 設計F の固定のトレース。保存・PNG はふつうどおり）。
    止めた・失敗したときもそこまでの結果を残す。1回も重ねないうちに終わったら、トレースの設定を始める前に戻す
  - 終わり方は task_done / task_cancelled / task_failed（済んだ段の数・全体の段の数・結果のトレース・残したか）
- 間は掃引条件・モード・動作・元に戻すの命令を受け付けない（基準を取る間と同じ: _check_not_taking_reference）。結果の
  トレースの動作も変えない（表示するか・線は変えてよい）
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import display_marks as dm
from . import step_sweep as ss
from .instrument_common import sweep_key
from .instrument_display import apply_trace_settings, display_values, trace_settings
from .storage import Cancelled
from .trace import ConditionChange, HoldKeep, TraceAction, formats
from .types import AdoptedSweep, Mode, RawSweep, SweepRequest

TASK = "step_sweep"
# 状態の run.state（ステップ掃引をしている間）
RUN_STATE = "step_sweep"

_UNSET = object()


@dataclass
class _StepJob:
    """動いているステップ掃引。"""

    task: object
    cfg: dict
    freqs: list[int]
    prev_run: str
    orig_sg_hz: Optional[int]
    trace: int
    before_trace: dict
    undo_entry: Optional[dict]
    ic_used: bool
    started: float
    combiner: Optional[ss.Combiner] = None
    index: int = 0
    done_in_step: int = 0
    steps_done: int = 0  # 値を重ねた段の数（途中でやめた段も1回でも重ねていれば数える）
    adopted: Optional[AdoptedSweep] = None  # いまの段で機器へ渡す条件（イメージ除去なし）


class StepSweepMixin:
    """SG のステップ掃引（Instrument に混ぜる）。"""

    def _init_step(self, st: Optional[dict]) -> None:
        self._step_cfg = ss.restore((st or {}).get("step_sweep"), ss.sg_range(self._profile))
        self._step_job: Optional[_StepJob] = None
        self._step_est_cache: tuple = ((), None)

    def _step_state_to_save(self) -> dict:
        return {"step_sweep": dict(self._step_cfg)}

    # ------------------------------------------------------------------ 状態

    def _step_sweep_s(self) -> Optional[float]:
        """いまの SP の条件を SG モード・イメージ除去なしで掃引したときの1回の見積もり秒（設計B §7.2）。"""
        if ss.sg_range(self._profile) is None:
            return None
        key = (sweep_key(self._adopted), self._profile.get("model"))
        if self._step_est_cache[0] == key:
            return self._step_est_cache[1]
        try:
            a = self._model.apply(SweepRequest(mode=Mode.SG, image_reject="off", one_shot=True), self._adopted)
            est = float(a.estimated_sweep_s)
        except (ValueError, TypeError):
            est = None
        self._step_est_cache = (key, est)
        return est

    def _step_reason(self) -> Optional[str]:
        """始められない理由のコード（無ければ None）。画面はボタンを理由付きで無効にする（U-08）。"""
        if self._step_job is not None:
            return "step_sweep_in_progress"
        if ss.sg_range(self._profile) is None:
            return "step_sweep_no_sg"
        if self._conn != "connected":
            return "not_connected"
        if getattr(self, "_playback", None) is not None:
            return "playback_active"  # 記録の再生中は機器の掃引を受けない（ライブに戻ってから: 設計P §9.4）
        if self._adopted.mode != Mode.SG:
            return "step_sweep_needs_sg"
        if self._ref_job is not None:
            return "reference_in_progress"
        if getattr(self, "_cal_job", None) is not None:
            return "calibration_in_progress"
        return ss.start_problem(self._step_cfg)

    def _step_state(self) -> dict:
        job = self._step_job
        return {
            **self._step_cfg,
            "steps": ss.count(self._step_cfg),
            "sweep_s": self._step_sweep_s(),
            "reason": self._step_reason(),
            "running": job is not None,
            "task_id": None if job is None else job.task.task_id,
            "step": None if job is None else job.index + 1,
            "result_trace": None if job is None else job.trace,
            "limits": {"steps_max": ss.STEPS_MAX, "all_steps_max": ss.ALL_STEPS_MAX,
                       "per_step_min": ss.PER_STEP_MIN, "per_step_max": ss.PER_STEP_MAX},
        }

    # ------------------------------------------------------------------ 命令

    def _cmd_set_step_sweep(self, start_hz=_UNSET, stop_hz=_UNSET, step_hz=_UNSET, per_step=_UNSET,
                            combine=_UNSET, trace=_UNSET) -> dict:
        """ステップ掃引の設定を変える（渡した欄だけ）。SG の範囲・単位に直した値は adjusted で返す。"""
        if self._step_job is not None:
            raise self._error("step_sweep_in_progress")
        changes = {k: v for k, v in (("start_hz", start_hz), ("stop_hz", stop_hz), ("step_hz", step_hz),
                                     ("per_step", per_step), ("combine", combine), ("trace", trace))
                   if v is not _UNSET}
        try:
            cfg, adj = ss.check(self._step_cfg, changes, ss.sg_range(self._profile))
        except ValueError as e:
            name = str(e)
            numeric = name in ("start_hz", "stop_hz", "step_hz", "per_step")
            raise self._error("not_integer" if numeric else "bad_value", name, {"value": changes.get(name)}) from None
        self._step_cfg = cfg
        self._changed()
        return {"step_sweep": self._step_state(), "adjusted": [dataclasses.asdict(a) for a in adj]}

    def _cmd_step_sweep_start(self) -> dict:
        """ステップ掃引を始める（設計L §1.3）。task_id を返し、進み具合と終わりは長い処理の知らせで出す。"""
        why = self._step_reason()
        if why is not None:
            n = ss.count(self._step_cfg)
            raise self._error(why, None, {"steps": n, "max": ss.ALL_STEPS_MAX if why == "too_many_steps_all"
                                          else ss.STEPS_MAX})
        cfg = dict(self._step_cfg)
        freqs = ss.plan(cfg)
        t = self._traces[cfg["trace"]]
        before = self._undo_snapshot()
        before_trace = trace_settings(t)
        # 結果を入れるトレースを固定にして空にする（元に戻すで、始める前のトレースの設定に戻せる）
        t.configure(action=TraceAction.FIXED.value, visible=True)
        t.set_result(None)
        self._record_undo(before)
        undo_entry = self._undo[-1] if self._undo and self._undo[-1] is before else None
        task = self._new_task(TASK, total=len(freqs), detail={"steps": len(freqs), "trace": cfg["trace"],
                                                                 "combine": cfg["combine"],
                                                                 "per_step": cfg["per_step"]})
        job = _StepJob(task=task, cfg=cfg, freqs=freqs, prev_run=self._run, orig_sg_hz=self._adopted.sg_hz,
                       trace=cfg["trace"], before_trace=before_trace, undo_entry=undo_entry,
                       ic_used=self._adopted.image_reject_used != "off", started=time.time())
        self._step_job = job
        self._run = RUN_STATE
        self._last_stop_reason = None
        if job.ic_used:
            # 機器へ渡す条件のイメージ除去が「なし」になる（値の意味が変わる: 設計F §5.3）。平均は捨てるが、最大・最小
            # 保持は残す（ステップ掃引が条件を動かすときは特性を積み上げて描く: 設計F §8 の 2 と同じ扱い。捨てると、
            # 段ごとに積み上げた保持が終わった瞬間に消えた: 2026-10-03 反証）
            self._on_condition(ConditionChange.IMAGE_REJECT, holds=HoldKeep.ALL)
        self._rebuild_views()
        self._refresh_markers()
        self._step_goto(0)
        self._task_progress(task, 0, len(freqs), 0.0)
        self._changed()
        return {"task_id": task.task_id, "steps": len(freqs)}

    # ------------------------------------------------------------------ 段を進める

    def _step_goto(self, index: int) -> None:
        """index 番目の段へ: SG の周波数を動かし（設計F §8 の 2）、機器へ渡す条件を作り直して回数分の掃引を渡す。"""
        job = self._step_job
        job.index = index
        job.done_in_step = 0
        old = self._adopted
        new = self._model.apply(SweepRequest(sg_hz=job.freqs[index], one_shot=True), old)
        new = dataclasses.replace(new, adjustments=(), alternative=None)
        job.adopted = self._model.apply(SweepRequest(image_reject="off", one_shot=True), new)
        changed = sweep_key(new) != sweep_key(old)
        self._set_adopted(new, step_sweep=True)  # 変わったら _push_job が段の掃引を渡す
        if not changed:
            self._push_job()

    def _step_push_job(self) -> bool:
        """機器スレッドへ、いまの段の残りの回数を渡す（_push_job から呼ぶ）。ステップ掃引の間でなければ False。"""
        job = self._step_job
        if job is None:
            return False
        n = max(0, int(job.cfg["per_step"]) - job.done_in_step)
        wanted = self._conn == "connected" and n > 0 and job.adopted is not None
        self._worker.set_job(self._gen, job.adopted if wanted else None, n)
        return True

    def _step_on_sweep(self, raw: RawSweep) -> bool:
        """ステップ掃引の掃引を受ける（_on_sweep から呼ぶ）。間でなければ False。"""
        job = self._step_job
        if job is None:
            return False
        self._sweep_count += 1
        frame = self._build_frame(raw)
        self._rtltcp_on_sweep(raw, frame)
        self._last_raw = raw
        marked = self._mark_frame(frame)  # SG モードなので基準は当てない（電力のまま）
        if job.combiner is None:
            job.combiner = ss.Combiner(job.cfg["combine"], marked.axis.points)
        job.combiner.add(job.index, marked.values, marked.flags)
        job.done_in_step += 1
        step_done = job.done_in_step >= int(job.cfg["per_step"])
        job.steps_done = job.index + 1  # 値を重ねた段の数（途中でやめた段も、1回でも重ねていれば数える）
        self._step_put_result(job, marked)
        # いまのトレースにもふつうに入れる（結果のトレースは固定なので触らない）。マーカーは結果も読む
        self._process_frame(frame)
        self._emit_step_trace(job)
        self._record_sweep(raw, self._last_quantity, frame.value_kind)
        if step_done:
            # 進み具合は回数分を測り終えた段の数
            self._task_progress(job.task, job.index + 1, len(job.freqs), time.time() - job.started)
            if job.index + 1 >= len(job.freqs):
                self._step_end(None)
                return True
            self._step_goto(job.index + 1)
        self._changed()
        return True

    def _step_info(self, job: _StepJob) -> dict:
        c = job.cfg
        a = job.adopted
        return {"combine": c["combine"], "steps_done": job.steps_done, "steps": len(job.freqs),
                "per_step": c["per_step"], "start_hz": c["start_hz"], "stop_hz": c["stop_hz"], "step_hz": c["step_hz"],
                # 測った条件（周波数の範囲・RBW・点の間隔・送った b3）。結果のトレースの内部の漏れは、あとで Center・Span・
                # RBW を変えてもこの条件で判定する（_step_leaks: 設計L §1.5）。ステップ掃引はいつもイメージ除去なし
                # （b3=1 だけ）で送る
                "measured": {"start_hz": int(a.start_hz), "stop_hz": int(a.stop_hz), "rbw_hz": int(a.rbw_hz),
                             "point_step_hz": int(a.axis.step_hz), "b3_both": False}}

    def _step_put_result(self, job: _StepJob, marked) -> None:
        vals = job.combiner.result()
        res = dataclasses.replace(marked, values=vals, flags=job.combiner.flags)
        self._traces[job.trace].set_result(res, steps=job.combiner.step_values(), step_info=self._step_info(job))

    def _emit_step_trace(self, job: _StepJob) -> None:
        t = self._traces[job.trace]
        if t.visible and t.values is not None:
            self._emit("trace", self._trace_payload(t))

    # ------------------------------------------------------------------ 終わり

    def _step_end(self, exc: Optional[BaseException], stop: bool = False) -> None:
        """終わる（exc が None なら全部の段が済んだ。Cancelled はやめた、それ以外は失敗）。

        そこまでの結果は残す。SG の周波数と動作を始める前に戻し、終わり方を知らせる（U-19）。stop は上段の「停止」で
        止めたとき（2026-10-09 ユーザー決定）: 終わり方は「やめる」と同じで、その後の掃引も止める（始める前が連続でも
        連続に戻さない。「停止」を押した人は掃引が止まることを期待する）。
        """
        job = self._step_job
        if job is None:
            return
        self._step_job = None
        t = self._traces[job.trace]
        kept = t.values is not None
        if not kept:
            # 1回も重ねないうちに終わった: トレースの設定を始める前に戻す（積んだ元に戻すも外す）
            apply_trace_settings(t, job.before_trace)
            if job.undo_entry is not None and self._undo and self._undo[-1] is job.undo_entry:
                self._undo.pop()
        self._run = "continuous" if job.prev_run == "continuous" and not stop else "stopped"
        if stop:
            self._last_stop_reason = "user"
        if job.orig_sg_hz is not None and self._adopted.sg_hz != job.orig_sg_hz:
            back = self._model.apply(SweepRequest(sg_hz=job.orig_sg_hz, one_shot=True), self._adopted)
            self._set_adopted(dataclasses.replace(back, adjustments=(), alternative=None), step_sweep=True)
        if job.ic_used:
            # 機器へ渡す条件のイメージ除去が選択に戻る。始めたときと同じく最大・最小保持は残す（設計F §8 の 2）
            self._on_condition(ConditionChange.IMAGE_REJECT, holds=HoldKeep.ALL)
        self._push_job()
        self._rebuild_views()
        self._refresh_markers()
        self._emit_step_trace(job)
        result = {"steps_done": job.steps_done, "steps": len(job.freqs), "trace": job.trace, "kept": kept,
                  "combine": job.cfg["combine"]}
        self._end_task(job.task, exc, result)

    def _step_stop(self, exc: BaseException) -> None:
        """外から止める（やめる・切断・送れない条件・終了）。"""
        self._step_end(exc)

    def _step_stop_by_user(self) -> bool:
        """上段の「停止」（命令 stop）から: ステップ掃引をしていれば「やめる」と同じ終わり方でやめて True。"""
        if self._step_job is None:
            return False
        self._step_end(Cancelled(), stop=True)
        return True

    def _step_cancel(self, task) -> bool:
        """cancel_task から: このステップ掃引の処理ならやめて True。"""
        job = self._step_job
        if job is None or job.task is not task:
            return False
        self._step_end(Cancelled())
        return True

    def _step_check_trace(self, number: int, kw: dict) -> None:
        """ステップ掃引が結果を入れているトレースの動作は変えない（表示するか・線は変えてよい）。"""
        job = self._step_job
        if job is not None and number == job.trace and set(kw) - {"visible", "line"}:
            raise self._error("step_sweep_trace", "number", {"number": number})

    def _step_leaks(self, t) -> Optional[dict]:
        """ステップ掃引の結果のトレースの内部の漏れと、漏れとみなす幅（{"leaks", "tolerance_hz"}。設計L §1.4・§1.5）。
        ステップ掃引の結果でなければ None。

        判定はそのトレースを測った条件（step_info の measured: 周波数の範囲・RBW・点の間隔・送った b3）で行う。いまの
        条件（_adopted）で判定すると、あとで Center・Span・RBW を変えたりゼロスパンにしたりしたときに、測ったときの山が
        漏れと分からなくなる。段ごとに SG の周波数が違うので、SG + ずれ の漏れも段の数だけ別の周波数に重なっている。
        SG で測ったトレースなので固定の周波数の漏れも持つ（いまのモードが SA でも）。b3 で動く漏れは送った b3 の位置
        （ステップ掃引は b3=1 だけ: 956MHz → 946MHz。display_marks.fixed_leaks）。
        """
        info = getattr(t, "step_info", None)
        m = (info or {}).get("measured")
        if not m:
            return None
        lo, hi = int(m["start_hz"]), int(m["stop_hz"])
        profile = t.profile or self._profile
        offs = (profile.get("internal_spurs_hz") or {}).get("sg_offset") or []
        out = [lk for lk in dm.fixed_leaks(profile, b3_both=bool(m["b3_both"])) if lo <= lk["freq_hz"] <= hi]
        for f in ss.plan(info):
            for off in offs:
                g = int(f) + int(off)
                if lo <= g <= hi:
                    out.append({"freq_hz": g, "kind": "sg_offset", "offset_hz": int(off)})
        return {"leaks": out, "tolerance_hz": dm.leak_tolerance_hz(int(m["rbw_hz"]), int(m["point_step_hz"]))}

    # ------------------------------------------------------------------ 画面へ送る形

    def _step_rows_payload(self, t, shift: float, fmt: str = formats.DB) -> dict:
        """全部重ねるときの段ごとの線（主の値と同じ画面の単位・表示の形式の float32 [段, 点]）。1本の点が多ければ続く点の
        最大で間引き、x0・dx で横の位置を渡す。"""
        rows = display_values(t.steps, t.quantity, shift, fmt)
        dec, k = ss.decimate_rows(rows)
        ax = t.axis
        unit = ax.step_hz if ax.step_hz > 0 else 1
        base = ax.start_hz if ax.step_hz > 0 else 0
        return {"x0": float(base + unit * (k - 1) / 2), "dx": float(unit * k),
                "values": np.asarray(dec, dtype=np.float32)}
