"""計測器の本体のうち、PlutoSDR のスカラー S11 のオープンの前の「利得を合わせる」（設計Q §7.8 の決定②）。Instrument に混ぜる。

- 「オープンを測る」（reference_take の standard="open"）は、PlutoSDR ではまず送信しながら受信の利得を合わせ（TG と同じ
  多トーンを出し、振り切れないいちばん大きい利得から 3dB 下げる: 機器の fit_gain）、その利得でオープンを測る。受ける電力が
  いちばん大きいのはオープン（受け身の被測定物は |Γ| ≤ 1）なので、オープンで振り切れない利得なら被測定物でも振り切れない
- 合わせるのは基準を取る作業（_RefJob）の最初の段: 進み具合・やめるは同じ作業のまま（U-19）。その間は機器スレッドに
  掃引を渡さない（_RefJob.fitting）
- 選んだ利得は手動の値として設定に残し（自動だったら手動へ移す）、欄に出して知らせる（s11_open_gain_fitted）
- 合わせられなかった（失敗・いちばん小さい利得でも振り切れる）ときはオープンを測らず、理由を作業の終わりに出す。
  やめたときは機器が試す前の利得へ戻す
- 利得と送信の減衰を変えさせない期間（オープンを測り始めてから）は instrument_pluto_cal.py の _pluto_s11_lock
"""
from __future__ import annotations

import time

from .instrument_common import CommandError
from .storage import Cancelled
from .types import Mode

FIT_TASK_KINDS = ("rtlsdr_fit_gain", "plutosdr_fit_gain")


class PlutoS11Mixin:
    """PlutoSDR の S11 のオープンの前の利得合わせ（Instrument に混ぜる）。"""

    def _pluto_s11_open_needs_fit(self, mode: Mode, standard) -> bool:
        """合わせてからオープンを測るか。利得を変えさせない間（測ったオープンがある・基準を選んでいる: 決定①）に
        オープンを測り直すときは、決まっている利得のまま測る（合わせると、いまの基準・オープンと違う利得になる）。
        ただし自動の利得のままなら合わせる（自動の利得で取った値は利得が分からず、比べられない）。"""
        if not (self._is_plutosdr() and mode == Mode.S11 and standard == "open"):
            return False
        return self._pluto_s11_lock() is None or bool(self._pluto_cfg["agc"])

    def _pluto_s11_check_fit(self) -> None:
        """合わせられない状態なら断る（基準の作業を作る前）。"""
        if any(t.kind in FIT_TASK_KINDS for t in self._tasks.values()):
            raise CommandError("rtlsdr_fit_running")

    def _pluto_s11_start_fit(self, job) -> None:
        dev = self._device
        previous = dev.gain_db
        was_auto = bool(self._pluto_cfg["agc"])
        job.task.detail["phase"] = "fit_gain"
        total = self._plutosdr_fit_steps(job.adopted)
        self._task_progress(job.task, 0, total, 0.0)

        def progress(done: int, tot: int, elapsed: float) -> None:
            self._post_call(lambda: self._pluto_s11_fit_progress(job, done, tot, elapsed))

        fut = self._sdr_fit_future(dev, job.adopted, job.task.cancel, progress)
        fut.add_done_callback(lambda f: self._post_call(lambda: self._pluto_s11_fit_done(job, f, previous, was_auto)))

    def _pluto_s11_fit_progress(self, job, done: int, total: int, elapsed: float) -> None:
        if self._ref_job is job and job.fitting:
            self._task_progress(job.task, done, total, elapsed)

    def _pluto_s11_fit_done(self, job, fut, previous, was_auto: bool) -> None:
        if self._ref_job is not job or not job.fitting:
            return  # やめた・切れた（基準の作業はもう終えて知らせてある）
        exc = fut.exception()
        if exc is not None:
            if isinstance(exc, Cancelled):
                self._abort_ref_job(exc)
            else:
                self._abort_ref_job(CommandError("s11_open_fit_failed", None, {"message": str(exc)}))
            return
        res = fut.result()
        overload = bool(res.get("overload"))
        if not overload:
            job.fitting = False  # 次の _push_job（_sdr_fit_applied）からオープンの掃引を渡す
            job.task.detail["phase"] = "measure"
        self._plutosdr_take_fit(res)
        job.fitted = {"gain_db": float(res["gain_db"]), "previous_gain_db": previous, "was_auto": was_auto}
        applied = self._sdr_fit_applied(res)
        # 選んだ利得を知らせる（黙って設定を変えない: J §4.10。欄は状態で新しい値になる）
        self._notice("s11_open_gain_fitted", {"gain_db": float(res["gain_db"]), "previous_gain_db": previous,
                                              "was_auto": was_auto, "overload": overload, **applied})
        if overload:
            # いちばん小さい利得でも振り切れる: オープンを測っても値にならないので測らない
            self._abort_ref_job(CommandError("s11_open_overload", None, {"gain_db": float(res["gain_db"])}))
            return
        self._task_progress(job.task, 0, job.count, time.time() - job.task.started_at)
        self._changed()
