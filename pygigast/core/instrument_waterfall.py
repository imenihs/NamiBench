"""計測器の本体のうち、ウォーターフォールと密度表示（設計P §3・§4・§8・§9.2・§9.3）。Instrument に混ぜる。

- 行: 選んだトレース（既定はトレース1）の、トレースの処理の後の値（P §9.2）。掃引ごとに1行を画面へ送る（イベント
  `waterfall`）。画面に持つ行はここが持ち（上限は表示の設定 wf_rows）、途中から繋いだ画面・広げた画面は命令
  `waterfall_rows` で取り直す。上限を超えた古い行は、記録中なら作業ファイルから読み直す（P §3・H §3.1）
- 色の範囲: 自動（ノイズフロアの中央値〜最大値を数秒ごとに追う）か、手で決めた下限・上限（表示の設定）。Qt と Web は
  ここが決めた範囲で描く（P §8: 同じ色の範囲）。|Γ|・SWR の行（設計R §3）は縦軸と同じ範囲（下端〜下端 + 10 目盛）で
  色を付ける（縦軸は下端が決まり1目盛だけを選ぶ。「ノイズフロア」に当たるものが無いので自動は使わない）
- 行をクリック → その時刻の掃引を「過去のトレース」としてスペクトルに出す（参照トレースと同じ仕組み・id は "W.1"）。
  再生中は時刻のカーソルをそこへ動かす（instrument_playback.py）
- 密度: 表示の組み合わせが「＋密度」のときだけ作る（イベント `density`）。縦の範囲はいまの縦軸（REF・dB/div、
  |Γ|・SWR は下端〜下端 + 10 目盛）、横は掃引の列。N 回で重みが半分（表示の設定 density_half）。何秒分の密度か
  （N × 掃引の間隔）も送る（P §4）
- 値は測った単位の dB で持ち、画面へ出すときに表示の値にする: dB の形式は表示の単位（dBμV・外部補正: 設計O）へ
  ずらし、|Γ|・SWR の形式はその値（行にするトレースの表示の形式: 設計R §3。形式を変えたら持っている行も新しい形式で
  出し直す）
- ゼロスパン（横軸が時間: 設計N §1）の掃引は行にしない（周波数の横軸に載らない。表示の組み合わせは画面が理由付きで
  スペクトルのみにする: ui_qt/display_view.py）
- TG の基準との比の行は、どの基準の比か（id と当てた基準の値）を持つ。基準の値を直したら、その基準の行（ライブの行・
  密度の元の行・過去のトレース）を新しい値へ動かし、色の範囲も同じだけずらす（設計E §6.7: 単位の変更と同じく絵は
  変えない）。記録（再生・作業ファイル）から読む行は記録したときの値のまま（_wf_ref_value_moved）
"""
from __future__ import annotations

import collections
import dataclasses
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
from .trace import formats
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
        self._wf_last: Optional[wf.Row] = None  # いちばん新しく入れた行（ライブ・再生とも。単位と色の範囲を決める）
        self._wf_format_key: Optional[tuple] = None  # 行にするトレースの番号と表示の形式（変わったら出し直す）
        # 基準の id → いまの名前（境目の条件は id で比べ、文にはいまの名前を出す。名前を変えても境目にしない）
        self._wf_ref_names: dict[str, str] = {}
        self._wf_range_sent: Optional[dict] = None

    def _shutdown_waterfall(self) -> None:
        src = self._wf_rec_src
        self._wf_rec_src = None
        if src is not None:
            try:
                self._writer.submit(src.close)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ 表示の値（単位・形式）

    def _wf_format_setting(self) -> str:
        """行に当てる表示の形式の設定: 縦軸の形式（view.s11_format。|Γ|・SWR は S11 の比の行だけに当たる: formats.effective）。
        形式を変えたら持っている行を新しい形式で出し直す（_wf_follow_format）。"""
        return self._axis_format()

    def _wf_shown(self, row: wf.Row) -> tuple[float, Optional[str], str]:
        """行を出すときの (足す dB, 単位, 形式)。|Γ|・SWR はずらさず、単位はその名前（instrument_display._shown と同じ）。"""
        extra = row.extra or {}
        q = extra.get("quantity")
        fmt = formats.effective(self._wf_format_setting(), (row.cond or {}).get("mode"),
                                Quantity(q) if q else None)
        shift, unit = level_shift(row.unit, self._view_opts)
        if fmt != formats.DB:
            return 0.0, formats.unit_of(fmt, unit), fmt
        return shift, unit, fmt

    def _wf_values(self, row: wf.Row) -> np.ndarray:
        """行の表示の値（float32）。"""
        shift, _unit, fmt = self._wf_shown(row)
        if fmt != formats.DB:
            return formats.from_db(row.values, fmt).astype(np.float32)
        return row.values + np.float32(shift) if shift else row.values

    def _wf_unit_now(self) -> Optional[str]:
        row = self._wf_last or self._wf_history.latest()
        return self._wf_shown(row)[1] if row is not None else None

    def _wf_range_is_power(self) -> bool:
        """手で決めた色の範囲が電力の値か（単位・外部補正を変えたときにずらすか: instrument_view）。"""
        row = self._wf_last or self._wf_history.latest()
        if row is None:
            return True
        return (row.extra or {}).get("quantity", "power") == Quantity.POWER.value and self._wf_shown(row)[2] == formats.DB

    def _linear_axis_range(self, unit: str) -> tuple[float, float]:
        """|Γ|・SWR の縦軸の範囲（下端〜下端 + 10 目盛。1目盛は振幅の状態: 設計R §3）。"""
        per = formats.per_div_of(unit, self._amplitude)
        return float(formats.LINEAR_AXES[unit]["bottom"]), formats.linear_top(unit, per, DIVISIONS)

    # ------------------------------------------------------------------ 状態

    def _wf_range(self) -> dict:
        o = self._view_opts
        unit = self._wf_unit_now()
        if formats.is_linear_unit(unit):
            # |Γ|・SWR: 縦軸と同じ範囲（自動も手で決めた範囲も使わない。表示の設定の値は dB 系の行のために残す）
            lo, hi = self._linear_axis_range(unit)
            return {"lo": lo, "hi": hi, "auto": False, "axis": True}
        if o["wf_color_auto"] and self._wf_auto.range is not None:
            lo, hi = self._wf_auto.range
        else:
            lo, hi = float(o["wf_color_lo"]), float(o["wf_color_hi"])
        return {"lo": lo, "hi": hi, "auto": bool(o["wf_color_auto"]), "axis": False}

    def _waterfall_state(self) -> dict:
        h = self._wf_history
        latest = h.latest()
        return {"range": self._wf_range(), "epoch": self._wf_epoch, "seq": self._wf_seq, "rows": len(h),
                "limit": h.limit, "t_first": h.first_time(), "t_last": latest.t_end if latest else None,
                "unit": self._wf_unit_now(), "density": self._density_info()}

    def _density_info(self) -> dict:
        interval = wf.sweep_interval_s(list(self._wf_times))
        half = int(self._view_opts["density_half"])
        return {"half": half, "interval_s": interval, "seconds": None if interval is None else half * interval}

    # ------------------------------------------------------------------ 1回の掃引

    def _wf_row_from_trace(self, frame, pos: Optional[tuple[int, int]] = None, live: bool = True) -> Optional[wf.Row]:
        """選んだトレースの、いま表示している範囲の点から行を作る（条件が変わったら並びの番号を変える）。
        ゼロスパン（横軸が時間）の掃引は行にしない（設計N §1）。

        並び（seg）と境目の条件（cond）は、範囲・RBW・モードに加えて、量（電力 ⇔ 比）・単位・当てた基準が変わっても変える
        （基準を取る・外す・替えるのは条件の変更: 設計F §5.3。P §3 の境目の線と条件）。基準の値を直したときは変えない
        （行を新しい値へ動かす: 設計E §6.7）。"""
        n = int(self._view_opts["wf_trace"])
        t = self._traces[n]
        if t.values is None or t.axis is None or t.quantity is None or t.axis.step_hz == 0:
            return None
        freqs = t.axis.freqs()
        db = db_values(t.values, t.quantity)
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
        rec = self._normalization_record() if live and t.quantity == Quantity.RATIO else None
        # 並びを分けるのは基準が替わったとき（id）。基準の値を直したときは同じ並びのまま、持っている行を新しい値へ動かす
        # （_wf_ref_value_moved: 設計E §6.7）
        ref = None if rec is None else rec.get("id")
        if rec is not None:
            cond["ref_id"] = str(rec.get("id") or "")
            self._wf_ref_names[cond["ref_id"]] = str(rec.get("name") or "")
        key = (f0, int(t.axis.step_hz), int(vals.size), cond["rbw_hz"], cond["mode"], t.quantity.value, unit, n, ref)
        if key != self._wf_seg_key:
            self._wf_seg_key = key
            self._wf_seg += 1
        row = wf.row_from_values(frame.time, f0, int(t.axis.step_hz), vals, unit=unit, seg=self._wf_seg, cond=cond,
                                 pos=pos)
        row.extra = {"quantity": t.quantity.value, "profile": t.profile, "trace": n}
        ref_id = self._tg_ref_of(t)
        if ref_id is not None:
            # TG の基準との比の行: どの基準と、どの基準の値で当てたか（基準の値を直したときに動かす: 設計E §6.7）
            row.extra.update({"ref_id": ref_id, "ref_value_db": self._trace_refs[n][1]})
        return row

    def _waterfall_on_frame(self, frame, live: bool = True) -> None:
        """トレースを更新した後に呼ぶ。ライブの掃引は行を足して画面へ送る。再生の掃引は色の範囲と密度だけ。

        行の単位が前の行と変わった（基準を取った・外した: 電力 ⇔ 比）ら、色の範囲を新しい単位で決め直し（自動は追い直す）、
        画面は行を取り直す（単位を変えたときと同じ: P §9.8）。前の行は前の単位のまま、境目の線と条件を出す（P §3）。"""
        prev = self._wf_history.latest() if live else None
        row = self._wf_row_from_trace(frame, live=live)
        if row is None:
            return
        self._wf_times.append(float(frame.time))
        self._wf_last = row
        self._wf_follow_format()
        _shift, unit, fmt = self._wf_shown(row)
        unit_changed = prev is not None and self._wf_shown(prev)[1] != unit
        if unit_changed:
            self._wf_auto.reset()
        changed = False
        if fmt == formats.DB and self._view_opts["wf_color_auto"]:
            changed = self._wf_auto.feed(self._wf_values(row), time.monotonic())
        if live:
            self._wf_history.add(row)
            self._wf_seq += 1
            if unit_changed:
                self._wf_reset_rows()
            else:
                self._wf_range_sent = self._wf_range()
                self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "unit": unit,
                                         "range": self._wf_range_sent, "rows": [self._wf_row_out(row)],
                                         "append": True})
        elif changed:
            self._wf_emit_range()
        self._density_src.append(row)
        if self._view_opts["layout"] == "density":
            self._density_feed(row)

    def _wf_row_out(self, row: wf.Row) -> dict:
        """画面へ送る行。単位は行ごと（読み値はその行の単位: 基準の前後で dBm と dB が並ぶ）。境目の条件にも入れる。"""
        shift, unit, _fmt = self._wf_shown(row)
        head = row.head(shift)
        head["cond"] = {**head["cond"], "unit": unit}
        rid = head["cond"].get("ref_id")
        if rid is not None:
            # 境目の文に出す基準の名前は、いま覚えている名前（id が同じ行は同じ名前になる: 名前の変更で境目を作らない）
            cur = self._ref if getattr(self, "_ref", None) is not None and self._ref.ref_id == rid else None
            name = cur.name if cur is not None else self._wf_ref_names.get(rid, head["cond"].get("ref", ""))
            head["cond"]["ref"] = str(name or "")
        head["unit"] = unit
        return {**head, "values": self._wf_values(row).astype(np.float32)}

    def _wf_emit_range(self) -> None:
        """色の範囲だけを送る（行は足さない）。"""
        self._wf_range_sent = self._wf_range()
        self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "unit": self._wf_unit_now(),
                                 "range": self._wf_range_sent, "rows": [], "append": True})

    def _wf_reset_rows(self) -> None:
        """行の値の単位・形式が変わった: 画面は行を取り直す。"""
        self._wf_epoch += 1
        self._wf_range_sent = self._wf_range()
        self._emit("waterfall", {"epoch": self._wf_epoch, "seq": self._wf_seq, "reset": True,
                                 "range": self._wf_range_sent, "rows": []})

    def _wf_follow_format(self) -> bool:
        """行にするトレースの表示の形式（dB・|Γ|・SWR: 設計R §3）が変わったら、持っている行を新しい形式で出し直す。"""
        key = (int(self._view_opts["wf_trace"]), self._wf_format_setting())
        if self._wf_format_key is None:
            self._wf_format_key = key
            return False
        if key == self._wf_format_key:
            return False
        self._wf_format_key = key
        self._wf_auto.reset()
        self._wf_reset_rows()
        if self._view_opts.get("layout") == "density":
            self._density.key = None
            self._density_rebuild()
        return True

    # ------------------------------------------------------------------ 基準の値を直した（設計E §6.7）

    def _wf_ref_value_moved(self, ref_id: str, new: float) -> None:
        """基準 ref_id の値が new になった。その基準の比の行（画面に持つライブの行・密度の元の行・いちばん新しい行）を
        新しい値へ動かし（行ごとに当てた値から）、密度を作り直し、画面に行を取り直させる。過去のトレース（行をクリック
        した掃引）も動かす。手で決めた色の範囲と自動の範囲は、いまの行がその基準の比の dB でライブを出している間だけ
        同じだけずらす（単位を変えたときの _wf_range_is_power と同じ決まり。再生中は記録の絵なので動かさない）。
        記録（再生・作業ファイル）から読む行は記録したときの値で作るので、ここでは動かない。"""
        latest = self._wf_last or self._wf_history.latest()
        ext = (latest.extra or {}) if latest is not None else {}
        if (self._playback is None and ext.get("ref_id") == ref_id and self._wf_shown(latest)[2] == formats.DB):
            d = float(new) - float(ext["ref_value_db"])
            for k in ("wf_color_lo", "wf_color_hi"):
                self._view_opts[k] = round(float(self._view_opts[k]) + d, 2)
            self._wf_auto.shift(d)
        seen: set[int] = set()
        moved = False
        for row in [*self._wf_history.rows(), *self._density_src, *([self._wf_last] if self._wf_last else [])]:
            ext = row.extra or {}
            if id(row) in seen or ext.get("ref_id") != ref_id:
                continue
            seen.add(id(row))
            d = float(new) - float(ext["ref_value_db"])
            row.values = (row.values.astype(np.float64) + d).astype(np.float32)
            ext["ref_value_db"] = float(new)
            moved = True
        past = self._files.get(PAST_FILE_ID)
        if past is not None and past.get("ref_id") == ref_id:
            d = float(new) - float(past["ref_value_db"])
            tid = f"{PAST_FILE_ID}.1"
            td = past["traces"][tid]
            past["traces"][tid] = dataclasses.replace(td, values=td.values * 10.0 ** (d / 20.0))
            past["ref_value_db"] = float(new)
            self._emit("trace", self._file_trace_payload(tid))
        if moved:
            self._wf_reset_rows()
            if self._view_opts.get("layout") == "density":
                self._density.key = None
                self._density_rebuild()
        else:
            self._wf_emit_range()

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
            self._wf_format_key = (int(new["wf_trace"]), self._wf_format_setting())
            self._wf_reset_rows()
        elif any(old.get(k) != new.get(k) for k in ("wf_color_auto", "wf_color_lo", "wf_color_hi")):
            self._wf_emit_range()
        if new.get("layout") == "density" and (old.get("layout") != "density" or unit_changed
                                               or old.get("density_half") != new.get("density_half")):
            self._density.key = None
            self._density_rebuild()

    def _waterfall_on_changed(self) -> None:
        """状態が変わるたびに呼ぶ。行にするトレースの表示の形式が変わったら行を出し直し、色の範囲（|Γ|・SWR は縦軸の
        1目盛で変わる）が変わったら送り直す。密度は縦軸（REF・dB/div・|Γ|・SWR の1目盛）が変わったら作り直す。"""
        if not self._wf_follow_format() and self._wf_range_sent is not None and self._wf_range() != self._wf_range_sent:
            self._wf_emit_range()
        if self._view_opts.get("layout") != "density":
            return
        amp = self._density_level_range()
        if amp != self._density_amp:
            self._density_rebuild()

    # ------------------------------------------------------------------ 密度（P §4・§9.3）

    def _density_level_range(self) -> tuple[float, float, float]:
        """密度の縦の範囲と升目の高さ（下, 上, 升目）。いまの縦軸と同じ範囲（|Γ|・SWR は下端〜下端 + 10 目盛）。"""
        unit = self._wf_unit_now()
        if formats.is_linear_unit(unit):
            lo, hi = self._linear_axis_range(unit)
            return lo, hi, formats.per_div_of(unit, self._amplitude) / wf.DENSITY_LINEAR_BINS_PER_DIV
        amp = self._amplitude
        ref = 0.0 if amp.get("ref_level") is None else float(amp["ref_level"])
        div = float(amp.get("db_per_div") or 10.0)
        return ref - DIVISIONS * div, ref, wf.DENSITY_BIN_DB

    def _density_key(self, row: wf.Row) -> tuple:
        lo, hi, bin_size = self._density_level_range()
        shift, unit, fmt = self._wf_shown(row)
        return (row.seg, row.columns, row.start_hz, row.step_hz, lo, hi, bin_size,
                int(self._view_opts["density_half"]), shift, fmt)

    def _density_feed(self, row: wf.Row, emit: bool = True) -> None:
        lo, hi, bin_size = self._density_level_range()
        self._density_amp = (lo, hi, bin_size)
        key = self._density_key(row)
        if self._density.setup(key, row.columns, lo, hi, int(self._view_opts["density_half"]), bin_size):
            for r in list(self._density_src)[:-1]:
                if r.seg == row.seg and r.columns == row.columns:
                    self._density.add(self._wf_values(r))
        self._density.add(self._wf_values(row))
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
        lo, hi, bin_size = self._density_level_range()
        self._density_amp = (lo, hi, bin_size)
        half = int(self._view_opts["density_half"])
        self._density.setup(self._density_key(row), row.columns, lo, hi, half, bin_size)
        for r in self._density_src:
            if r.seg == row.seg and r.columns == row.columns:
                self._density.add(self._wf_values(r))
        self._emit_density(row)

    def _density_payload(self, row: wf.Row) -> dict:
        d = self._density
        stop = row.start_hz + row.step_hz * max(0, row.columns - 1)
        return {"image": d.image(), "bins": d.bins, "cols": d.n_cols, "start_hz": int(row.start_hz),
                "stop_hz": int(stop), "level_lo": d.level_lo, "level_hi": d.level_hi, "bin_db": d.bin,
                "unit": self._wf_shown(row)[1], "zero_span": row.zero_span, "sweeps": d.added, **self._density_info()}

    def _emit_density(self, row: wf.Row) -> None:
        self._emit("density", self._density_payload(row))

    def _cmd_density_get(self) -> dict:
        """いまの密度（途中から繋いだ画面・密度に切り替えた画面が描くため）。まだ無ければ image は None。"""
        rows = list(self._density_src)
        if self._view_opts["layout"] != "density" or not rows or self._density.key is None:
            return {"image": None, **self._density_info()}
        return self._density_payload(rows[-1])

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
        # ファイルから読むのは持っている行より前だけ。選んだ範囲の終わり（t_hi）は越えない
        hi = t_hi if first is None else (first - 1e-6 if t_hi is None else min(float(t_hi), first - 1e-6))

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
                "unit": self._wf_shown(rows[-1])[1] if rows else self._wf_unit_now(),
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
                             "unit": row.unit,
                             # 行と同じ表示の形式で出す（|Γ|・SWR: 設計R §3。参照トレースは保存したときの形式で出る）
                             "format": self._wf_shown(row)[2], "measurement": (row.cond or {}).get("mode")},
                       flags={}, profile=dict((row.extra or {}).get("profile") or {}),
                       origin={"file": "", "created_at": when.isoformat(timespec="seconds")})
        self._files[PAST_FILE_ID] = {
            "file_id": PAST_FILE_ID, "kind": "past", "path": "", "name": when.strftime("%H:%M:%S"),
            "created_at": when.isoformat(timespec="milliseconds"), "device_model": "", "device_serial": "",
            "note": "", "traces": {f"{PAST_FILE_ID}.1": td}, "markers": [], "t": row.t, "sweeps": row.sweeps,
            # ライブの TG の基準との比の行なら、その基準と当てた値（基準の値を直したら動かす: 設計E §6.7）
            "ref_id": (row.extra or {}).get("ref_id"), "ref_value_db": (row.extra or {}).get("ref_value_db"),
        }
        self._emit("trace", self._file_trace_payload(f"{PAST_FILE_ID}.1"))
        self._diff_refresh()
        self._changed()
