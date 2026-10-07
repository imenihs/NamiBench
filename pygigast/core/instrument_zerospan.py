"""計測器の本体のうち、ゼロスパンの時間軸と流す表示（設計N §1）。Instrument に混ぜて使う（制御スレッドだけが触る）。

- 横軸は時間（推定）。位置はマイクロ秒の整数（core/zero_span.py）。表示する長さ（表示の設定 view.zero_span_s）が
  0 なら1回の掃引、ほかは掃引を続けて横軸を流す（いちばん新しい点が 0、古いものは負）
- マーカー・XY カーソルは、周波数軸とは別の表（時間軸のマーカー表）に置く。ゼロスパンの間はそちらを出し、
  抜けたら周波数軸の表に戻る（周波数のマーカーを時間の位置として読まない。逆も同じ）
- トレースのイベントは時間軸（axis.kind = "time"）。流す表示は区切りごとに間引いた値で、掃引の間は NaN（空白）
- 区間の最大・最小・平均（パワー平均）: X1・X2 を両方置いていればその間、無ければ表示している範囲。
  表示中のトレースごとに、間引かない全部の点から求めてマーカー表の状態（interval）に出す
- 周波数を前提にする操作（Center・Span へ・N dB 帯域幅・しきい値・周波数のオフセット・ピークの一覧）は理由付きで断る
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import Optional

import numpy as np

from . import display_marks as dm
from . import zero_span as zs
from .instrument_common import CommandError
from .marker import TraceView
from .storage import adopted_to_dict as storage_conditions
from .zero_span_export import export_zero_span_csv

X_FREQ = "freq"
X_TIME = "time"


def zero_span_request(req):
    """Start = Stop で残した条件（前回終了時の状態・プリセット）を、ゼロスパンとして戻す命令にする。

    Start と Stop を打った形のままだと、掃引条件モデルは Start = Stop を最小 Span にする（設計C §5.10: ゼロスパンに
    入るのは Span に 0 を入れたときだけ）ので、ゼロスパンで終えた状態が戻らなかった。
    """
    if req.start_hz is not None and req.start_hz == req.stop_hz:
        return dataclasses.replace(req, start_hz=None, stop_hz=None, center_hz=req.start_hz, span_hz=0,
                                   edited=("center_hz", "span_hz"))
    return req


class ZeroSpanMixin:
    """ゼロスパンの時間軸（Instrument に混ぜる。トレース・マーカーは instrument_display.py）。"""

    def _init_zero_span(self) -> None:
        self._zs = zs.History()
        self._zs_windows: dict[int, Optional[zs.Window]] = {}

    # ------------------------------------------------------------------ 軸

    def _x_kind(self) -> str:
        return X_TIME if self._adopted.axis.step_hz == 0 else X_FREQ

    def _zs_length(self) -> float:
        opts = getattr(self, "_view_opts", None) or {}
        return float(opts.get("zero_span_s", 10) or 0)

    def _zs_range(self) -> tuple[int, int]:
        a = self._adopted
        return zs.x_range_us(self._zs_length(), a.axis.points, a.point_time_s)

    # ------------------------------------------------------------------ 掃引ごと

    def _zs_append(self, frame) -> None:
        """トレースの処理（設計F）の後の値を、流す表示のために時刻付きで持つ。"""
        a = frame.adopted
        if a is None or a.axis.step_hz != 0 or a.point_time_s <= 0:
            return
        per = {}
        for t in self._traces:
            if t.values is None or t.axis is None or t.quantity is None or t.axis.step_hz != 0:
                continue
            v = TraceView.build(t.number, t.axis, t.values, t.quantity, int(t.display_start_hz),
                                int(t.display_stop_hz), t.flags)
            per[t.number] = (v.level_db, t.flags)
        t0 = zs.first_point_time(frame.time, a.preroll_points, a.point_time_s)
        self._zs.add_sweep(self._sweep_count, t0, a.point_time_s, per)

    def _zs_views(self, notes: tuple[str, ...]) -> dict:
        """時間軸のマーカーが読むトレース（位置はマイクロ秒）。流す表示は表示する長さの中の全部の点。"""
        a = self._adopted
        length = self._zs_length()
        x0, x1 = self._zs_range()
        views, wins = {}, {}
        # 流す表示（60 秒で 30 万点）は、表示しているトレースとマーカーが載るトレースだけ読む（隠したトレースの分まで
        # 掃引ごとに作ると間に合わない）
        wanted = {m.trace for m in self._markers.markers()}
        for t in self._traces:
            if t.values is None or t.axis is None or t.quantity is None or t.axis.step_hz != 0:
                continue
            if length and not t.visible and t.number not in wanted:
                continue
            own = notes + self._trace_own_notes(t.profile)
            if not length:
                base = TraceView.build(t.number, t.axis, t.values, t.quantity, int(t.display_start_hz),
                                       int(t.display_stop_hz), t.flags)
                xs = zs.offsets_us(t.axis.points, a.point_time_s)
                v = TraceView.from_levels(t.number, xs, base.level_db, t.quantity, x0, int(xs[-1]), t.flags, own)
            else:
                win = self._zs.window(t.number, length)
                wins[t.number] = win
                if win is None:
                    continue
                v = TraceView.from_levels(t.number, win.x_us, win.level_db, t.quantity, x0, x1, win.flags, own)
            views[t.number] = v
        self._zs_windows = wins
        return views

    def _zs_payload(self, t, p: dict) -> dict:
        """イベント `trace` の時間軸の形（設計N §1）。p は周波数軸のときの中身（単位・動作などはそのまま使う）。"""
        a = self._adopted
        dt_us = a.point_time_s * zs.US
        length = self._zs_length()
        x0, x1 = self._zs_range()
        out = {**p, "x_kind": X_TIME, "display": {"start_us": x0, "stop_us": x1, "tail_gap": False}}
        if not length:
            out["axis"] = {"kind": X_TIME, "start_us": 0.0, "step_us": dt_us, "points": t.axis.points}
            return out
        win = self._zs_windows.get(t.number) if t.number in self._zs_windows else self._zs.window(t.number, length)
        shift = self._level_shift(t.quantity, t.profile)[0]
        if win is None or win.empty:
            n = 1
            start, step, values, flags = float(x0), float(x1 - x0) or 1.0, np.full(1, np.nan, np.float32), {}
        else:
            start, step, values, flags = zs.bins(win, length, a.point_time_s, use_min=t.action.value == "min_hold")
            n = values.size
            if shift:
                values = (values.astype(np.float64) + shift).astype(np.float32)
        out["axis"] = {"kind": X_TIME, "start_us": start, "step_us": step, "points": n}
        out["values"] = values
        out["flags"] = {"transient_head_points": 0,
                        "out_of_cal_range": flags.get("out_of_cal_range", np.zeros(n, dtype=bool)),
                        "no_calibration": p.get("flags", {}).get("no_calibration"),
                        "points": {k: v for k, v in flags.items()}}
        return out

    # ------------------------------------------------------------------ 読み値（区間の統計・REF の自動）

    def _zs_interval(self) -> dict:
        """区間の最大・最小・平均（パワー平均）。X1・X2 が両方あればその間、無ければ表示している範囲。"""
        xy = self._markers.xy
        if xy.x1_hz is not None and xy.x2_hz is not None:
            rng, lo, hi = "xy", min(xy.x1_hz, xy.x2_hz), max(xy.x1_hz, xy.x2_hz)
        else:
            (lo, hi), rng = self._zs_range(), "window"
        rows = []
        for n in sorted(self._views):
            t = self._traces[n]
            if not t.visible:
                continue
            v = self._views[n]
            s = zs.interval_stats(v.freqs, v.level_db, v.searchable, lo, hi)
            rows.append({"trace": n, "unit": v.unit, **(s or {"max": None, "max_us": None, "min": None,
                                                             "min_us": None, "avg": None, "points": 0})})
        return {"range": rng, "x0_us": int(lo), "x1_us": int(hi), "rows": rows}

    def _zs_ref_peaks(self) -> dict:
        """REF の自動（設計J §4.8）に使う最大値。ゼロスパンは表示している範囲の全部の点（流す表示の古い掃引も）。"""
        out = {}
        for n, v in self._views.items():
            if self._traces[n].visible:
                out[str(n)] = {"unit": v.unit,
                               "max": dm.usable_max(v.level_db, v.freqs, v.flags, v.start_hz, v.stop_hz)}
        return out

    def _zs_check_freq_op(self, field: Optional[str] = None) -> None:
        """周波数を前提にする操作は、ゼロスパンでは断る（横軸が時間: 設計N §1）。"""
        if self._x_kind() == X_TIME:
            raise CommandError("zero_span_axis", field)

    # ------------------------------------------------------------------ CSV（設計N §1・H §7.3）

    def _zs_one_sweep(self, t) -> Optional[zs.Window]:
        """1回の掃引の表示のときの、いまのトレースの点（横軸は表示する最初の点から）。時刻は最後の掃引の推定。"""
        if t.axis is None or t.axis.step_hz != 0:
            return None
        a = self._adopted
        level = TraceView.build(t.number, t.axis, t.values, t.quantity, int(t.display_start_hz),
                                int(t.display_stop_hz), t.flags).level_db
        xs = zs.offsets_us(t.axis.points, a.point_time_s)
        last = self._zs.latest(t.number)
        t0 = last.t0 if last is not None else float(t.last_update_time or 0.0)
        n = xs.size
        return zs.Window(x_us=xs, t=t0 + xs / zs.US, level_db=level.astype(np.float32), flags={},
                         piece=np.zeros(n, dtype=np.int64), index=np.arange(n, dtype=np.int64),
                         sweep_no=np.full(n, last.sweep_no if last is not None else self._sweep_count, dtype=np.int64))

    def _zs_export_csv(self, path: str, sel: list) -> dict:
        """表示している範囲（1回の掃引はいまの掃引、流す表示は表示する長さ）を時間付きで書き出す。"""
        cols = []
        length = self._zs_length()
        for tid in sel:
            if isinstance(tid, bool) or not isinstance(tid, int):
                raise CommandError("zero_span_csv_live_only", "traces", {"trace": tid})
            t = self._trace_of(tid)
            if t.values is None:
                raise CommandError("no_trace_data", "traces", {"trace": tid})
            if length:
                win = self._zs.window(tid, length)
            else:
                win = self._zs_one_sweep(t)
            if win is None or win.empty:
                raise CommandError("no_trace_data", "traces", {"trace": tid})
            shift, unit = self._level_shift(t.quantity, t.profile)
            cols.append({"number": tid, "unit": unit or "dB", "x_us": win.x_us, "t": win.t,
                         "sweep_no": win.sweep_no, "index": win.index,
                         "level_db": win.level_db.astype(np.float64) + shift})
        if not cols:
            raise CommandError("no_data", "traces")
        ident = self._identity or {}
        meta = {
            "device": {"model": ident.get("model") or self._profile.get("model"), "serial": ident.get("serial")},
            "conditions": storage_conditions(self._adopted),
            "calibration": self._calibration_record(),
            "normalization": self._normalization_record(),
            "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "time_axis": {"estimated": True, "point_time_s": self._adopted.point_time_s, "length_s": length,
                          "zero": "first_point" if not length else "newest_point"},
        }

        def submit(progress, cancel):
            return self._writer.submit(export_zero_span_csv, path, cols, meta=meta, progress=progress, cancel=cancel)

        return self._start_task("export_csv", submit,
                                lambda r: {"path": str(r.path), "rows": r.rows, "warnings": list(r.warnings)},
                                detail={"path": str(path)})
