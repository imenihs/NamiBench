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
from .marker.markers import Y_KIND_NF, Y_KIND_POWER, Marker
from .instrument_common import CommandError, adopted_to_dict
from .instrument_diff import DIFF_TRACE_ID
from .normalize import apply_reference
from .trace import ConditionChange, HoldKeep, TraceAction, TraceLine, TraceSet, formats, to_db
from .types import AdoptedSweep, Frame, Mode, Quantity, RawSweep, ValueKind
from .view_units import shift_values

# 引数を渡さなかった（null で「消す」と区別する）
_UNSET = object()

# 元に戻せる数（設計J §3。数は設計に無いので、1日の操作で足りる数を置いた）
UNDO_LIMIT = 100

# ノーマライズを当てるモード（設計E: TG の通過・S11 の反射）
NORMALIZED_MODES = (Mode.TG, Mode.S11)

# REF レベルの dB/div の選択肢（設計J §4.1）
DB_PER_DIV_CHOICES = (1, 2, 5, 10)

UNIT_OF = {Quantity.POWER: "dBm", Quantity.RATIO: "dB", Quantity.NF: "dB"}

# 縦軸（REF レベル・dB/div: 設計J §4.1）はモードの量ごとに持つ。NF は NF の dB の縦軸で、既定は REF 20dB・2dB/div
# （設計M §1: 旧アプリの NF モードと同じ見やすさ。0〜20dB が見える）。ほかのモードは今までどおり1つ
AMP_KIND_LEVEL = "level"
AMP_KIND_NF = "nf"
# |Γ|・SWR の1目盛（swr_per_div・gamma_per_div: 設計R §3）は S11 の縦軸。NF の縦軸にも同じ欄を持つ（使わない）
AMPLITUDE_DEFAULTS = {AMP_KIND_LEVEL: {"ref_level": None, "unit": None, "db_per_div": None,
                                       "swr_per_div": None, "gamma_per_div": None},
                      AMP_KIND_NF: {"ref_level": 20.0, "unit": None, "db_per_div": 2,
                                    "swr_per_div": None, "gamma_per_div": None}}


def amp_kind_of(mode) -> str:
    return AMP_KIND_NF if mode == Mode.NF else AMP_KIND_LEVEL

# 比の縦軸の単位 → 表示の形式（測ったトレースが無いときの縦軸の種類: _axis_kind）
_RATIO_AXIS_FORMATS = {"dB": formats.DB, formats.UNIT_GAMMA: formats.GAMMA, formats.UNIT_SWR: formats.SWR}

# 生の値の形（機種のプロファイルの raw.encoding）が RTL-SDR のもの（instrument_rtlsdr.py で換算する）
_RTLSDR_ENCODING = "rtlsdr_power_w"
# マーカーの読み値の注意のコード: Hann 窓の FFT で測ったトレース（_trace_own_notes）
NOTE_HANN_SCALLOPING = "hann_scalloping"

# トレースの蓄積の決まり（Qt・Web で共有し、前回終了時の状態に入る。命令 set_trace_options）。
# sg_clears_holds: SG の周波数を手で変えたら最大・最小保持も捨てる（設計F §8。初期値: 捨てる）
TRACE_OPTION_DEFAULTS = {"sg_clears_holds": True}


def restore_trace_options(d) -> dict:
    """保存した形から戻す。読めない値は初期値。"""
    out = dict(TRACE_OPTION_DEFAULTS)
    if isinstance(d, dict):
        for k in TRACE_OPTION_DEFAULTS:
            if isinstance(d.get(k), bool):
                out[k] = d[k]
    return out


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

    SG モードの SG の周波数の変更は SG_FREQ（設計F §8）。平均は捨て、最大・最小保持を残すかは呼ぶ側が
    選択とステップ掃引かで決める（_on_sweep_condition）。SG を出さないモードの SG の周波数は値を変えない。
    """
    if old.mode != new.mode:
        return ConditionChange.MODE
    if old.axis != new.axis or (old.start_hz, old.stop_hz) != (new.start_hz, new.stop_hz):
        return ConditionChange.AXIS
    if old.rbw_hz != new.rbw_hz:
        return ConditionChange.RBW
    if old.image_reject_used != new.image_reject_used:
        # イメージ除去の選択（設計F §7.3）。NF・ゼロスパンでは選択を変えても測る値は変わらないので捨てない
        return ConditionChange.IMAGE_REJECT
    if new.mode == Mode.SG and old.sg_hz != new.sg_hz:
        return ConditionChange.SG_FREQ
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
        "line": t.line.value,
        "format": t.format,  # 表示の形式（設計R §3）
    }


def apply_trace_settings(t, d: dict) -> tuple[str, ...]:
    # 線（設計F §9）・表示の形式（設計R §3）は前の版の設定・プリセットに無い。無い・読めないときは既定（実線・dB）にし、
    # ほかの欄は戻す（読めない欄が1つあるだけでトレースの設定を全部捨てない）
    line = d.get("line")
    fmt = d.get("format")
    return t.configure(
        action=d["action"], count=d.get("count"), average_type=d.get("average_type"),
        average_after=d.get("average_after", "exponential"), hold_after=d.get("hold_after", "continue"),
        visible=bool(d.get("visible", True)),
        line=line if line in {x.value for x in TraceLine} else TraceLine.SOLID,
        fmt=fmt if formats.is_format(fmt) and fmt not in formats.VECTOR_FORMATS else formats.DB,
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


def display_values(values: np.ndarray, quantity: Quantity, shift: float, fmt: str) -> np.ndarray:
    """画面に出す値（float32）: dB の形式は表示の単位の分だけずらした dB、|Γ|・SWR はその値（設計R §3）。"""
    v = db_values(values, quantity)
    if fmt != formats.DB:
        return formats.from_db(v, fmt).astype(np.float32)
    return shift_values(v, shift)


def file_trace_format(td) -> str:
    """参照トレース（開いたファイル）の表示の形式: 保存したときの形式（info.format）。S11 の比でなければ dB（設計R §3）。"""
    info = td.info if isinstance(getattr(td, "info", None), dict) else {}
    return formats.effective(info.get("format"), info.get("measurement"), td.quantity)


def swr_limit_points(values: np.ndarray, quantity: Quantity, fmt: str) -> Optional[np.ndarray]:
    """SWR の上限に当てた点の印（設計R §3。SWR の形式でなければ None）。"""
    if fmt != formats.SWR:
        return None
    return formats.swr_limit_mask(db_values(values, quantity), fmt)


class DisplayMixin:
    """トレース・マーカー・ノーマライズ・元に戻す・振幅（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・状態の保存

    @property
    def _markers(self) -> MarkerTable:
        """いまの横軸のマーカー表。ゼロスパンの間は時間軸の表（位置はマイクロ秒: 設計N §1）、ほかは周波数軸の表。"""
        return self._marker_tables["time" if self._adopted.axis.step_hz == 0 else "freq"]

    def _init_display(self, st: Optional[dict]) -> None:
        self._traces = TraceSet()
        # 周波数軸と時間軸（ゼロスパン）のマーカー表を別に持つ（周波数のマーカーを時間の位置として読まない: 設計N §1）
        self._marker_tables = {"freq": MarkerTable(), "time": MarkerTable()}
        self._views: dict[int, TraceView] = {}
        self._marker_snap: dict = self._markers.snapshot({})
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        # 縦軸（設計J §4.1）。None は画面が決める既定。|Γ|・SWR の縦軸の1目盛（設計R §3: 下端は決まった値）。
        # モードの量ごとに持つ（NF は NF の dB の縦軸: AMPLITUDE_DEFAULTS）。いまのモードの縦軸が _amplitude
        self._amp_store = {k: dict(v) for k, v in AMPLITUDE_DEFAULTS.items()}
        self._ref = None  # 選んでいるノーマライズの基準（normalize.Reference）
        self._norm_state: dict = {"used": False}
        self._norm_key: Optional[tuple] = None
        self._no_calibration = True
        self._last_raw: Optional[RawSweep] = None
        self._last_quantity = Quantity.POWER
        # いま画面に出している測定の量（電力・比）。起動してまだ掃引が無ければ None（J §4.10: 電力 → 比に
        # 変わったときだけ知らせる。起動して最初の掃引は「変わった」にしない）
        self._shown_quantity: Optional[Quantity] = None
        self._ref_auto_file_cache: dict = {}  # REF の自動の参照トレースの最大値（_ref_auto_state）
        st = st or {}
        self._trace_opts = restore_trace_options(st.get("trace_options"))
        # 前回終了時のトレースの設定・マーカーの置き方を戻す（設計O §1.2）。読めないものは既定のまま
        for d in st.get("traces") or []:
            try:
                apply_trace_settings(self._traces[int(d["number"])], d)
            except (KeyError, TypeError, ValueError):
                pass
        # 読めない行・読めない XY は飛ばす（MarkerTable.from_defs）。時間軸の表は time_markers・time_xy
        self._marker_tables["freq"].from_defs({"markers": st.get("markers") or [], "xy": st.get("xy") or {}})
        self._marker_tables["time"].from_defs({"markers": st.get("time_markers") or [], "xy": st.get("time_xy") or {}})
        for kind, key in ((AMP_KIND_LEVEL, "amplitude"), (AMP_KIND_NF, "amplitude_nf")):
            amp = st.get(key) or {}
            for k in self._amp_store[kind]:
                if k in amp:
                    self._amp_store[kind][k] = amp[k]
        self._marker_snap = self._markers.snapshot({})
        # 起動したときの横軸（前回がゼロスパンなら時間: 設計N §1）。画面はマーカー表の列の名前に使う
        self._marker_snap["x_kind"] = self._x_kind()

    def _display_state_to_save(self) -> dict:
        return {
            "traces": [trace_settings(t) for t in self._traces],
            **{k: v for k, v in self._marker_tables["freq"].to_defs().items() if k in ("markers", "xy")},
            **{"time_" + k: v for k, v in self._marker_tables["time"].to_defs().items() if k in ("markers", "xy")},
            "amplitude": dict(self._amp_store[AMP_KIND_LEVEL]),
            "amplitude_nf": dict(self._amp_store[AMP_KIND_NF]),
            "trace_options": dict(self._trace_opts),
        }

    @property
    def _amplitude(self) -> dict:
        """いまのモードの縦軸（NF は NF の縦軸: AMPLITUDE_DEFAULTS）。書き換えるとそのモードの縦軸が変わる。"""
        return self._amp_store[amp_kind_of(self._adopted.mode)]

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
            "unit": self._trace_display(t)[1],
            # 表示の形式（設計R §3）: いま使う形式と、値を測ったモード（画面が選べる形式と名前を決める）
            "format_effective": t.effective_format,
            "measurement": None if t.mode is None else t.mode.value,
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
            "trace_options": dict(self._trace_opts),
            # いま出している測定の量（"power"・"ratio"。まだ掃引が無ければ None）。単位の欄が実際の単位を出す（J §4.10）
            "display_quantity": None if self._shown_quantity is None else self._shown_quantity.value,
        }

    def _trace_display(self, t) -> tuple[float, Optional[str], str]:
        """測っているトレースの (足す dB, 出す単位, 表示の形式)。|Γ|・SWR の形式では単位はその名前で、ずらさない。"""
        return self._shown(t.quantity, t.profile, t.effective_format)

    def _shown(self, quantity, profile, fmt: str) -> tuple[float, Optional[str], str]:
        shift, unit = self._level_shift(quantity, profile)
        if fmt != formats.DB:
            return 0.0, formats.unit_of(fmt, unit), fmt
        return shift, unit, fmt

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

        def put_shifted(tid: str, values, quantity, freqs, flags, profile, fmt: str = formats.DB) -> None:
            # 表示の単位・外部補正の分だけずらした最大（縦軸と同じ数: core/view_units.py）。|Γ|・SWR はその値の最大
            shift, unit, _f = self._shown(quantity, profile, fmt)
            put(tid, display_values(values, quantity, 0.0, fmt), freqs, flags, unit)
            if shift and peaks[tid]["max"] is not None:
                peaks[tid]["max"] = peaks[tid]["max"] + shift

        if a.axis.step_hz == 0:
            peaks.update(self._zs_ref_peaks())  # ゼロスパン: 表示している範囲の全部の点（設計N §1）
        else:
            for t in self._traces:
                if t.visible and t.values is not None and t.axis is not None and t.quantity is not None:
                    put_shifted(str(t.number), t.values, t.quantity, t.axis.freqs(), t.flags, t.profile,
                                t.effective_format)
        cache = self._ref_auto_file_cache
        new_cache = {}
        for e in self._files.values():
            for tid, td in e["traces"].items():
                fmt = file_trace_format(td)
                key = (tid, id(td), lo, hi, self._shown(td.quantity, td.profile, fmt))
                if key not in cache:
                    put_shifted(tid, td.values, td.quantity, td.freqs(), td.flags, td.profile, fmt)
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
        # s11_open: 測ってあるオープン（S11 の基準の1段目。次はショート: 設計E §6.5）
        return {"selected": sel, **self._norm_state, "s11_open": self._s11_open_state()}

    # ------------------------------------------------------------------ 1回の掃引

    def _build_frame(self, raw: RawSweep) -> Frame:
        """生の値 → dBm（設計D §5.3）→ リニア W の Frame（設計F §5.1）。RTL-SDR は instrument_rtlsdr.py。"""
        if raw.encoding == _RTLSDR_ENCODING:
            return self._build_rtlsdr_frame(raw)
        if raw.adopted.mode == Mode.NF:
            return self._build_nf_frame(raw)  # HOT・COLD の組から NF（設計M: instrument_nf.py）
        a = raw.adopted
        levels = self._converter.to_dbm(a, raw.raw)
        self._no_calibration = levels.no_calibration
        n = a.axis.points
        transient = np.zeros(n, dtype=bool)
        transient[: max(0, min(n, int(raw.transient_head_points)))] = True
        for s in a.segments[1:]:
            # 2つ目からの区間も、前置きが足りなければその区間の先頭に印（設計F §7.2。区間は帯の境目から始まるので
            # 範囲の下限には当たらず、ふつうは足りる）
            transient[s.point0: s.point0 + max(0, s.transient_head_points)] = True
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
        if self._adopted.axis.step_hz == 0:
            # ゼロスパン: 横軸は時間（推定。位置はマイクロ秒: 設計N §1）
            views = self._zs_views(notes)
            for n, v in list(views.items()):
                t = self._traces[n]
                shift, unit = self._level_shift(t.quantity, t.profile)
                if shift:
                    v = dataclasses.replace(v, level_db=v.level_db + shift)
                if unit != v.unit:
                    v = dataclasses.replace(v, unit=unit)
                views[n] = v
            self._views = views
            return
        views = {}
        for t in self._traces:
            if t.values is None or t.axis is None or t.quantity is None:
                continue
            v = TraceView.build(
                t.number, t.axis, t.values, t.quantity, int(t.display_start_hz), int(t.display_stop_hz),
                t.flags, notes + self._trace_own_notes(t.profile),
            )
            shift, unit, fmt = self._trace_display(t)
            if shift:
                # 表示の単位（dBμV など）・外部補正の分だけずらした値で読む（core/view_units.py）。
                # 盛り上がり・帯域幅は差で決まるので、ずらしても同じ点になる
                v = dataclasses.replace(v, level_db=v.level_db + shift)
            if fmt != formats.DB:
                # 表示の形式（設計R §3）: 探すのは dB のまま、読み値を |Γ|・SWR で出す
                v = dataclasses.replace(v, fmt=fmt)
            if unit != v.unit:
                # 測った機器の単位（「dB（未校正）」など）・表示の単位・形式でマーカーを読む。単位の違うトレース同士の
                # デルタは作らない
                v = dataclasses.replace(v, unit=unit)
            views[t.number] = v
        self._views = views
        self._follow_axis_format()

    def _axis_kind(self, without_traces: bool = True) -> Optional[str]:
        """いまの縦軸の単位の種類（XY カーソルの Y を置いたときに記録する: markers.Y_KINDS）。

        縦軸は、表示していて値のある一番番号の小さいトレースの単位（画面の screen_s11.axis_unit と同じ決まり）。
        そのトレースが比なら表示の形式（"db"・"gamma"・"swr"）、NF なら "nf"（設計M）、電力なら "power"。値のある
        トレースが無ければ、NF モードは "nf"、ほかは縦軸の単位（振幅の単位: 比の dB なら "db"、無い・電力の単位なら
        "power"）。without_traces が偽なら None。
        """
        t = next((t for t in self._traces if t.visible and t.values is not None and t.quantity is not None), None)
        if t is None:
            if not without_traces:
                return None
            if self._adopted.mode == Mode.NF:
                return Y_KIND_NF  # NF の縦軸（設計M）。掃引の前でも電力・比の値として扱わない
            return _RATIO_AXIS_FORMATS.get(self._amplitude.get("unit"), Y_KIND_POWER)
        if t.quantity == Quantity.NF:
            return Y_KIND_NF
        if t.quantity != Quantity.RATIO:
            return Y_KIND_POWER
        return self._trace_display(t)[2]

    def _follow_axis_format(self) -> None:
        """縦軸が比の dB・|Γ|・SWR のどれかのとき、XY カーソルの Y のうち別の比の形式で置いたものを、同じ反射の
        大きさのいまの形式の値に直す（設計R §3・設計書 O §1.4: 絵の意味はそのまま、数字の単位だけが替わる）。

        直すのは、置いたときの単位が比の形式だった Y だけ（電力の縦軸で置いた Y・NF の縦軸で置いた Y・前の版の分からない
        Y は比の形式の値ではないので直さない）。縦軸が電力・NF の間は何もしない（比の形式の Y はそのまま持ち、比の
        縦軸に戻ったときに置いたときの形式から直す）。値のあるトレースが無い間も何もしない（掃引を捨てた直後の間に、
        残っている振幅の単位で形式を行き来させない）。電力の単位の替え方は _set_view_opts。
        """
        fn = self._axis_kind(without_traces=False)
        if fn in (None, Y_KIND_POWER, Y_KIND_NF):
            return
        xy = self._markers.xy
        for k in ("y1", "y2"):
            v, fp = getattr(xy, k), getattr(xy, k + "_kind")
            if v is None or fp in (None, Y_KIND_POWER, Y_KIND_NF) or fp == fn:
                continue
            setattr(xy, k, formats.convert_value(v, fp, fn))
            setattr(xy, k + "_kind", fn)

    def _refresh_markers(self, emit: bool = True) -> None:
        self._marker_snap = self._markers.snapshot(self._views)
        # 横軸の種類（"freq"・"time"）。時間軸では位置（freq_hz・x1_hz など）はマイクロ秒（設計N §1）。
        # 時間軸では区間の最大・最小・平均も出す
        self._marker_snap["x_kind"] = self._x_kind()
        if self._marker_snap["x_kind"] == "time":
            self._marker_snap["interval"] = self._zs_interval()
        self._listen_follow_marker()  # 聞いているマーカーを動かしたら、聞く位置も動かす（設計Q §6.8）
        if emit:
            self._emit("markers", {"sweep_no": self._sweep_count, **self._marker_snap})

    def _process_frame(self, frame: Frame) -> None:
        """基準 → トレース → マーカー → イベント（設計A §4.3.1: トレースは番号ごと、マーカー表は掃引ごと）。"""
        frame = self._mark_frame(frame)
        frame = self._normalize(frame)
        self._last_quantity = frame.quantity
        shown_before, self._shown_quantity = self._shown_quantity, frame.quantity
        if shown_before == Quantity.POWER and frame.quantity == Quantity.RATIO:
            # 電力 → 基準との比に変わった（基準を選んだ TG・S11 へ移った・基準を選んだ・上段で基準を取った）。
            # 単位が黙って変わったと思わせないよう、画面が一度知らせる（J §4.10 の 3）
            self._notice("ratio_display", {"name": self._ref.name if self._ref is not None else "",
                                           "unit": self._view_opts.get("level_unit")})
        elif shown_before == Quantity.RATIO and frame.quantity == Quantity.POWER:
            # 基準との比 → 電力に戻った（SA・SG へ移った・基準を外した・基準と条件が合わなくなった）。電力 → 比と同じく
            # 黙って変えずに、なぜ戻ったかを画面が一度知らせる（J §4.10 の 3。片向きだけ知らせると遷移が非対称で、
            # イメージキャンセルを切ったら比が黙って外れた: 2026-10-03 ユーザー指摘）
            a = frame.adopted
            if a is None or a.mode not in NORMALIZED_MODES:
                why, mode = "mode", (a.mode.value if a is not None else None)
            else:
                why, mode = self._norm_state.get("reason") or "", a.mode.value
            self._notice("power_display", {"reason": why, "mode": mode, "unit": self._view_opts.get("level_unit")})
        updates = self._traces.apply(frame)
        self._zs_append(frame)  # ゼロスパンの流す表示のために時刻付きで持つ（設計N §1）
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
        shift, unit, fmt = self._trace_display(t)
        lim = swr_limit_points(t.values, t.quantity, fmt)
        if lim is not None and lim.any():
            pts[formats.FLAG_SWR_LIMIT] = lim  # SWR の上限に当てた点（マウスの位置の読み値の注意: 設計R §3）
        payload = {
            "trace": t.number,
            "source": "live",
            "sweep_no": self._sweep_count,
            "time": t.last_update_time,
            "duration_s": self._last_sweep_s,
            "axis": {"start_hz": t.axis.start_hz, "step_hz": t.axis.step_hz, "points": n},
            "display": {"start_hz": t.display_start_hz, "stop_hz": t.display_stop_hz,
                        "tail_gap": self._adopted.tail_gap},
            "quantity": t.quantity.value,
            "unit": unit,
            "format": fmt,  # 表示の形式（設計R §3）
            "values": display_values(t.values, t.quantity, shift, fmt),
            "action": t.action.value,
            "visible": t.visible,
            "line": t.line.value,  # 描く線（設計F §9）
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
        if self._adopted.axis.step_hz == 0 and t.axis.step_hz == 0:
            return self._zs_payload(t, payload)  # 横軸は時間（設計N §1）
        return payload

    # ------------------------------------------------------------------ 条件の変更

    def _on_condition(self, kind: ConditionChange, cause: Optional[str] = None,
                      holds: HoldKeep = HoldKeep.DISCARD) -> None:
        """値の意味が変わる条件の変更。蓄積を捨て、捨てたトレースを1回だけ知らせる（設計F §3・§5.3）。

        cause は知らせに書く原因（省略は kind の値）。RTL-SDR の利得・ppm のように、種類は校正と同じでも
        原因を分けて見せたいときに渡す。holds は最大・最小保持を残すか（設計F §8）。
        """
        resets = self._traces.on_condition_change(kind, holds)
        self._zs.clear()  # 流す表示の古い掃引は、値の意味が違うので捨てる（設計N §1）
        self._diff_refresh()
        if resets:
            self._notice("trace_reset", {"traces": sorted(resets), "cause": cause or kind.value,
                                         "codes": {str(k): v for k, v in resets.items()}})

    def _on_sweep_condition(self, old: AdoptedSweep, new: AdoptedSweep, step_sweep: bool = False) -> None:
        """step_sweep: SG のステップ掃引（設計L）が SG の周波数を変えたとき True。選択によらず最大・最小保持を
        残す（特性を積み上げて描く: 設計F §8 の 2）。手で変えたときは選択 sg_clears_holds に従う（§8 の 1）。"""
        kind = _condition_kind(old, new)
        if kind is None:
            return
        holds = HoldKeep.DISCARD
        if kind == ConditionChange.SG_FREQ:
            if step_sweep:
                holds = HoldKeep.ALL
            elif not self._trace_opts["sg_clears_holds"]:
                holds = HoldKeep.UNLIMITED
        self._on_condition(kind, holds=holds)

    def _cmd_set_trace_options(self, sg_clears_holds=_UNSET) -> dict:
        """トレースの蓄積の決まり（設計F §8）。真偽だけ受ける。いまの蓄積は変えない（次の変更から効く）。"""
        if sg_clears_holds is not _UNSET:
            if not isinstance(sg_clears_holds, bool):
                raise self._error("bad_value", "sg_clears_holds", {"value": sg_clears_holds})
            self._trace_opts["sg_clears_holds"] = sg_clears_holds
        self._changed()
        return {"trace_options": dict(self._trace_opts)}

    # ------------------------------------------------------------------ 元に戻す（設計J §3）

    def _undo_snapshot(self, keys: tuple[str, ...] = ("sweep",)) -> dict:
        """元に戻すために持つ、いまの状態のうち keys の部分（積むのは変えた部分だけ）。

        sweep: 掃引条件とトレースの設定／view: 表示の設定（単位・外部補正・周波数オフセット・描き方: 設計O）／
        amplitude: 縦軸（プリセットの呼び出しで置き換えたとき）／palette: 配色（設計J §4.4）
        """
        s: dict = {"keys": tuple(keys)}
        if "sweep" in keys:
            s["adopted"] = self._adopted
            s["traces"] = [trace_settings(t) for t in self._traces]
        if "view" in keys:
            s["view"] = dict(self._view_opts)
        if "amplitude" in keys:
            s["amplitude"] = dict(self._amplitude)
            # モードの量ごとの縦軸を全部（プリセットは別のモードの縦軸を書き換えうる: AMPLITUDE_DEFAULTS）
            s["amplitude_store"] = {k: dict(v) for k, v in self._amp_store.items()}
        if "palette" in keys:
            s["palette"] = self._palette.to_saved()
        return s

    @staticmethod
    def _undo_key(s: dict) -> tuple:
        out: list = [s.get("keys", ("sweep",))]
        if "adopted" in s:
            a = s["adopted"]
            out += [a.start_hz, a.stop_hz, a.axis, a.rbw_hz, a.rbw_preference_hz, a.mode, a.sg_hz, a.image_reject,
                    a.points_setting, a.span_lock_hz, tuple(tuple(sorted(d.items())) for d in s["traces"])]
        for k in ("view", "amplitude"):
            if k in s:
                out.append(tuple(sorted(s[k].items())))
        if "amplitude_store" in s:
            out.append(tuple(sorted((k, tuple(sorted(v.items()))) for k, v in s["amplitude_store"].items())))
        if "palette" in s:
            out.append((s["palette"]["base"], tuple(sorted(s["palette"]["colors"].items()))))
        return tuple(out)

    def _record_undo(self, before: dict) -> None:
        """before から変わっていれば元に戻す列に積み、やり直す列を捨てる。"""
        if self._undo_key(before) == self._undo_key(self._undo_snapshot(before.get("keys", ("sweep",)))):
            return
        self._undo.append(before)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def _restore_snapshot(self, s: dict) -> None:
        if "view" in s:
            # 縦軸も一緒に戻すときは、縦軸は持っている値にする（ずらさない）
            self._set_view_opts(s["view"], shift_levels="amplitude" not in s)
        if "amplitude_store" in s:
            for k, v in s["amplitude_store"].items():
                self._amp_store[k].update(v)
        elif "amplitude" in s:
            self._amplitude.update(s["amplitude"])
        if "palette" in s:
            from .palette import Palette

            self._palette = Palette.from_saved(s["palette"])
            self._save_palette_setting()
        if "adopted" in s:
            self._check_not_taking_reference()
            adopted = dataclasses.replace(s["adopted"], adjustments=(), alternative=None)
            for d in s["traces"]:
                apply_trace_settings(self._traces[int(d["number"])], d)
            self._set_adopted(adopted)

    def _undo_redo(self, src: list, dst: list) -> dict:
        top = src[-1]
        if "adopted" in top:
            self._check_not_taking_reference()
            self._rtlsdr_check_condition(top["adopted"])  # 取り出す前に確かめる（失敗で履歴を失わない）
        cur = self._undo_snapshot(top.get("keys", ("sweep",)))
        self._restore_snapshot(src.pop())
        dst.append(cur)
        return self._undo_result()

    def _cmd_undo(self) -> dict:
        if not self._undo:
            raise self._error("nothing_to_undo")
        return self._undo_redo(self._undo, self._redo)

    def _cmd_redo(self) -> dict:
        if not self._redo:
            raise self._error("nothing_to_redo")
        return self._undo_redo(self._redo, self._undo)

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

    def _check_format(self, t, fmt) -> None:
        """表示の形式を選べるか（設計R §2・§3・§5.1）。位相系は位相を測れる機器（複素数の値）だけ、|Γ|・SWR は反射（S11）
        だけ（S11 で測った値のトレースか、いまのモードが S11）。"""
        if not formats.is_format(fmt):
            raise self._error("bad_trace_setting", "format", {"format": fmt})
        if fmt in formats.VECTOR_FORMATS:
            kind = t.value_kind.value if t.value_kind is not None else self._profile.get("value_kind", "real")
            if kind != "complex":
                raise self._error("format_needs_phase", "format", {"format": fmt})
            raise self._error("format_not_yet", "format", {"format": fmt})  # 位相系は SDR の VNA と一緒に作る（R §5.1）
        if fmt in formats.S11_FORMATS:
            # 比の値を持つトレースはその値を測ったモードで決まる（固定した S21 の値に |Γ|・SWR は当たらない）。比の値を
            # 持たない（まだ値が無い・基準を取る前の電力）ときは、いまのモードが S11 なら選んでおける
            has_ratio = t.values is not None and t.quantity == Quantity.RATIO
            if (t.mode != Mode.S11) if has_ratio else (self._adopted.mode != Mode.S11):
                raise self._error("format_s11_only", "format", {"format": fmt})

    def _cmd_set_trace(self, number, action=_UNSET, count=_UNSET, visible=_UNSET, average_type=_UNSET,
                       average_after=_UNSET, hold_after=_UNSET, line=_UNSET, format=_UNSET) -> dict:  # noqa: A002
        """トレースの設定（設計F §5）。line は描く線（設計F §9: thin・solid・thick・dash・dot）、format は表示の形式
        （設計R §3: db・gamma・swr）。どちらも描き方・表し方だけなので、変えても蓄積を捨てない。"""
        t = self._trace_of(number)
        kw = {k: v for k, v in (("action", action), ("count", count), ("visible", visible),
                                ("average_type", average_type), ("average_after", average_after),
                                ("hold_after", hold_after), ("line", line)) if v is not _UNSET}
        if format is not _UNSET:
            self._check_format(t, format)
            kw["fmt"] = format
        if "count" in kw and kw["count"] is not None and (isinstance(kw["count"], bool) or not isinstance(kw["count"], int)):
            raise self._error("not_integer", "count")
        before = self._undo_snapshot()
        try:
            notices = t.configure(**kw)
        except ValueError as e:
            raise self._error("bad_trace_setting", None, {"message": str(e)}) from e
        self._record_undo(before)
        if set(kw) - {"visible", "line"}:
            self._zs.clear(t.number)  # 動作・回数を変えたら、流す表示の前の値は別の動作のもの（設計N §1）
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
        self._zs.clear(t.number)
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

    def _cmd_set_amplitude(self, ref_level=_UNSET, unit=_UNSET, db_per_div=_UNSET, swr_per_div=_UNSET,
                           gamma_per_div=_UNSET) -> dict:
        """縦軸（設計J §4.1）。swr_per_div・gamma_per_div は |Γ|・SWR の形式の縦軸の1目盛（設計R §3。下端は決まった値）。"""
        for key, v in (("swr_per_div", swr_per_div), ("gamma_per_div", gamma_per_div)):
            if v is _UNSET:
                continue
            choices = formats.LINEAR_AXES[formats.UNIT_SWR if key == "swr_per_div" else formats.UNIT_GAMMA]["choices"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or float(v) not in choices):
                raise self._error("bad_value", key, {"choices": list(choices)})
            self._amplitude[key] = None if v is None else float(v)
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

    def _op_result(self, r, polarity: Optional[str] = None) -> dict:
        """polarity は探した向き（谷が見つからないとき「谷が見つかりません」と言うため: 設計G §3.8）。"""
        if not r.ok:
            detail = {"id": r.marker_id, "requested_hz": r.requested_hz}
            if polarity is not None:
                detail["polarity"] = polarity
            raise self._error(r.code, None, detail)
        self._markers_changed()
        return self._op_dict(r)

    def _markers_changed(self) -> None:
        self._refresh_markers()
        self._changed()

    @staticmethod
    def _check_hz(name: str, v) -> None:
        if isinstance(v, bool) or not isinstance(v, int):
            raise CommandError("not_integer", name)

    def _polarity(self, polarity) -> str:
        """探すもの（山 peak・谷 dip: 設計G §3.8）。画面の山／谷の選択をそのまま受ける。"""
        if polarity not in an.POLARITIES:
            raise self._error("bad_polarity", "polarity", {"polarity": polarity})
        return polarity

    def _no_dips_on_power(self, v, polarity: str) -> None:
        """ピークの一覧・上位 N 個は、電力のスペクトルでは谷を探さない（設計P §9.7。マーカーの谷は探せる）。"""
        if polarity == an.DIP and v.quantity == Quantity.POWER:
            raise self._error("dips_on_power", "polarity")

    def _cmd_marker_add(self, kind: str = "M", trace: int = 1, freq_hz: Optional[int] = None,
                        polarity: str = an.PEAK) -> dict:
        """polarity は置いたときの向き（設計G §3.8）。周波数を省いたら、山は最大の点・谷は一番深い点に置く。"""
        try:
            k = Kind(kind)
        except ValueError:
            raise self._error("bad_marker_kind", "kind", {"kind": kind}) from None
        polarity = self._polarity(polarity)
        self._trace_of(trace)
        v = self._views.get(trace)
        if freq_hz is None:
            # 周波数を省いたらピーク（設計A §4.3.1）
            v = self._view(trace)
            idx = an.extreme_index(v, polarity)
            if idx is None:
                raise self._error("no_peak", "trace", {"polarity": polarity})
            freq_hz = v.freq_at(idx)
        self._check_hz("freq_hz", freq_hz)
        return self._op_result(self._markers.add(k, trace, freq_hz, v, polarity))

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

    def _cmd_marker_peak(self, id=None, direction: str = "highest", trace: int = 1,  # noqa: A002
                         polarity: str = an.PEAK) -> dict:
        """ピーク・次のピーク（左・右・次に高い）。polarity が谷なら谷・次の谷・次に深い（設計G §3.8）。"""
        if direction not in ("highest", an.LEFT, an.RIGHT, an.LOWER):
            raise self._error("bad_direction", "direction", {"direction": direction})
        polarity = self._polarity(polarity)
        created = False
        if id is None:
            # 何も選んでいなければ M を作ってから動かす（設計G §3.5）
            r = self._markers.peak_search(None, self._view(trace), polarity)
            if not r.ok or direction == "highest":
                return self._op_result(r, polarity)
            id = r.marker_id  # noqa: A001
            created = True
        m = self._marker(id)
        v = self._view(m.trace)
        if direction == "highest":
            r = self._markers.peak_search(m.id, v, polarity)
        else:
            r = self._markers.next_peak(m.id, direction, v, polarity)
        if not r.ok and created:
            self._markers_changed()  # 作った M は残る（次のピークが無いだけ）。画面にも出す
        return self._op_result(r, polarity)

    def _cmd_marker_top_peaks(self, trace: int = 1, n: int = 1, polarity: str = an.PEAK) -> dict:
        if isinstance(n, bool) or not isinstance(n, int):
            raise self._error("not_integer", "n")
        polarity = self._polarity(polarity)
        self._zs_check_freq_op()  # ピークの一覧の欄の操作は周波数のピーク（設計P §9.7）
        v = self._view(trace)
        self._no_dips_on_power(v, polarity)
        rs = self._markers.place_top_peaks(v, n, polarity)
        self._markers_changed()
        return {"placed": [self._op_dict(r) for r in rs]}

    def _cmd_marker_register_peak(self, trace: int, freq_hz: int, polarity: str = an.PEAK) -> dict:
        self._check_hz("freq_hz", freq_hz)
        return self._op_result(self._markers.register_peak(self._view(trace), freq_hz, self._polarity(polarity)))

    def _cmd_marker_offset_add(self, id, type: str, sign: str, delta) -> dict:  # noqa: A002
        m = self._marker(id)
        if type == "freq":
            self._zs_check_freq_op("type")  # ゼロスパンの横軸は周波数ではない（設計N §1）
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

    def _cmd_marker_offset_set(self, id, type: str, index: int, delta) -> dict:  # noqa: A002
        """オフセットのカーソルのずれを変える。delta は向き付き（Hz の整数か dB）。±Δ は大きさだけ変える。"""
        m = self._marker(id)
        if type not in ("freq", "level"):
            raise self._error("bad_offset_type", "type", {"type": type})
        try:
            self._markers.set_offset(m.id, type, index, delta)
        except (IndexError, TypeError):
            raise self._error("bad_offset_index", "index", {"index": index}) from None
        except ValueError as e:
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
        if (bandwidth_db is not _UNSET and bandwidth_db) or (threshold is not _UNSET and threshold is not None):
            self._zs_check_freq_op()  # N dB 帯域幅・しきい値の交点は周波数の幅を求める（ゼロスパンでは使わない）
        try:
            if bandwidth_db is not _UNSET:
                self._markers.set_bandwidth(m.id, tuple(bandwidth_db or ()))
            if threshold is not _UNSET:
                # しきい値はいまのトレースの表示の形式の値で受け、その形式と電力のしきい値かを覚える（設計R §3: SWR 2
                # 以下など。電力のしきい値だけが電力の単位・外部補正に従ってずれる: 設計書 O §1.4）
                v = self._views.get(m.trace)
                self._markers.set_threshold(
                    m.id, None if threshold is None else ThresholdSetting(
                        float(threshold["level"]), str(threshold["side"]), v.fmt if v is not None else formats.DB,
                        power=(v.quantity == Quantity.POWER) if v is not None else self._last_quantity == Quantity.POWER))
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
        # Y は、置いたときの縦軸の単位の値として持つ（あとで単位・外部補正・形式を替えたときは、その単位に従ってだけ
        # ずらす・直す: _set_view_opts・_follow_axis_format）
        kind = self._axis_kind()
        for k in ("y1", "y2"):
            if k in kw:
                self._markers.set_y_kind(k, kind)
        self._markers_changed()
        return {"xy": self._marker_snap["xy"]}

    def _cmd_peak_list(self, trace: int = 1, floor_margin_db: float = 10.0, polarity: str = an.PEAK) -> dict:
        """ピークの一覧（設計P §9.7）。floor_margin_db は基準からの N dB（電力はノイズフロア + N、基準との比は最大 − N）。

        戻り値: base（"noise_floor" | "max"）・base_level（その値）・polarity・peaks（周波数・レベル・基準からの差 diff_db）。
        """
        polarity = self._polarity(polarity)
        self._zs_check_freq_op()
        v = self._view(trace)
        self._no_dips_on_power(v, polarity)
        pl = an.peak_list(v, float(floor_margin_db), self._markers.excursion_db, polarity)
        # 探すのは dB（盛り上がり・基準からの N dB）。レベルは表示の形式の値で返す（|Γ|・SWR: 設計R §3）
        return {"trace": trace, "unit": v.unit, "base": pl.base, "base_level": v.display(pl.base_level),
                "polarity": pl.polarity,
                "peaks": [{"freq_hz": r.freq_hz, "level": v.display(r.level), "diff_db": r.diff_db} for r in pl.rows]}

    def _cmd_marker_to(self, id, target: str) -> dict:  # noqa: A002
        """マーカーから条件を変える（設計G §3.4）。Center・Span は設計C §5.11 の規則を通る。"""
        m = self._marker(id)
        if target in ("center", "span"):
            self._zs_check_freq_op("target")  # 時間の位置は周波数の条件にできない（設計N §1）
        if target == "center":
            cmd = self._markers.center_command(m.id, self._views)
        elif target == "span":
            cmd = self._markers.span_from_delta_command(m.id, self._views)
        elif target == "ref":
            cmd = self._markers.ref_level_command(m.id, self._views)
            if not cmd.ok:
                raise self._error(cmd.code, "id", {"id": m.id})
            if formats.is_linear_unit(cmd.unit):
                # |Γ|・SWR の縦軸は下端が決まっていて REF レベルを持たない（設計R §3）
                raise self._error("ref_level_linear", "id", {"id": m.id, "unit": cmd.unit})
            self._amplitude["ref_level"] = cmd.ref_level
            self._amplitude["unit"] = cmd.unit
            self._changed()
            return {"amplitude": dict(self._amplitude)}
        else:
            raise self._error("bad_target", "target", {"target": target})
        if not cmd.ok:
            raise self._error(cmd.code, "id", {"id": m.id})
        return self._apply(cmd.request)
