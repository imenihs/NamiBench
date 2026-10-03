"""計測器の本体のうち、ディスクと長い処理（ノーマライズの基準・保存・開く・書き出し・記録）。

Instrument（instrument.py）に混ぜて使う。メソッドは制御スレッドから呼ばれ、ディスクを待つ仕事は
書き込みスレッド（storage.WriterThread、1本）へ渡す（設計A §4.2）。制御スレッドはディスクを待たない。
- 短いディスクの仕事（基準の一覧・選ぶ・消す など）: 書き込みスレッドで行い、終わったら命令の結果を返す
- 長い処理（1秒を超えうるもの）: 命令はすぐ task_id を返し、`progress` と終わりの `notice`
  （task_done / task_failed / task_cancelled）で知らせる（設計A §4.3.1・設計H §4.1）
"""
from __future__ import annotations

import copy
import datetime
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .instrument_common import CommandError
from .instrument_diff import DIFF_TRACE_ID
from .instrument_display import NORMALIZED_MODES, db_values, unit_for
from .report_layout import DIVISIONS
from .normalize import (
    KIND_FINE,
    KIND_WIDE,
    ReferenceBuildError,
    ReferenceFileError,
    delete_reference,
    list_references,
    load_reference,
    new_reference,
    s11_segment_from_frames,
    save_reference,
    segment_from_frames,
    wide_range_hz,
)
from .storage import Cancelled, StorageError, WriterThread
from .storage import adopted_to_dict as storage_conditions
from .storage.export import export_traces_csv
from .display_export import export_display_csv
from .view_units import shift_values
from .storage.h5file import FileHeader, H5Record, TraceData, can_store, save_snapshot
from .storage.recording import (
    WORK_PREFIX,
    WORK_SUFFIX,
    Recorder,
    RecordingStats,
    convert_to_h5,
    discard_recording,
    find_unfinished,
    new_recording_path,
)
from .types import Mode, SweepRequest

# 縦軸の既定の dB/div（画面の既定と同じ。振幅に値が無いとき）
DEFAULT_DB_PER_DIV = 10.0

# 書き込みスレッドの仕事を、閉じるときに待つ上限
WRITER_CLOSE_TIMEOUT_S = 10.0

# 命令の結果を後で返す印（書き込みスレッドの仕事が終わってから Future に結果を入れる）
DEFERRED = object()

# GigaSt の生の値の形（機種のプロファイルの raw.encoding）。保存・記録に入れられる形の一覧は
# storage.h5file.RAW_ENCODINGS（GigaSt の AD 値と RTL-SDR のリニアの電力: 設計Q §6.1）
GIGAST_RAW_ENCODING = "gigast_ad_u16"

_SAFE = re.compile(r"[^0-9A-Za-z_-]")


@dataclass
class _Task:
    """動いている長い処理（状態の `tasks` の1行）。"""

    task_id: str
    kind: str
    started_at: float
    total: int = 0
    done: int = 0
    elapsed_s: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "kind": self.kind, "started_at": self.started_at, "done": self.done,
                "total": self.total, "elapsed_s": self.elapsed_s, "cancelling": self.cancel.is_set(),
                "detail": self.detail}


@dataclass
class _RefJob:
    """基準を取っている間の掃引（設計E §6.2）。"""

    task: _Task
    adopted: object
    count: int
    kind: str
    name: Optional[str]
    standard: Optional[str]
    prev_run: str
    frames: list = field(default_factory=list)
    # 取ったら 0dB が見える REF レベルにする（上段の「直結を 0dB にする」: 設計E §6.2 の「すぐ 0dB を示す」）
    show_zero: bool = False


def _json_float(x) -> Optional[float]:
    return None if x is None or not math.isfinite(x) else float(x)


def _storage_error(e: BaseException) -> dict:
    if isinstance(e, StorageError):
        return {"code": e.code, "detail": e.detail}
    if isinstance(e, CommandError):
        return {"code": e.code, "detail": e.detail}
    if isinstance(e, ReferenceFileError):
        return {"code": "reference_unreadable", "detail": {"message": str(e)}}
    if isinstance(e, OSError):
        return {"code": "os_error", "detail": {"message": str(e), "file": getattr(e, "filename", None)}}
    return {"code": "internal_error", "detail": {"message": repr(e)}}


class FilesMixin:
    """基準・保存・開く・書き出し・記録・長い処理（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・終わり

    def _init_files(self, st: Optional[dict]) -> None:
        self._writer = WriterThread()
        self._tasks: dict[str, _Task] = {}
        self._ref_job: Optional[_RefJob] = None
        self._s11_open: Optional[dict] = None  # S11 の基準の1段目（オープン）の測定
        self._files: dict[str, dict] = {}
        self._file_seq = 0
        self._recorder: Optional[Recorder] = None
        self._rec_stats: Optional[RecordingStats] = None
        self._rec_last: Optional[dict] = None  # 最後に終わった記録（状態表示用）
        self._cur_fut = None
        sel = (st or {}).get("reference") or {}
        self._saved_reference = sel if isinstance(sel, dict) and sel.get("id") and sel.get("serial") else None

    def _files_state_to_save(self) -> dict:
        ref = self._ref
        sel = {"serial": ref.serial, "id": ref.ref_id} if ref is not None else self._saved_reference
        return {"reference": sel}

    def _shutdown_files(self) -> None:
        for t in self._tasks.values():
            t.cancel.set()
        if self._recorder is not None and not self._recorder.stopped:
            # 記録中に閉じた: 作業ファイルは残り、次の起動でまとめるか尋ねる（設計H §4）
            self._recorder.stop()
        self._writer.close(WRITER_CLOSE_TIMEOUT_S)

    # ------------------------------------------------------------------ 場所

    def _references_dir(self, serial: str) -> Path:
        return self._root / "references" / _SAFE.sub("_", serial)

    def _recordings_dir(self) -> Path:
        return self._root / "recordings"

    def _serial_for_files(self) -> str:
        serial = (self._identity or {}).get("serial") or self._settings.last_serial()
        if not serial:
            raise self._error("device_not_selected", "serial")
        return serial

    # ------------------------------------------------------------------ 書き込みスレッドへ渡す

    def _post_call(self, fn: Callable[[], None]) -> None:
        """書き込みスレッドから、制御スレッドで fn を呼んでもらう。"""
        self._q.put(("call", fn))

    def _io(self, job: Callable[[], object], then: Callable[[object], object]) -> object:
        """短いディスクの仕事。書き込みスレッドで job、終わったら制御スレッドで then の値を命令の結果にする。"""
        fut = self._cur_fut
        wf = self._writer.submit(job)

        def done(f) -> None:
            def finish() -> None:
                try:
                    exc = f.exception()
                    if exc is not None:
                        raise exc
                    fut.set_result(then(f.result()))
                except CommandError as e:
                    fut.set_exception(e)
                except (StorageError, ReferenceFileError, OSError) as e:
                    err = _storage_error(e)
                    fut.set_exception(CommandError(err["code"], None, err["detail"]))
                except Exception as e:  # noqa: BLE001
                    fut.set_exception(e)

            self._post_call(finish)

        wf.add_done_callback(done)
        return DEFERRED

    def _new_task(self, kind: str, total: int = 0, detail: Optional[dict] = None) -> _Task:
        t = _Task(uuid.uuid4().hex[:12], kind, time.time(), total=total, detail=detail or {})
        self._tasks[t.task_id] = t
        return t

    def _task_progress(self, t: _Task, done: int, total: int, elapsed: float) -> None:
        t.done, t.total, t.elapsed_s = int(done), int(total), float(elapsed)
        self._emit("progress", {"kind": "task", "task_id": t.task_id, "task": t.kind, "done": t.done,
                                "total": t.total, "elapsed_s": t.elapsed_s})

    def _end_task(self, t: _Task, exc: Optional[BaseException], result: Optional[dict] = None) -> None:
        self._tasks.pop(t.task_id, None)
        t.elapsed_s = time.time() - t.started_at
        base = {"task_id": t.task_id, "task": t.kind, "elapsed_s": t.elapsed_s, **t.detail}
        if exc is None:
            self._notice("task_done", {**base, "result": result or {}})
        elif isinstance(exc, Cancelled) or (isinstance(exc, CommandError) and exc.code == "cancelled"):
            self._notice("task_cancelled", {**base, **(result or {})})
        else:
            self._notice("task_failed", {**base, "error": _storage_error(exc), **(result or {})})
        self._changed()

    def _start_task(
        self,
        kind: str,
        submit: Callable[[Callable[[int, int, float], None], threading.Event], object],
        then: Callable[[object], dict],
        total: int = 0,
        detail: Optional[dict] = None,
        on_error: Optional[Callable[[BaseException], dict]] = None,
    ) -> dict:
        """長い処理を始める。submit(progress, cancel) は書き込みスレッドの Future を返す。

        終わったら制御スレッドで then(結果) を呼び、その dict を task_done の result に入れる。
        """
        t = self._new_task(kind, total, detail)

        def progress(done: int, tot: int, elapsed: float) -> None:
            self._post_call(lambda: self._task_progress(t, done, tot, elapsed))

        wf = submit(progress, t.cancel)

        def done(f) -> None:
            def finish() -> None:
                exc = f.exception()
                if exc is not None:
                    self._end_task(t, exc, on_error(exc) if on_error else None)
                    return
                try:
                    res = then(f.result())
                except Exception as e:  # noqa: BLE001
                    self._end_task(t, e)
                    return
                self._end_task(t, None, res)

            self._post_call(finish)

        wf.add_done_callback(done)
        self._changed()
        return {"task_id": t.task_id}

    def _cmd_cancel_task(self, task_id: str) -> dict:
        """長い処理をやめる（U-19）。やめたことは task_cancelled で知らせる。"""
        t = self._tasks.get(task_id)
        if t is None:
            raise self._error("unknown_task", "task_id", {"task_id": task_id})
        t.cancel.set()
        if self._ref_job is not None and self._ref_job.task is t:
            self._abort_ref_job(Cancelled())
        elif getattr(self, "_cal_job", None) is not None and self._cal_job.task is t:
            self._cal_abort(Cancelled())  # 校正の測定（設計K）。測りかけの値は入れない
        else:
            self._changed()
        return {"task_id": task_id, "cancelling": True}

    def _tasks_state(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    # ------------------------------------------------------------------ ノーマライズの基準（設計E §6）

    def _info_dict(self, i) -> dict:
        return {"id": i.ref_id, "name": i.name, "serial": i.serial, "model": i.model, "mode": i.mode.value,
                "taken_at": i.taken_at, "selected": self._ref is not None and self._ref.ref_id == i.ref_id}

    def _cmd_reference_list(self) -> object:
        d = self._references_dir(self._serial_for_files())
        return self._io(lambda: list_references(d), lambda infos: {"references": [self._info_dict(i) for i in infos]})

    def _ref_path(self, serial: str, ref_id: str) -> Path:
        if not isinstance(ref_id, str) or not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", ref_id):
            raise self._error("bad_reference_id", "id", {"id": ref_id})
        return self._references_dir(serial) / f"{ref_id}.h5"

    def _set_reference(self, ref) -> None:
        """使う基準を替える。値の意味が変わるので蓄積を捨てる（設計F §5.3）。"""
        from .trace import ConditionChange

        self._ref = ref
        self._saved_reference = None if ref is None else {"serial": ref.serial, "id": ref.ref_id}
        self._norm_key = None
        self._on_condition(ConditionChange.REFERENCE)
        self._changed()

    def _cmd_reference_select(self, id=None) -> object:  # noqa: A002
        if id is None:
            self._set_reference(None)
            return {"selected": None}
        serial = self._serial_for_files()
        p = self._ref_path(serial, id)

        def then(ref) -> dict:
            self._set_reference(ref)
            return {"selected": {"id": ref.ref_id, "name": ref.name, "taken_at": ref.taken_at}}

        return self._io(lambda: load_reference(p), then)

    def _cmd_reference_delete(self, id) -> object:  # noqa: A002
        serial = self._serial_for_files()
        self._ref_path(serial, id)
        d = self._references_dir(serial)

        def then(ok) -> dict:
            if self._ref is not None and self._ref.ref_id == id:
                self._set_reference(None)
            return {"deleted": bool(ok)}

        return self._io(lambda: delete_reference(d, id), then)

    def _cmd_reference_rename(self, id, name: str) -> object:  # noqa: A002
        if not isinstance(name, str) or not name.strip():
            raise self._error("bad_name", "name")
        serial = self._serial_for_files()
        p = self._ref_path(serial, id)
        d = self._references_dir(serial)

        def job():
            ref = load_reference(p)
            ref.name = name
            save_reference(d, ref)
            return ref

        def then(ref) -> dict:
            if self._ref is not None and self._ref.ref_id == ref.ref_id:
                self._ref.name = ref.name
                self._changed()
            return {"id": ref.ref_id, "name": ref.name}

        return self._io(job, then)

    def _load_saved_reference(self) -> None:
        """接続したとき、前回選んでいた基準（同じ機器のもの）を読む（設計E §4: 次回起動時に最後の基準を読む）。"""
        sel = self._saved_reference
        serial = (self._identity or {}).get("serial")
        if not sel or sel.get("serial") != serial or (self._ref is not None and self._ref.serial == serial):
            return
        try:
            p = self._ref_path(serial, sel["id"])
        except CommandError:
            return
        wf = self._writer.submit(load_reference, p)

        def done(f) -> None:
            def finish() -> None:
                exc = f.exception()
                if exc is not None:
                    self._notice("reference_unreadable", _storage_error(exc))
                    return
                if (self._identity or {}).get("serial") == serial:
                    self._set_reference(f.result())

            self._post_call(finish)

        wf.add_done_callback(done)

    def _check_not_taking_reference(self) -> None:
        """いまの条件とは別の条件で測っている間（基準を取る・校正の測定）は、条件と動作を変えない。"""
        if self._ref_job is not None:
            raise self._error("reference_in_progress")
        if getattr(self, "_cal_job", None) is not None:
            raise self._error("calibration_in_progress")

    def _cmd_reference_take(self, kind: str = KIND_WIDE, name: Optional[str] = None, averages: int = 1,
                            standard: Optional[str] = None, show_zero: bool = False) -> dict:
        """基準を取る（設計E §6.2）。TG・S11 モードで、いまの条件とは別に掃引して区間を作る。

        kind: wide（機種の TG の範囲を 1MHz 刻み）/ fine（いまの Start・Stop・Step）。
        S11 は standard に open → short の順で2回呼ぶ（設計E §6.5）。
        show_zero: 取り終えたとき 0dB が縦軸の外なら、0dB が上から1目盛下に来る REF レベルにする（変えたら
        終わりの知らせの結果に ref_level を入れて知らせる: J §4 の「黙って変えない」）。
        """
        if not isinstance(show_zero, bool):
            raise self._error("bad_value", "show_zero")
        if self._conn != "connected":
            raise self._error("not_connected")
        self._check_not_taking_reference()
        mode = self._adopted.mode
        if mode not in NORMALIZED_MODES:
            raise self._error("mode_not_normalizable", "mode", {"mode": mode.value})
        if kind not in (KIND_WIDE, KIND_FINE):
            raise self._error("bad_reference_kind", "kind", {"kind": kind})
        if isinstance(averages, bool) or not isinstance(averages, int) or averages < 1:
            raise self._error("not_integer", "averages")
        if mode == Mode.S11:
            if standard not in ("open", "short"):
                raise self._error("s11_standard_required", "standard")
            if standard == "short" and (self._s11_open is None or self._s11_open["kind"] != kind):
                raise self._error("s11_open_missing", "standard")
        if kind == KIND_WIDE:
            lo, hi, _step = wide_range_hz(self._profile)
            # 基準の範囲はアプリが決めるもの（利用者のドラッグではない）。Span 固定の規則を通さず、
            # 送った Start・Stop をそのまま使う（その1回だけの命令。固定 Span も変えない: C §5.12）
            adopted = self._model.apply(
                SweepRequest(start_hz=lo, stop_hz=hi, edited=("start_hz", "stop_hz"), points=0, one_shot=True),
                self._adopted)
        else:
            adopted = self._adopted
        task = self._new_task("reference_take", total=averages,
                              detail={"kind": kind, "mode": mode.value, "standard": standard})
        self._ref_job = _RefJob(task, adopted, averages, kind, name, standard, self._run, show_zero=show_zero)
        self._run = "reference"
        self._push_job()
        self._task_progress(task, 0, averages, 0.0)
        self._changed()
        return {"task_id": task.task_id}

    def _show_zero_level(self) -> dict:
        """0dB が縦軸の中（一番上の線より下・一番下の線より上）に無ければ、上から1目盛下に置く REF レベルにする。"""
        amp = self._amplitude
        ref = 0.0 if amp.get("ref_level") is None else float(amp["ref_level"])
        div = float(amp.get("db_per_div") or DEFAULT_DB_PER_DIV)
        if ref - DIVISIONS * div < 0.0 < ref:
            return {}
        amp["ref_level"] = div
        self._changed()
        return {"ref_level": div, "previous_ref_level": ref}

    def _on_ref_sweep(self, raw) -> None:
        job = self._ref_job
        job.frames.append(self._build_frame(raw))
        self._task_progress(job.task, len(job.frames), job.count, time.time() - job.task.started_at)
        if len(job.frames) >= job.count:
            self._finish_ref_job()

    def _end_ref_sweeps(self, job: _RefJob) -> None:
        """基準の掃引を終え、取る前の動作に戻す。"""
        self._ref_job = None
        self._run = job.prev_run if job.prev_run in ("continuous", "single") else "stopped"
        self._push_job()

    def _abort_ref_job(self, exc: BaseException) -> None:
        """やめた・失敗した。基準は変わらない（設計E §6.2）。"""
        job = self._ref_job
        if job is None:
            return
        self._end_ref_sweeps(job)
        self._end_task(job.task, exc)

    def _finish_ref_job(self) -> None:
        job = self._ref_job
        self._end_ref_sweeps(job)
        serial = (self._identity or {}).get("serial") or ""
        mode = job.adopted.mode
        now = time.time()
        try:
            if mode == Mode.S11 and job.standard == "open":
                self._s11_open = {"kind": job.kind, "frames": job.frames}
                self._end_task(job.task, None, {"standard": "open", "next": "short"})
                return
            if mode == Mode.S11:
                seg = s11_segment_from_frames(self._s11_open["frames"], job.frames,
                                              connected_at=self._connected_at or 0.0, taken_at=now, kind=job.kind)
                self._s11_open = None
            else:
                seg = segment_from_frames(job.frames, connected_at=self._connected_at or 0.0, taken_at=now,
                                          kind=job.kind, expected_mode=mode)
        except (ReferenceBuildError, ValueError) as e:
            self._end_task(job.task, CommandError("reference_build_failed", None, {"message": str(e)}))
            return
        cur = self._ref
        if (cur is not None and cur.mode == mode and cur.serial == serial
                and (job.name is None or job.name == cur.name)):
            # いま選んでいる基準へ上書き合成する（範囲の外は残る: 設計E §6.2）。保存が済むまで元の基準は変えない
            target = copy.deepcopy(cur)
        else:
            default_name = f"{mode.value.upper()} {datetime.datetime.now():%Y-%m-%d %H:%M}"
            target = new_reference(job.name or default_name, self._profile, serial, mode)
        target.merge(seg)
        d = self._references_dir(serial)
        wf = self._writer.submit(save_reference, d, target)
        prev_run = job.prev_run

        def done(f) -> None:
            def finish() -> None:
                exc = f.exception()
                if exc is not None:
                    self._end_task(job.task, exc)
                    return
                self._set_reference(target)
                extra = self._show_zero_level() if job.show_zero else {}
                self._end_task(job.task, None, {"reference_id": target.ref_id, "name": target.name, **extra})
                if prev_run not in ("continuous", "single") and self._conn == "connected":
                    # 取った基準ですぐ測って 0dB 付近になることを示す（設計E §6.2）
                    try:
                        self._cmd_run("single")
                    except CommandError:
                        pass

            self._post_call(finish)

        wf.add_done_callback(done)

    # ------------------------------------------------------------------ 保存・開く・書き出し（設計H）

    def _calibration_record(self) -> Optional[dict]:
        """保存・書き出しに書く校正の写し。GigaSt は設計D §5.2 の形、RTL-SDR は個体の校正（ppm・レベルの補正。
        format で見分ける）。無ければ None。"""
        if self._cal is not None:
            return self._cal.to_dict()
        rc = getattr(self, "_rtlsdr_cal", None)
        if rc is not None and (self._profile.get("raw") or {}).get("encoding") != GIGAST_RAW_ENCODING:
            return rc.to_dict()
        return None

    def _header(self, note: str = "") -> FileHeader:
        ident = self._identity or {}
        return FileHeader(
            device_model=str(ident.get("model") or self._profile.get("model", "")),
            device_serial=str(ident.get("serial") or ""),
            profile=copy.deepcopy(self._profile),
            calibration=self._calibration_record(),
            note=note or "",
        )

    def _live_trace_data(self, t) -> TraceData:
        eff = t.effective_average_type()
        return TraceData(
            number=t.number,
            values=np.array(t.values, copy=True),
            quantity=t.quantity,
            value_kind=t.value_kind,
            axis=t.axis,
            display_start_hz=t.display_start_hz,
            display_stop_hz=t.display_stop_hz,
            info={"action": t.action.value, "visible": t.visible, "count": t.count,
                  "current_count": t.current_count, "average_type": None if eff is None else eff.value,
                  "average_after": t.average_after.value, "hold_after": t.hold_after.value,
                  "last_update_time": t.last_update_time, "unit": unit_for(t.quantity, t.profile)},
            flags={k: np.array(v, dtype=bool) for k, v in t.flags.items()},
            profile=copy.deepcopy(t.profile),
        )

    def _marker_defs_to_save(self) -> list[dict]:
        """保存ファイルの /markers（設計H §7.1）。ゼロスパンのマーカーの位置はマイクロ秒の時間なので、
        行に x_kind "time" を付けて周波数と取り違えないようにする（設計N §1）。"""
        defs = self._markers.to_defs()["markers"]
        if self._adopted.axis.step_hz == 0:
            defs = [{**d, "x_kind": "time"} for d in defs]
        return defs

    def _live_traces_data(self) -> list[TraceData]:
        """保存ファイルの /traces に入れるトレース。保存したときに表示していたもの（設計H §4・§7.1）だけで、
        非表示のトレースは値があっても入れない。参照トレース（開いたファイル）は元のファイルにあるので入れない。"""
        return [self._live_trace_data(t) for t in self._traces if t.visible and t.values is not None]

    def _cmd_save(self, path: Optional[str] = None, note: str = "") -> dict:
        """いまのトレースとマーカー・最後の掃引の生の値を1つのファイルへ（設計H §4・§7.1）。"""
        if not path:
            raise self._error("path_required", "path")
        header = self._header(note)
        raw = self._last_raw
        if raw is not None and not can_store(raw.encoding):
            # 保存ファイルに持つ形を決めていない生の値は入れず、トレース（単位付き）だけを保存する。
            # GigaSt・RTL-SDR はどちらも生の値を持つ（設計Q §6.1: RTL-SDR も GigaSt と同じ保存）
            raw = None
        traces = self._live_traces_data()
        markers = self._marker_defs_to_save()
        quantity = self._last_quantity
        norm = self._normalization_record()
        if raw is None and not traces:
            raise self._error("no_data")

        def job(progress, cancel):
            progress(0, 1, 0.0)
            out = save_snapshot(path, header, raw, traces, markers, quantity=quantity, normalization=norm)
            return out

        return self._start_task("save", lambda p, c: self._writer.submit(job, p, c),
                                lambda out: {"path": str(out)}, total=1, detail={"path": str(path)})

    def _cmd_open(self, paths) -> dict:
        """保存ファイルを開き、トレースを参照トレースとして重ねる（設計F §5.4・設計H §6）。"""
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            raise self._error("path_required", "paths")
        paths = [str(p) for p in paths]

        def job(progress, cancel):
            t0 = time.monotonic()
            out = []
            for i, p in enumerate(paths):
                if cancel.is_set():
                    raise Cancelled()
                progress(i, len(paths), time.monotonic() - t0)
                try:
                    with H5Record(p) as r:
                        out.append({"path": p, "header": r.header, "traces": r.traces(), "markers": r.markers()})
                except (StorageError, OSError, KeyError, ValueError) as e:
                    out.append({"path": p, "error": _storage_error(e)})
            progress(len(paths), len(paths), time.monotonic() - t0)
            return out

        def then(results) -> dict:
            files, errors = [], []
            for r in results:
                if "error" in r:
                    errors.append({"path": r["path"], **r["error"]})
                    continue
                self._file_seq += 1
                fid = f"F{self._file_seq}"
                h = r["header"]
                entry = {"file_id": fid, "path": r["path"], "name": Path(r["path"]).name,
                         "created_at": h.created_at, "device_model": h.device_model,
                         "device_serial": h.device_serial, "note": h.note,
                         "traces": {f"{fid}.{td.number}": td for td in r["traces"]}, "markers": r["markers"]}
                self._files[fid] = entry
                files.append(self._file_summary(entry))
                for tid in entry["traces"]:
                    self._emit("trace", self._file_trace_payload(tid))
            if files:
                self._diff_refresh()  # 差で選んでいたファイルを開き直した（設計F §5.4）
            return {"files": files, "errors": errors}

        return self._start_task("open", lambda p, c: self._writer.submit(job, p, c), then, total=len(paths))

    def _file_summary(self, e: dict) -> dict:
        out = {k: e[k] for k in ("file_id", "path", "name", "created_at", "device_model", "device_serial", "note")}
        rows = []
        for tid, td in e["traces"].items():
            f = td.freqs()
            rows.append({"id": tid, "number": td.number, "quantity": td.quantity.value,
                         "unit": self._level_shift(td.quantity, td.profile)[1], "points": int(f.size),
                         "start_hz": int(f[0]) if f.size else None, "stop_hz": int(f[-1]) if f.size else None,
                         "info": td.info})
        out["traces"] = rows
        return out

    def _files_state(self) -> list[dict]:
        return [self._file_summary(e) for e in self._files.values()]

    def _find_file_trace(self, tid: str) -> TraceData:
        fid = tid.split(".", 1)[0]
        e = self._files.get(fid)
        if e is None or tid not in e["traces"]:
            raise self._error("unknown_trace", "id", {"id": tid})
        return e["traces"][tid]

    def _file_trace_payload(self, tid: str) -> dict:
        """参照トレースのイベント `trace`（番号の代わりに "F1.1" のような id を持つ）。"""
        td = self._find_file_trace(tid)
        fid = tid.split(".", 1)[0]
        f = td.freqs()
        if td.axis is not None and td.freqs_hz is None:
            axis = {"start_hz": td.axis.start_hz, "step_hz": td.axis.step_hz, "points": td.axis.points}
        else:
            axis = {"freqs_hz": f.astype(np.int64), "points": int(f.size)}
        pts = {k: np.asarray(v, dtype=bool) for k, v in td.flags.items()}
        # 表示の単位・外部補正の分だけずらす（測定中のトレースと同じ: core/view_units.py）
        shift, unit = self._level_shift(td.quantity, td.profile)
        return {
            "trace": tid,
            "source": "file",
            "file_id": fid,
            "origin": dict(td.origin),
            "axis": axis,
            "display": {"start_hz": int(f[0]) if f.size else None, "stop_hz": int(f[-1]) if f.size else None,
                        "tail_gap": False},
            "quantity": td.quantity.value,
            "unit": unit,
            "values": shift_values(db_values(td.values, td.quantity), shift),
            "action": td.info.get("action"),
            "average_type": td.info.get("average_type"),
            "count": td.info.get("current_count"),
            "flags": {"points": pts},
        }

    def _cmd_close_file(self, file_id: str) -> dict:
        if self._files.pop(file_id, None) is None:
            raise self._error("unknown_file", "file_id", {"file_id": file_id})
        self._diff_refresh()  # 差の片側だったら、差は作れなくなる
        self._changed()
        return {}

    def _cmd_export_csv(self, path: Optional[str] = None, traces=None, start_hz: Optional[int] = None,
                        stop_hz: Optional[int] = None, unit: Optional[str] = None) -> dict:
        """選んだトレースを周波数の範囲で CSV に書き出す（設計H §5・§7.3）。"""
        if not path:
            raise self._error("path_required", "path")
        for n, v in (("start_hz", start_hz), ("stop_hz", stop_hz)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise self._error("not_integer", n)
        sel = traces if traces is not None else [t.number for t in self._traces if t.visible and t.values is not None]
        if self._adopted.axis.step_hz == 0:
            # ゼロスパン: 横軸は時間（推定）。表示している範囲を時間付きで書く（設計N §1・instrument_zerospan.py）
            return self._zs_export_csv(path, sel)
        tds = []
        for tid in sel:
            if isinstance(tid, int) and not isinstance(tid, bool):
                t = self._trace_of(tid)
                if t.values is None:
                    raise self._error("no_trace_data", "traces", {"trace": tid})
                tds.append(self._live_trace_data(t))
            elif tid == DIFF_TRACE_ID:
                tds.append(self._diff_trace_data())  # トレース間の差（設計F §5.4。dB の比）
            else:
                tds.append(self._find_file_trace(str(tid)))
        if not tds:
            raise self._error("no_data", "traces")
        ident = self._identity or {}
        meta = {
            "device": {"model": ident.get("model") or self._profile.get("model"), "serial": ident.get("serial")},
            "conditions": storage_conditions(self._adopted),
            "calibration": self._calibration_record(),
            "normalization": self._normalization_record(),
            "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }

        shifts = [self._level_shift(td.quantity, td.profile) for td in tds]
        offset = int(self._view_opts.get("freq_offset_hz") or 0)
        if unit is None and (offset or any(s for s, _u in shifts)):
            # 表示の単位・外部補正・周波数オフセットを当てて書く（画面と同じ数: 設計O。core/display_export.py）
            def submit(progress, cancel):
                return self._writer.submit(export_display_csv, path, tds, meta=meta, shifts=[s for s, _u in shifts],
                                           unit_label=shifts[0][1] or "dB", freq_offset_hz=offset,
                                           f_range_hz=(start_hz, stop_hz), progress=progress, cancel=cancel)
        else:
            def submit(progress, cancel):
                return self._writer.submit(export_traces_csv, path, tds, meta=meta, unit=unit,
                                           f_range_hz=(start_hz, stop_hz), progress=progress, cancel=cancel)

        return self._start_task("export_csv", submit,
                                lambda r: {"path": str(r.path), "rows": r.rows, "warnings": list(r.warnings)},
                                detail={"path": str(path)})

    # ------------------------------------------------------------------ 記録（設計H §4・§7.2）

    def _on_rec_stats(self, stats: RecordingStats) -> None:
        self._rec_stats = stats

    def _record_sweep(self, raw, quantity, value_kind) -> None:
        """掃引1回を記録の待ち行列へ入れる（待たない）。"""
        rec = self._recorder
        if rec is None or rec.stopped:
            return
        if not can_store(raw.encoding):
            # 記録に持つ形を決めていない生の値は入れずに1回知らせる。GigaSt と RTL-SDR は記録できる
            # （記録中につなぎ替えても続けて記録し、条件が変わるので区間が分かれる: 設計H §3）
            if not getattr(self, "_rec_skip_noticed", False):
                self._rec_skip_noticed = True
                self._notice("record_not_supported_for_device", {"encoding": raw.encoding})
            return
        try:
            # 校正は測った機器のもの（GigaSt は設計D §5.2、RTL-SDR は個体の校正: _calibration_record）
            rec.append(raw, quantity=quantity, value_kind=value_kind, normalization=self._normalization_record(),
                       calibration=self._calibration_record())
        except (RuntimeError, ValueError, TypeError) as e:
            self._notice("record_error", {"message": str(e)})

    def _recording_state(self) -> dict:
        rec = self._recorder
        if rec is None:
            return {"active": False, "stopped": False, "last": self._rec_last}
        s = self._rec_stats
        return {
            "active": not rec.stopped,
            "stopped": rec.stopped,
            "path": str(rec.path),
            "started_at": rec.started_at,
            "elapsed_s": time.time() - rec.started_at,
            "sweeps": s.sweeps_committed if s else 0,
            "segments": s.segments if s else 0,
            "file_bytes": s.file_bytes if s else 0,
            "pending": s.pending if s else 0,
            "last_error": s.last_error if s else None,
            "last": self._rec_last,
        }

    def _cmd_record_start(self) -> dict:
        if self._recorder is not None:
            raise self._error("already_recording")
        enc = (self._profile.get("raw") or {}).get("encoding")
        if not can_store(enc):
            # 作業ファイルに持つ形を決めていない機器（GigaSt・RTL-SDR 以外）は記録を始めない
            raise self._error("record_not_supported_for_device", None, {"encoding": enc})
        self._rec_skip_noticed = False
        path = new_recording_path(self._recordings_dir())
        self._rec_stats = None
        self._recorder = Recorder(
            path, self._header(), writer=self._writer,
            on_committed=lambda s: self._post_call(lambda: self._on_rec_stats(s)),
            on_error=lambda e: self._post_call(lambda: self._notice("record_error", _storage_error(e))),
        )
        self._changed()
        return {"path": str(path)}

    def _cmd_record_stop(self) -> dict:
        """記録を止める（作業ファイルは残る。まとめるのは record_finish）。"""
        rec = self._recorder
        if rec is None or rec.stopped:
            raise self._error("not_recording")
        fut = rec.stop()

        def done(f) -> None:
            if f.exception() is None:
                self._post_call(lambda: (self._on_rec_stats(f.result()), self._changed()))

        fut.add_done_callback(done)
        self._changed()
        return {"path": str(rec.path)}

    def _cmd_record_finish(self, path: Optional[str] = None) -> dict:
        """記録を止めて HDF5 の保存ファイルへまとめる（長い処理。やめたら作業ファイルが残る: 設計H §4.1）。"""
        rec = self._recorder
        if rec is None:
            raise self._error("not_recording")
        if not path:
            raise self._error("path_required", "path")
        traces = self._live_traces_data()
        markers = self._marker_defs_to_save()
        work = str(rec.path)

        def then(out) -> dict:
            self._rec_last = {"path": str(out), "work_path": work, "finished": True}
            self._recorder = None
            return {"path": str(out)}

        def on_error(exc) -> dict:
            # 作業ファイルは残る。あとで finish_unfinished でまとめられる（設計H §4.1）
            self._rec_last = {"work_path": work, "finished": False}
            self._recorder = None
            return {"work_path": work}

        return self._start_task(
            "record_finish",
            lambda p, c: rec.finish(path, traces=traces, markers=markers, progress=p, cancel=c),
            then, detail={"path": str(path), "work_path": work}, on_error=on_error)

    def _work_path(self, path) -> Path:
        """救出の対象にしてよい作業ファイルか（記録の場所の active_*.sqlite で、記録中のものでない）。"""
        p = Path(str(path)).resolve()
        ok = (p.parent == self._recordings_dir().resolve() and p.name.startswith(WORK_PREFIX)
              and p.name.endswith(WORK_SUFFIX))
        if not ok or (self._recorder is not None and p == self._recorder.path.resolve()):
            raise self._error("not_a_work_file", "path", {"path": str(path)})
        return p

    def _cmd_list_unfinished(self) -> object:
        d = self._recordings_dir()
        exclude = [self._recorder.path] if self._recorder is not None else []

        def then(rows) -> dict:
            return {"recordings": [
                {"path": str(u.path), "started_at": u.started_at, "n_sweeps": u.n_sweeps,
                 "t_first": _json_float(u.t_first), "t_last": _json_float(u.t_last),
                 "duration_s": _json_float(u.duration_s), "stopped": u.stopped, "device_model": u.device_model,
                 "error": u.error} for u in rows]}

        return self._io(lambda: find_unfinished(d, exclude), then)

    def _cmd_finish_unfinished(self, path: str, h5_path: str) -> dict:
        p = self._work_path(path)
        if not h5_path:
            raise self._error("path_required", "h5_path")

        def submit(progress, cancel):
            return self._writer.submit(convert_to_h5, p, h5_path, progress=progress, cancel=cancel)

        return self._start_task("finish_unfinished", submit, lambda out: {"path": str(out)},
                                detail={"path": str(h5_path), "work_path": str(p)})

    def _cmd_discard_unfinished(self, path: str) -> object:
        p = self._work_path(path)
        return self._io(lambda: discard_recording(p), lambda _r: {"discarded": str(p)})
