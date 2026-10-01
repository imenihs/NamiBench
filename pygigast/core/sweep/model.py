"""掃引条件モデル（設計C）。

「利用者が求めた条件」を「機器が受け付ける条件」に直す関数を1つだけ持つ（設計C §2）。
- 周波数の正本は start_hz と stop_hz（Hz の int）。Center と Span は毎回ここから計算する
- 範囲・単位・点数・RBW の制約は機種のプロファイルの値だけで当てはめる（設計Q §2.1）。
  機器に固有の定数（GigaSt の Sample など）はここに持たない。時間の見積もりは機器の層が渡す TimingFn
- 変えた値は必ず Adjustment（理由のコード付き）で返す。画面の文言は画面側（U-22）
- 純粋な計算だけ。スレッド・入出力を持たない
- Step の決め方はプロファイルの sweep_kind で分ける（設計Q §6.4）: "stepped"（GigaSt。既定）は設計C §5.3〜§5.6、
  "fft"（SDR）は Step = fs/N（RBW から決まる）・点数 = ceil(Span/Step)+1・前置きなし。
  Start・Stop・Center・Span の規則（設計C §5.1・§5.2・§5.11・§5.12）はどちらも同じ
"""
from __future__ import annotations

import math
import re
from typing import Callable, Optional

from ..types import Adjustment, AdoptedSweep, Alternative, FreqAxis, Mode, SweepRequest

# (step_hz, rbw_hz, mode) → (初バイトまでの秒, 1点あたりの秒)。機器の層が作る（設計B §7.2）
TimingFn = Callable[[int, int, Mode], tuple[float, float]]  # (step_hz, rbw_hz, mode)
# sweep_kind "fft" の見積もり: (測る点の軸, 実際の RBW) → 1回の測定の秒。位置の数を数えるのは機器の層（設計Q §6.4）
FftTimingFn = Callable[[FreqAxis, int], float]

SWEEP_STEPPED = "stepped"
SWEEP_FFT = "fft"

# イメージキャンセル ON の1画面の掃引の回数（b3=0・1・0・1: 設計F §6 の 1。機器の層の measure と同じ）
IMAGE_CANCEL_PASSES = 4

# 周波数の欄の名前（SweepRequest.edited に入れる名前。Adjustment.field と同じ）
START = "start_hz"
STOP = "stop_hz"
CENTER = "center_hz"
SPAN = "span_hz"
_FREQ_FIELDS = (START, STOP, CENTER, SPAN)

# Center を触って範囲を超えたときの規則（設計C §5.11）。調整の理由のコードにも使う
RULE_KEEP_CENTER = "keep_center"
RULE_KEEP_SPAN = "keep_span"
# Span 固定中に Start か Stop を打って範囲を超えたときの反対の結果: 打った欄を守り Span を縮める（設計C §5.12）
RULE_KEEP_TYPED = "keep_typed"
# Span 固定のために、打っていない欄（Start を打ったときの Stop など）を動かした理由のコード（設計C §5.12）
REASON_SPAN_LOCK = "span_lock"

# 信用できない条件の知らせ（設計C §5.3 の2）のプロファイルのキー。コードは rbw{kHz}k_narrow_span
# （Span が狭い）・rbw{kHz}k_fine_step（Step が細かい）・rbw{kHz}k_unstable（常に）
_UNRELIABLE_KEY = re.compile(r"rbw(\d+)k_(span_below_hz|step_below_hz|always)")

# 点数の欄で「自動に戻す」を表す値（SweepRequest.points の None は「前の値を引き継ぐ」なので別に要る）
POINTS_AUTO = 0
# 選べる点数の下限（2点未満では Step が決まらない）
_POINTS_MIN = 2


def _round_to_unit(value: int, unit: int) -> int:
    """最も近い単位の倍数へ丸める（ちょうど中間は大きい側）。負の値も同じ規則。"""
    return (value + unit // 2) // unit * unit


def _ceil_div(a: int, b: int) -> int:
    """整数の切り上げ除算（b > 0）。"""
    return -(-a // b)


def _check_int(name: str, value: object) -> None:
    # 周波数・点数は int で受ける。float を混ぜると桁落ちする（設計A §2.1）
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} は int で渡す（{type(value).__name__} が来た）")


class _Adj:
    """調整の記録を集める。同じ欄は最初の求めた値と最後の採用値にまとめる。"""

    def __init__(self) -> None:
        self._items: list[Adjustment] = []

    def add(self, field: str, requested: object, adopted: object, reason: str) -> None:
        if requested == adopted:
            return
        self._items.append(Adjustment(field=field, requested=requested, adopted=adopted, reason=reason))

    def requested_of(self, field: str, fallback: object) -> object:
        """同じ欄に前の調整があれば、その「求めた値」を返す。"""
        for a in self._items:
            if a.field == field:
                return a.requested
        return fallback

    def replace(self, field: str, requested: object, adopted: object, reason: str) -> object:
        """同じ欄の前の調整を消し、最初の求めた値→最終の値の1件にする。"""
        req = self.requested_of(field, requested)
        self._items = [a for a in self._items if a.field != field]
        self.add(field, req, adopted, reason)
        return adopted

    def has(self, field: str) -> bool:
        return any(a.field == field for a in self._items)

    def reasons(self) -> set[str]:
        return {a.reason for a in self._items}

    def items(self) -> tuple[Adjustment, ...]:
        return tuple(self._items)


class SweepModel:
    """掃引条件モデル。profile は機種のプロファイル（dict）、timing は機器の層の時間の見積もり。"""

    def __init__(self, profile: dict, timing) -> None:
        """timing は sweep_kind が "stepped" なら TimingFn、"fft" なら FftTimingFn。"""
        self._kind = profile.get("sweep_kind", SWEEP_STEPPED)
        if self._kind not in (SWEEP_STEPPED, SWEEP_FFT):
            raise ValueError(f"知らない sweep_kind: {self._kind}")
        # イメージキャンセルの既定（機種のプロファイル。GigaSt は ON: 設計F §4・T16）
        self._profile_image_cancel_default = bool(profile.get("image_cancel_default", False))
        f = profile["freq"]
        self._unit = int(f["unit_hz"])
        self._min = int(f["min_hz"])
        self._max = int(f["max_hz"])
        # 起動時の既定の範囲（無ければ機種の範囲いっぱい）。RTL-SDR は範囲いっぱいだと1回の測定に
        # 約 117s かかるので、機種のプロファイルに狭い範囲を持つ（設計Q §6.9）
        self._default_start = int(f.get("default_start_hz", self._min))
        self._default_stop = int(f.get("default_stop_hz", self._max))
        if not (self._min <= self._default_start < self._default_stop <= self._max):
            raise ValueError("freq.default_start_hz / default_stop_hz は機種の範囲の中で start < stop")
        self._points_max = int(f["points_max"])
        sg = (profile.get("source") or {}).get("sg")
        self._sg = (int(sg["min_hz"]), int(sg["max_hz"]), int(sg["unit_hz"])) if sg else None
        self._timing = timing
        if self._kind == SWEEP_FFT:
            self._init_fft(profile)
            return
        self._points_device_max = int(f["points_device_max"])
        self._points_default = int(f["points_default"])
        auto = f["auto_step"]
        self._auto_threshold = int(auto["span_threshold_hz"])
        self._auto_divisions = int(auto["divisions"])
        self._auto_wide_step = int(auto["wide_step_hz"])
        self._step_max = int(f["step_max_hz"])
        self._zero_points_default = int(f["zero_span_points_default"])

        r = profile["rbw"]
        self._rbw_choices = tuple(int(x) for x in r["choices_hz"])
        self._rbw_default = int(r["default_hz"])
        self._rbw_fixed = int(r["fixed_hz"])
        self._rbw_modes = frozenset(Mode(m) for m in r["selectable_modes"])
        self._rbw_span_max = int(r["selectable_span_max_hz"])

        # 信用できない条件（設計C §5.3 の2）。キーの形は rbw{kHz}k_{条件}。RBW の値はキーから読む
        self._unreliable: list[tuple[int, str, object]] = []
        for key, value in profile.get("unreliable", {}).items():
            m = _UNRELIABLE_KEY.fullmatch(key)
            if m is None:
                raise ValueError(f"unreliable に知らないキー: {key}")
            self._unreliable.append((int(m.group(1)) * 1000, m.group(2), value))

        self._transient_s = float(profile.get("sweep_quirks", {}).get("transient_s", 0.0))

    def _init_fft(self, profile: dict) -> None:
        """sweep_kind "fft" の制約（設計Q §6.3・§6.4）。RBW の選択肢は (窓, FFT の点数 N) の表。"""
        self._fs = int(profile["fft"]["sample_rate_hz"])
        r = profile["rbw"]
        self._fft_table: dict[int, tuple[str, int]] = {}
        for c in r["choices"]:
            n = int(c["fft_n"])
            if self._fs % n:
                # 格子 fs/N を Hz の整数にする（周波数軸は整数の格子: 設計Q §6.4・設計A §2.1）
                raise ValueError(f"fs/N が整数でない: N={n}")
            self._fft_table[int(c["rbw_hz"])] = (str(c["window"]), n)
        self._rbw_choices = tuple(sorted(self._fft_table))
        self._rbw_default = int(r["default_hz"])
        if self._rbw_default not in self._fft_table:
            raise ValueError("rbw.default_hz が選択肢に無い")
        self._min_span_fft = int(profile["freq"]["min_span_hz"])
        self._measurements = tuple(Mode(m) for m in profile["measurements"])
        self._unreliable = []
        self._transient_s = 0.0

    @property
    def sweep_kind(self) -> str:
        return self._kind

    def fft_setting(self, rbw_hz: int) -> tuple[str, int]:
        """RBW に当たる (窓, FFT の点数 N)（sweep_kind "fft" のとき）。"""
        return self._fft_table[rbw_hz]

    # ------------------------------------------------------------------ 公開の口

    def default(self) -> AdoptedSweep:
        """既定の条件: SA・機種の既定の範囲（無ければ周波数範囲いっぱい）・点数は自動・RBW は既定・
        イメージキャンセルは機種の既定（GigaSt は ON: 設計F §4。生の値に掃引ごとに位置の変わる山が出て、
        2回の min でほぼ消えるため。T16）。"""
        adj = _Adj()
        return self._finish(
            start=self._default_start, stop=self._default_stop, points_setting=None,
            rbw_pref=self._rbw_default, mode=Mode.SA, sg_hz=None,
            image_cancel=bool(self._profile_image_cancel_default),
            prev_rbw=None, rbw_requested=False, adj=adj,
        )

    def apply(self, request: SweepRequest, current: AdoptedSweep) -> AdoptedSweep:
        """求めた条件を、いまの条件に重ねて機器が受け付ける条件に直す（設計C §5）。"""
        adj = _Adj()

        mode = current.mode if request.mode is None else Mode(request.mode)
        image_cancel = current.image_cancel if request.image_cancel is None else bool(request.image_cancel)
        if self._kind == SWEEP_FFT:
            mode, image_cancel = self._fft_mode(mode, image_cancel, adj)

        # RBW の好み（設計C §5.4）。選択肢に無い値は受け付けない
        rbw_pref = current.rbw_preference_hz
        if request.rbw_preference_hz is not None:
            _check_int("rbw_preference_hz", request.rbw_preference_hz)
            if request.rbw_preference_hz not in self._rbw_choices:
                raise ValueError(f"rbw_preference_hz は {self._rbw_choices} のどれか")
            rbw_pref = request.rbw_preference_hz

        points_setting = self._points_setting(request, current, adj)
        sg_hz = self._sg_hz(request, current, mode, adj)

        # Span 固定（設計C §5.11）: ON の間は固定 Span を守る。ON にした時点の Span が固定 Span
        lock_on = current.span_lock_hz is not None if request.span_lock is None else bool(request.span_lock)
        lock_hz = current.span_lock_hz if lock_on else None
        rule = RULE_KEEP_SPAN if lock_on else RULE_KEEP_CENTER

        min_span = self._min_span(points_setting)
        edited = self._edited(request)
        alternative = None
        if lock_hz is not None and not request.one_shot and set(edited) & {START, STOP}:
            # Span 固定中に Start・Stop・ドラッグで変えた: 固定 Span を守る（設計C §5.12 の表）。
            # その1回だけの命令はこの規則を通さず、送った Start・Stop をそのまま使う（範囲だけ守る）
            start, stop, alternative = self._resolve_locked_edges(request, current, edited, min_span, adj, lock_hz)
        else:
            start, stop = self._resolve_range(request, current, min_span, adj, rule, lock_hz)
            if CENTER in edited and adj.reasons() & {RULE_KEEP_CENTER, RULE_KEEP_SPAN}:
                # 規則で値を動かした: 反対の規則の結果を1つ添える（その1回だけ選べる。設計C §5.11 の 3）
                other = RULE_KEEP_CENTER if rule == RULE_KEEP_SPAN else RULE_KEEP_SPAN
                a_start, a_stop = self._resolve_range(request, current, min_span, _Adj(), other, lock_hz)
                if (a_start, a_stop) != (start, stop):
                    alternative = Alternative(rule=other, start_hz=a_start, stop_hz=a_stop)

        if lock_on and (lock_hz is None or (SPAN in edited and not request.one_shot)):
            # ON にした時点と、利用者が Span の欄に値を打ったときだけ、固定 Span を採用した Span にする。
            # 固定中に Span が変わるのは Span を打ったときだけ（固定の意味。設計C §5.12）。
            # その1回だけの命令（反対の結果のボタン）は固定 Span を変えない（設計C §5.11 の 3）
            lock_hz = stop - start

        return self._finish(
            start=start, stop=stop, points_setting=points_setting,
            rbw_pref=rbw_pref, mode=mode, sg_hz=sg_hz, image_cancel=image_cancel,
            prev_rbw=current.rbw_hz, rbw_requested=request.rbw_preference_hz is not None, adj=adj,
            span_lock_hz=lock_hz, alternative=alternative,
        )

    # ------------------------------------------------------------------ 点数・信号源

    def _fft_mode(self, mode: Mode, image_cancel: bool, adj: _Adj) -> tuple[Mode, bool]:
        """sweep_kind "fft" で測れないモード・イメージキャンセルは、測れる形に直して知らせる。"""
        if mode not in self._measurements:
            adj.add("mode", mode, self._measurements[0], "mode_unsupported")
            mode = self._measurements[0]
        if image_cancel:
            adj.add("image_cancel", True, False, "image_cancel_unsupported")
            image_cancel = False
        return mode, image_cancel

    def _points_setting(self, request: SweepRequest, current: AdoptedSweep, adj: _Adj) -> Optional[int]:
        """利用者が選んだ点数（None は自動）。None の要求は前の値を引き継ぎ、POINTS_AUTO で自動へ戻す。"""
        if self._kind == SWEEP_FFT:
            # 点数は RBW（格子 fs/N）と Span で決まる。選んだ点数は受け付けず、自動のままにする
            if request.points not in (None, POINTS_AUTO):
                _check_int("points", request.points)
                adj.add("points", request.points, None, "points_by_rbw")
            return None
        if request.points is None:
            return current.points_setting
        _check_int("points", request.points)
        if request.points == POINTS_AUTO:
            return None
        n = request.points
        if n < _POINTS_MIN:
            adj.add("points", n, _POINTS_MIN, "points_min")
            return _POINTS_MIN
        if n > self._points_max:
            adj.add("points", n, self._points_max, "points_max")
            return self._points_max
        return n

    def _sg_hz(self, request: SweepRequest, current: AdoptedSweep, mode: Mode, adj: _Adj) -> Optional[int]:
        """SG の周波数（設計C §5.7）。単位へ丸め、範囲へ収める。SG 以外のモードでも覚えておく。"""
        sg_hz = current.sg_hz
        if request.sg_hz is not None:
            if self._sg is None:
                raise ValueError("この機種は SG を持たない")
            _check_int("sg_hz", request.sg_hz)
            lo, hi, unit = self._sg
            v = _round_to_unit(request.sg_hz, unit)
            adj.add("sg_hz", request.sg_hz, v, "freq_unit")
            clamped = min(max(v, lo), hi)
            if clamped != v:
                adj.replace("sg_hz", request.sg_hz, clamped, "sg_range")
            sg_hz = clamped
        if mode == Mode.SG and sg_hz is None:
            if self._sg is None:
                raise ValueError("この機種は SG を持たない")
            # SG モードで周波数が一度も決まっていないときは範囲の下限から始める（設計に無い判断）
            sg_hz = self._sg[0]
            adj.add("sg_hz", None, sg_hz, "sg_range")
        return sg_hz

    def _min_span(self, points_setting: Optional[int]) -> int:
        """受け付ける最小の Span（0 を除く）= 単位 ×（点数 − 1）（設計C §5.3 の1）。自動は既定の点数で数える。"""
        if self._kind == SWEEP_FFT:
            return self._min_span_fft
        n = self._points_default if points_setting is None else points_setting
        return self._unit * (n - 1)

    # ------------------------------------------------------------------ 周波数（設計C §5.1・§5.2）

    def _edited(self, request: SweepRequest) -> tuple[str, ...]:
        # 欄の名前は "start_hz" と "start" のどちらでも受ける（Adjustment.field は "_hz" 付き）
        edited = tuple(f if f.endswith("_hz") else f + "_hz" for f in request.edited)
        if not edited:
            # 触った欄の指定が無ければ、値の入っている欄を触ったものとみなす
            edited = tuple(f for f in _FREQ_FIELDS if getattr(request, f) is not None)
        for f in edited:
            if f not in _FREQ_FIELDS:
                raise ValueError(f"edited に知らない欄: {f}")
            if getattr(request, f) is None:
                raise ValueError(f"edited に {f} があるのに値が無い")
            _check_int(f, getattr(request, f))
        for f in _FREQ_FIELDS:
            if getattr(request, f) is not None and f not in edited:
                raise ValueError(f"{f} に値があるのに edited に無い")
        if set(edited) & {START, STOP} and set(edited) & {CENTER, SPAN}:
            # Start/Stop の組と Center/Span の組を同時に触ると、どちらを守るか決まらない
            raise ValueError("Start/Stop と Center/Span を同時に変えられない")
        return edited

    def _resolve_range(self, request: SweepRequest, current: AdoptedSweep, min_span: int, adj: _Adj,
                       rule: str = RULE_KEEP_CENTER, lock_hz: Optional[int] = None) -> tuple[int, int]:
        edited = self._edited(request)
        if START in edited or STOP in edited:
            return self._resolve_start_stop(request, current, edited, min_span, adj)
        if CENTER in edited or SPAN in edited:
            return self._resolve_center_span(request, current, edited, min_span, adj, rule, lock_hz)
        # 周波数を触っていない（点数だけ変えた等）: 点数で最小 Span が上がったときだけ Center を守って広げる
        span = current.span_hz
        if 0 < span < min_span:
            adj.add(SPAN, span, min_span, "min_span")
            return self._place_center_span(current.center_hz, min_span, adj, requested_center=None)
        return current.start_hz, current.stop_hz

    def _round_field(self, name: str, value: int, adj: _Adj) -> int:
        v = _round_to_unit(value, self._unit)
        adj.add(name, value, v, "freq_unit")
        return v

    def _resolve_start_stop(self, request, current, edited, min_span, adj) -> tuple[int, int]:
        lo, hi = self._min, self._max
        start, stop = current.start_hz, current.stop_hz
        req = {}
        for name in (START, STOP):
            if name in edited:
                req[name] = getattr(request, name)
                v = self._round_field(name, req[name], adj)
                if v < lo:
                    v = adj.replace(name, req[name], lo, "lower_limit")
                elif v > hi:
                    v = adj.replace(name, req[name], hi, "upper_limit")
                if name == START:
                    start = v
                else:
                    stop = v
        if stop - start >= min_span:
            return start, stop
        # 狭すぎる・Start > Stop・Start = Stop: 打った欄を守り、相方を「同じ値 ± 最小 Span」にする（入れ替えない）
        if START in edited:
            new_stop = start + min_span
            if new_stop > hi:
                new_stop = hi
                new_start = hi - min_span
                adj.replace(START, req[START], new_start, "upper_limit")
                start = new_start
            adj.add(STOP, req.get(STOP, current.stop_hz), new_stop, "min_span")
            return start, new_stop
        new_start = stop - min_span
        if new_start < lo:
            new_start = lo
            new_stop = lo + min_span
            adj.replace(STOP, req[STOP], new_stop, "lower_limit")
            stop = new_stop
        adj.add(START, current.start_hz, new_start, "min_span")
        return new_start, stop

    def _resolve_locked_edges(self, request, current, edited, min_span, adj, lock_hz):
        """Span 固定中に Start・Stop を打った・ドラッグしたとき（設計C §5.12）。(start, stop, alternative) を返す。

        守る順番は ①機器の範囲 ②固定 Span ③打った欄。
        - Start だけ: Stop = Start + 固定 Span（窓ごと動かす）。Stop が上限を超えるなら Start = 上限 − 固定 Span
          にして、反対の結果（打った Start を守り、Stop を上限にして Span を縮める）を添える。Stop だけも同じ形
        - 両方（ドラッグ）: 選んだ範囲の真ん中を Center にし、幅は固定 Span（Center を打ったときと同じ規則）
        """
        lo, hi = self._min, self._max
        span = lock_hz
        if 0 < span < min_span:
            # 点数を増やして最小 Span が固定 Span より上がったとき。作れる最小 Span で守る
            span = min_span
        span = min(span, hi - lo)
        if START in edited and STOP in edited:
            return self._locked_drag(request, span, min_span, adj)

        typed = START if START in edited else STOP
        req = getattr(request, typed)
        v = self._round_field(typed, req, adj)
        if v < lo:
            v = adj.replace(typed, req, lo, "lower_limit")
        elif v > hi:
            v = adj.replace(typed, req, hi, "upper_limit")
        alternative = None
        if typed == START:
            start, stop = v, v + span
            if stop > hi:
                # 固定 Span を守って Start を下げる。反対の結果は打った Start を守り Span を縮めたもの
                start, stop = hi - span, hi
                adj.replace(START, req, start, RULE_KEEP_SPAN)
                a_start = min(v, hi - min_span)
                if (a_start, hi) != (start, stop):
                    alternative = Alternative(rule=RULE_KEEP_TYPED, start_hz=a_start, stop_hz=hi)
            adj.add(STOP, current.stop_hz, stop, REASON_SPAN_LOCK)
        else:
            start, stop = v - span, v
            if start < lo:
                start, stop = lo, lo + span
                adj.replace(STOP, req, stop, RULE_KEEP_SPAN)
                a_stop = max(v, lo + min_span)
                if (lo, a_stop) != (start, stop):
                    alternative = Alternative(rule=RULE_KEEP_TYPED, start_hz=lo, stop_hz=a_stop)
            adj.add(START, current.start_hz, start, REASON_SPAN_LOCK)
        return start, stop, alternative

    def _locked_drag(self, request, span, min_span, adj):
        """Span 固定中のドラッグ: 選んだ範囲の真ん中を Center にし、幅は固定 Span（設計C §5.12。ユーザー決定 A）。"""
        a, b = sorted((request.start_hz, request.stop_hz))
        # 真ん中は打った値ではないので、単位への丸めは知らせない
        center = _round_to_unit(a + (b - a) // 2, self._unit)
        tmp = _Adj()
        start, stop = self._place_by_center(center, span, min_span, tmp, center, RULE_KEEP_SPAN)
        alternative = None
        moved = [x for x in tmp.items() if x.reason == RULE_KEEP_SPAN]
        for x in moved:
            # 範囲に収めるため Center を動かした: Center の欄に知らせ、反対の結果（Center を守って Span を縮める）を添える
            adj.add(x.field, x.requested, x.adopted, x.reason)
        if moved:
            a_start, a_stop = self._place_by_center(center, span, min_span, _Adj(), center, RULE_KEEP_CENTER)
            if (a_start, a_stop) != (start, stop):
                alternative = Alternative(rule=RULE_KEEP_CENTER, start_hz=a_start, stop_hz=a_stop)
        adj.add(START, request.start_hz, start, REASON_SPAN_LOCK)
        adj.add(STOP, request.stop_hz, stop, REASON_SPAN_LOCK)
        return start, stop, alternative

    def _resolve_center_span(self, request, current, edited, min_span, adj, rule, lock_hz) -> tuple[int, int]:
        center = current.center_hz
        span = current.span_hz
        if CENTER in edited and SPAN not in edited and rule == RULE_KEEP_SPAN and lock_hz is not None:
            # 固定中に Center を触ったら、守るのは固定 Span（いまの Span ではない。設計C §5.11 の 2）
            span = lock_hz
        req_center = None
        if CENTER in edited:
            req_center = request.center_hz
            center = self._round_field(CENTER, req_center, adj)
        if SPAN in edited:
            req_span = request.span_hz
            span = self._round_field(SPAN, req_span, adj)
            if span < 0:
                # 負の Span は作れない。最小 Span にする（ゼロスパンへは明示の 0 でだけ入る）
                span = adj.replace(SPAN, req_span, min_span, "min_span")
            elif span == 0 and self._kind == SWEEP_FFT:
                # sweep_kind "fft" はゼロスパン（設計N）を持たない。最小 Span にして知らせる
                span = adj.replace(SPAN, req_span, min_span, "zero_span_unsupported")
            elif 0 < span < min_span:
                span = adj.replace(SPAN, req_span, min_span, "min_span")
        elif 0 < span < min_span:
            adj.add(SPAN, span, min_span, "min_span")
            span = min_span
        if CENTER in edited:
            return self._place_by_center(center, span, min_span, adj, req_center, rule)
        return self._place_center_span(center, span, adj, requested_center=req_center)

    def _place_by_center(self, center: int, span: int, min_span: int, adj: _Adj, req_center: int,
                         rule: str) -> tuple[int, int]:
        """Center を触ったときの範囲の収め方（設計C §5.11）。

        keep_center（既定）: Center を守り、Span を範囲に収まる最大に縮める
        keep_span（Span 固定の間）: Span を守り、Center を動かす。Span が範囲より広いときは範囲いっぱい
        Center と Span を同時に触って両方守れるときは両方守る。
        """
        lo, hi = self._min, self._max
        full = hi - lo
        if span > full:
            adj.replace(SPAN, adj.requested_of(SPAN, span), full, "upper_limit")
            span = full
        start, stop = self._around(center, span)
        if lo <= start and stop <= hi:
            new_center = start + span // 2
            if new_center != req_center and not adj.has(CENTER):
                # Span が単位の奇数倍のとき、Center は単位の半分だけずれる
                adj.add(CENTER, req_center, new_center, "freq_unit")
            return start, stop
        if rule == RULE_KEEP_CENTER and span > 0:
            c = min(max(center, lo), hi)
            if c != center:
                # Center 自体が範囲の外: 範囲の端に合わせてから Span を縮める
                adj.replace(CENTER, adj.requested_of(CENTER, req_center), c,
                            "lower_limit" if c == lo else "upper_limit")
                center = c
            room = min(center - lo, hi - center)
            fit = max(2 * room, 0) // self._unit * self._unit
            while fit > 0:
                s2, e2 = self._around(center, fit)
                if lo <= s2 and e2 <= hi:
                    break
                # Center が単位の半分ずれていると丸めで1単位はみ出す。1単位縮めて収める
                fit -= self._unit
            if fit >= min_span:
                adj.replace(SPAN, adj.requested_of(SPAN, span), fit, RULE_KEEP_CENTER)
                return self._around(center, fit)
            # 縮めると最小 Span を割る: 最小 Span を守り、Center を内側へ動かす（下で行う）
            adj.replace(SPAN, adj.requested_of(SPAN, span), min_span, "min_span")
            span = min_span
        start, stop = self._around(center, span)
        if start < lo:
            stop += lo - start
            start = lo
        elif stop > hi:
            start -= stop - hi
            stop = hi
        new_center = start + span // 2
        adj.replace(CENTER, adj.requested_of(CENTER, req_center), new_center, RULE_KEEP_SPAN)
        return start, stop

    def _place_center_span(self, center: int, span: int, adj: _Adj,
                           requested_center) -> tuple[int, int]:
        """Center と Span から Start/Stop を作り、範囲へ収める。

        Span を守り、収まらない分だけ Center を内側へ動かす（Span が範囲より広ければ範囲いっぱい）。
        Span を打って範囲を超えたときも、打った Span を守る（守る順番は 範囲 → Span 固定 → 打った欄。設計C §5.12）。
        """
        lo, hi = self._min, self._max
        full = hi - lo
        req_span = span
        if span > full:
            span = full
            adj.replace(SPAN, adj.requested_of(SPAN, req_span), span, "upper_limit")

        start, stop = self._around(center, span)
        reason = None
        if start < lo:
            stop += lo - start
            start = lo
            reason = "lower_limit"
        elif stop > hi:
            start -= stop - hi
            stop = hi
            reason = "upper_limit"
        new_center = start + span // 2
        orig_center = requested_center if requested_center is not None else center
        if reason is not None:
            adj.replace(CENTER, adj.requested_of(CENTER, orig_center), new_center, reason)
        elif requested_center is not None and new_center != requested_center and not adj.has(CENTER):
            # Span が単位の奇数倍のとき、Center は単位の半分だけずれる
            adj.add(CENTER, requested_center, new_center, "freq_unit")
        return start, stop

    def _around(self, center: int, span: int) -> tuple[int, int]:
        """Center を中心に Span の Start/Stop を作る。Start は単位の格子へ丸める（Center は最大で単位の半分ずれる）。"""
        start = _round_to_unit(center - span // 2, self._unit)
        return start, start + span

    # ------------------------------------------------------------------ 点・RBW・前置き（設計C §5.3〜§5.6）

    def _step_points(self, span: int, points_setting: Optional[int], adj: _Adj) -> tuple[int, int]:
        """Step と点数（機器に測らせる点。前置きを含まない）。"""
        u = self._unit
        if span == 0:
            n = self._zero_points_default if points_setting is None else points_setting
            return 0, n
        if points_setting is None:
            if span <= self._auto_threshold:
                # 間隔 divisions 個。単位に切り捨て、単位未満にはしない
                step = max(u, span // self._auto_divisions // u * u)
            else:
                step = self._auto_wide_step
        else:
            # 点数 = ceil(Span/Step)+1 が選んだ点数を超えない最小の Step
            step = _ceil_div(span, (points_setting - 1) * u) * u
            if step > self._step_max:
                step = self._step_max
        step = min(step, self._step_max)
        points = _ceil_div(span, step) + 1
        if points_setting is not None and points != points_setting:
            reason = "step_max" if step == self._step_max and points > points_setting else "freq_unit"
            # 点数の欄の調整は1件にまとめる（選んだ点数の上下限の調整があれば、その求めた値から最終の点数へ）
            adj.replace("points", points_setting, points, reason)
        return step, points

    def _rbw(self, mode: Mode, span: int, pref: int) -> int:
        if mode in self._rbw_modes and span <= self._rbw_span_max:
            return pref
        return self._rbw_fixed

    def _warnings(self, rbw: int, span: int, step: int) -> tuple[str, ...]:
        """実際に使う RBW と Span・Step から、信用できない条件のコードを並べる（止めない）。"""
        w = []
        for rbw_hz, cond, value in self._unreliable:
            if rbw != rbw_hz:
                continue
            k = rbw_hz // 1000
            if cond == "span_below_hz" and 0 < span < int(value):
                w.append(f"rbw{k}k_narrow_span")
            elif cond == "step_below_hz" and 0 < step < int(value):
                w.append(f"rbw{k}k_fine_step")
            elif cond == "always" and value:
                w.append(f"rbw{k}k_unstable")
        return tuple(w)

    def _preroll(self, start: int, step: int, points: int, per_point_s: float) -> tuple[int, int]:
        """前置きの点数と、足りなかった点数（設計C §5.6）。"""
        if self._transient_s <= 0 or per_point_s <= 0:
            return 0, 0
        want = math.ceil(self._transient_s / per_point_s)
        n = want
        if step > 0:
            n = min(n, (start - self._min) // step)
        n = min(n, self._points_device_max - points)
        n = max(n, 0)
        return n, min(want - n, points)

    def _finish(self, *, start, stop, points_setting, rbw_pref, mode, sg_hz, image_cancel,
                prev_rbw, rbw_requested, adj: _Adj, span_lock_hz: Optional[int] = None,
                alternative: Optional[Alternative] = None) -> AdoptedSweep:
        if self._kind == SWEEP_FFT:
            return self._finish_fft(
                start=start, stop=stop, rbw_pref=rbw_pref, mode=mode, sg_hz=sg_hz, prev_rbw=prev_rbw,
                rbw_requested=rbw_requested, adj=adj, span_lock_hz=span_lock_hz, alternative=alternative,
            )
        span = stop - start
        step, points = self._step_points(span, points_setting, adj)
        tail_gap = False
        if points > 1 and start + step * (points - 1) > self._max:
            # 超えた最後の点が機器の上限の外: 1点減らし、Stop の手前にデータの無い区間があると印を付ける
            points -= 1
            tail_gap = True

        rbw = self._rbw(mode, span, rbw_pref)
        if rbw != rbw_pref and (rbw_requested or prev_rbw != rbw):
            # 好みが使えない条件に入ったとき・使えない条件で好みを選んだときに知らせる（好みは消さない）
            adj.add("rbw_hz", rbw_pref, rbw, "rbw_fixed")

        first_s, per_point_s = self._timing(step, rbw, mode)
        preroll, head = self._preroll(start, step, points, per_point_s)
        est = first_s + (points + preroll) * per_point_s
        # 1画面の掃引の回数（機器の層の measure と同じ並び）: NF は HOT/COLD の2回（b3 は 1 に固定し、
        # イメージキャンセルは使わない）、イメージキャンセル ON は b3=0・1・0・1 の4回（設計F §6 の 4）
        if mode == Mode.NF:
            est *= 2
        elif image_cancel:
            est *= IMAGE_CANCEL_PASSES

        axis = FreqAxis(start_hz=start, step_hz=step, points=points)
        self._check_device(axis, preroll)
        return AdoptedSweep(
            start_hz=start, stop_hz=stop, axis=axis, rbw_hz=rbw, rbw_preference_hz=rbw_pref,
            mode=mode, sg_hz=sg_hz, image_cancel=image_cancel, preroll_points=preroll,
            transient_head_points=head, estimated_sweep_s=est, adjustments=adj.items(),
            points_setting=points_setting, tail_gap=tail_gap,
            warnings=self._warnings(rbw, span, step),
            span_lock_hz=span_lock_hz, alternative=alternative,
        )

    def _check_device(self, axis: FreqAxis, preroll: int) -> None:
        """機器へ送る条件がプロファイルの制約に収まっているか（ここで外れたらモデルの誤り）。"""
        dev_start = axis.start_hz - preroll * axis.step_hz
        if not (self._min <= dev_start and axis.last_hz <= self._max):
            raise AssertionError(f"機器へ送る範囲が外れた: {dev_start}〜{axis.last_hz}")
        if not (1 <= axis.points + preroll <= self._points_device_max):
            raise AssertionError(f"機器へ送る点数が外れた: {axis.points + preroll}")
        if axis.step_hz > self._step_max or axis.step_hz % self._unit or axis.start_hz % self._unit:
            raise AssertionError(f"Step・Start が制約の外: {axis.step_hz}, {axis.start_hz}")

    # ------------------------------------------------------------------ sweep_kind "fft"（設計Q §6.4）

    def _fft_points(self, span: int, rbw: int) -> tuple[int, int]:
        step = self._fs // self._fft_table[rbw][1]
        return step, _ceil_div(span, step) + 1

    def _finish_fft(self, *, start, stop, rbw_pref, mode, sg_hz, prev_rbw, rbw_requested, adj: _Adj,
                    span_lock_hz: Optional[int], alternative: Optional[Alternative]) -> AdoptedSweep:
        span = stop - start
        # 点数が上限を超えるときは、同じ窓の RBW を1段ずつ広げて収める。好みは消さない（設計C §5.4 と同じ扱い）
        window = self._fft_table[rbw_pref][0]
        ladder = [r for r in self._rbw_choices if self._fft_table[r][0] == window and r >= rbw_pref]
        rbw = ladder[0]
        step, points = self._fft_points(span, rbw)
        for r in ladder[1:]:
            if points <= self._points_max:
                break
            rbw = r
            step, points = self._fft_points(span, rbw)
        if points > self._points_max:
            raise AssertionError(f"いちばん広い RBW でも点数が上限を超える: {points}")
        if rbw != rbw_pref and (rbw_requested or prev_rbw != rbw):
            adj.add("rbw_hz", rbw_pref, rbw, "rbw_points_max")

        tail_gap = False
        if points > 1 and start + step * (points - 1) > self._max:
            # 超えた最後の点が機種の上限の外: 1点減らし、Stop の手前にデータの無い区間があると印を付ける
            points -= 1
            tail_gap = True
        axis = FreqAxis(start_hz=start, step_hz=step, points=points)
        if not (self._min <= axis.start_hz and axis.last_hz <= self._max):
            raise AssertionError(f"測る範囲が外れた: {axis.start_hz}〜{axis.last_hz}")
        est = float(self._timing(axis, rbw))
        return AdoptedSweep(
            start_hz=start, stop_hz=stop, axis=axis, rbw_hz=rbw, rbw_preference_hz=rbw_pref,
            mode=mode, sg_hz=sg_hz, image_cancel=False, preroll_points=0,
            transient_head_points=0, estimated_sweep_s=est, adjustments=adj.items(),
            points_setting=None, tail_gap=tail_gap, warnings=(),
            span_lock_hz=span_lock_hz, alternative=alternative,
        )
