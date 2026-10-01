"""計測器の本体のうち、トレース間の差「A − B」（設計F §2・§5.4）。

Instrument（instrument.py）に混ぜて使う。メソッドは制御スレッドから呼ばれる（設計A §4.2）。
- A・B は生きたトレースの番号（1〜4）か参照トレースの id（"F1.1" など: 開いたファイルのトレース）
- 差は dB（リニアの比）。B を A の周波数軸へ dB で線形補間し、重なる範囲だけ値を持つ。重ならない点と、
  どちらかが値なしの点は値なし（NaN: 設計E §6.4）。量の種類（電力と比）が違えば作らず、理由を出す
- 計算は trace.ops.difference（設計F §5.4 の純粋な計算）。ここは選んだ2本を探し、どちらかが変わるたびに
  計算し直してイベント `trace`（trace: "D"）を送るだけ
- 保存ファイル（HDF5）には入れない（設計H §7.1 のトレースは 1〜4 だけ）。CSV の書き出しには "D" で選べる
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from .storage.h5file import TraceData
from .trace import difference
from .types import FreqAxis, Quantity, ValueKind

# イベント `trace`・get_trace・export_csv で差を指す id
DIFF_TRACE_ID = "D"

# 差を作れない理由のコード（状態の difference.error.code と命令の失敗。文言は画面側）
DIFF_ERR_QUANTITY_MISMATCH = "diff_quantity_mismatch"  # 電力と比の差は作らない（設計F §5.4）
DIFF_ERR_NO_OVERLAP = "diff_no_overlap"  # A と B の周波数の範囲が重ならない
DIFF_ERR_UNKNOWN_TRACE = "diff_unknown_trace"  # 選んだ参照トレースが開かれていない（閉じた・起動し直した）

_ERR_OF_OPS = {"quantity_mismatch": DIFF_ERR_QUANTITY_MISMATCH, "no_overlap": DIFF_ERR_NO_OVERLAP}


def _nan_equal(x: Optional[np.ndarray], y: Optional[np.ndarray]) -> bool:
    if x is None or y is None:
        return x is y
    return x.shape == y.shape and bool(np.array_equal(x, y, equal_nan=True))


class DiffMixin:
    """トレース間の差（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・状態の保存

    def _init_diff(self, st: Optional[dict]) -> None:
        # 選んでいる2本。片側は int（生きたトレース）か {"id", "path", "number"}（参照トレース）。
        # 参照トレースの id（"F1.1"）は開いた順の番号で、起動し直すと別のファイルを指しうるので、
        # ファイルの場所とトレースの番号も持ち、id が無効になったら同じ場所のファイルへ結び直す
        self._diff_sel: Optional[dict] = None
        self._diff_error: Optional[str] = None
        self._diff_last: Optional[dict] = None  # 最後に計算した差（_diff_compute の結果）
        self._diff_sent: Optional[tuple] = None  # 最後に送った差（2本の id・軸・値）。同じなら送り直さない
        d = (st or {}).get("difference")
        if isinstance(d, dict):
            a, b = self._diff_side_from_saved(d.get("a")), self._diff_side_from_saved(d.get("b"))
            if a is not None and b is not None:
                self._diff_sel = {"a": a, "b": b}
        # 参照トレースの側は、開くまで「開かれていない」を状態に出す（イベントは送らない）
        self._diff_refresh(emit=False)

    @staticmethod
    def _diff_side_from_saved(v) -> object:
        if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 4:
            return v
        if isinstance(v, dict) and isinstance(v.get("path"), str) and isinstance(v.get("number"), int):
            # 起動直後は何も開いていないので id は無効。開いたときに場所で結び直す
            return {"id": None, "path": v["path"], "number": int(v["number"])}
        return None

    def _diff_state_to_save(self) -> dict:
        sel = self._diff_sel
        if sel is None:
            return {"difference": None}

        def side(s):
            return s if isinstance(s, int) else {"path": s["path"], "number": s["number"]}

        return {"difference": {"a": side(sel["a"]), "b": side(sel["b"])}}

    # ------------------------------------------------------------------ 状態

    @staticmethod
    def _diff_side_id(s) -> object:
        """片側の id（番号か "F1.1"）。参照トレースが開かれていなければ None。"""
        return s if isinstance(s, int) else s["id"]

    def _diff_state(self) -> dict:
        sel = self._diff_sel
        err = None
        if self._diff_error is not None:
            err = {"code": self._diff_error}
            if self._diff_error == DIFF_ERR_UNKNOWN_TRACE and sel is not None:
                # 開けば差が戻るファイル（場所とトレースの番号）。画面が「どれを開けばよいか」を出すため
                err["missing"] = [{"side": k, "path": sel[k]["path"], "name": Path(sel[k]["path"]).name,
                                   "number": sel[k]["number"]}
                                  for k in ("a", "b") if not isinstance(sel[k], int) and sel[k]["id"] is None]
        return {
            "a": None if sel is None else self._diff_side_id(sel["a"]),
            "b": None if sel is None else self._diff_side_id(sel["b"]),
            "active": self._diff_last is not None,
            "error": err,
        }

    # ------------------------------------------------------------------ 2本を探す

    def _diff_parse_side(self, v, field: str) -> object:
        """命令の引数（番号か参照トレースの id）を片側の持ち方にする。知らないものは diff_unknown_trace。"""
        if isinstance(v, int) and not isinstance(v, bool):
            if not 1 <= v <= 4:
                raise self._error(DIFF_ERR_UNKNOWN_TRACE, field, {"trace": v})
            return v
        if not isinstance(v, str) or v == DIFF_TRACE_ID:
            raise self._error(DIFF_ERR_UNKNOWN_TRACE, field, {"trace": v})
        fid = v.split(".", 1)[0]
        e = self._files.get(fid)
        if e is None or v not in e["traces"]:
            raise self._error(DIFF_ERR_UNKNOWN_TRACE, field, {"trace": v})
        return {"id": v, "path": e["path"], "number": int(e["traces"][v].number)}

    def _diff_resolve(self, s) -> Optional[dict]:
        """片側の今の中身。生きたトレースはまだ値が無ければ values が None。参照トレースが開かれていなければ None。"""
        if isinstance(s, int):
            t = self._traces[s]
            if t.values is None or t.axis is None or t.quantity is None:
                return {"kind": "live", "trace": t, "values": None}
            return {"kind": "live", "trace": t, "values": t.values, "freqs": t.axis.freqs(), "quantity": t.quantity}
        e = self._files.get((s["id"] or "").split(".", 1)[0])
        if e is None or s["id"] not in e["traces"] or e["path"] != s["path"]:
            # 閉じた・起動し直した: 同じ場所のファイルを開いていれば、そのトレースへ結び直す
            s["id"] = None
            for fid, e2 in self._files.items():
                tid = f"{fid}.{s['number']}"
                if e2["path"] == s["path"] and tid in e2["traces"]:
                    s["id"], e = tid, e2
                    break
            else:
                return None
        td = e["traces"][s["id"]]
        return {"kind": "file", "id": s["id"], "file": e, "td": td, "values": td.values, "freqs": td.freqs(),
                "quantity": td.quantity}

    # ------------------------------------------------------------------ 計算

    def _diff_compute(self) -> tuple[Optional[dict], Optional[str]]:
        """(差, 作れない理由)。どちらかにまだ値が無いときは (None, None)（理由ではなく、待っているだけ）。"""
        sel = self._diff_sel
        if sel is None:
            return None, None
        ra, rb = self._diff_resolve(sel["a"]), self._diff_resolve(sel["b"])
        if ra is None or rb is None:
            return None, DIFF_ERR_UNKNOWN_TRACE
        if ra["values"] is None or rb["values"] is None:
            return None, None
        fa, fb = np.asarray(ra["freqs"], dtype=np.int64), np.asarray(rb["freqs"], dtype=np.int64)
        res = difference(fa, ra["values"], ra["quantity"], fb, rb["values"], rb["quantity"])
        if res.error is not None:
            return None, _ERR_OF_OPS.get(res.error, res.error)
        # A の軸の全点に並べ直す。重ならない点は値なし（NaN）。0 W・値なしの点の ±inf・NaN も値なし（設計E §6.4）
        lo, hi = max(int(fa.min()), int(fb.min())), min(int(fa.max()), int(fb.max()))
        mask = (fa >= lo) & (fa <= hi)
        full = np.full(fa.shape, np.nan, dtype=np.float64)
        full[mask] = res.values_db
        full = np.where(np.isfinite(full), full, np.nan)
        return {"a": ra, "b": rb, "freqs": fa, "db": full}, None

    def _diff_refresh(self, emit: bool = True, swept: frozenset = frozenset()) -> bool:
        """差を計算し直す。値が変わったらイベント `trace`（trace: "D"）を送る。
        swept はこの掃引で値が変わった生きたトレースの番号。A か B がそこにあれば、値が同じでも送る
        （生きたトレースのイベントと同じく掃引ごとに送り、sweep_no を進める）。
        作れなくなったら理由を1回知らせる。状態（active・error）が変わったら True（呼び側が状態を送る）。"""
        before = (self._diff_last is not None, self._diff_error)
        data, err = self._diff_compute()
        self._diff_last = data
        if err is not None and err != self._diff_error:
            self._notice("difference_unavailable", {"code": err, **self._diff_ids()})
        self._diff_error = err
        if data is None:
            self._diff_sent = None
        elif emit:
            ids = self._diff_ids()
            ids = (ids["a"], ids["b"])
            sent = self._diff_sent
            if (swept.intersection(ids) or sent is None or sent[0] != ids or not np.array_equal(sent[1], data["freqs"])
                    or not _nan_equal(sent[2], data["db"])):
                self._emit("trace", self._diff_payload())
                self._diff_sent = (ids, data["freqs"], data["db"])
        return before != (self._diff_last is not None, self._diff_error)

    def _diff_ids(self) -> dict:
        sel = self._diff_sel or {"a": None, "b": None}
        return {k: None if sel[k] is None else self._diff_side_id(sel[k]) for k in ("a", "b")}

    # ------------------------------------------------------------------ 送る形

    @staticmethod
    def _diff_origin(r: dict) -> dict:
        """凡例に出す片側の出どころ。参照トレースはファイル名・測定日時・機器（設計F §5.4）。"""
        if r["kind"] == "live":
            return {"source": "live", "trace": r["trace"].number}
        e = r["file"]
        return {"source": "file", "trace": r["id"], "file_id": e["file_id"], **dict(r["td"].origin)}

    def _diff_axis_display(self, r: dict) -> tuple[dict, dict]:
        """A の軸と表示の範囲（イベント `trace` の axis・display と同じ形）。"""
        if r["kind"] == "live":
            t = r["trace"]
            return ({"start_hz": t.axis.start_hz, "step_hz": t.axis.step_hz, "points": t.axis.points},
                    {"start_hz": t.display_start_hz, "stop_hz": t.display_stop_hz,
                     "tail_gap": self._adopted.tail_gap})
        td, f = r["td"], r["freqs"]
        if td.axis is not None and td.freqs_hz is None:
            axis = {"start_hz": td.axis.start_hz, "step_hz": td.axis.step_hz, "points": td.axis.points}
        else:
            axis = {"freqs_hz": f.astype(np.int64), "points": int(f.size)}
        return axis, {"start_hz": int(f[0]) if f.size else None, "stop_hz": int(f[-1]) if f.size else None,
                      "tail_gap": False}

    def _diff_payload(self) -> dict:
        """イベント `trace` の差（trace: "D"）。値は dB の float32、量は比（設計F §5.4）。"""
        d = self._diff_last
        if d is None:
            raise self._error(self._diff_error or "no_trace_data", "id", {"id": DIFF_TRACE_ID})
        ra, rb = d["a"], d["b"]
        axis, display = self._diff_axis_display(ra)
        ids = self._diff_ids()
        # 生きたトレースの側が最後に更新された時刻（参照トレースだけなら None）
        times = [r["trace"].last_update_time for r in (ra, rb)
                 if r["kind"] == "live" and r["trace"].last_update_time is not None]
        return {
            "trace": DIFF_TRACE_ID,
            "source": "difference",
            "a": ids["a"],
            "b": ids["b"],
            "origin": {"a": self._diff_origin(ra), "b": self._diff_origin(rb)},
            "sweep_no": self._sweep_count,
            "time": max(times) if times else None,
            "axis": axis,
            "display": display,
            "quantity": Quantity.RATIO.value,
            "unit": "dB",
            "value_kind": ValueKind.REAL.value,
            "values": d["db"].astype(np.float32),
            "flags": {"points": {}},
        }

    def _diff_trace_data(self) -> TraceData:
        """CSV の書き出しに渡す差。値はリニアの振幅比（書き出しで dB に戻る）。値なしは NaN（CSV では空欄）。

        書き出しの列名は番号（int）からしか作れないので number は 0。どの差かは info・origin に書く。
        """
        d = self._diff_last
        if d is None:
            raise self._error(self._diff_error or "no_trace_data", "traces", {"trace": DIFF_TRACE_ID})
        ra, rb = d["a"], d["b"]
        ids = self._diff_ids()
        lin = np.power(10.0, d["db"] / 20.0)
        axis = None
        freqs = None
        if ra["kind"] == "live":
            t = ra["trace"]
            axis = FreqAxis(t.axis.start_hz, t.axis.step_hz, t.axis.points)
            start, stop = t.display_start_hz, t.display_stop_hz
        else:
            td = ra["td"]
            if td.axis is not None and td.freqs_hz is None:
                axis = td.axis
            else:
                freqs = d["freqs"].copy()
            start, stop = td.display_start_hz, td.display_stop_hz
        return TraceData(
            number=0, values=lin, quantity=Quantity.RATIO, value_kind=ValueKind.REAL, axis=axis, freqs_hz=freqs,
            display_start_hz=start, display_stop_hz=stop,
            info={"trace": DIFF_TRACE_ID, "a": ids["a"], "b": ids["b"], "unit": "dB"},
            origin={"a": self._diff_origin(ra), "b": self._diff_origin(rb)},
        )

    # ------------------------------------------------------------------ 命令（設計F §5.4）

    def _cmd_set_difference(self, a, b) -> dict:
        """差「A − B」を選ぶ。いま作れないと分かるもの（量の種類が違う・重ならない）は選ばずに断る。
        片側にまだ値が無いときは選んでおき、値が来たら作る。"""
        sa, sb = self._diff_parse_side(a, "a"), self._diff_parse_side(b, "b")
        prev = self._diff_sel
        self._diff_sel = {"a": sa, "b": sb}
        data, err = self._diff_compute()
        if err is not None:
            self._diff_sel = prev
            raise self._error(err, None, {"a": a, "b": b})
        self._diff_sent = None
        self._diff_refresh()
        self._changed()
        return {"difference": self._diff_state()}

    def _cmd_clear_difference(self) -> dict:
        self._diff_sel = None
        self._diff_refresh()
        self._changed()
        return {"difference": self._diff_state()}
