"""計測器の本体のうち、ウォーターフォールと密度表示（設計P §3・§4・§8・§9.2・§9.3）。Instrument に混ぜる。

- 行: 選んだトレース（既定はトレース1）の、トレースの処理の後の値（P §9.2）。掃引ごとに1行を画面へ送る（イベント
  `waterfall`）。画面に持つ行はここが持ち（上限は表示の設定 wf_rows）、途中から繋いだ画面・広げた画面は命令
  `waterfall_rows` で取り直す。上限を超えた古い行は、記録中なら作業ファイルから読み直す（P §3・H §3.1）
- 色の範囲: 自動（ノイズフロアの中央値〜最大値を数秒ごとに追う）か、手で決めた下限・上限（表示の設定）。Qt と Web は
  ここが決めた範囲で描く（P §8: 同じ色の範囲）
- 行をクリック → その時刻の掃引を「過去のトレース」としてスペクトルに出す（参照トレースと同じ仕組み・id は "W.1"）。
  再生中は時刻のカーソルをそこへ動かす（instrument_playback.py）
- 密度: 表示の組み合わせが「＋密度」のときだけ作る（イベント `density`）。縦の範囲はいまの縦軸（REF・dB/div）、
  横は掃引の列。N 回で重みが半分（表示の設定 density_half）。何秒分の密度か（N × 掃引の間隔）も送る（P §4）
- 値は測った単位の dB で持ち、画面へ出すときに表示の単位（dBμV・外部補正: 設計O）へずらす
"""
from __future__ import annotations

import collections
import datetime
import time
from typing import Optional

import numpy as np

from . import waterfall as wf
from .instrument_common import CommandError
from .instrument_display import db_values, unit_for
from .level.convert import dbm_to_watt
from .report_layout import DIVISIONS
from .storage.h5file import TraceData
from .types import FreqAxis, Quantity, ValueKind
from .view_units import level_shift

# 過去のトレース（行をクリックした掃引）の参照トレースの番号（ファイルの id は PAST_FILE_ID）
PAST_FILE_ID = "W"
# 1回の waterfall_rows で返す行の上限（画面の縦の画素より多くは要らない）
MAX_ROWS_REQUEST = 4000


class WaterfallMixin:
    """ウォーターフォール・密度表示（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化

    def _init_waterfall(self) -> None:
        o = self._view_opts
        self._wf_history = wf.History(o["wf_rows"])
        self._wf_seg = 0
        self._wf_seg_key: Optional[tuple] = None
        self._wf_auto = wf.AutoRange()
        self._wf_seq = 0
        self._wf_epoch = 0
        self._wf_times: collections.deque[float] = collections.deque(maxlen=16)
        self._density = wf.Density()
        self._density_src: collections.deque[wf.Row] = collections.deque(
            maxlen=wf.DENSITY_REPLAY_HALVES * int(o["density_half"]))
        self._density_amp: Optional[tuple] = None
        self._wf_rec_src = None  # 記録中の作業ファイルを読む RecordSource（書き込みスレッドだけが触る）

    def _shutdown_waterfall(self) -> None:
        src = self._wf_rec_src
        self._wf_rec_src = None
        if src is not None:
            try:
                self._writer.submit(src.close)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ 状態

    def _wf_range(self) -> dict:
        o = self._view_opts
        if o["wf_color_auto"] and self._wf_auto.range is not None:
            lo, hi = self._wf_auto.range
        else:
            lo, hi = float(o["wf_color_lo"]), float(o["wf_color_hi"])
        return {"lo": lo, "hi": hi, "auto": bool(o["wf_color_auto"])}

    def _wf_shift(self, row: wf.Row) -> tuple[float, Optional[str]]:
        return level_shift(row.unit, self._view_opts)

    def _waterfall_state(self) -> dict:
        h = self._wf_history
        latest = h.latest()
        return {"range": self._wf_range(), "epoch": self._wf_epoch, "seq": self._wf_seq, "rows": len(h),
                "limit": h.limit, "t_first": h.first_time(), "t_last": latest.t_end if latest else None,
                "unit": self._wf_shift(latest)[1] if latest else None,
                "density": self._density_info()}

    def _density_info(self) -> dict:
        interval = wf.sweep_interval_s(list(self._wf_times))
        half = int(self._view_opts["density_half"])
        return {"half": half, "interval_s": interval, "seconds": None if interval is None else half * interval}

    # ------------------------------------------------------------------ 1回の掃引

    def _wf_row_from_trace(self, frame, pos: Optional[tuple[int, int]] = None) -> Optional[wf.Row]:
        """選んだトレースの、いま表示している範囲の点から行を作る（条件が変わったら並びの番号を変える）。"""
        n = int(self._view_opts["wf_trace"])
        t = self._traces[n]
        if t.values is None or t.axis is None or t.quantity is None:
            return None
        freqs = t.axis.freqs()
        db = db_values(t.values, t.quantity)
        if t.axis.step_hz == 0:
            mask = np.ones(freqs.shape, dtype=bool)
        else:
            mask = (freqs >= int(t.display_start_hz)) & (freqs <= int(t.display_stop_hz))
        if not mask.any():
            return None
        vals = db[mask]
        f0 = int(freqs[mask][0])
        unit = unit_for(t.quantity, t.profile) or "dBm"
        a = frame.adopted
        cond = {"start_hz": int(t.display_start_hz), "stop_hz": int(t.display_stop_hz),
                "rbw_hz": int(a.rbw_hz) if a is not None else None,
                "mode": a.mode.value if a is not None else None}
        key = (f0, int(t.axis.step_hz), int(vals.size), cond["rbw_hz"], cond["mode"], t.quantity.value, unit, n)
        if key != self._wf_seg_key:
            self._wf_seg_key = key
            self._wf_seg += 1
        row = wf.row_from_values(frame.time, f0, int(t.axis.step_hz), vals, unit=unit, seg=self._wf_seg, cond=cond,
                                 pos=pos)
        row.extra = {"quantity": t.quantity.value, "profile": t.profile, "trace": n}
        return row

    def _waterfall_on_frame(self, frame, live: bool = True) -> None:
        """トレースを更新した後に呼ぶ。ライブの掃引は行を足して画面へ送る。再生の掃引は色の範囲と密度だけ。"""
        row = self._wf_row_from_trace(frame)
        if row is None:
            return
        self._wf_times.append(float(frame.time))
        shift, unit = self._wf_shift(row)
        shown = row.values + np.float32(shift) if shift else row.values
        changed = bool(self._view_opts["wf_color_auto"]) and self._wf_auto.feed(shown, time.monotonic())
        if live:
            self._wf_history.add(row)
            self._wf_seq += 1
            self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "unit": unit,
                                     "range": self._wf_range(), "rows": [self._wf_row_out(row)], "append": True})
        elif changed:
            self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "unit": unit,
                                     "range": self._wf_range(), "rows": [], "append": True})
        self._density_src.append(row)
        if self._view_opts["layout"] == "density":
            self._density_feed(row)

    def _wf_row_out(self, row: wf.Row) -> dict:
        shift, _unit = self._wf_shift(row)
        vals = row.values + np.float32(shift) if shift else row.values
        return {**row.head(shift), "values": vals.astype(np.float32)}

    # ------------------------------------------------------------------ 表示の設定が変わった

    def _waterfall_view_changed(self, old: dict, new: dict) -> None:
        self._wf_history.set_limit(int(new["wf_rows"]))
        if new.get("density_half") != old.get("density_half"):
            self._density_src = collections.deque(self._density_src,
                                                  maxlen=wf.DENSITY_REPLAY_HALVES * int(new["density_half"]))
        unit_changed = any(old.get(k) != new.get(k) for k in ("level_unit", "ext_db"))
        if unit_changed:
            self._wf_auto.reset()
        if unit_changed or old.get("wf_trace") != new.get("wf_trace"):
            # 行の値の単位が変わった・行にするトレースが変わった: 画面は行を取り直す
            self._wf_epoch += 1
            self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "reset": True,
                                     "range": self._wf_range(), "rows": []})
        elif any(old.get(k) != new.get(k) for k in ("wf_color_auto", "wf_color_lo", "wf_color_hi")):
            self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "range": self._wf_range(),
                                     "rows": [], "append": True})
        if new.get("layout") == "density" and (old.get("layout") != "density" or unit_changed
                                               or old.get("density_half") != new.get("density_half")):
            self._density.key = None
            self._density_rebuild()

    def _waterfall_on_changed(self) -> None:
        """状態が変わるたびに呼ぶ。縦軸（REF・dB/div）が変わったら密度を作り直す（縦の範囲が変わる）。"""
        if self._view_opts.get("layout") != "density":
            return
        amp = self._density_level_range()
        if amp != self._density_amp:
            self._density_rebuild()

    # ------------------------------------------------------------------ 密度（P §4・§9.3）

    def _density_level_range(self) -> tuple[float, float]:
        amp = self._amplitude
        ref = 0.0 if amp.get("ref_level") is None else float(amp["ref_level"])
        div = float(amp.get("db_per_div") or 10.0)
        return ref - DIVISIONS * div, ref

    def _density_feed(self, row: wf.Row, emit: bool = True) -> None:
        lo, hi = self._density_level_range()
        self._density_amp = (lo, hi)
        shift, _unit = self._wf_shift(row)
        key = (row.seg, row.columns, row.start_hz, row.step_hz, lo, hi, int(self._view_opts["density_half"]), shift)
        if self._density.setup(key, row.columns, lo, hi, int(self._view_opts["density_half"])):
            for r in list(self._density_src)[:-1]:
                if r.seg == row.seg and r.columns == row.columns:
                    self._density.add(r.values + np.float32(shift))
        self._density.add(row.values + np.float32(shift))
        if emit:
            self._emit_density(row)

    def _density_rebuild(self) -> None:
        rows = list(self._density_src)
        if not rows:
            self._density_amp = self._density_level_range()
            return
        self._density.key = None
        self._density_feed_replay(rows[-1])

    def _density_feed_replay(self, row: wf.Row) -> None:
        lo, hi = self._density_level_range()
        self._density_amp = (lo, hi)
        shift, _unit = self._wf_shift(row)
        half = int(self._view_opts["density_half"])
        key = (row.seg, row.columns, row.start_hz, row.step_hz, lo, hi, half, shift)
        self._density.setup(key, row.columns, lo, hi, half)
        for r in self._density_src:
            if r.seg == row.seg and r.columns == row.columns:
                self._density.add(r.values + np.float32(shift))
        self._emit_density(row)

    def _emit_density(self, row: wf.Row) -> None:
        d = self._density
        stop = row.start_hz + row.step_hz * max(0, row.columns - 1)
        self._emit("density", {"image": d.image(), "bins": d.bins, "cols": d.n_cols, "start_hz": int(row.start_hz),
                               "stop_hz": int(stop), "level_lo": d.level_lo, "level_hi": d.level_hi,
                               "bin_db": wf.DENSITY_BIN_DB, "zero_span": row.zero_span, "sweeps": d.added,
                               **self._density_info()})

    def _cmd_density_get(self) -> dict:
        """いまの密度（途中から繋いだ画面・密度に切り替えた画面が描くため）。まだ無ければ image は None。"""
        rows = list(self._density_src)
        if self._view_opts["layout"] != "density" or not rows or self._density.key is None:
            return {"image": None, **self._density_info()}
        row = rows[-1]
        d = self._density
        stop = row.start_hz + row.step_hz * max(0, row.columns - 1)
        return {"image": d.image(), "bins": d.bins, "cols": d.n_cols, "start_hz": int(row.start_hz),
                "stop_hz": int(stop), "level_lo": d.level_lo, "level_hi": d.level_hi, "bin_db": wf.DENSITY_BIN_DB,
                "zero_span": row.zero_span, "sweeps": d.added, **self._density_info()}

    # ------------------------------------------------------------------ 行を取り直す（P §9.2）

    def _cmd_waterfall_rows(self, max_rows: int = 600, t_lo: Optional[float] = None,
                            t_hi: Optional[float] = None) -> object:
        """画面に描く行（古い順）。t_lo・t_hi（UNIX 秒）で時間の範囲を選べる（時間方向の拡大: P §5）。

        ライブ: 画面に持つ行から。範囲が持つ行より古く、記録中なら作業ファイルから読み直して足す（P §3）。
        再生中: 記録のファイルから（instrument_playback.py）。行が max_rows より多ければ最大値でまとめる。
        """
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_ROWS_REQUEST:
            raise self._error("bad_value", "max_rows", {"max": MAX_ROWS_REQUEST})
        for name, v in (("t_lo", t_lo), ("t_hi", t_hi)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise self._error("bad_value", name)
        if self._playback is not None:
            return self._playback_rows(max_rows, t_lo, t_hi)
        if t_lo is None and t_hi is None:
            # 最新に付いていく表示: 新しい max_rows 行を1掃引ずつ（まとめない）
            return self._wf_rows_result(self._wf_history.rows()[-max_rows:], "live")
        rows = self._wf_history.between(t_lo, t_hi)
        first = self._wf_history.first_time()
        rec = self._recorder
        older = rec is not None and t_lo is not None and (first is None or t_lo < first)
        if not older:
            return self._wf_rows_result(wf.select_rows(rows, max_rows), "live")
        path = rec.path
        hi = (first - 1e-6) if first is not None else t_hi

        def job():
            from .storage.record_source import RecordSource

            src = self._wf_rec_src
            if src is None or src.path != path:
                if src is not None:
                    src.close()
                src = self._wf_rec_src = RecordSource(path)
            else:
                src.refresh()
            return self._record_rows(src, max_rows, t_lo, hi)

        def then(file_rows) -> dict:
            return self._wf_rows_result(wf.select_rows(list(file_rows) + rows, max_rows), "live")

        return self._io(job, then)

    def _wf_rows_result(self, rows: list[wf.Row], source: str) -> dict:
        latest = self._wf_history.latest()
        return {"rows": [self._wf_row_out(r) for r in rows], "range": self._wf_range(), "epoch": self._wf_epoch,
                "seq": self._wf_seq, "source": source,
                "unit": self._wf_shift(rows[-1])[1] if rows else (self._wf_shift(latest)[1] if latest else None),
                "t_first": self._wf_t_first(), "t_last": latest.t_end if latest else None}

    def _wf_t_first(self) -> Optional[float]:
        """さかのぼって見られる一番古い時刻（記録中なら記録の始め）。"""
        first = self._wf_history.first_time()
        rec = self._recorder
        if rec is not None:
            return min(first, rec.started_at) if first is not None else rec.started_at
        return first

    # ------------------------------------------------------------------ 行をクリック（P §3）

    def _cmd_waterfall_pick(self, t: float, pos: Optional[list] = None) -> object:
        """時刻 t の掃引を、ライブなら「過去のトレース」として出し、再生中ならカーソルをそこへ動かす。"""
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            raise self._error("bad_value", "t")
        if self._playback is not None:
            return self._playback_seek_time(float(t), pos)
        t = float(t)
        first = self._wf_history.first_time()
        if self._recorder is not None and (first is None or t < first):
            # 画面に持つ行より古い: 記録の作業ファイルから読む（P §3）
            return self._wf_pick_from_record(t)
        row = self._wf_history.find(t)
        if row is None:
            raise self._error("no_waterfall_row", "t")
        self._show_past_row(row)
        return {"file_id": PAST_FILE_ID, "t": row.t}

    def _wf_pick_from_record(self, t: float) -> object:
        path = self._recorder.path

        def job():
            from .storage.record_source import RecordSource

            src = self._wf_rec_src
            if src is None or src.path != path:
                if src is not None:
                    src.close()
                src = self._wf_rec_src = RecordSource(path)
            else:
                src.refresh()
            rows = self._record_rows(src, 1, t, t)
            if not rows:
                hit = src.locate(t)
                if hit is None:
                    return None
                tt = float(src.times(hit[0])[hit[1]])
                rows = self._record_rows(src, 1, tt, tt)
            return rows[-1] if rows else None

        def then(row) -> dict:
            if row is None:
                raise CommandError("no_waterfall_row", "t")
            self._show_past_row(row)
            return {"file_id": PAST_FILE_ID, "t": row.t}

        return self._io(job, then)

    def _show_past_row(self, row: wf.Row) -> None:
        """行の値を「過去のトレース」（参照トレース W.1）にする。前の過去のトレースは置き換える。"""
        q = Quantity((row.extra or {}).get("quantity") or ("ratio" if row.unit == "dB" else "power"))
        db = row.values.astype(np.float64)
        values = dbm_to_watt(db) if q == Quantity.POWER else np.power(10.0, db / 20.0)
        when = datetime.datetime.fromtimestamp(row.t).astimezone()
        td = TraceData(number=1, values=values, quantity=q, value_kind=ValueKind.REAL,
                       axis=FreqAxis(int(row.start_hz), int(row.step_hz), int(row.columns)),
                       info={"action": "update", "visible": True, "count": None, "current_count": row.sweeps,
                             "unit": row.unit},
                       flags={}, profile=dict((row.extra or {}).get("profile") or {}),
                       origin={"file": "", "created_at": when.isoformat(timespec="seconds")})
        self._files[PAST_FILE_ID] = {
            "file_id": PAST_FILE_ID, "kind": "past", "path": "", "name": when.strftime("%H:%M:%S"),
            "created_at": when.isoformat(timespec="milliseconds"), "device_model": "", "device_serial": "",
            "note": "", "traces": {f"{PAST_FILE_ID}.1": td}, "markers": [], "t": row.t, "sweeps": row.sweeps,
        }
        self._emit("trace", self._file_trace_payload(f"{PAST_FILE_ID}.1"))
        self._diff_refresh()
        self._changed()
