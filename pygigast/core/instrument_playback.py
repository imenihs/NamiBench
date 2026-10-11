"""計測器の本体のうち、記録の再生（設計P §5・§9.4・設計H §3.1・§4）。Instrument に混ぜる。

- 記録のファイル（掃引が2つ以上ある保存ファイル）を開くと再生に入る（1回分のファイルは今までどおり参照トレース:
  設計H §4）。ライブの掃引は止め（止めたことを知らせる）、ウォーターフォールには記録の全体を出す
- 時刻のカーソル（掃引の番号）を動かすと、その掃引をトレースの処理に通してスペクトルに出す（マーカー・Max Hold・
  書き出し・切り出しはライブと同じに使える: P §5）。再生・一時停止・速さ（等倍・数倍）
- 掃引はそのとき使った条件・校正で換算する（record_frames.py）。基準との比で記録した区間は、記録に書いた基準の
  ファイルを読んで当てる（読めなければ電力のまま、理由を状態に出す）。基準の値は記録に書いた値（記録したときの
  値。書いていない前の版は 0 dB）で当て、いまの基準のファイルの値は使わない（記録は測ったときの設定ごと再現する。
  基準の値を後で直しても過去の記録は変わらない: 設計E §6.7）。再生のトレースはどの基準の比としても覚えない
  （基準の値を直しても動かさない）
- 周波数方向の拡大（上段の欄・グラフのドラッグ・ホイール）は、再生中は表示の範囲を変えるだけ（測り直さない。
  保存時の Step より細かくはならない: H §3.1）。範囲は記録の区間の中に収め、収めたら知らせる（M-02）
- 読むのは書き込みスレッド（設計A §4.2）。次の掃引を出す時刻は記録の時刻の差 ÷ 速さ（間が長いところは
  PLAYBACK_MAX_GAP_S で詰める）
- ウォーターフォールの色の範囲の「自動」は、出している行（記録の全体、時間方向に選んだときはその範囲）の値で決め、
  カーソルを動かしても変えない（P §9.2）。行の数は画面の行数によらず PLAYBACK_AUTO_ROWS 行で読む（Qt と Web の画面の
  高さが違っても同じ範囲になる）
- 密度の元は記録の中のカーソルまでの直近の掃引（重みが 1/256 になるまで = 半分になる回数の 8 倍）を記録から読み、
  何秒分かは記録の時刻から出す（P §9.3）。カーソルが1つ進んだだけなら、その1掃引を足す
"""
from __future__ import annotations

import collections
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from . import waterfall as wf
from .instrument_common import CommandError
from .normalize import load_reference
from .normalize.apply import apply_reference
from .record_frames import SegmentLevels
from .trace import ConditionChange, formats
from .types import Frame, Quantity, ValueKind

# 再生の速さ（等倍・数倍: P §5）。値の並びは設計に無い
PLAYBACK_SPEEDS = (1, 2, 5, 10, 30)
# 記録の中の長い間（記録を止めていた間など）は、再生ではこの秒まで詰める
PLAYBACK_MAX_GAP_S = 2.0
# 表示の範囲は、少なくとも保存時の Step の 10 点分（それより狭くは拡大しない: H §3.1 の「拡大の限界」）
PLAYBACK_MIN_POINTS = 10
# 色の範囲の自動を決めるときに読む行の数（概観の段の最大値の行。値は設計に無い）
PLAYBACK_AUTO_ROWS = 512


@dataclass
class _PbSegment:
    pos: int
    n: int
    first: int  # 記録全体の通し番号でのこの区間の最初の掃引
    t_first: float
    t_last: float
    start_hz: int
    stop_hz: int
    step_hz: int
    points: int
    rbw_hz: Optional[int]
    mode: str
    quantity: str
    times: np.ndarray
    levels: SegmentLevels
    norm_ref: object = None
    norm_meta: Optional[dict] = None
    norm_offset: Optional[np.ndarray] = None

    def head(self) -> dict:
        return {"index": self.pos, "first": self.first, "n": self.n, "t_first": self.t_first, "t_last": self.t_last,
                "start_hz": self.start_hz, "stop_hz": self.stop_hz, "step_hz": self.step_hz, "points": self.points,
                "rbw_hz": self.rbw_hz, "mode": self.mode, "quantity": self.quantity}


@dataclass
class _Session:
    path: str
    name: str
    created_at: str
    device_model: str
    device_serial: str
    src: object  # RecordSource（書き込みスレッドだけが触る）
    segs: list[_PbSegment]
    total: int
    cursor: int = 0
    playing: bool = False
    speed: int = 1
    view: Optional[tuple[int, int]] = None
    gen: int = 0
    busy: bool = False
    want: Optional[int] = None
    problems: list = field(default_factory=list)
    shown: Optional[int] = None  # いまスペクトルに出している掃引の通し番号
    shown_raw: object = None  # その掃引の生の値（再生中の保存に入れる: 設計H §4）
    auto_window: object = None  # 色の範囲の自動を決めた時間の範囲 (t_lo, t_hi)。決めていなければ None
    dens: Optional[tuple] = None  # 密度の元に入れた (区間, 表示の範囲, 掃引の数, 最後の通し番号)
    dens_req: int = 0  # 密度の元を読む求めの番号（後の求めが来たら前の結果は使わない）


def _norm_offset(levels: SegmentLevels, ref, serial: str, connected_at: float, now: float) -> Optional[np.ndarray]:
    """基準との比で記録した区間に足す dB（点ごと。基準の無い点は NaN）。基準を当てられなければ None。

    比の dB = 電力の dBm − 30 − 基準の dB（normalize.apply の式）なので、1W の掃引に当てた結果から点ごとの足す量を
    一度だけ求める（最大値でまとめた行にもそのまま足せる）。"""
    a = levels.adopted
    n = a.axis.points
    frame = Frame(axis=a.axis, values=np.ones(n), quantity=Quantity.POWER, value_kind=ValueKind.REAL, time=now,
                  duration_s=math.nan, sweep_no=0, display_start_hz=a.start_hz, display_stop_hz=a.stop_hz, adopted=a)
    res = apply_reference(frame, ref, serial=serial, connected_at=connected_at, now=now)
    if not res.applied:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        return (20.0 * np.log10(np.asarray(res.frame.values, dtype=np.float64)) - 30.0).astype(np.float32)


def _recorded_value(meta: dict) -> float:
    """記録に書いた基準の値（dB）。書いていない前の版・読めない値は 0 dB。"""
    from .normalize import check_value_db

    try:
        return check_value_db(meta.get("value_db", 0.0))
    except ValueError:
        return 0.0


class PlaybackMixin:
    """記録の再生（Instrument に混ぜる）。"""

    def _init_playback(self) -> None:
        self._playback: Optional[_Session] = None
        # 再生した記録の通し番号。記録を開くたびに増える（再生中に別の記録を開いても変わる）。画面は記録ごとの見方
        # （ウォーターフォールで選んだ時間の範囲など）をこれが変わったら捨てる（P §5: 開いた記録は全体を出す）
        self._pb_session = 0
        self._pb_timer: Optional[threading.Timer] = None
        self._rec_levels: dict = {}  # 記録中の作業ファイルの区間の換算（書き込みスレッドだけが触る）

    def _shutdown_playback(self) -> None:
        self._pb_cancel_timer()
        s = self._playback
        self._playback = None
        if s is not None:
            try:
                self._writer.submit(s.src.close)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ 開く（書き込みスレッドで作る）

    def _load_ref_by_id(self, serial: str, ref_id: Optional[str]):
        """記録に書いた基準（id）を、その機器の基準のフォルダから読む。読めなければ None。書き込みスレッドで呼ぶ。"""
        if not serial or not ref_id:
            return None
        try:
            p = self._ref_path(serial, ref_id)
        except CommandError:
            return None
        try:
            return load_reference(p)
        except Exception:  # noqa: BLE001 — 読めない基準は「当てられない」として扱う
            return None

    def _segment_levels(self, src, pos: int):
        """区間の換算と、比の区間なら点ごとの足す量（書き込みスレッドで呼ぶ。記録中の作業ファイル用に覚えておく）。"""
        key = (str(src.path), pos)
        hit = self._rec_levels.get(key)
        if hit is not None:
            return hit
        s = src.segments[pos]
        levels = SegmentLevels(s, src.header)
        offset = None
        meta = s.normalization if isinstance(s.normalization, dict) else None
        ref = None
        if s.quantity == "ratio" and meta:
            ref = self._load_ref_by_id(src.header.device_serial, meta.get("id"))
            if ref is not None:
                # 記録したときの基準の値で当てる（いまのファイルの値ではない。書いていない前の版は 0 dB: 設計E §6.7）
                ref.value_db = _recorded_value(meta)
                offset = _norm_offset(levels, ref, src.header.device_serial, float(meta.get("connected_at") or 0.0),
                                      float(s.t_first) if s.n_sweeps else 0.0)
        hit = (levels, offset, ref, meta)
        self._rec_levels[key] = hit
        return hit

    def _playback_open_job(self, path: str) -> _Session:
        from .storage.record_source import RecordSource

        src = RecordSource(path)
        try:
            segs: list[_PbSegment] = []
            first = 0
            problems: list[dict] = []
            for pos, s in enumerate(src.segments):
                if not s.n_sweeps:
                    continue
                if int(s.step_hz) == 0:
                    # ゼロスパンの区間（横軸が時間: 設計N §1）は周波数の横軸のウォーターフォールとスペクトルに載らない。
                    # 時間軸の再生は決めていないので、開かずに理由を返す
                    raise CommandError("playback_zero_span", None, {"file": Path(path).name})
                if str(s.quantity) == Quantity.NF.value:
                    # NF の区間（設計M）は HOT と COLD の組から NF を出す。記録の1つの生の値からは出せないので開かない
                    raise CommandError("playback_nf", None, {"file": Path(path).name})
                levels, offset, ref, meta = self._segment_levels(src, pos)
                if not levels.can_convert:
                    problems.append({"code": "playback_segment_unreadable", "segment": pos})
                if s.quantity == "ratio" and offset is None:
                    problems.append({"code": "playback_reference_missing", "segment": pos,
                                     "name": (meta or {}).get("name")})
                cond = levels.adopted
                stop = int(cond.stop_hz)
                segs.append(_PbSegment(
                    pos=pos, n=int(s.n_sweeps), first=first, t_first=float(s.t_first), t_last=float(s.t_last),
                    start_hz=int(s.start_hz), stop_hz=stop, step_hz=int(s.step_hz), points=int(s.points),
                    rbw_hz=int(cond.rbw_hz), mode=cond.mode.value, quantity=s.quantity, times=src.times(pos),
                    levels=levels, norm_ref=ref if offset is not None else None, norm_meta=meta,
                    norm_offset=offset))
                first += int(s.n_sweeps)
            if not segs:
                raise CommandError("playback_empty", None, {"file": Path(path).name})
            h = src.header
            return _Session(path=str(path), name=Path(path).name, created_at=h.created_at, device_model=h.device_model,
                            device_serial=h.device_serial, src=src, segs=segs, total=first, problems=problems)
        except BaseException:
            src.close()
            raise

    def _playback_install(self, session: _Session) -> dict:
        """制御スレッドで再生に入る。ライブの掃引を止め、トレースの蓄積を捨てる（条件が変わるのと同じ: 設計F §5.3）。"""
        old = self._playback
        if old is not None:
            self._pb_cancel_timer()
            self._writer.submit(old.src.close)
        was_running = self._run in ("continuous", "single", "stopping")
        if was_running:
            self._run = "stopped"
            self._last_stop_reason = "playback"
            self._push_job()
        self._playback = session
        self._pb_session += 1
        self._on_condition(ConditionChange.MODE, cause="playback")
        self._density_src.clear()
        self._density.key = None
        self._wf_auto.reset()
        self._wf_last = None  # 行の単位は次に出す掃引で決まる（記録の行・ライブの行）
        self._wf_reset_rows()
        layout_changed = False
        if self._view_opts.get("layout") != "waterfall":
            # 記録を開くとウォーターフォールに全体が出る（P §5）。表示の組み合わせを変えたことは知らせる（黙って変えない）
            self._view_opts = {**self._view_opts, "layout": "waterfall"}
            layout_changed = True
        self._playback_show(0)
        self._changed()
        # 開くの終わりの知らせ（task_done）の結果に入れて知らせる（止めたライブ・出したウォーターフォール・
        # 見つからない基準: 黙って変えない J §4）
        return {"name": session.name, "sweeps": session.total, "live_stopped": was_running,
                "layout_changed": layout_changed, "problems": list(session.problems)}

    # ------------------------------------------------------------------ 状態

    def _playback_state(self) -> dict:
        s = self._playback
        if s is None:
            return {"active": False, "speeds": list(PLAYBACK_SPEEDS)}
        seg, i = self._pb_locate(s.cursor)
        view = self._pb_view(s, seg)
        return {
            "active": True, "session": self._pb_session, "name": s.name, "path": s.path, "created_at": s.created_at,
            "device_model": s.device_model, "device_serial": s.device_serial, "total": s.total,
            "index": s.cursor, "t": float(seg.times[i]), "t_first": s.segs[0].t_first, "t_last": s.segs[-1].t_last,
            "playing": s.playing, "speed": s.speed, "speeds": list(PLAYBACK_SPEEDS),
            "segments": [g.head() for g in s.segs], "segment": seg.pos,
            "view": {"start_hz": view[0], "stop_hz": view[1]}, "step_hz": seg.step_hz, "points": seg.points,
            "problems": list(s.problems),
        }

    def _pb_locate(self, g: int) -> tuple[_PbSegment, int]:
        s = self._playback
        g = min(max(0, int(g)), s.total - 1)
        for seg in s.segs:
            if g < seg.first + seg.n:
                return seg, g - seg.first
        last = s.segs[-1]
        return last, last.n - 1

    @staticmethod
    def _pb_view(s: _Session, seg: _PbSegment) -> tuple[int, int]:
        """いまの区間で使う表示の範囲（区間の範囲の中に収める）。"""
        if s.view is None or seg.step_hz == 0:
            return seg.start_hz, seg.stop_hz
        lo, hi = s.view
        lo, hi = max(lo, seg.start_hz), min(hi, seg.stop_hz)
        if hi - lo < seg.step_hz * PLAYBACK_MIN_POINTS:
            return seg.start_hz, seg.stop_hz
        return lo, hi

    # ------------------------------------------------------------------ 掃引を出す

    def _playback_show(self, g: int) -> None:
        """通し番号 g の掃引を読み（書き込みスレッド）、トレースの処理に通す（制御スレッド）。読んでいる間に別の番号を
        求められたら、終わってから最後に求められた番号を読む。"""
        s = self._playback
        if s is None:
            return
        s.cursor = min(max(0, int(g)), s.total - 1)
        if s.busy:
            s.want = s.cursor
            return
        s.busy = True
        s.want = None
        seg, i = self._pb_locate(s.cursor)
        gen = s.gen
        view = self._pb_view(s, seg)
        target = s.cursor

        def job():
            raw = s.src.raw_sweep(seg.pos, i)
            return seg.levels.frame(raw, target, view)

        wfut = self._writer.submit(job)

        def done(f) -> None:
            def finish() -> None:
                if self._playback is not s:
                    return
                s.busy = False
                exc = f.exception()
                if exc is not None:
                    s.playing = False
                    self._notice("playback_read_failed", {"message": str(exc)})
                    self._changed()
                    return
                if gen == s.gen:
                    self._pb_process(s, seg, f.result(), target)
                if s.want is not None and s.want != target:
                    self._playback_show(s.want)
                    return
                self._changed()

            self._post_call(finish)

        wfut.add_done_callback(done)

    def _pb_process(self, s: _Session, seg: _PbSegment, frame: Frame, g: int) -> None:
        s.shown = g
        s.shown_raw = frame.raw

        def normalize(fr: Frame) -> Frame:
            return self._pb_normalize(s, seg, fr)

        self._process_frame(frame, normalize=normalize, live=False)
        self._pb_density(s, seg, g)

    def _pb_normalize(self, s: _Session, seg: _PbSegment, frame: Frame) -> Frame:
        """記録した区間が基準との比なら、記録に書いた基準を当てる。当てられなければ電力のまま理由を出す。"""
        # 再生のトレースは記録したときの値のまま（どの基準の比としても覚えず、基準の値を直しても動かさない: 設計E §6.7）
        self._note_trace_refs(None)
        if seg.quantity != "ratio":
            self._norm_state = {"used": False}
            return frame
        meta = seg.norm_meta or {}
        if seg.norm_ref is None:
            self._norm_state = {"used": True, "applied": False, "reason": "playback_reference_missing",
                                "detail": {"name": meta.get("name")}, "accuracy": None, "out_of_range_hz": [],
                                "power_cycled": False, "oldest_taken_at": None, "age_s": None}
            return frame
        res = apply_reference(frame, seg.norm_ref, serial=s.device_serial,
                              connected_at=float(meta.get("connected_at") or 0.0), now=frame.time)
        self._norm_state = {
            "used": True, "applied": res.applied, "reason": res.reason, "detail": res.detail,
            "accuracy": res.accuracy, "out_of_range_hz": [[int(a), int(b)] for a, b in res.out_of_range_hz],
            "power_cycled": res.power_cycled, "oldest_taken_at": res.oldest_taken_at, "age_s": res.age_s,
        }
        return res.frame

    # ------------------------------------------------------------------ 再生・一時停止・速さ・カーソル

    def _need_playback(self) -> _Session:
        if self._playback is None:
            raise self._error("not_playing")
        return self._playback

    def _cmd_playback_play(self, speed: Optional[int] = None) -> dict:
        s = self._need_playback()
        if speed is not None:
            self._check_speed(speed)
            s.speed = int(speed)
        if s.cursor >= s.total - 1:
            s.cursor = 0  # 終わりまで出したあとの再生は始めから
            self._playback_show(0)
        s.playing = True
        self._pb_schedule(s)
        self._changed()
        return {"playing": True, "speed": s.speed}

    def _cmd_playback_pause(self) -> dict:
        s = self._need_playback()
        s.playing = False
        self._pb_cancel_timer()
        self._changed()
        return {"playing": False}

    def _check_speed(self, speed) -> None:
        if isinstance(speed, bool) or not isinstance(speed, int) or speed not in PLAYBACK_SPEEDS:
            raise self._error("bad_value", "speed", {"choices": list(PLAYBACK_SPEEDS)})

    def _cmd_playback_speed(self, speed: int) -> dict:
        s = self._need_playback()
        self._check_speed(speed)
        s.speed = int(speed)
        if s.playing:
            self._pb_schedule(s)
        self._changed()
        return {"speed": s.speed}

    def _cmd_playback_seek(self, index: Optional[int] = None, t: Optional[float] = None) -> dict:
        """カーソルを通し番号 index（または時刻 t に最も近い掃引）へ動かし、その掃引を出す。"""
        s = self._need_playback()
        if index is not None:
            if isinstance(index, bool) or not isinstance(index, int):
                raise self._error("not_integer", "index")
            g = min(max(0, index), s.total - 1)
        elif t is not None:
            if isinstance(t, bool) or not isinstance(t, (int, float)):
                raise self._error("bad_value", "t")
            g = self._pb_index_at(s, float(t))
        else:
            raise self._error("bad_value", "index")
        self._playback_show(g)
        if s.playing:
            self._pb_schedule(s)
        self._changed()
        return {"index": s.cursor}

    def _playback_seek_time(self, t: float, pos=None) -> dict:
        s = self._playback
        if isinstance(pos, (list, tuple)) and len(pos) == 2 and all(isinstance(v, int) for v in pos):
            for seg in s.segs:
                if seg.pos == pos[0] and 0 <= pos[1] < seg.n:
                    return self._cmd_playback_seek(index=seg.first + pos[1])
        return self._cmd_playback_seek(t=t)

    @staticmethod
    def _pb_index_at(s: _Session, t: float) -> int:
        best = (math.inf, 0)
        for seg in s.segs:
            i = int(np.searchsorted(seg.times, t))
            for j in (i - 1, i):
                if 0 <= j < seg.n:
                    d = abs(float(seg.times[j]) - t)
                    if d < best[0]:
                        best = (d, seg.first + j)
        return best[1]

    def _cmd_playback_close(self) -> dict:
        """再生を終えてライブに戻る。再生の掃引で作ったトレースの蓄積は捨てる（条件が変わる）。"""
        s = self._need_playback()
        self._pb_cancel_timer()
        self._playback = None
        self._writer.submit(s.src.close)
        self._on_condition(ConditionChange.MODE, cause="playback")
        self._norm_state = {"used": False}
        self._density_src.clear()
        self._density.key = None
        self._wf_auto.reset()
        self._wf_last = None  # 行の単位は次に出す掃引で決まる（記録の行・ライブの行）
        self._wf_reset_rows()
        self._emit_visible_traces()
        self._notice("playback_closed", {"name": s.name})
        self._changed()
        return {}

    # ---- 時計（記録の時刻の差 ÷ 速さで次の掃引へ）

    def _pb_cancel_timer(self) -> None:
        t = self._pb_timer
        self._pb_timer = None
        if t is not None:
            t.cancel()

    def _pb_schedule(self, s: _Session) -> None:
        self._pb_cancel_timer()
        if not s.playing or self._playback is not s:
            return
        if s.cursor >= s.total - 1:
            s.playing = False
            self._notice("playback_end", {"name": s.name})
            return
        a, i = self._pb_locate(s.cursor)
        b, j = self._pb_locate(s.cursor + 1)
        gap = float(b.times[j]) - float(a.times[i])
        delay = min(max(0.0, gap), PLAYBACK_MAX_GAP_S) / max(1, s.speed)
        gen = s.gen

        def fire() -> None:
            self._q.put(("call", lambda: self._pb_tick(s, gen)))

        t = threading.Timer(delay, fire)
        t.daemon = True
        self._pb_timer = t
        t.start()

    def _pb_tick(self, s: _Session, gen: int) -> None:
        if self._playback is not s or not s.playing or gen != s.gen:
            return
        self._playback_show(s.cursor + 1)
        self._pb_schedule(s)
        self._changed()

    # ------------------------------------------------------------------ 周波数方向の拡大（再生中の set_sweep）

    def _playback_set_view(self, start_hz=None, stop_hz=None, center_hz=None, span_hz=None) -> dict:
        """表示の範囲を変える（測り直さない）。記録の区間の範囲と、保存時の Step の 10 点分より狭くしない。"""
        s = self._playback
        seg, _i = self._pb_locate(s.cursor)
        if seg.step_hz == 0:
            raise self._error("zero_span_axis")
        lo, hi = self._pb_view(s, seg)
        span, center = hi - lo, lo + (hi - lo) // 2
        if start_hz is not None or stop_hz is not None:
            lo = lo if start_hz is None else int(start_hz)
            hi = hi if stop_hz is None else int(stop_hz)
        else:
            if span_hz is not None:
                span = int(span_hz)
            if center_hz is not None:
                center = int(center_hz)
            lo, hi = center - span // 2, center - span // 2 + span
        req = {"start_hz": lo, "stop_hz": hi}
        adj: list[dict] = []
        minspan = seg.step_hz * PLAYBACK_MIN_POINTS
        if hi - lo < minspan:
            c = lo + (hi - lo) // 2
            lo, hi = c - minspan // 2, c - minspan // 2 + minspan
            adj.append({"field": "span_hz", "requested": req["stop_hz"] - req["start_hz"], "adopted": minspan,
                        "reason": "record_resolution"})
        if lo < seg.start_hz:
            hi, lo = min(seg.stop_hz, hi + (seg.start_hz - lo)), seg.start_hz
        if hi > seg.stop_hz:
            lo, hi = max(seg.start_hz, lo - (hi - seg.stop_hz)), seg.stop_hz
        for k, v in (("start_hz", lo), ("stop_hz", hi)):
            if req[k] != v and not any(a["field"] == "span_hz" for a in adj):
                adj.append({"field": k, "requested": req[k], "adopted": v, "reason": "record_range"})
        s.view = (lo, hi)
        if s.shown is not None:
            self._playback_show(s.shown)  # 表示の範囲でトレース・マーカーを作り直す
        self._changed()
        return {"adopted": {"start_hz": lo, "stop_hz": hi, "center_hz": lo + (hi - lo) // 2, "span_hz": hi - lo},
                "adjustments": adj, "playback": True}

    # ------------------------------------------------------------------ ウォーターフォールの行（記録から）

    def _playback_rows(self, max_rows: int, t_lo: Optional[float], t_hi: Optional[float]) -> object:
        """再生中のウォーターフォールの行（記録から）。出す行の時間の範囲が変わったら（記録を開いた・時間方向に選んだ・
        単位や形式が変わって決め直す）、その範囲の行の全体で色の範囲の自動を決める（P §9.2）。"""
        s = self._playback
        window = (t_lo, t_hi)
        decide = s.auto_window != window or not self._wf_auto.fixed

        def job():
            rows = self._record_rows(s.src, max_rows, t_lo, t_hi)
            return rows, (self._record_rows(s.src, PLAYBACK_AUTO_ROWS, t_lo, t_hi) if decide else None)

        def then(res) -> dict:
            rows, auto_rows = res
            if auto_rows is not None and self._playback is s:
                self._pb_decide_auto(auto_rows)
                s.auto_window = window
            out = self._wf_rows_result(list(rows), "file")
            out.update({"t_first": s.segs[0].t_first, "t_last": s.segs[-1].t_last})
            return out

        return self._io(job, then)

    def _record_rows(self, src, max_rows: int, t_lo: Optional[float], t_hi: Optional[float]) -> list:
        """記録の [t_lo, t_hi] の行（古い順・多くても max_rows 行くらい。書き込みスレッドで呼ぶ）。"""
        rows: list[wf.Row] = []
        for pos, s in enumerate(src.segments):
            if not s.n_sweeps or s.step_hz == 0 or str(s.quantity) == Quantity.NF.value:
                # ゼロスパンの区間は周波数の横軸に載らない（設計N §1）。NF の区間は記録の1つの生の値から NF を出せない
                # （HOT と COLD の組: 設計M）。どちらも行にしない
                continue
            ts = src.times(pos)
            s0 = 0 if t_lo is None else int(np.searchsorted(ts, t_lo, side="left"))
            s1 = len(ts) if t_hi is None else int(np.searchsorted(ts, t_hi, side="right"))
            if s1 <= s0:
                continue
            rows += self._segment_rows(src, pos, s0, s1, max_rows)
        rows.sort(key=lambda r: r.t)
        return wf.select_rows(rows, max_rows)

    def _segment_rows(self, src, pos: int, s0: int, s1: int, max_rows: int,
                      view: Optional[tuple[int, int]] = None) -> list:
        """区間 pos の掃引 [s0, s1) の行（多くても max_rows 行くらい）。view を渡せば、その周波数の範囲の点だけの行
        （密度の元: 画面の表示の範囲と同じ横軸）。書き込みスレッドで呼ぶ。"""
        s = src.segments[pos]
        levels, offset, _ref, meta = self._segment_levels(src, pos)
        ar = src.rows(pos, s0, s1, max_rows)
        db = levels.db_rows(ar.values)
        if offset is not None:
            db = db + offset[None, :]
        f0 = int(s.start_hz)
        if view is not None:
            freqs = f0 + int(s.step_hz) * np.arange(db.shape[1], dtype=np.int64)
            mask = (freqs >= int(view[0])) & (freqs <= int(view[1]))
            if not mask.any():
                return []
            f0 = int(freqs[mask][0])
            db = db[:, mask]
        unit = "dB" if offset is not None else levels.unit
        a = levels.adopted
        cond = {"start_hz": int(a.start_hz), "stop_hz": int(a.stop_hz), "rbw_hz": int(a.rbw_hz),
                "mode": a.mode.value}
        if offset is not None:
            # 当てた基準（境目の条件は id で比べ、文は名前: P §3。ライブの行と同じ形）
            cond["ref_id"] = str((meta or {}).get("id") or "")
            cond["ref"] = str((meta or {}).get("name") or "")
        quantity = "ratio" if offset is not None else "power"
        prof = levels.profile
        if levels.encoding != "gigast_ad_u16":
            prof = {**prof, "level_unit": levels.unit}
        rows = []
        for j in range(db.shape[0]):
            r = wf.row_from_values(float(ar.t_first[j]), f0, s.step_hz, db[j], unit=unit, seg=-(pos + 1),
                                   cond=cond, pos=(pos, int(ar.first_index[j])), sweeps=int(ar.sweeps[j]),
                                   t_end=float(ar.t_last[j]))
            r.extra = {"quantity": quantity, "profile": prof}
            rows.append(r)
        return rows

    # ------------------------------------------------------------------ 色の範囲の自動・密度（P §9.2・§9.3）

    def _pb_decide_auto(self, rows: list) -> None:
        """出している行の全体から、表示の単位ごとに色の範囲の自動を決める（|Γ|・SWR の行は縦軸と同じ範囲なので使わない）。
        いま出している範囲が変わったら画面へ送る。"""
        groups: dict[str, list] = {}
        for r in rows:
            _shift, unit, fmt = self._wf_shown(r)
            if fmt == formats.DB and unit:
                groups.setdefault(unit, []).append(self._wf_values(r))
        self._wf_auto.decide({u: np.concatenate(v) for u, v in groups.items()})
        if self._wf_range_sent is None or self._wf_range() != self._wf_range_sent:
            self._wf_emit_range()

    def _pb_density(self, s: _Session, seg: _PbSegment, g: int) -> None:
        """密度の元を、記録の中のカーソル（通し番号 g）までの直近の掃引にする（記録から読む: P §9.3）。表示の組み合わせが
        密度でなければ作らない。前に入れた最後の掃引のすぐ次なら、その1掃引だけ読んで足す。"""
        if self._view_opts.get("layout") != "density":
            s.dens = None
            return
        n = wf.DENSITY_REPLAY_HALVES * int(self._view_opts["density_half"])
        view = self._pb_view(s, seg)
        i = g - seg.first
        step = s.dens == (seg.pos, view, n, g - 1) and bool(self._density_src)
        i0 = i if step else max(0, i - n + 1)
        s.dens_req += 1
        req = s.dens_req
        fut = self._writer.submit(self._segment_rows, s.src, seg.pos, i0, i + 1, i + 1 - i0, view)

        def done(f) -> None:
            def finish() -> None:
                if self._playback is not s or req != s.dens_req or f.exception() is not None:
                    return  # 後の求めがある・再生を終えた・読めなかった（読めないことは掃引を出す方が知らせる）
                rows = f.result()
                if not rows:
                    return
                s.dens = (seg.pos, view, n, g)
                if step:
                    self._density_src.append(rows[-1])
                    self._density_feed(rows[-1])
                else:
                    self._density_src = collections.deque(rows, maxlen=n)
                    self._density_rebuild()

            self._post_call(finish)

        fut.add_done_callback(done)

    def _pb_density_refresh(self) -> None:
        """再生中に密度へ切り替えた・半分になる回数を変えた: カーソルまでの掃引を記録から読み直す。"""
        s = self._playback
        g = s.shown if s.shown is not None else s.cursor
        seg, _i = self._pb_locate(g)
        s.dens = None
        self._density.key = None
        self._pb_density(s, seg, g)

    # ------------------------------------------------------------------ 再生中の保存・書き出し・切り出し（P §5・H §3.1・§5）

    def _pb_context(self) -> Optional[dict]:
        """再生中なら、保存・書き出しに書く「何を測ったか」を記録から（いまの機器のものではなく: M-08）。"""
        s = self._playback
        if s is None:
            return None
        seg, _i = self._pb_locate(s.shown if s.shown is not None else s.cursor)
        h = s.src.header
        return {"header": h, "raw": s.shown_raw, "adopted": seg.levels.adopted,
                "normalization": seg.norm_meta if seg.norm_ref is not None else None,
                "calibration": seg.levels.seg.calibration if seg.levels.seg.calibration is not None else h.calibration,
                "device": {"model": h.device_model, "serial": h.device_serial}, "source": s.name, "segment": seg}

    def _pb_range(self, t_lo, t_hi, start_hz, stop_hz) -> tuple:
        """切り出し・書き出しの範囲。省いた時刻は記録の全体、省いた周波数はいまの表示の範囲。"""
        s = self._need_playback()
        for name, v in (("t_lo", t_lo), ("t_hi", t_hi)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise self._error("bad_value", name)
        for name, v in (("start_hz", start_hz), ("stop_hz", stop_hz)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise self._error("not_integer", name)
        seg, _i = self._pb_locate(s.cursor)
        lo, hi = self._pb_view(s, seg)
        f = (lo if start_hz is None else int(start_hz), hi if stop_hz is None else int(stop_hz))
        t = (s.segs[0].t_first if t_lo is None else float(t_lo), s.segs[-1].t_last if t_hi is None else float(t_hi))
        if f[1] < f[0] or t[1] < t[0]:
            raise self._error("range_empty")
        return s, seg, t, f

    def _cmd_playback_extract(self, path: Optional[str] = None, t_lo=None, t_hi=None, start_hz=None,
                              stop_hz=None) -> dict:
        """再生している記録の時間・周波数の範囲を、新しい保存ファイルにする（切り出し: H §3.1）。長い処理。"""
        if not path:
            raise self._error("path_required", "path")
        s, _seg, t, f = self._pb_range(t_lo, t_hi, start_hz, stop_hz)
        if s.src.kind != "h5":
            raise self._error("playback_not_saved_file")
        from .storage.h5file import extract_h5

        def submit(progress, cancel):
            return self._writer.submit(extract_h5, s.path, path, t_range=t, f_range_hz=f, progress=progress,
                                       cancel=cancel)

        return self._start_task("playback_extract", submit, lambda out: {"path": str(out)},
                                detail={"path": str(path)})

    def _cmd_playback_export_csv(self, path: Optional[str] = None, t_lo=None, t_hi=None, start_hz=None,
                                 stop_hz=None, layout: str = "wide") -> dict:
        """再生している記録の時間・周波数の範囲を CSV に（1行＝1掃引: H §5・§7.3）。カーソルのある区間（条件が同じ
        並び）だけ（区間ごとに周波数の列が違うため）。長い処理。"""
        import datetime as _dt

        from .level.convert import dbm_to_watt
        from .storage import adopted_to_dict
        from .storage.export import LAYOUT_LONG, LAYOUT_WIDE, export_record_csv
        from .types import Quantity

        if not path:
            raise self._error("path_required", "path")
        if layout not in (LAYOUT_WIDE, LAYOUT_LONG):
            raise self._error("bad_value", "layout", {"choices": [LAYOUT_WIDE, LAYOUT_LONG]})
        s, seg, t, f = self._pb_range(t_lo, t_hi, start_hz, stop_hz)
        ctx = self._pb_context()
        meta = {"device": ctx["device"], "conditions": adopted_to_dict(seg.levels.adopted),
                "calibration": ctx["calibration"], "normalization": ctx["normalization"], "source": s.name,
                "exported_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        ratio = seg.norm_offset is not None
        src = s.src

        def job(progress, cancel):
            rec = src._r  # noqa: SLF001 — 再生の記録を読む口（書き込みスレッドだけ）
            s0, s1 = int(np.searchsorted(seg.times, t[0], side="left")), int(np.searchsorted(seg.times, t[1],
                                                                                              side="right"))
            if s1 <= s0:
                raise CommandError("range_empty")
            first = rec.read(seg.pos, (s0, min(s1, s0 + 1)), None)
            freqs = np.asarray(first.freqs_hz, dtype=np.int64)
            pts = (int(np.searchsorted(freqs, f[0], side="left")), int(np.searchsorted(freqs, f[1], side="right")))
            if pts[1] <= pts[0]:
                raise CommandError("range_empty")

            def blocks():
                for a in range(s0, s1, 256):
                    b = rec.read(seg.pos, (a, min(s1, a + 256)), None)
                    db = seg.levels.db_rows(b.adopted()).astype(np.float64)
                    if ratio:
                        db = db + seg.norm_offset[None, :]
                        yield b.t_start, np.power(10.0, db[:, pts[0]:pts[1]] / 20.0)
                    else:
                        yield b.t_start, dbm_to_watt(db[:, pts[0]:pts[1]])

            return export_record_csv(path, freqs_hz=freqs[pts[0]:pts[1]], blocks=blocks(), total_sweeps=s1 - s0,
                                     quantity=Quantity.RATIO if ratio else Quantity.POWER, meta=meta, layout=layout,
                                     progress=progress, cancel=cancel)

        return self._start_task("playback_export_csv", lambda p, c: self._writer.submit(job, p, c),
                                lambda r: {"path": str(r.path), "rows": r.rows, "warnings": list(r.warnings)},
                                detail={"path": str(path)})

    # ------------------------------------------------------------------ 再生中に断る命令

    def _playback_request(self, req) -> dict:
        """再生中に利用者が周波数の範囲を変えた（_apply の view_in_playback）。周波数の範囲だけなら表示の範囲を変え
        （測り直さない: P §5）、ほかの条件（モード・RBW・点数・イメージ除去・Span 固定・SG の周波数・その1回だけ）を
        含むものは断る（ライブの条件を変えない）。"""
        others = (req.rbw_preference_hz, req.points, req.mode, req.sg_hz, req.image_reject, req.span_lock)
        freq = (req.start_hz, req.stop_hz, req.center_hz, req.span_hz)
        if any(v is not None for v in others) or req.one_shot or all(v is None for v in freq):
            raise self._error("playback_active")
        return self._playback_set_view(start_hz=req.start_hz, stop_hz=req.stop_hz, center_hz=req.center_hz,
                                       span_hz=req.span_hz)

    def _check_not_playing(self) -> None:
        if self._playback is not None:
            raise self._error("playback_active")

    def _playback_refusal(self) -> Optional[dict]:
        """いま再生に入れない理由（開く結果の errors の1行の形）。入れるなら None。

        ゼロスパンの間: マーカー・トレースの横軸がいまの条件の時間軸になる（設計N §1）ので、周波数の記録を載せない。
        基準・校正の測定とステップ掃引の最中: いまの条件と別の条件で測っている（終わるまで条件と動作を変えない）。
        """
        if self._adopted.axis.step_hz == 0:
            return {"code": "playback_zero_span_live", "detail": {}}
        if getattr(self, "_listen", None) is not None:
            # 音を聞いている間はライブの掃引でチューナーを動かしている（やめると聞く前の条件に戻す: instrument_listen）
            return {"code": "playback_listening", "detail": {}}
        try:
            self._check_not_taking_reference()
        except CommandError as e:
            return {"code": e.code, "detail": dict(e.detail or {})}
        return None

