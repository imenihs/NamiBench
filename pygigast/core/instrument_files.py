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
from .instrument_display import (NORMALIZED_MODES, display_values, file_trace_format, swr_limit_points,
                                 unit_for)
from .trace import TraceAction, formats
from .report_layout import DIVISIONS
from .normalize import (
    KIND_FINE,
    KIND_WIDE,
    VALUE_DB_LIMIT,
    ReferenceBuildError,
    ReferenceFileError,
    check_value_db,
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
from .ref_value import shift_ratio_levels
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
from .types import Mode, Quantity, SweepRequest

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
    # 取ったら 0dB が見える REF レベルにする（上段の「基準を取る」: 設計E §6.2 の「すぐ 0dB を示す」）
    show_zero: bool = False
    # 基準の値（設計E §6.7）。None は指定なし（新しい基準は 0dB、上書き合成ではその基準の値のまま）
    value_db: Optional[float] = None
    # 掃引の前に受信の利得を合わせている（PlutoSDR の S11 のオープン: 設計Q §7.8）。その間は掃引を渡さない
    fitting: bool = False
    # 合わせた利得（{"gain_db", "previous_gain_db", "was_auto"}）。オープンを測り終えた知らせにも入れる
    fitted: Optional[dict] = None


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
        # トレースの値がどの基準の比か（番号 → (基準の id, 当てた基準の値)。比でなければ None）。基準の値を直したとき、
        # その基準の比を持っているトレースだけを動かすため（設計E §6.7。固定のトレースは固定したときの基準のまま）
        self._trace_refs: dict[int, Optional[tuple[str, float]]] = {}

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
                                "total": t.total, "elapsed_s": t.elapsed_s, "detail": t.detail})

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
        elif self._step_cancel(t):
            pass  # SG のステップ掃引（設計L §1.3）。そこまでの結果は残る
        else:
            self._changed()
        return {"task_id": task_id, "cancelling": True}

    def _tasks_state(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    # ------------------------------------------------------------------ ノーマライズの基準（設計E §6）

    def _info_dict(self, i) -> dict:
        return {"id": i.ref_id, "name": i.name, "serial": i.serial, "model": i.model, "mode": i.mode.value,
                "taken_at": i.taken_at, "selected": self._ref is not None and self._ref.ref_id == i.ref_id,
                "value_db": i.value_db}

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

    # ------------------------------------------------------------------ 基準の値（設計E §6.7）

    def _value_arg(self, value_db) -> float:
        """命令の基準の値（dB）。数でなければ bad_value、範囲（±VALUE_DB_LIMIT）の外は reference_value_range（範囲を detail に）。"""
        try:
            return check_value_db(value_db)
        except ValueError:
            pass
        if isinstance(value_db, (int, float)) and not isinstance(value_db, bool) and math.isfinite(value_db):
            raise self._error("reference_value_range", "value_db",
                              {"value": value_db, "lo": -VALUE_DB_LIMIT, "hi": VALUE_DB_LIMIT})
        raise self._error("bad_value", "value_db", {"value": value_db})

    def _cmd_reference_set_value(self, id, value_db) -> object:  # noqa: A002
        """基準の値を直す（測り直さない: 設計E §6.7）。その基準の比で表した値（トレース・XY カーソルの Y・しきい値・
        ウォーターフォールの行など）を同じだけずらす（絵は変えない: 設計書 O §1.4 と同じ。_ref_value_moved）。いま使って
        いない基準でも、その比の値（固定したトレース・再生している記録）は動かす。
        S11 の基準は全反射が 0dB と決まっているので直せない（reference_value_s11）。"""
        v = self._value_arg(value_db)
        if self._ref_job is not None:
            raise self._error("reference_in_progress")  # 取り終えた基準の上書き合成と書き込みが重ならないように
        serial = self._serial_for_files()
        p = self._ref_path(serial, id)
        d = self._references_dir(serial)

        def job():
            ref = load_reference(p)
            if ref.mode != Mode.TG:
                raise CommandError("reference_value_s11", "value_db", {"mode": ref.mode.value})
            old = ref.value_db
            if old != v:
                ref.value_db = v
                save_reference(d, ref)
            return ref, old

        def then(res) -> dict:
            ref, old = res
            cur = self._ref
            if cur is not None and cur.ref_id == ref.ref_id:
                old = cur.value_db
                cur.value_db = v
            if old != v:
                self._ref_value_moved(ref.ref_id, ref.name, old, v)
                self._changed()
            return {"id": ref.ref_id, "value_db": v, "previous": old}

        return self._io(job, then)

    def _note_trace_refs(self, ref_id: Optional[str], value_db: float = 0.0) -> None:
        """この掃引を受けるトレース（固定のほか全部）の値が、どの基準の比（と基準の値）になるかを覚える
        （_normalize・再生の _pb_normalize から）。"""
        ent = None if ref_id is None else (ref_id, float(value_db))
        for t in self._traces:
            if t.action != TraceAction.FIXED:
                self._trace_refs[t.number] = ent

    def _tg_ref_of(self, t) -> Optional[str]:
        """トレースが TG の基準との比の値を持っていれば、その基準の id（S11 の比・電力・NF・値なしは None）。"""
        ent = self._trace_refs.get(t.number)
        if ent is None or t.values is None or t.quantity != Quantity.RATIO or t.mode != Mode.TG:
            return None
        return ent[0]

    def _axis_ref(self) -> Optional[str]:
        """いまの縦軸が TG の基準との比なら、その基準の id（値のあるトレースが無ければ、TG モードで比の縦軸のとき
        いま選んでいる基準）。"""
        from .marker.markers import Y_KIND_TG

        t = self._axis_trace()
        if t is not None:
            return self._tg_ref_of(t)
        ref = self._ref
        return ref.ref_id if self._axis_kind() == Y_KIND_TG and ref is not None and ref.mode == Mode.TG else None

    def _ref_value_moved(self, ref_id: str, name: str, old: float, new: float) -> None:
        """基準 ref_id の値が old → new になった（直した・上書き合成で変わった）。その基準の比で表した値だけを、
        同じだけ動かす（絵は変えず数字だけが新しい値になる: 設計書 O §1.4 と同じ。決まりは core/ref_value.py）。

        - トレース（固定も。値・平均と保持の蓄積・流す表示の点）: その基準の比を持つものだけ（当てた値から新しい値へ）
        - XY カーソルの Y・マーカーのしきい値: その基準の比で決めたものだけ
        - REF・ピーク探索のしきい値: いまの縦軸がその基準の比のときだけ（電力・S11・NF の縦軸の間は動かさない）
        - ウォーターフォールのライブの行・密度・色の範囲・過去のトレース（instrument_waterfall.py）
        記録（再生・記録の作業ファイル）は記録したときの値のまま動かさない（instrument_playback.py）。再生中は画面が
        記録の絵なので、REF・XY カーソルの Y・しきい値・色の範囲も動かさない（動かすのはライブで測った値だけ）。
        """
        delta = float(new) - float(old)
        if not delta:
            return
        # どの口から変わっても全部の画面が一度知らせる（黙って数字を変えない: J §4.10 の 4 と同じ）
        self._notice("reference_value_changed", {"name": name, "value_db": float(new), "previous": float(old)})
        live = self._playback is None
        axis = live and self._axis_ref() == ref_id  # 動かす前の縦軸で決める
        for t in self._traces:
            ent = self._trace_refs.get(t.number)
            if ent is None or ent[0] != ref_id:
                continue
            self._trace_refs[t.number] = (ref_id, float(new))
            if t.values is not None and t.quantity == Quantity.RATIO:
                t.scale_db(float(new) - ent[1])
                self._zs.shift_db(t.number, float(new) - ent[1])
        if live:
            shift_ratio_levels(self._marker_tables.values(), ref_id, delta, self._amplitude if axis else None)
        self._wf_ref_value_moved(ref_id, float(new))
        self._emit_visible_traces()

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
        if getattr(self, "_step_job", None) is not None:
            raise self._error("step_sweep_in_progress")  # SG のステップ掃引（設計L §1.3）

    def _s11_open_state(self) -> Optional[dict]:
        """測ってあるオープン（S11 の基準の1段目: 設計E §6.5）。画面が「次はショート」を示すのに使う。"""
        o = self._s11_open
        return None if o is None else {"kind": o["kind"], "taken_at": o["taken_at"]}

    def _s11_open_follow(self, new) -> None:
        """測ってあるオープンが、いまの条件で測るショートと組めなくなったら捨てて1回知らせる（設計E §6.5）。

        組めない: S11 でなくなった・電源を入れ直した（再接続）・イメージ除去が変わった・「いまの条件で細かく」で
        周波数軸が変わった（オープンとショートは同じ点・同じ条件で測る）。黙って捨てない（J §4）。
        """
        o = self._s11_open
        if o is None:
            return
        why = None
        if new.mode != Mode.S11:
            why = "mode"
        elif o["connected_at"] != self._connected_at:
            why = "power_cycled"
        else:
            # 次に測るショートの条件（「広く」は機種の TG の範囲、「細かく」はいまの条件）とオープンの条件を比べる
            try:
                nxt = self._ref_adopted(o["kind"], new)
            except (ValueError, TypeError, KeyError):
                nxt = None
            if nxt is None or nxt.image_reject_used != o["image_reject"] or nxt.rbw_hz != o["rbw_hz"]:
                why = "conditions"
            elif nxt.axis != o["axis"]:
                why = "axis"
        if why is not None:
            self._s11_open = None
            self._notice("s11_open_discarded", {"reason": why})
            self._changed()

    def _ref_adopted(self, kind: str, base):
        """基準を取る掃引の条件。wide は機種の TG の範囲を 1MHz 刻み、fine はいまの条件（base）のまま。"""
        if kind != KIND_WIDE:
            return base
        lo, hi, _step = wide_range_hz(self._profile)
        # 基準の範囲はアプリが決めるもの（利用者のドラッグではない）。Span 固定の規則を通さず、
        # 送った Start・Stop をそのまま使う（その1回だけの命令。固定 Span も変えない: C §5.12）
        return self._model.apply(
            SweepRequest(start_hz=lo, stop_hz=hi, edited=("start_hz", "stop_hz"), points=0, one_shot=True), base)

    def _device_standard(self, standard: Optional[str]) -> None:
        """機器に、いま測る S11 の標準器を伝える（実機は何もしない。シミュレータはそれを繋いだことにする）。"""
        dev = self._device
        hook = getattr(dev, "on_reference_standard", None)
        if hook is not None:
            try:
                hook(standard)
            except Exception:  # noqa: BLE001 — 伝えられなくても基準を取るのは続ける
                pass

    def _cmd_reference_take(self, kind: Optional[str] = None, name: Optional[str] = None, averages: int = 1,
                            standard: Optional[str] = None, show_zero: bool = False,
                            value_db: Optional[float] = None) -> dict:
        """基準を取る（設計E §6.2）。TG・S11 モードで、いまの条件とは別に掃引して区間を作る。

        kind: wide（機種の TG の範囲を 1MHz 刻み）/ fine（いまの Start・Stop・Step）。省いたら wide。
        S11 は standard に open → short の順で2回呼ぶ（設計E §6.5）。ショートはオープンと同じ点・同じ条件で測るので、
        kind を省いたらオープンを測ったときの kind（違う kind を渡したら s11_open_missing）。
        show_zero: 取り終えたとき 0dB（基準の値があればその値）が縦軸の外なら、上から1目盛下に来る REF レベルにする
        （変えたら終わりの知らせの結果に ref_level を入れて知らせる: J §4 の「黙って変えない」）。
        value_db: 基準の値（設計E §6.7。TG だけ。省いたら新しい基準は 0dB、上書き合成ではその基準の値のまま）。
        上書き合成で値が違えば基準の値を直したのと同じ（_ref_value_moved）。S11 は 0 以外を受けない（全反射 = 0dB）。
        """
        if not isinstance(show_zero, bool):
            raise self._error("bad_value", "show_zero")
        if value_db is not None:
            value_db = self._value_arg(value_db)
        if self._conn != "connected":
            raise self._error("not_connected")
        self._check_not_taking_reference()
        self._check_not_playing()
        mode = self._adopted.mode
        if mode not in NORMALIZED_MODES:
            raise self._error("mode_not_normalizable", "mode", {"mode": mode.value})
        if kind is None:
            kind = self._s11_open["kind"] if mode == Mode.S11 and standard == "short" and self._s11_open else KIND_WIDE
        if kind not in (KIND_WIDE, KIND_FINE):
            raise self._error("bad_reference_kind", "kind", {"kind": kind})
        if kind == KIND_WIDE and self._profile.get("sweep_kind") == "fft":
            # FFT の機器（PlutoSDR）は基準と測定の RBW（FFT の点数）が同じでないと当たらない（設計E §6.4 の rbw_mismatch）。
            # 全範囲を1回で取ると RBW が広がって当たらないので、いまの条件で取る
            raise self._error("reference_wide_fft", "kind", {"kind": kind})
        if isinstance(averages, bool) or not isinstance(averages, int) or averages < 1:
            raise self._error("not_integer", "averages")
        if mode == Mode.S11:
            if value_db:
                raise self._error("reference_value_s11", "value_db", {"mode": mode.value})
            value_db = None
            if standard not in ("open", "short"):
                raise self._error("s11_standard_required", "standard")
            self._s11_open_follow(self._adopted)
            if standard == "short" and (self._s11_open is None or self._s11_open["kind"] != kind):
                raise self._error("s11_open_missing", "standard")
        adopted = self._ref_adopted(kind, self._adopted)
        fit = self._pluto_s11_open_needs_fit(mode, standard)
        if fit:
            self._pluto_s11_check_fit()  # ほかの「利得を合わせる」が動いていないこと
        task = self._new_task("reference_take", total=averages,
                              detail={"kind": kind, "mode": mode.value, "standard": standard})
        self._ref_job = _RefJob(task, adopted, averages, kind, name, standard, self._run, show_zero=show_zero,
                                value_db=value_db, fitting=fit)
        self._device_standard(standard if mode == Mode.S11 else None)
        self._run = "reference"
        self._push_job()
        self._task_progress(task, 0, averages, 0.0)
        if fit:
            # PlutoSDR の S11 のオープンは、まず送信しながら受信の利得を合わせてから測る（設計Q §7.8 の決定②）
            self._pluto_s11_start_fit(self._ref_job)
        self._changed()
        return {"task_id": task.task_id}

    def _ref_axis_is_db(self, mode: Mode) -> bool:
        """基準を当てた後の縦軸が dB か（REF と 0dB を持つ縦軸か）。縦軸は測定中のトレースで決まり（_axis_trace）、
        縦軸の形式（view.s11_format）が |Γ|・SWR なら下端の決まった縦軸で、REF も 0dB の線も無い（設計R §3・設計J §4.7 の
        末尾）。測定中のトレースが表示していないときは、表示している固定のトレースがいま持っている値の形式のまま。"""
        if any(t.visible and t.action != TraceAction.FIXED for t in self._traces):
            return formats.effective(self._axis_format(), mode, Quantity.RATIO) == formats.DB
        t = self._axis_trace()
        return t is None or self._fmt_of(t) == formats.DB

    def _show_zero_level(self, level: float = 0.0) -> dict:
        """基準と同じ通過の高さ（0dB。基準の値があればその値: 設計E §6.7）が縦軸の中（一番上の線より下・一番下の線
        より上）に無ければ、上から1目盛下に置く REF レベルにする。dB の縦軸のときだけ呼ぶ（_ref_axis_is_db）。"""
        amp = self._amplitude
        ref = 0.0 if amp.get("ref_level") is None else float(amp["ref_level"])
        div = float(amp.get("db_per_div") or DEFAULT_DB_PER_DIV)
        if ref - DIVISIONS * div < level < ref:
            return {}
        amp["ref_level"] = round(level + div, 2)
        self._changed()
        return {"ref_level": amp["ref_level"], "previous_ref_level": ref}

    def _on_ref_sweep(self, raw) -> None:
        job = self._ref_job
        job.frames.append(self._build_frame(raw))
        self._task_progress(job.task, len(job.frames), job.count, time.time() - job.task.started_at)
        if len(job.frames) >= job.count:
            self._finish_ref_job()

    def _end_ref_sweeps(self, job: _RefJob) -> None:
        """基準の掃引を終え、取る前の動作に戻す。"""
        self._ref_job = None
        self._device_standard(None)
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
                a = job.adopted
                # ショートと組めるかを確かめる条件も持つ（_s11_open_follow）
                self._s11_open = {"kind": job.kind, "frames": job.frames, "taken_at": now,
                                  "connected_at": self._connected_at, "axis": a.axis,
                                  "image_reject": a.image_reject_used, "rbw_hz": a.rbw_hz}
                self._end_task(job.task, None, {"standard": "open", "next": "short", "kind": job.kind,
                                                **({"gain_fitted": job.fitted} if job.fitted else {})})
                self._changed()
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
        merge = (cur is not None and cur.mode == mode and cur.serial == serial
                 and (job.name is None or job.name == cur.name))
        if merge:
            # いま選んでいる基準へ上書き合成する（範囲の外は残る: 設計E §6.2）。保存が済むまで元の基準は変えない。
            # 基準の値を渡していれば、その値にする（同じ基準の経路の値: 設計E §6.7）
            target = copy.deepcopy(cur)
            if job.value_db is not None:
                target.value_db = job.value_db
        else:
            default_name = f"{mode.value.upper()} {datetime.datetime.now():%Y-%m-%d %H:%M}"
            target = new_reference(job.name or default_name, self._profile, serial, mode, value_db=job.value_db or 0.0)
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
                if merge and self._ref is cur and cur.value_db != target.value_db:
                    # 上書き合成で基準の値が変わった: 直したのと同じく、その基準の比の値を同じだけずらす
                    self._ref_value_moved(cur.ref_id, cur.name, cur.value_db, target.value_db)
                self._set_reference(target)
                # 0dB が見える REF にするのは dB の縦軸だけ（|Γ|・SWR の縦軸は REF を持たず、黙って REF の数字を変えない）
                extra = (self._show_zero_level(target.applied_value_db)
                         if job.show_zero and self._ref_axis_is_db(mode) else {})
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
                  "last_update_time": t.last_update_time, "unit": unit_for(t.quantity, t.profile),
                  # 保存したときの縦軸の形式（記録。開いたときはいまの縦軸の形式で重ねる: 設計R §3）と測ったモード
                  "format": self._fmt_of(t), "measurement": None if t.mode is None else t.mode.value,
                  # SG のステップ掃引の結果なら、その設定と済んだ段の数（何の測定か分かる形で残す: M-08）
                  **({"step_sweep": dict(t.step_info)} if t.step_info else {})},
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
        pb = self._pb_context()
        if pb is not None:
            # 再生中: 記録した機器・校正・掃引の生の値で保存する（いまの機器のものを書かない: M-08）
            h = pb["header"]
            header = FileHeader(device_model=h.device_model, device_serial=h.device_serial, profile=dict(h.profile),
                                calibration=pb["calibration"], note=note or "")
            raw = pb["raw"]
        if raw is not None and not can_store(raw.encoding):
            # 保存ファイルに持つ形を決めていない生の値は入れず、トレース（単位付き）だけを保存する。
            # GigaSt・RTL-SDR はどちらも生の値を持つ（設計Q §6.1: RTL-SDR も GigaSt と同じ保存）
            raw = None
        traces = self._live_traces_data()
        markers = self._marker_defs_to_save()
        quantity = self._last_quantity
        norm = self._normalization_record() if pb is None else pb["normalization"]
        # NF の ENR・増幅度（設計M）。再生中の掃引には付けない（NF の区間は再生しない: instrument_playback.py）
        nf = self._nf_record(raw.adopted.mode) if raw is not None and pb is None else None
        if raw is None and not traces:
            raise self._error("no_data")

        def job(progress, cancel):
            progress(0, 1, 0.0)
            out = save_snapshot(path, header, raw, traces, markers, quantity=quantity, normalization=norm, nf=nf)
            return out

        return self._start_task("save", lambda p, c: self._writer.submit(job, p, c),
                                lambda out: {"path": str(out)}, total=1, detail={"path": str(path)})

    def _cmd_open(self, paths) -> dict:
        """保存ファイルを開き、トレースを参照トレースとして重ねる（設計F §5.4・設計H §6）。

        記録のファイル（掃引が2つ以上）は再生する（設計H §4・設計P §5: instrument_playback.py）。再生できるのは1つずつで、
        2つ目からの記録のファイルは開かずに理由を返す。"""
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
                        is_record = sum(s.n_sweeps for s in r.segments) > 1
                        if not is_record:
                            out.append({"path": p, "header": r.header, "traces": r.traces(), "markers": r.markers()})
                    if is_record:
                        if any("playback" in o for o in out):
                            out.append({"path": p, "error": {"code": "playback_one_at_a_time", "detail": {}}})
                        else:
                            out.append({"path": p, "playback": self._playback_open_job(p)})
                except (StorageError, OSError, KeyError, ValueError, CommandError) as e:
                    out.append({"path": p, "error": _storage_error(e)})
            progress(len(paths), len(paths), time.monotonic() - t0)
            return out

        def then(results) -> dict:
            files, errors = [], []
            played = None
            for r in results:
                if "error" in r:
                    errors.append({"path": r["path"], **r["error"]})
                    continue
                if "playback" in r:
                    why = self._playback_refusal()
                    if why is not None:
                        # 再生に入れない間（ゼロスパン・基準や校正の測定・ステップ掃引の最中）は開かずに理由を返す
                        self._writer.submit(r["playback"].src.close)
                        errors.append({"path": r["path"], **why})
                        continue
                    played = self._playback_install(r["playback"])
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
            return {"files": files, "errors": errors, "playback": played}

        return self._start_task("open", lambda p, c: self._writer.submit(job, p, c), then, total=len(paths))

    def _file_summary(self, e: dict) -> dict:
        out = {k: e[k] for k in ("file_id", "path", "name", "created_at", "device_model", "device_serial", "note")}
        if e.get("kind"):
            # 過去のトレース（ウォーターフォールの行をクリックした掃引: 設計P §3）。kind と時刻を画面が名前にする
            out.update({"kind": e["kind"], "t": e.get("t"), "sweeps": e.get("sweeps")})
        rows = []
        for tid, td in e["traces"].items():
            f = td.freqs()
            rows.append({"id": tid, "number": td.number, "quantity": td.quantity.value,
                         "unit": self._shown(td.quantity, td.profile, file_trace_format(td, self._axis_format()))[1], "points": int(f.size),
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
        # 表示の単位・外部補正の分だけずらす（測定中のトレースと同じ: core/view_units.py）。表示の形式はいまの縦軸の形式
        shift, unit, fmt = self._shown(td.quantity, td.profile, file_trace_format(td, self._axis_format()))
        lim = swr_limit_points(td.values, td.quantity, fmt)
        if lim is not None and lim.any():
            pts["swr_limit"] = lim
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
            "format": fmt,
            "values": display_values(td.values, td.quantity, shift, fmt),
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
        fmts = []  # トレースごとの表示の形式（設計R §3: 画面と同じ形式で書く）
        for tid in sel:
            if isinstance(tid, int) and not isinstance(tid, bool):
                t = self._trace_of(tid)
                if t.values is None:
                    raise self._error("no_trace_data", "traces", {"trace": tid})
                tds.append(self._live_trace_data(t))
                fmts.append(self._fmt_of(t))
            elif tid == DIFF_TRACE_ID:
                tds.append(self._diff_trace_data())  # トレース間の差（設計F §5.4。dB の比）
                fmts.append(formats.DB)
            else:
                tds.append(self._find_file_trace(str(tid)))
                fmts.append(file_trace_format(tds[-1], self._axis_format()))
        if not tds:
            raise self._error("no_data", "traces")
        ident = self._identity or {}
        meta = {
            "device": {"model": ident.get("model") or self._profile.get("model"), "serial": ident.get("serial")},
            "conditions": storage_conditions(self._adopted),
            "calibration": self._calibration_record(),
            "normalization": self._normalization_record(),
            "nf": self._nf_record(),  # NF の ENR・増幅度（NF モードのときだけ: 設計M）
            "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        pb = self._pb_context()
        if pb is not None:
            # 再生中のトレースは記録の掃引から作ったもの。何を測ったかは記録から書く（M-08）
            meta.update({"device": pb["device"], "conditions": storage_conditions(pb["adopted"]),
                         "calibration": pb["calibration"], "normalization": pb["normalization"],
                         "source": pb["source"]})

        shown = [self._shown(td.quantity, td.profile, f) for td, f in zip(tds, fmts)]
        offset = int(self._view_opts.get("freq_offset_hz") or 0)
        if unit is None and (offset or any(s for s, _u, _f in shown) or any(f != formats.DB for f in fmts)):
            # 表示の単位・外部補正・周波数オフセット・表示の形式を当てて書く（画面と同じ数: 設計O・設計R §3。
            # core/display_export.py）
            def submit(progress, cancel):
                return self._writer.submit(export_display_csv, path, tds, meta=meta, shifts=[s for s, _u, _f in shown],
                                           unit_label=shown[0][1] or "dB", freq_offset_hz=offset,
                                           f_range_hz=(start_hz, stop_hz), progress=progress, cancel=cancel,
                                           fmts=fmts, unit_labels=[u or "dB" for _s, u, _f in shown])
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
                       calibration=self._calibration_record(), nf=self._nf_record(raw.adopted.mode))
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
