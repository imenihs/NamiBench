"""計測器の本体のうち、PlutoSDR の受信の利得の補正（設計Q §7.6）。Instrument に混ぜる。

- 補正の表（level/pluto_gain.py）は PlutoSDR の個体（シリアル番号）ごとに <cal_root>/<シリアル番号>/plutosdr/ に
  履歴として足し、current.json が使うものを指す（RTL-SDR の個体の校正と同じ置き方: 設計Q §6.6）。つないだときに読む
- 表示のレベル = dBFS − 利得 + 補正(周波数, 利得)。表が無ければ補正なし（画面は利得の欄のそばに「利得の補正なし」）。
  単位は「dB（未校正）」のまま（絶対のレベルはこの補正では決まらない）。自動の利得の間は利得が分からないので当てない
- 校正の作業（校正の画面の「PlutoSDR の利得の補正」。全自動）: 機器の口（driver.gain_calibration）を別のスレッドで
  動かす。その間は機器スレッドに掃引を渡さない（掃引と口を取り合わない）。周波数を1つ測るたびに作業に列を足す。
  やめた・失敗した・切れたときも、それまでの列は残る（保存すれば、測っていない周波数は前の表のまま）
- TG・S11 では、基準を取り始めた時点（S11 はオープンを測り始めた時点）から、その基準を捨てるかそのモードを出るまで、
  受信の利得と送信の減衰の両方を変えさせない（設計Q §7.8 の決定①・§7.9）。基準と違う利得・出力で測ると比がその差
  だけずれる（利得は補正の表があっても誤差が比に乗る。送信の減衰は基準にも測った値にも入っていない: 減衰 −40 → −20dB
  で受信は +20dB）。利得の欄・「利得を合わせる」・送信の減衰の欄・rtl_tcp の相手の利得の命令のどれも。同じ値を送るのは
  変えていないので断らない。固定の理由にするのは、いまのモードの基準だけ（ほかのモードの基準は当たらない）。
  S11 の測ったオープンを捨てる条件（S11 を出た・電源の入れ直し）でも外れる
"""
from __future__ import annotations

import datetime
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .devices.plutosdr.gain_cal import NoSignal, TooStrong
from .instrument_common import CommandError
from .level import cal_work as cw
from .level import pluto_gain as pg
from .storage import Cancelled
from .types import DeviceDisconnected, DeviceError, Mode, SweepDiscarded

TASK_PLUTO_GAIN = "cal_pluto_gain"
# 利得と送信の減衰を変えさせない理由のコード（状態と命令の断りで同じ）。TG は基準を取り始めた後（設計Q §7.9）、
# S11 はオープンを測り始めた後（設計Q §7.8）
TG_CONDITIONS_LOCK = "plutosdr_tg_conditions_locked"
S11_CONDITIONS_LOCK = "plutosdr_s11_conditions_locked"
_CONDITIONS_LOCK = {Mode.TG: TG_CONDITIONS_LOCK, Mode.S11: S11_CONDITIONS_LOCK}


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class _PlutoCalJob:
    """利得の補正を測っている間（校正の作業の _cal_job に置く。GigaSt の _CalJob と違い、掃引の段を持たない）。"""

    task: object
    prev_run: str
    serial: str

    def step(self):  # 校正の掃引の段は無い（_cal_source が見る）
        return None


class PlutoGainCalMixin:
    """PlutoSDR の受信の利得の補正（Instrument に混ぜる）。"""

    def _init_pluto_gain(self) -> None:
        self._pluto_gain_store = pg.GainCorrectionStore(self._cal_store.root)
        self._pluto_gain_cal: Optional[pg.GainCorrection] = None
        # 測る周波数（試験はここを少なくして短くする）
        self._pluto_gain_grid: list[int] = pg.grid_freqs()

    # ------------------------------------------------------------------ 表を読む・当てる

    def _pluto_gain_load(self, serial: str) -> None:
        try:
            self._pluto_gain_cal = self._pluto_gain_store.load_current(serial)
        except Exception as e:  # noqa: BLE001 — 読めない表は「無し」として知らせる
            self._pluto_gain_cal = None
            self._notice("calibration_unreadable", {"message": str(e), "kind": "plutosdr_gain"})

    def _pluto_gain_offset(self, freqs_hz, gain_db: Optional[float]) -> Optional[np.ndarray]:
        """いまの掃引の周波数の補正（dB）。PlutoSDR でない・表が無い・自動の利得の間は None。"""
        cal = self._pluto_gain_cal
        if cal is None or gain_db is None or not self._is_plutosdr():
            return None
        return cal.values_at(freqs_hz, float(gain_db))

    def _pluto_gain_state(self) -> Optional[dict]:
        cal = self._pluto_gain_cal
        if cal is None or not self._is_plutosdr():
            return None
        f = cal.freqs_hz
        return {"created_at": cal.created_at, "freqs": len(f), "range_hz": [f[0], f[-1]] if f else None}

    def _pluto_gain_lock(self) -> Optional[str]:
        """受信の利得を変えさせない理由のコード（_pluto_conditions_lock の間）。"""
        return self._pluto_conditions_lock() if self._is_plutosdr() else None

    def _pluto_tx_atten_lock(self) -> Optional[str]:
        """送信の減衰を変えさせない理由のコード（利得と同じ期間: 設計Q §7.8・§7.9）。"""
        return self._pluto_conditions_lock() if self._is_plutosdr() else None

    def _pluto_conditions_lock(self) -> Optional[str]:
        """いまのモード（TG・S11）で、基準を取っている（取る前に利得を合わせている間も）・選んでいる基準がそのモードの
        ものである・S11 で測ったオープンがある間は、理由のコード。基準を捨てる・そのモードを出る（S11 のオープンは
        _s11_open_follow が捨てる）と外れる。取るのをやめた・失敗した・切れたときは、ほかに理由が無ければ外れる。"""
        mode = self._adopted.mode
        code = _CONDITIONS_LOCK.get(mode)
        if code is None:
            return None
        job = getattr(self, "_ref_job", None)
        taking = job is not None and job.adopted.mode == mode
        has_open = mode == Mode.S11 and getattr(self, "_s11_open", None) is not None
        if taking or has_open or self._pluto_ref_of(mode):
            return code
        return None

    def _pluto_ref_of(self, mode: Mode) -> bool:
        """選んでいる基準がそのモードのものか（モードを替えても基準は選んだまま残る。ほかのモードの基準は当たらないので
        固定の理由にしない）。"""
        ref = getattr(self, "_ref", None)
        return ref is not None and getattr(ref, "mode", None) == mode

    def _pluto_check_gain_lock(self) -> None:
        why = self._pluto_gain_lock()
        if why is not None:
            raise CommandError(why, "gain_db")

    # ------------------------------------------------------------------ 校正の作業

    def _pluto_cal_info(self) -> tuple[Optional[str], Optional[str], Optional[pg.GainCorrection]]:
        """(model, serial, 前の表)。PlutoSDR につないでいなければ全部 None。"""
        if self._conn != "connected" or not self._is_plutosdr():
            return None, None, None
        return str(self._profile.get("model")), str((self._identity or {}).get("serial") or ""), self._pluto_gain_cal

    def _pluto_cal_sync(self, s: cw.CalSession) -> None:
        """作業へ、つないでいる PlutoSDR と前の表を入れる（開いた・つないだ・保存した）。切れただけなら前のまま。"""
        model, serial, base = self._pluto_cal_info()
        if serial is None and s.pluto_serial is not None:
            return
        s.set_pluto(model, serial, base, self._pluto_gain_grid)

    def _pluto_cal_reason(self, s: cw.CalSession) -> Optional[str]:
        if not self._is_plutosdr():
            return "calibration_not_for_device"
        if s.pluto_serial is not None and s.pluto_serial != (self._identity or {}).get("serial"):
            return "calibration_device_changed"
        return None

    def _cal_measure_pluto_gain(self, s: cw.CalSession) -> dict:
        """全自動: 周波数ごとに利得を帯の範囲いっぱいに 1dB 刻みで変えて送信のトーンを読み、補正の列を作る。"""
        if any(t.kind in ("rtlsdr_fit_gain", "plutosdr_fit_gain") for t in self._tasks.values()):
            raise CommandError("rtlsdr_fit_running")
        dev = self._device
        self._pluto_cal_sync(s)  # 測る PlutoSDR（シリアル番号）と前の表を、いまつないでいるものにする
        freqs = list(s.pluto_grid)
        ext = float(s.ext_atten_db)
        total = 0  # 機器の口が最初に数えて知らせる
        task = self._new_task(TASK_PLUTO_GAIN, total=total, detail={"freqs": len(freqs), "ext_atten_db": ext})
        job = _PlutoCalJob(task, prev_run=self._run, serial=str((self._identity or {}).get("serial") or ""))
        self._cal_job = job
        s.message = None
        s.pluto_rows, s.pluto_meta = {}, {}  # やり直しは全部を測り直す（前の作業の列は捨てる: 保存していない値は聞いてから）
        self._run = "calibration"
        self._push_job()  # 機器スレッドには掃引を渡さない（口を取り合わない）
        t0 = time.monotonic()

        def progress(done: int, tot: int) -> None:
            self._post_call(lambda: self._cal_pluto_progress(job, done, tot, time.monotonic() - t0))

        def on_freq(res) -> None:
            row = pg.row_from_levels(res.freq_hz, res.levels, res.gain_lo_db, res.gain_hi_db, tuple(res.tx_atten_db))
            meta = {"rx_lo_hz": res.rx_lo_hz, "gain_range_db": [res.gain_lo_db, res.gain_hi_db],
                    "dropped_db": list(res.dropped)}
            self._post_call(lambda: self._cal_pluto_row(job, row, meta))

        def run() -> None:
            try:
                dev.gain_calibration(freqs, ext, task.cancel, progress, on_freq)
                exc = None
            except BaseException as e:  # noqa: BLE001 — 終わり方として知らせる
                exc = e
            self._post_call(lambda: self._cal_pluto_end(job, exc))

        threading.Thread(target=run, name="pygigast-pluto-gain-cal", daemon=True).start()
        self._task_progress(task, 0, total, 0.0)
        self._changed()
        return {"task_id": task.task_id}

    def _cal_pluto_progress(self, job: _PlutoCalJob, done: int, total: int, elapsed: float) -> None:
        if self._cal_job is job:
            self._task_progress(job.task, done, total, elapsed)

    def _cal_pluto_row(self, job: _PlutoCalJob, row: Optional[pg.GainRow], meta: dict) -> None:
        s = self._cal_session
        if self._cal_job is not job or s is None or row is None:
            return
        s.add_pluto_row(row, meta)
        self._changed()

    def _cal_pluto_end(self, job: _PlutoCalJob, exc: Optional[BaseException]) -> None:
        """測る手順が終わった（機器の口は送信を止め、利得を戻してある）。やめた・切れたで先に終えていれば何もしない。"""
        if self._cal_job is not job:
            return
        s = self._cal_session
        if exc is None:
            self._cal_end_sweeps(job)
            n = len(s.pluto_rows) if s is not None else 0
            self._end_task(job.task, None, {"freqs": n})
            return
        if isinstance(exc, SweepDiscarded) and exc.reason in ("cancelled", "closing"):
            exc = Cancelled()
        elif isinstance(exc, NoSignal):
            exc = CommandError("cal_pluto_no_signal", None, {"freq_hz": exc.freq_hz})
        elif isinstance(exc, TooStrong):
            exc = CommandError("cal_pluto_overload", None, {"freq_hz": exc.freq_hz})
        elif isinstance(exc, DeviceDisconnected):
            exc = CommandError("disconnected")
        elif isinstance(exc, (DeviceError, SweepDiscarded)):
            exc = CommandError("device_error", None, {"message": str(exc)})
        elif not isinstance(exc, CommandError):
            exc = CommandError("internal_error", None, {"message": f"{type(exc).__name__}: {exc}"})
        self._cal_abort(exc)

    def _pluto_cal_after_save(self, cal: pg.GainCorrection) -> None:
        """保存した表をこれから使う（値の意味が変わるので蓄積を捨てる: 設計F §5.3）。"""
        if self._is_plutosdr() and (self._identity or {}).get("serial") == cal.serial:
            self._pluto_gain_cal = cal
            self._rtlsdr_changed_meaning("gain_correction")

    def _cal_save_pluto(self, s: cw.CalSession) -> object:
        cand = s.pluto_candidate(_now_iso())
        if cand is None:
            raise CommandError("calibration_nothing_to_save")
        store = self._pluto_gain_store

        def job():
            try:
                return store.save(cand).name
            except (OSError, ValueError) as e:
                raise CommandError("calibration_unwritable", None, {"message": str(e)}) from e

        def then(name: str) -> dict:
            self._pluto_cal_after_save(cand)
            if self._cal_session is s:
                s.after_save(cand, name)
            self._changed()
            return {"file": name, "created_at": cand.created_at, "kind": s.kind, **self._cal_result()}

        return self._io(job, then)
