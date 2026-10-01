"""計測器の本体のうち、掃引の値を画面に出すまで（トレース・マーカー・ノーマライズ・元に戻す・振幅）。

Instrument（instrument.py）に混ぜて使う。ここのメソッドはすべて制御スレッドから呼ばれる（設計A §4.2）。
- 1回の掃引: 生の値 → dBm → Frame（リニア W・点ごとの印・プロファイルの写し）→ TG・S11 では基準を当てる
  （設計E §6.3・§6.4）→ 4本のトレース（設計F §5.2）→ マーカー表（設計G §3.5）→ イベント
- 命令は設計A §4.3.1 の名前と引数。失敗は CommandError（コード）。文言は画面側（U-22）
"""
from __future__ import annotations

import copy
import dataclasses
import time
from typing import Optional

import numpy as np

from . import display_marks as dm
from .level.convert import dbm_to_watt
from .marker import Kind, MarkerTable, Sign, ThresholdSetting, TraceView
from .marker import analysis as an
from .marker.markers import Marker
from .instrument_common import CommandError, adopted_to_dict
from .instrument_diff import DIFF_TRACE_ID
from .normalize import apply_reference
from .trace import ConditionChange, TraceAction, TraceSet, to_db
from .types import AdoptedSweep, Frame, Mode, Quantity, RawSweep, ValueKind

# 引数を渡さなかった（null で「消す」と区別する）
_UNSET = object()

# 元に戻せる数（設計J §3。数は設計に無いので、1日の操作で足りる数を置いた）
UNDO_LIMIT = 100

# ノーマライズを当てるモード（設計E: TG の通過・S11 の反射）
NORMALIZED_MODES = (Mode.TG, Mode.S11)

# REF レベルの dB/div の選択肢（設計J §4.1）
DB_PER_DIV_CHOICES = (1, 2, 5, 10)

UNIT_OF = {Quantity.POWER: "dBm", Quantity.RATIO: "dB"}

# 生の値の形（機種のプロファイルの raw.encoding）が RTL-SDR のもの（instrument_rtlsdr.py で換算する）
_RTLSDR_ENCODING = "rtlsdr_power_w"
# マーカーの読み値の注意のコード: Hann 窓の FFT で測ったトレース（_trace_own_notes）
NOTE_HANN_SCALLOPING = "hann_scalloping"


def unit_for(quantity: Optional[Quantity], profile: Optional[dict]) -> Optional[str]:
    """トレースの値の単位。電力は、測った機器の単位（RTL-SDR の「dB（未校正）」など: Frame.profile の
    level_unit）があればそれ、無ければ dBm。比は dB。"""
    if quantity is None:
        return None
    if quantity == Quantity.POWER:
        u = (profile or {}).get("level_unit")
        if u:
            return str(u)
    return UNIT_OF[quantity]


def _condition_kind(old: AdoptedSweep, new: AdoptedSweep) -> Optional[ConditionChange]:
    """値の意味が変わる条件の変更の種類（設計F §5.3）。複数が同時に変わっても捨てるのは1回なので最初の1つ。

    SG の周波数だけの変更は捨てない（SG 掃引は最大保持で積み上げる: 設計L）。
    """
    if old.mode != new.mode:
        return ConditionChange.MODE
    if old.axis != new.axis or (old.start_hz, old.stop_hz) != (new.start_hz, new.stop_hz):
        return ConditionChange.AXIS
    if old.rbw_hz != new.rbw_hz:
        return ConditionChange.RBW
    if old.image_cancel != new.image_cancel:
        return ConditionChange.IMAGE_CANCEL
    return None


# ---------------------------------------------------------------------------
# 設定の辞書（状態の保存・元に戻す・保存ファイル）
# ---------------------------------------------------------------------------


def trace_settings(t) -> dict:
    return {
        "number": t.number,
        "action": t.action.value,
        "visible": t.visible,
        "count": t.count,
        "average_type": None if t.average_type is None else t.average_type.value,
        "average_after": t.average_after.value,
        "hold_after": t.hold_after.value,
    }


def apply_trace_settings(t, d: dict) -> tuple[str, ...]:
    return t.configure(
        action=d["action"], count=d.get("count"), average_type=d.get("average_type"),
        average_after=d.get("average_after", "exponential"), hold_after=d.get("hold_after", "continue"),
        visible=bool(d.get("visible", True)),
    )


def _finite_or_none(x) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else float(x)


def _leading_true(a: Optional[np.ndarray]) -> int:
    if a is None or len(a) == 0:
        return 0
    idx = np.flatnonzero(~np.asarray(a, dtype=bool))
    return int(idx[0]) if idx.size else int(len(a))


def db_values(values: np.ndarray, quantity: Quantity) -> np.ndarray:
    """画面の単位の値（float32）。dB にできない点（0 W・値なし）は NaN（数値を作らない: 設計E §6.4）。"""
    v = to_db(values, quantity)
    v = np.where(np.isfinite(v), v, np.nan)
    return v.astype(np.float32)


class DisplayMixin:
    """トレース・マーカー・ノーマライズ・元に戻す・振幅（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・状態の保存

    def _init_display(self, st: Optional[dict]) -> None:
        self._traces = TraceSet()
        self._markers = MarkerTable()
        self._views: dict[int, TraceView] = {}
        self._marker_snap: dict = self._markers.snapshot({})
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        # 縦軸（設計J §4.1）。None は画面が決める既定
        self._amplitude = {"ref_level": None, "unit": None, "db_per_div": None}
        self._ref = None  # 選んでいるノーマライズの基準（normalize.Reference）
        self._norm_state: dict = {"used": False}
        self._norm_key: Optional[tuple] = None
        self._no_calibration = True
        self._last_raw: Optional[RawSweep] = None
        self._last_quantity = Quantity.POWER
        self._ref_auto_file_cache: dict = {}  # REF の自動の参照トレースの最大値（_ref_auto_state）
        st = st or {}
        # 前回終了時のトレースの設定・マーカーの置き方を戻す（設計O §1.2）。読めないものは既定のまま
        for d in st.get("traces") or []:
            try:
                apply_trace_settings(self._traces[int(d["number"])], d)
            except (KeyError, TypeError, ValueError):
                pass
        # 読めない行・読めない XY は飛ばす（MarkerTable.from_defs）
        self._markers.from_defs({"markers": st.get("markers") or [], "xy": st.get("xy") or {}})
        amp = st.get("amplitude") or {}
        for k in self._amplitude:
            if k in amp:
                self._amplitude[k] = amp[k]
        self._marker_snap = self._markers.snapshot({})

    def _display_state_to_save(self) -> dict:
        return {
            "traces": [trace_settings(t) for t in self._traces],
            **{k: v for k, v in self._markers.to_defs().items() if k in ("markers", "xy")},
            "amplitude": dict(self._amplitude),
        }

    # ------------------------------------------------------------------ 状態

    def _trace_state(self, t) -> dict:
        eff = t.effective_average_type()
        return {
            **trace_settings(t),
            "count_target": t.effective_count,
            "current_count": t.current_count,
            "count_reached": t.count_reached,
            "average_type_effective": None if eff is None else eff.value,
            "quantity": None if t.quantity is None else t.quantity.value,
            # 値の種類（設計F §5.1）。ベクトル平均は複素数のときだけ選べる（設計F §5.2.1）ので、画面が選べる平均を
            # 決めるのに使う。まだ値が無いトレースは、つないでいる機器のプロファイルの値の種類
            "value_kind": (t.value_kind.value if t.value_kind is not None
                           else self._profile.get("value_kind", "real")),
            "unit": unit_for(t.quantity, t.profile),
            "has_values": t.values is not None,
            "axis_differs": t.action == TraceAction.FIXED and t.axis_differs(self._adopted.axis),
            "last_update_time": t.last_update_time,
        }

    def _display_snapshot(self) -> dict:
        return {
            "traces": [self._trace_state(t) for t in self._traces],
            "markers": self._marker_snap,
            "normalization": self._normalization_state(),
            "amplitude": dict(self._amplitude),
            "undo": {"can_undo": bool(self._undo), "can_redo": bool(self._redo)},
            # 機器の範囲の外・意味の無い区間の帯（設計J §4.8）。画面は計算し直さずに描く
            "display_marks": dm.display_marks(self._adopted.mode, self._profile, int(self._adopted.start_hz),
                                              int(self._adopted.stop_hz)),
            "ref_auto": self._ref_auto_state(),
        }

    def _ref_auto_state(self) -> dict:
        """REF の「自動」に使う、トレースごとの最大値（設計J §4.8）。id は "1"〜"4"・"D"（差）・"F1.1"（参照）。

        参照トレースの表示・非表示は画面ごとの状態なので、参照トレースは全部を出し、画面が表示中で縦軸と単位が
        同じものの最大を取る（ui_qt/screen_model.ref_auto_view）。測定中のトレースは表示中のものだけ（隠したものは
        どの画面にも出ない。RTL-SDR の 20 万点を掃引ごとに数えないため）。参照トレースは値が変わらないので、
        表示範囲ごとに覚えておく。
        使わない点: 局発の漏れ・前置き不足・値の無い点・TG の範囲の外（display_marks.AUTO_REF_EXCLUDE）と、
        いまの表示範囲の外（ゼロスパンは横軸が時間なので範囲で切らない）。
        """
        a = self._adopted
        lo, hi = (a.start_hz, a.stop_hz) if a.axis.step_hz > 0 else (None, None)
        peaks: dict[str, dict] = {}

        def put(tid: str, values_db, freqs, flags, unit) -> None:
            peaks[tid] = {"unit": unit, "max": dm.usable_max(values_db, freqs, flags, lo, hi)}

        for t in self._traces:
            if t.visible and t.values is not None and t.axis is not None and t.quantity is not None:
                put(str(t.number), db_values(t.values, t.quantity), t.axis.freqs(), t.flags,
                    unit_for(t.quantity, t.profile))
        cache = self._ref_auto_file_cache
        new_cache = {}
        for e in self._files.values():
            for tid, td in e["traces"].items():
                key = (tid, id(td), lo, hi)
                if key not in cache:
                    put(tid, db_values(td.values, td.quantity), td.freqs(), td.flags, unit_for(td.quantity, td.profile))
                    cache[key] = peaks[tid]
                peaks[tid] = new_cache[key] = cache[key]
        self._ref_auto_file_cache = new_cache
        d = self._diff_last
        if d is not None:
            put(DIFF_TRACE_ID, d["db"], d["freqs"], None, "dB")
        return {"peaks": peaks}

    def _normalization_state(self) -> dict:
        ref = self._ref
        sel = None
        if ref is not None:
            sel = {"id": ref.ref_id, "name": ref.name, "mode": ref.mode.value, "serial": ref.serial,
                   "taken_at": ref.taken_at}
        return {"selected": sel, **self._norm_state}

    # ------------------------------------------------------------------ 1回の掃引

    def _build_frame(self, raw: RawSweep) -> Frame:
        """生の値 → dBm（設計D §5.3）→ リニア W の Frame（設計F §5.1）。RTL-SDR は instrument_rtlsdr.py。"""
        if raw.encoding == _RTLSDR_ENCODING:
            return self._build_rtlsdr_frame(raw)
        a = raw.adopted
        levels = self._converter.to_dbm(a, raw.raw)
        self._no_calibration = levels.no_calibration
        n = a.axis.points
        transient = np.zeros(n, dtype=bool)
        transient[: max(0, min(n, int(raw.transient_head_points)))] = True
        return Frame(
            axis=a.axis,
            values=dbm_to_watt(levels.dbm),
            quantity=Quantity.POWER,
            value_kind=ValueKind.REAL,
            time=raw.started_at,
            duration_s=raw.duration_s,
            sweep_no=self._sweep_count,
            display_start_hz=a.start_hz,
            display_stop_hz=a.stop_hz,
            adopted=a,
            raw=raw,
            flags={"transient": transient, "out_of_cal_range": np.asarray(levels.out_of_cal_range, dtype=bool)},
            profile=copy.deepcopy(self._profile),
        )

    def _normalize(self, frame: Frame) -> Frame:
        """TG・S11 では選んだ基準を当てる。当てられなければ電力のまま、理由を状態に出す（設計E §6.4）。"""
        a = frame.adopted
        if a is None or a.mode not in NORMALIZED_MODES:
            self._norm_state = {"used": False}
            self._norm_key = None
            return frame
        serial = (self._identity or {}).get("serial") or ""
        res = apply_reference(frame, self._ref, serial=serial, connected_at=self._connected_at or 0.0, now=time.time())
        self._norm_state = {
            "used": True,
            "applied": res.applied,
            "reason": res.reason,
            "detail": res.detail,
            "accuracy": res.accuracy,
            "out_of_range_hz": [[int(lo), int(hi)] for lo, hi in res.out_of_range_hz],
            "power_cycled": res.power_cycled,
            "oldest_taken_at": res.oldest_taken_at,
            "age_s": res.age_s,
        }
        key = (res.applied, res.reason, res.power_cycled, bool(res.out_of_range_hz))
        if key != self._norm_key:
            # 当てた／当てなかった・理由・電源の入れ直し前・一部が範囲外、が変わったときだけ1回知らせる
            self._norm_key = key
            self._notice("normalization", {k: self._norm_state[k] for k in
                                           ("applied", "reason", "power_cycled", "out_of_range_hz")})
        return res.frame

    def _normalization_record(self) -> Optional[dict]:
        """保存・記録に書く「当てた基準」（設計H §7.1: 名前・取得日時・電源投入の時刻）。当てていなければ None。"""
        if not self._norm_state.get("applied") or self._ref is None:
            return None
        return {"id": self._ref.ref_id, "name": self._ref.name, "taken_at": self._ref.taken_at,
                "connected_at": self._connected_at, "accuracy": self._norm_state.get("accuracy"),
                "power_cycled": self._norm_state.get("power_cycled")}

    def _trace_notes(self) -> tuple[str, ...]:
        """読み値に添える注意（設計G §3.3）。掃引条件の信用できない条件と、基準の精度・電源の入れ直し前。"""
        notes = list(self._adopted.warnings)
        if self._norm_state.get("applied"):
            if self._norm_state.get("accuracy") in ("coarse", "mixed"):
                notes.append("reference_coarse")
            if self._norm_state.get("power_cycled"):
                notes.append("reference_power_cycled")
        return tuple(notes)

    @staticmethod
    def _trace_own_notes(profile: Optional[dict]) -> tuple[str, ...]:
        """そのトレースを測った機器から決まる読み値の注意（設計G §3.3）。

        hann_scalloping: FFT の機器（sweep_kind "fft"）を Hann 窓で測ったトレース。格子の間にある
        連続波は最大約 1.4dB 低く読める（設計Q §6.4）。Flat top では出さない（その誤差が小さい窓なので）。
        """
        p = profile or {}
        if p.get("sweep_kind") == "fft" and p.get("fft_window") == "hann":
            return (NOTE_HANN_SCALLOPING,)
        return ()

    def _rebuild_views(self) -> None:
        notes = self._trace_notes()
        views = {}
        for t in self._traces:
            if t.values is None or t.axis is None or t.quantity is None:
                continue
            v = TraceView.build(
                t.number, t.axis, t.values, t.quantity, int(t.display_start_hz), int(t.display_stop_hz),
                t.flags, notes + self._trace_own_notes(t.profile),
            )
            unit = unit_for(t.quantity, t.profile)
            if unit != v.unit:
                # 測った機器の単位（「dB（未校正）」など）でマーカーを読む。単位の違うトレース同士のデルタは作らない
                v = dataclasses.replace(v, unit=unit)
            views[t.number] = v
        self._views = views

    def _refresh_markers(self, emit: bool = True) -> None:
        self._marker_snap = self._markers.snapshot(self._views)
        self._listen_follow_marker()  # 聞いているマーカーを動かしたら、聞く位置も動かす（設計Q §6.8）
        if emit:
            self._emit("markers", {"sweep_no": self._sweep_count, **self._marker_snap})

    def _process_frame(self, frame: Frame) -> None:
        """基準 → トレース → マーカー → イベント（設計A §4.3.1: トレースは番号ごと、マーカー表は掃引ごと）。"""
        frame = self._mark_frame(frame)
        frame = self._normalize(frame)
        self._last_quantity = frame.quantity
        updates = self._traces.apply(frame)
        for u in updates:
            if u.notices:
                self._notice("trace", {"trace": u.number, "codes": list(u.notices)})
        self._rebuild_views()
        moved = self._markers.update(self._views)
        self._refresh_markers(emit=False)
        for u in updates:
            t = self._traces[u.number]
            if t.visible and u.changed:
                self._emit("trace", self._trace_payload(t))
        # A か B が生きたトレースなら差も変わる（設計F §5.4）
        self._diff_refresh(swept=frozenset(u.number for u in updates if u.changed))
        self._emit("markers", {"sweep_no": self._sweep_count, "moved": moved, **self._marker_snap})

    @staticmethod
    def _mark_frame(frame: Frame) -> Frame:
        """機器の範囲の外・意味の無い点の印（局発の漏れ・負の側の鏡・TG の範囲の外: 設計J §4.8）を足す。
        印は点の周波数とモードと測った機器のプロファイルだけで決まる。マーカーの読み値の注意にもなる（設計G §3.3）。"""
        mode = frame.adopted.mode if frame.adopted is not None else None
        marks = dm.point_marks(frame.axis.freqs(), mode, frame.profile)
        if not marks:
            return frame
        return dataclasses.replace(frame, flags={**frame.flags, **marks})

    def _trace_payload(self, t) -> dict:
        """イベント `trace` の中身（設計A §4.3・§4.3.1）。値は画面の単位の float32。"""
        pts = {k: np.asarray(v, dtype=bool) for k, v in t.flags.items()}
        n = t.axis.points
        eff = t.effective_average_type()
        return {
            "trace": t.number,
            "source": "live",
            "sweep_no": self._sweep_count,
            "time": t.last_update_time,
            "duration_s": self._last_sweep_s,
            "axis": {"start_hz": t.axis.start_hz, "step_hz": t.axis.step_hz, "points": n},
            "display": {"start_hz": t.display_start_hz, "stop_hz": t.display_stop_hz,
                        "tail_gap": self._adopted.tail_gap},
            "quantity": t.quantity.value,
            "unit": unit_for(t.quantity, t.profile),
            "values": db_values(t.values, t.quantity),
            "action": t.action.value,
            "visible": t.visible,
            "average_type": None if eff is None else eff.value,
            "count": t.current_count,
            "count_target": t.effective_count,
            "axis_differs": t.action == TraceAction.FIXED and t.axis_differs(self._adopted.axis),
            "flags": {
                "transient_head_points": _leading_true(pts.get("transient")),
                "out_of_cal_range": pts.get("out_of_cal_range", np.zeros(n, dtype=bool)),
                "no_calibration": self._no_calibration,
                "points": pts,
            },
        }

    # ------------------------------------------------------------------ 条件の変更

    def _on_condition(self, kind: ConditionChange, cause: Optional[str] = None) -> None:
        """値の意味が変わる条件の変更。蓄積を捨て、捨てたトレースを1回だけ知らせる（設計F §3・§5.3）。

        cause は知らせに書く原因（省略は kind の値）。RTL-SDR の利得・ppm のように、種類は校正と同じでも
        原因を分けて見せたいときに渡す。
        """
        resets = self._traces.on_condition_change(kind)
        self._diff_refresh()
        if resets:
            self._notice("trace_reset", {"traces": sorted(resets), "cause": cause or kind.value,
                                         "codes": {str(k): v for k, v in resets.items()}})

    def _on_sweep_condition(self, old: AdoptedSweep, new: AdoptedSweep) -> None:
        kind = _condition_kind(old, new)
        if kind is not None:
            self._on_condition(kind)

    # ------------------------------------------------------------------ 元に戻す（設計J §3）

    def _undo_snapshot(self) -> dict:
        return {"adopted": self._adopted, "traces": [trace_settings(t) for t in self._traces]}

    @staticmethod
    def _undo_key(s: dict) -> tuple:
        a = s["adopted"]
        return (a.start_hz, a.stop_hz, a.axis, a.rbw_hz, a.rbw_preference_hz, a.mode, a.sg_hz, a.image_cancel,
                a.points_setting, a.span_lock_hz, tuple(tuple(sorted(d.items())) for d in s["traces"]))

    def _record_undo(self, before: dict) -> None:
        """before から変わっていれば元に戻す列に積み、やり直す列を捨てる。"""
        if self._undo_key(before) == self._undo_key(self._undo_snapshot()):
            return
        self._undo.append(before)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def _restore_snapshot(self, s: dict) -> None:
        self._check_not_taking_reference()
        adopted = dataclasses.replace(s["adopted"], adjustments=(), alternative=None)
        for d in s["traces"]:
            apply_trace_settings(self._traces[int(d["number"])], d)
        self._set_adopted(adopted)

    def _cmd_undo(self) -> dict:
        if not self._undo:
            raise self._error("nothing_to_undo")
        self._check_not_taking_reference()
        self._rtlsdr_check_condition(self._undo[-1]["adopted"])  # 取り出す前に確かめる（失敗で履歴を失わない）
        cur = self._undo_snapshot()
        self._restore_snapshot(self._undo.pop())
        self._redo.append(cur)
        return self._undo_result()

    def _cmd_redo(self) -> dict:
        if not self._redo:
            raise self._error("nothing_to_redo")
        self._check_not_taking_reference()
        self._rtlsdr_check_condition(self._redo[-1]["adopted"])
        cur = self._undo_snapshot()
        self._restore_snapshot(self._redo.pop())
        self._undo.append(cur)
        return self._undo_result()

    def _undo_result(self) -> dict:
        self._emit_visible_traces()
        self._changed()
        return {"adopted": adopted_to_dict(self._adopted), "adjustments": [],
                "traces": [self._trace_state(t) for t in self._traces]}

    # ------------------------------------------------------------------ トレースの命令（設計F）

    def _trace_of(self, number) -> object:
        if isinstance(number, bool) or not isinstance(number, int):
            raise self._error("bad_trace", "number", {"number": number})
        try:
            return self._traces[number]
        except KeyError:
            raise self._error("bad_trace", "number", {"number": number}) from None

    def _emit_visible_traces(self) -> None:
        self._rebuild_views()
        self._refresh_markers()
        for t in self._traces:
            if t.visible and t.values is not None:
                self._emit("trace", self._trace_payload(t))
        self._diff_refresh()

    def _cmd_set_trace(self, number, action=_UNSET, count=_UNSET, visible=_UNSET, average_type=_UNSET,
                       average_after=_UNSET, hold_after=_UNSET) -> dict:
        t = self._trace_of(number)
        kw = {k: v for k, v in (("action", action), ("count", count), ("visible", visible),
                                ("average_type", average_type), ("average_after", average_after),
                                ("hold_after", hold_after)) if v is not _UNSET}
        if "count" in kw and kw["count"] is not None and (isinstance(kw["count"], bool) or not isinstance(kw["count"], int)):
            raise self._error("not_integer", "count")
        before = self._undo_snapshot()
        try:
            notices = t.configure(**kw)
        except ValueError as e:
            raise self._error("bad_trace_setting", None, {"message": str(e)}) from e
        self._record_undo(before)
        for code in notices:
            self._notice(code, {"trace": t.number})
        self._rebuild_views()
        self._refresh_markers()
        if t.visible and t.values is not None:
            self._emit("trace", self._trace_payload(t))
        self._diff_refresh()  # 動作を変えて蓄積を捨てたとき
        self._changed()
        return {"trace": self._trace_state(t), "notices": list(notices)}

    def _cmd_clear_trace(self, number) -> dict:
        t = self._trace_of(number)
        t.discard()
        self._rebuild_views()
        self._refresh_markers()
        self._diff_refresh()
        self._changed()
        return {"trace": self._trace_state(t)}

    def _cmd_get_trace(self, id) -> dict:  # noqa: A002 — 命令の引数名（Web と同じ）
        """トレース1本の値（イベント `trace` と同じ形）。途中から繋いだ画面が描き直すため（設計に無い命令）。"""
        if isinstance(id, int) and not isinstance(id, bool):
            t = self._trace_of(id)
            if t.values is None:
                raise self._error("no_trace_data", "id")
            return self._trace_payload(t)
        if id == DIFF_TRACE_ID:
            return self._diff_payload()  # トレース間の差（設計F §5.4）
        return self._file_trace_payload(str(id))

    # ------------------------------------------------------------------ 振幅（設計J §4.1）

    def _cmd_set_amplitude(self, ref_level=_UNSET, unit=_UNSET, db_per_div=_UNSET) -> dict:
        if ref_level is not _UNSET:
            if ref_level is not None and (isinstance(ref_level, bool) or not isinstance(ref_level, (int, float))
                                          or not np.isfinite(ref_level)):
                raise self._error("bad_value", "ref_level")
            self._amplitude["ref_level"] = None if ref_level is None else float(ref_level)
        if unit is not _UNSET:
            self._amplitude["unit"] = unit
        if db_per_div is not _UNSET:
            if db_per_div is not None and db_per_div not in DB_PER_DIV_CHOICES:
                raise self._error("bad_value", "db_per_div", {"choices": list(DB_PER_DIV_CHOICES)})
            self._amplitude["db_per_div"] = db_per_div
        self._changed()
        return {"amplitude": dict(self._amplitude)}

    # ------------------------------------------------------------------ マーカーの命令（設計G）

    def _view(self, trace) -> TraceView:
        self._trace_of(trace)
        v = self._views.get(trace)
        if v is None:
            raise self._error("no_trace_data", "trace", {"trace": trace})
        return v

    def _marker(self, marker_id) -> Marker:
        try:
            return self._markers.get(str(marker_id))
        except KeyError:
            raise self._error("unknown_marker", "id", {"id": marker_id}) from None

    @staticmethod
    def _op_dict(r) -> dict:
        return {"code": r.code, "id": r.marker_id, "requested_hz": r.requested_hz, "freq_hz": r.freq_hz,
                "snapped": r.snapped}

    def _op_result(self, r) -> dict:
        if not r.ok:
            raise self._error(r.code, None, {"id": r.marker_id, "requested_hz": r.requested_hz})
        self._markers_changed()
        return self._op_dict(r)

    def _markers_changed(self) -> None:
        self._refresh_markers()
        self._changed()

    @staticmethod
    def _check_hz(name: str, v) -> None:
        if isinstance(v, bool) or not isinstance(v, int):
            raise CommandError("not_integer", name)

    def _cmd_marker_add(self, kind: str = "M", trace: int = 1, freq_hz: Optional[int] = None) -> dict:
        try:
            k = Kind(kind)
        except ValueError:
            raise self._error("bad_marker_kind", "kind", {"kind": kind}) from None
        self._trace_of(trace)
        v = self._views.get(trace)
        if freq_hz is None:
            # 周波数を省いたらピーク（設計A §4.3.1）
            v = self._view(trace)
            idx = an.highest_index(v)
            if idx is None:
                raise self._error("no_peak", "trace")
            freq_hz = v.freq_at(idx)
        self._check_hz("freq_hz", freq_hz)
        return self._op_result(self._markers.add(k, trace, freq_hz, v))

    def _cmd_marker_remove(self, id) -> dict:  # noqa: A002
        self._markers.remove(self._marker(id).id)
        self._markers_changed()
        return {}

    def _cmd_marker_clear(self) -> dict:
        self._markers.clear()
        self._markers_changed()
        return {}

    def _cmd_marker_set(self, id, freq_hz=_UNSET, step=_UNSET, ref=_UNSET, peak_track=_UNSET,  # noqa: A002
                        trace=_UNSET) -> dict:
        m = self._marker(id)
        result: dict = {"id": m.id}
        if trace is not _UNSET:
            self._trace_of(trace)
            self._markers.set_trace(m.id, trace)
        try:
            if ref is not _UNSET:
                self._markers.set_ref(m.id, ref)
            if peak_track is not _UNSET:
                self._markers.set_peak_track(m.id, bool(peak_track))
        except KeyError:
            raise self._error("unknown_marker", "ref", {"id": ref}) from None
        except ValueError as e:
            raise self._error("bad_marker_setting", None, {"message": str(e)}) from e
        if freq_hz is not _UNSET and freq_hz is not None:
            self._check_hz("freq_hz", freq_hz)
            r = self._markers.set_frequency(m.id, freq_hz, self._view(m.trace))
            if not r.ok:
                raise self._error(r.code, "freq_hz", {"requested_hz": freq_hz})
            result.update(self._op_dict(r))
        if step is not _UNSET and step is not None:
            if isinstance(step, bool) or not isinstance(step, int):
                raise self._error("not_integer", "step")
            r = self._markers.step(m.id, step, self._view(m.trace))
            if not r.ok:
                raise self._error(r.code, "step")
            result.update(self._op_dict(r))
        self._markers_changed()
        return result

    def _cmd_marker_peak(self, id=None, direction: str = "highest", trace: int = 1) -> dict:  # noqa: A002
        if direction not in ("highest", an.LEFT, an.RIGHT, an.LOWER):
            raise self._error("bad_direction", "direction", {"direction": direction})
        if id is None:
            # 何も選んでいなければ M を作ってから動かす（設計G §3.5）
            r = self._markers.peak_search(None, self._view(trace))
            if not r.ok or direction == "highest":
                return self._op_result(r)
            id = r.marker_id  # noqa: A001
        m = self._marker(id)
        v = self._view(m.trace)
        if direction == "highest":
            r = self._markers.peak_search(m.id, v)
        else:
            r = self._markers.next_peak(m.id, direction, v)
        return self._op_result(r)

    def _cmd_marker_top_peaks(self, trace: int = 1, n: int = 1) -> dict:
        if isinstance(n, bool) or not isinstance(n, int):
            raise self._error("not_integer", "n")
        rs = self._markers.place_top_peaks(self._view(trace), n)
        self._markers_changed()
        return {"placed": [self._op_dict(r) for r in rs]}

    def _cmd_marker_register_peak(self, trace: int, freq_hz: int) -> dict:
        self._check_hz("freq_hz", freq_hz)
        return self._op_result(self._markers.register_peak(self._view(trace), freq_hz))

    def _cmd_marker_offset_add(self, id, type: str, sign: str, delta) -> dict:  # noqa: A002
        m = self._marker(id)
        try:
            s = Sign("-" if sign in ("−", "-") else sign)
            if type == "freq":
                self._check_hz("delta", delta)
                self._markers.add_freq_offset(m.id, s, delta)
            elif type == "level":
                self._markers.add_level_offset(m.id, s, float(delta))
            else:
                raise self._error("bad_offset_type", "type", {"type": type})
        except (ValueError, TypeError) as e:
            raise self._error("bad_offset", None, {"message": str(e)}) from e
        self._markers_changed()
        return {"id": m.id}

    def _cmd_marker_offset_remove(self, id, type: str, index: int) -> dict:  # noqa: A002
        m = self._marker(id)
        try:
            if type == "freq":
                self._markers.remove_freq_offset(m.id, index)
            elif type == "level":
                self._markers.remove_level_offset(m.id, index)
            else:
                raise self._error("bad_offset_type", "type", {"type": type})
        except (IndexError, TypeError):
            raise self._error("bad_offset_index", "index", {"index": index}) from None
        self._markers_changed()
        return {"id": m.id}

    def _cmd_marker_function(self, id, bandwidth_db=_UNSET, threshold=_UNSET) -> dict:  # noqa: A002
        m = self._marker(id)
        try:
            if bandwidth_db is not _UNSET:
                self._markers.set_bandwidth(m.id, tuple(bandwidth_db or ()))
            if threshold is not _UNSET:
                self._markers.set_threshold(
                    m.id, None if threshold is None else ThresholdSetting(float(threshold["level"]),
                                                                          str(threshold["side"])))
        except (ValueError, TypeError, KeyError) as e:
            raise self._error("bad_marker_function", None, {"message": str(e)}) from e
        self._markers_changed()
        return {"id": m.id}

    def _cmd_set_xy(self, x1_hz=_UNSET, x2_hz=_UNSET, y1=_UNSET, y2=_UNSET) -> dict:
        kw = {k: v for k, v in (("x1_hz", x1_hz), ("x2_hz", x2_hz), ("y1", y1), ("y2", y2)) if v is not _UNSET}
        try:
            self._markers.set_xy(**kw)
        except TypeError:
            raise self._error("not_integer", None, {"fields": sorted(kw)}) from None
        except ValueError as e:
            raise self._error("bad_value", None, {"message": str(e)}) from e
        self._markers_changed()
        return {"xy": self._marker_snap["xy"]}

    def _cmd_peak_list(self, trace: int = 1, floor_margin_db: float = 10.0) -> dict:
        v = self._view(trace)
        floor, rows = an.peak_list(v, float(floor_margin_db), self._markers.excursion_db)
        return {"trace": trace, "unit": v.unit, "floor": floor,
                "peaks": [{"freq_hz": r.freq_hz, "level": r.level, "height_db": r.height_db} for r in rows]}

    def _cmd_marker_to(self, id, target: str) -> dict:  # noqa: A002
        """マーカーから条件を変える（設計G §3.4）。Center・Span は設計C §5.11 の規則を通る。"""
        m = self._marker(id)
        if target == "center":
            cmd = self._markers.center_command(m.id, self._views)
        elif target == "span":
            cmd = self._markers.span_from_delta_command(m.id, self._views)
        elif target == "ref":
            cmd = self._markers.ref_level_command(m.id, self._views)
            if not cmd.ok:
                raise self._error(cmd.code, "id", {"id": m.id})
            self._amplitude["ref_level"] = cmd.ref_level
            self._amplitude["unit"] = cmd.unit
            self._changed()
            return {"amplitude": dict(self._amplitude)}
        else:
            raise self._error("bad_target", "target", {"target": target})
        if not cmd.ok:
            raise self._error(cmd.code, "id", {"id": m.id})
        return self._apply(cmd.request)
