"""計測器の本体のうち、校正作業（設計K §4）。

Instrument（instrument.py）に混ぜて使う。メソッドは制御スレッドから呼ばれる。
- 作業の値（どの点を測ったか・RBW・NF の結果・取り込む旧ファイル）は level/cal_work.CalSession が持ち、状態の
  `calibration_work` で全部の画面へ届く（Qt と Web は同じ作業を映す: A §4.4）
- 測る掃引は、いまの条件とは別の自分専用の条件で、決めた回数だけ機器スレッドへ渡す（ノーマライズの基準を取るのと
  同じ仕組み: 設計E §6.2）。メイン画面の条件は変えず、終わったら取る前の動作に戻す（K §4.1）
- 測るのは長い処理（数秒〜数十秒）なので、命令はすぐ task_id を返し、進み具合と終わりを知らせる。やめられる（U-19）
- 保存するまで校正データは何も変わらない。保存は新しいファイルを足し、これから使うものにする（K §4.1 の 4）
"""
from __future__ import annotations

import datetime
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .instrument_common import CommandError
from .level import cal_work as cw
from .level.calibration import Calibration, CalibrationStore, load_file
from .level.convert import LevelConverter
from .level.legacy_cal import LegacyCalError, parse_legacy_cal
from .storage import Cancelled
from .trace import ConditionChange
from .types import AdoptedSweep, Mode, SweepRequest

# 校正の形（設計D §5.2）が使える機器の生の値の形（GigaSt とシミュレータ）
_GIGAST_ENCODING = "gigast_ad_u16"

# 長い処理の種類（画面の文言は画面側: U-22）
TASK_POINT = "cal_point"
TASK_RBW = "cal_rbw"
TASK_NF = "cal_nf"
CAL_TASKS = (TASK_POINT, TASK_RBW, TASK_NF)


@dataclass
class _CalStep:
    """同じ条件で決めた回数だけ測る1段。key は結果の置き場所の名前。"""

    key: tuple
    adopted: AdoptedSweep
    count: int


@dataclass
class _CalJob:
    """校正のための掃引（段の並び）。段ごとにパワー平均を作り、on_step で調べ、全部済んだら on_done。"""

    task: object
    steps: list
    on_step: Callable[["_CalJob", _CalStep, dict], None]
    on_done: Callable[["_CalJob"], dict]
    prev_run: str
    index: int = 0
    n: int = 0
    acc: Optional[np.ndarray] = None
    acc_cold: Optional[np.ndarray] = None
    results: dict = field(default_factory=dict)

    def step(self) -> _CalStep:
        return self.steps[self.index]

    @property
    def total(self) -> int:
        return sum(s.count for s in self.steps)

    @property
    def done(self) -> int:
        return sum(s.count for s in self.steps[: self.index]) + self.n


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_store(store: CalibrationStore, serial: Optional[str]):
    """書き込みスレッドで: いま使っている校正・そのファイルの名前・校正作業で測った値の最新。"""
    if not serial:
        return None, None, {}
    base, name = None, None
    try:
        path = store.current_file(serial)
        if path is not None:
            base, name = load_file(path), path.name
    except Exception:  # noqa: BLE001 — 読めない校正は「無し」として扱う（換算と同じ: 設計D §5.5）
        base, name = None, None
    history = []
    for p in store.history(serial):
        try:
            history.append(load_file(p))
        except Exception:  # noqa: BLE001 — 読めないファイルは比べる材料にしない
            continue
    return base, name, cw.measured_items(history)


class CalMixin:
    """校正作業（Instrument に混ぜる）。"""

    def _init_cal(self) -> None:
        self._cal_session: Optional[cw.CalSession] = None
        self._cal_job: Optional[_CalJob] = None

    # ------------------------------------------------------------------ 状態

    def _cal_serial(self) -> Optional[str]:
        """校正を当てる機器（つないでいる GigaSt）。RTL-SDR の校正は別の形（設計Q §6.6）なので None。"""
        if self._conn != "connected" or (self._profile.get("raw") or {}).get("encoding") != _GIGAST_ENCODING:
            return None
        return (self._identity or {}).get("serial")

    def _cal_device_reason(self) -> Optional[str]:
        """測れない・取り込めない理由のコード（入口で止める: U-02）。"""
        if self._conn != "connected":
            return "not_connected"
        if (self._profile.get("raw") or {}).get("encoding") != _GIGAST_ENCODING:
            return "calibration_not_for_device"
        s = self._cal_session
        if s is not None and s.serial != (self._identity or {}).get("serial"):
            # 測った点は前の機器のもの。別の機器の校正に混ぜない
            return "calibration_device_changed"
        return None

    def _cal_state(self) -> Optional[dict]:
        s = self._cal_session
        if s is None:
            return None
        return s.snapshot(task_id=self._cal_job.task.task_id if self._cal_job else None,
                          device_reason=self._cal_device_reason())

    def _cal_need(self) -> cw.CalSession:
        if self._cal_session is None:
            raise CommandError("calibration_not_open")
        return self._cal_session

    def _cal_check_idle(self) -> None:
        if self._cal_job is not None:
            raise CommandError("calibration_in_progress")

    def _cal_result(self) -> dict:
        return {"calibration_work": self._cal_state()}

    # ------------------------------------------------------------------ 開く・切り替える・閉じる

    def _cmd_cal_open(self, kind: str = cw.KIND_FREQ) -> object:
        """校正の画面を開く（K §4.1）。作業があればそれを返す（保存していない作業は消さない: U-18）。"""
        if kind not in cw.KINDS:
            raise CommandError("bad_value", "kind", {"choices": list(cw.KINDS)})
        s = self._cal_session
        if s is not None:
            if s.kind != kind and not s.unsaved() and self._cal_job is None:
                s.set_kind(kind)
                self._changed()
            return self._cal_result()
        serial = self._cal_serial()
        model = self._profile.get("model") if serial else None
        store = self._cal_store

        def then(r) -> dict:
            if self._cal_session is None:
                self._cal_session = cw.CalSession(kind, model, serial, *r)
                self._changed()
            return self._cal_result()

        return self._io(lambda: _read_store(store, serial), then)

    def _cmd_cal_set_kind(self, kind: str, discard: bool = False) -> dict:
        """何を校正するかを選ぶ（K §4.1 の 1）。保存していない作業があれば、discard で捨てると決めるまで断る。"""
        s = self._cal_need()
        if kind not in cw.KINDS:
            raise CommandError("bad_value", "kind", {"choices": list(cw.KINDS)})
        self._cal_check_idle()
        if kind != s.kind:
            if s.unsaved() and discard is not True:
                raise CommandError("calibration_unsaved", "kind", {"count": s.unsaved(), "kind": s.kind})
            s.set_kind(kind)
            self._changed()
        return self._cal_result()

    def _cmd_cal_close(self) -> dict:
        """校正の画面を閉じる。測っている途中ならやめる。保存していない作業は捨てる（聞くのは画面: U-18）。"""
        if self._cal_job is not None:
            self._cal_abort(Cancelled())
        self._cal_session = None
        self._changed()
        return {}

    def _cal_refresh(self) -> None:
        """機器をつないだ・校正が変わったとき、作業が空なら「前の値」と機器を読み直す。"""
        s = self._cal_session
        if s is None or s.unsaved() or self._cal_job is not None:
            return
        serial = self._cal_serial()
        model = self._profile.get("model") if serial else None
        if serial is None and s.serial is not None:
            return  # 切れただけ（同じ機器が戻るのを待つ）。前の値はそのまま
        wf = self._writer.submit(_read_store, self._cal_store, serial)

        def done(f) -> None:
            def finish() -> None:
                if self._cal_session is not s or s.unsaved() or self._cal_job is not None:
                    return
                r = (None, None, {}) if f.exception() is not None else f.result()
                s.rebase(model, serial, *r)
                self._changed()

            self._post_call(finish)

        wf.add_done_callback(done)

    # ------------------------------------------------------------------ 入れる値・点を選ぶ・飛ばす・確かめる

    def _cmd_cal_set(self, sg_level_dbm=None, cable_loss_db=None, rbw_extra=None, nf_freq_hz=None,
                     current=None) -> dict:
        """SG のレベル・ケーブルの損失（K §4.2）・RBW のほかの周波数（K §4.3）・NF の周波数・いまの点を変える。"""
        s = self._cal_need()
        self._cal_check_idle()

        def num(name, v, lo, hi) -> float:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= float(v) <= hi):
                raise CommandError("bad_value", name, {"min": lo, "max": hi})
            return float(v)

        if sg_level_dbm is not None:
            s.sg_level_dbm = num("sg_level_dbm", sg_level_dbm, *cw.SG_LEVEL_RANGE_DBM)
        if cable_loss_db is not None:
            s.cable_loss_db = num("cable_loss_db", cable_loss_db, *cw.CABLE_LOSS_RANGE_DB)
        if rbw_extra is not None:
            if not isinstance(rbw_extra, bool):
                raise CommandError("bad_value", "rbw_extra")
            s.rbw_extra = rbw_extra
        if nf_freq_hz is not None:
            if isinstance(nf_freq_hz, bool) or not isinstance(nf_freq_hz, int):
                raise CommandError("not_integer", "nf_freq_hz")
            lo, hi = cw.NF_FREQ_RANGE_HZ
            if not (lo <= nf_freq_hz <= hi):
                raise CommandError("bad_value", "nf_freq_hz", {"min": lo, "max": hi})
            s.nf_freq_hz = nf_freq_hz
        if current is not None:
            if isinstance(current, bool) or not isinstance(current, int) or not (0 <= current < cw.GRID_POINTS):
                raise CommandError("bad_value", "current", {"min": 0, "max": cw.GRID_POINTS - 1})
            if s.pending is not None:
                raise CommandError("calibration_confirm_pending")
            s.select(current)
        self._changed()
        return self._cal_result()

    def _cmd_cal_skip(self) -> dict:
        """いまの点を飛ばす（前の値のまま。印を付ける: K §4.2）。"""
        s = self._cal_need()
        self._cal_check_idle()
        if s.kind != cw.KIND_FREQ:
            raise CommandError("calibration_wrong_kind", "kind", {"kind": s.kind})
        if s.pending is not None:
            raise CommandError("calibration_confirm_pending")
        s.skip()
        self._changed()
        return self._cal_result()

    def _cmd_cal_confirm(self, accept: bool) -> dict:
        """いまの補正との差が大きい点を入れるか（K §4.2）。"""
        s = self._cal_need()
        if not isinstance(accept, bool):
            raise CommandError("bad_value", "accept")
        if s.pending is None:
            raise CommandError("calibration_no_pending")
        s.confirm(accept)
        self._changed()
        return self._cal_result()

    # ------------------------------------------------------------------ 測る

    def _cal_adopted(self, mode: Mode, center_hz: int, rbw_hz: int) -> AdoptedSweep:
        """校正専用の掃引条件。Span 1MHz（Step はモデルが決める 2kHz）・イメージ除去なし（b3=1: K §4.2）。
        いまの条件とは別に作り、メイン画面の条件は変えない（K §4.1）。"""
        req = SweepRequest(center_hz=int(center_hz), span_hz=cw.SPAN_HZ, edited=("center_hz", "span_hz"),
                           rbw_preference_hz=rbw_hz, mode=mode, sg_hz=int(center_hz) if mode == Mode.SG else None,
                           image_reject="off", points=0, one_shot=True)
        try:
            a = self._model.apply(req, self._adopted)
        except (ValueError, TypeError) as e:
            raise CommandError("bad_value", None, {"message": str(e)}) from e
        if a.rbw_hz != rbw_hz or a.image_reject_used != "off":
            raise CommandError("bad_value", "rbw_hz", {"requested": rbw_hz, "adopted": a.rbw_hz})
        return a

    def _cmd_cal_measure(self, state: Optional[str] = None) -> dict:
        """いまの手順を測る。周波数特性は選んでいる点、RBW は全部（全自動）、NF は state（with_att / without_att）。"""
        s = self._cal_need()
        self._cal_check_idle()
        self._check_not_taking_reference()
        why = self._cal_device_reason()
        if why is not None:
            raise CommandError(why)
        if s.kind == cw.KIND_FREQ:
            return self._cal_measure_point(s)
        if s.kind == cw.KIND_RBW:
            return self._cal_measure_rbw(s)
        if s.kind == cw.KIND_NF:
            if state not in cw.NF_STATES:
                raise CommandError("bad_value", "state", {"choices": list(cw.NF_STATES)})
            return self._cal_measure_nf(s, state)
        raise CommandError("calibration_wrong_kind", "kind", {"kind": s.kind})

    def _cal_measure_point(self, s: cw.CalSession) -> dict:
        if s.pending is not None:
            raise CommandError("calibration_confirm_pending")
        idx = s.current
        f = cw.measure_freq(s.points[idx].freq_hz)
        step = _CalStep(("point", idx), self._cal_adopted(Mode.SA, f, cw.FREQ_RBW_HZ), cw.AVERAGES)

        def on_step(job: _CalJob, st: _CalStep, res: dict) -> None:
            peak, _hz, floor = self._cal_peak(st.adopted, res["db"], res["mask"])
            if peak - floor < cw.PEAK_ABOVE_FLOOR_DB:
                raise CommandError("cal_no_signal", None, {"index": idx, "freq_hz": f, "peak_dbm": peak,
                                                           "floor_dbm": floor})
            job.results["peak"] = peak

        def on_done(job: _CalJob) -> dict:
            pend = s.point_result(idx, job.results["peak"], _now_iso())
            return {"index": idx, "pending": pend, "peak_dbm": job.results["peak"]}

        return self._cal_start(TASK_POINT, [step], on_step, on_done, {"index": idx, "freq_hz": f})

    def _cal_measure_rbw(self, s: cw.CalSession) -> dict:
        freqs = [cw.RBW_FREQ_HZ] + (list(cw.RBW_EXTRA_HZ) if s.rbw_extra else [])
        # 500MHz・180kHz を最初に測る（TG 出力と SP 入力がつながっているかを先に確かめる: K §4.3 の自己診断）
        steps = [_CalStep(("rbw", f, r), self._cal_adopted(Mode.SG, f, r), cw.AVERAGES)
                 for f in freqs for r in cw.RBW_LIST_HZ]

        def on_step(job: _CalJob, st: _CalStep, res: dict) -> None:
            peak, _hz, floor = self._cal_peak(st.adopted, res["db"], res["mask"])
            _k, f, r = st.key
            if peak - floor < cw.PEAK_ABOVE_FLOOR_DB:
                raise CommandError("cal_no_signal_direct", None, {"freq_hz": f, "rbw_hz": r, "peak_dbm": peak,
                                                                  "floor_dbm": floor})
            job.results[(f, r)] = peak

        def on_done(job: _CalJob) -> dict:
            rows = [{"freq_hz": f, "peaks_dbm": {r: job.results[(f, r)] for r in cw.RBW_LIST_HZ}} for f in freqs]
            s.set_rbw(rows)
            return {"offsets_db": {str(r): v for r, v in (s.rbw_new() or {}).items()}}

        return self._cal_start(TASK_RBW, steps, on_step, on_done, {"freqs_hz": freqs})

    def _cal_measure_nf(self, s: cw.CalSession, state: str) -> dict:
        f = s.nf_freq_hz
        step = _CalStep(("nf", state), self._cal_adopted(Mode.NF, f, cw.FREQ_RBW_HZ), cw.AVERAGES)

        def on_step(job: _CalJob, st: _CalStep, res: dict) -> None:
            m = res["mask"]
            # HOT と COLD はそれぞれパワー平均してから差を取る（設計M §1）。点ごとの差の平均を、その状態の値にする
            y = float(np.mean(res["db"][m] - res["cold_db"][m]))
            if y < cw.NF_MIN_Y_DB:
                raise CommandError("cal_nf_no_difference", None, {"state": state, "y_db": y})
            job.results["y"] = y

        def on_done(job: _CalJob) -> dict:
            s.set_nf(state, job.results["y"])
            d = s.nf_diff()
            if d is not None and d <= 0:
                # 測れたが、アッテネータの有無で差が逆か無い（入れ替えて測った・アッテネータが入っていない）
                s.message = {"code": "cal_nf_att_no_difference", "detail": {"diff_db": d}}
            return {"state": state, "y_db": job.results["y"], "amp_raw": s.nf_amp()}

        return self._cal_start(TASK_NF, [step], on_step, on_done, {"state": state, "freq_hz": f})

    def _cal_start(self, kind: str, steps: list, on_step, on_done, detail: dict) -> dict:
        total = sum(st.count for st in steps)
        task = self._new_task(kind, total=total, detail=detail)
        self._cal_job = _CalJob(task, steps, on_step, on_done, prev_run=self._run)
        self._cal_session.message = None
        self._run = "calibration"
        self._push_job()
        self._task_progress(task, 0, total, 0.0)
        self._changed()
        return {"task_id": task.task_id}

    # ---- 機器スレッドとのやりとり（instrument.py の _push_job・_on_sweep から呼ぶ）

    def _cal_push_job(self) -> bool:
        """校正の掃引を機器スレッドへ渡す。校正の掃引中でなければ False（いつもの掃引を渡す）。"""
        job = self._cal_job
        if job is None:
            return False
        st = job.step()
        count = max(0, st.count - job.n)
        wanted = self._conn == "connected" and count > 0
        self._worker.set_job(self._gen, st.adopted if wanted else None, count)
        return True

    def _cal_source(self) -> Optional[AdoptedSweep]:
        """校正の掃引中なら、いま機器に渡している条件（TG・SG を出しているかの表示: 設計L）。"""
        return None if self._cal_job is None else self._cal_job.step().adopted

    @staticmethod
    def _cal_mask(a: AdoptedSweep) -> np.ndarray:
        """使う点: Start〜Stop の中で、掃引の始めの不安定な点を除く（設計C §5.6）。"""
        f = a.axis.freqs()
        m = (f >= a.start_hz) & (f <= a.stop_hz)
        m[: max(0, int(a.transient_head_points))] = False
        return m

    def _cal_on_sweep(self, raw) -> None:
        job = self._cal_job
        st = job.step()
        to_w = lambda ad: np.power(10.0, self._converter.ad_to_db(ad) / 10.0)  # noqa: E731
        try:
            if st.adopted.mode == Mode.NF:
                hot, cold = to_w(raw.raw_hot), to_w(raw.raw_cold)
                job.acc = hot if job.acc is None else job.acc + hot
                job.acc_cold = cold if job.acc_cold is None else job.acc_cold + cold
            else:
                p = to_w(raw.raw)
                job.acc = p if job.acc is None else job.acc + p
        except Exception as e:  # noqa: BLE001 — 形の違う値で校正の掃引を止めたままにしない（やめて知らせる）
            self._cal_abort(CommandError("internal_error", None, {"message": f"{type(e).__name__}: {e}"}))
            return
        job.n += 1
        self._task_progress(job.task, job.done, job.total, time.time() - job.task.started_at)
        if job.n < st.count:
            return
        res = {"db": 10.0 * np.log10(job.acc / job.n), "mask": self._cal_mask(st.adopted)}
        if job.acc_cold is not None:
            res["cold_db"] = 10.0 * np.log10(job.acc_cold / job.n)
        try:
            job.on_step(job, st, res)
        except CommandError as e:
            self._cal_abort(e)
            return
        job.index += 1
        job.n, job.acc, job.acc_cold = 0, None, None
        if job.index < len(job.steps):
            self._push_job()
            return
        self._cal_end_sweeps(job)
        try:
            result = job.on_done(job)
        except CommandError as e:
            self._cal_set_message(job, e)
            self._end_task(job.task, e)
            return
        self._end_task(job.task, None, result)

    def _cal_peak(self, a: AdoptedSweep, db: np.ndarray, mask: np.ndarray) -> tuple[float, int, float]:
        """パワー平均のピーク（補正なしの dBm）・その周波数・ノイズフロア（機種のプロファイルの RBW ごとの実測値。
        無ければ使う点の中央値）。"""
        idx = np.flatnonzero(mask)
        i = int(idx[int(np.argmax(db[idx]))])
        floors = ((self._profile.get("image_reject") or {}).get("noise_floor_ad") or {})
        ad = floors.get(str(int(a.rbw_hz)))
        floor = float(self._converter.ad_to_db(np.array([ad]))[0]) if ad is not None else float(np.median(db[idx]))
        return float(db[i]), int(a.axis.freqs()[i]), floor

    def _cal_end_sweeps(self, job: _CalJob) -> None:
        """校正の掃引を終え、測る前の動作に戻す（メイン画面の条件は変えていない: K §4.1）。"""
        self._cal_job = None
        self._run = job.prev_run if job.prev_run in ("continuous", "single") else "stopped"
        self._push_job()

    def _cal_set_message(self, job: _CalJob, exc: BaseException) -> None:
        """やめた・測れなかったことを、作業の値に残す（その手順のそばに出す: U-25。どの画面にも同じに出る）。"""
        s = self._cal_session
        if s is None:
            return
        code = "cancelled" if isinstance(exc, Cancelled) or getattr(exc, "code", None) == "cancelled" \
            else getattr(exc, "code", "internal_error")
        s.message = {"code": code, "task": job.task.kind, "detail": dict(getattr(exc, "detail", None) or {})}

    def _cal_abort(self, exc: BaseException) -> None:
        """やめた・失敗した・切れた。校正データは変わらない（測りかけの点は入れない）。"""
        job = self._cal_job
        if job is None:
            return
        self._cal_end_sweeps(job)
        self._cal_set_message(job, exc)
        self._end_task(job.task, exc)

    # ------------------------------------------------------------------ 旧ファイル（K §4.5・設計D §5.6）

    def _cmd_cal_legacy_load(self, path: str) -> object:
        """旧校正ファイルを読み、取り込む値として作業に置く（保存するまで何も変わらない）。"""
        s = self._cal_need()
        self._cal_check_idle()
        if s.kind != cw.KIND_LEGACY:
            raise CommandError("calibration_wrong_kind", "kind", {"kind": s.kind})
        if s.serial is None:
            raise CommandError(self._cal_device_reason() or "device_not_selected")
        p = Path(path)

        def job() -> bytes:
            try:
                return p.read_bytes()
            except OSError as e:
                raise CommandError("file_unreadable", "path", {"message": str(e)}) from e

        def then(data: bytes) -> dict:
            try:
                legacy = parse_legacy_cal(data)
            except LegacyCalError as e:
                raise CommandError("legacy_unreadable", "path",
                                   {"line": e.line, "field": e.field, "reason": e.reason}) from e
            if self._cal_session is not s:
                raise CommandError("calibration_not_open")
            sha = hashlib.sha256(data).hexdigest()
            cal = Calibration.from_legacy(legacy, model=s.model, serial=s.serial, file_name=p.name, sha256=sha,
                                          created_at=_now_iso())
            s.set_legacy(cal, p.name, sha)
            self._changed()
            return self._cal_result()

        return self._io(job, then)

    # ------------------------------------------------------------------ 保存（K §4.1 の 4）

    def _cmd_cal_save(self) -> object:
        """新しい校正データのファイルを足し、これから使うものにする（古いものは消さない: 設計D §5.2）。"""
        s = self._cal_need()
        self._cal_check_idle()
        if s.kind == cw.KIND_FREQ and s.pending is not None:
            raise CommandError("calibration_confirm_pending")
        cand = s.candidate(_now_iso())
        if cand is None:
            raise CommandError("calibration_nothing_to_save")
        store = self._cal_store
        serial = s.serial

        def job():
            try:
                path = store.save(cand, make_current=True)
            except (OSError, ValueError) as e:
                raise CommandError("calibration_unwritable", None, {"message": str(e)}) from e
            return path.name, _read_store(store, serial)[2]

        def then(r) -> dict:
            name, measured = r
            if self._cal_serial() == serial:
                # 校正が変わると値の意味が変わる（設計F §5.3）。掃引はそのまま続け、次の掃引から新しい校正で換算する
                self._cal = cand
                self._converter = LevelConverter(self._profile, cand)
                self._on_condition(ConditionChange.CALIBRATION)
                self._push_job()
            if self._cal_session is s:
                s.after_save(cand, name, measured)
            self._changed()
            return {"file": name, "created_at": cand.created_at, "kind": s.kind, **self._cal_result()}

        return self._io(job, then)
