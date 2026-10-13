"""SG のステップ掃引（設計L §1.3）の計算: 設定の確かめ・段の並び・重ね方。

- 設定: 開始・終了・刻み（SG の周波数。SG の範囲と単位は機種のプロファイル: L §1.2・設計C §5.7）・1段あたりの掃引の
  回数・重ね方（最大保持・平均・全部重ねる）・結果を入れるトレースの番号。SP の掃引条件はいまのもの
- 段は 開始, 開始 ± 刻み, … で、終了を越えない所まで（開始が終了より上なら下へ向かう）
- 重ね方
  - 最大保持: 全部の掃引の点ごとの最大
  - 平均: 全部の掃引の点ごとのパワー平均（W のまま平均）
  - 全部重ねる: 段ごとに、その段の回数分をパワー平均した1本を残す。主の値は段ごとの値の点ごとの最大（包絡線。
    マーカー・ピークの一覧・保存はこの値を読む）
- 値の無い点（NaN）は、その点を数えない（どの掃引にも値が無ければ値なしのまま）
- 純粋な計算だけ。スレッド・入出力を持たない
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

MAX = "max"
AVERAGE = "average"
ALL = "all"
COMBINES = (MAX, AVERAGE, ALL)

# 1段あたりの掃引の回数の範囲（設計に値は無い。1段で 100 回を超えて重ねる用事は見当たらない）
PER_STEP_MIN = 1
PER_STEP_MAX = 100
# 段の数の上限（設計に値は無い）。1段 0.1 秒でも 10000 段で 17 分。それより多いのは刻みの打ち間違いとみなす
STEPS_MAX = 10000
# 全部重ねるときの段の数の上限（設計に値は無い）。段ごとの1本を全部持って描くので、本数が多いと重ねた線が
# 塗りつぶしになって読めず、画面へ送る量も増える。多く重ねて見る用事は密度表示（設計P §4）のもの
ALL_STEPS_MAX = 200
# 全部重ねるときの段ごとの線の濃さ（トレースの色の不透明度。Qt の画面・PNG・Web の canvas が同じ値を使う）
LINE_ALPHA = 0.35
# 段ごとの線を画面へ送るときの1本の点数の上限。これより多い点は、続く点の最大で間引く（描くときの間引きと同じ:
# 設計F §5.5。画面の横の画素数より細かくしても見えない）
DRAW_POINTS_MAX = 2000

# 初めての起動の設定（設計に値は無い。L §1.3 の例「40 段」に近い段の数になる 100MHz〜200MHz・刻み 2.5MHz）
DEFAULTS = {"start_hz": 100_000_000, "stop_hz": 200_000_000, "step_hz": 2_500_000, "per_step": 1,
            "combine": MAX, "trace": 2}
FIELDS = tuple(DEFAULTS)


@dataclass(frozen=True)
class Adjusted:
    """設定を確かめて値を動かしたこと（欄のそばに一度知らせる: M-02）。"""

    field: str
    requested: object
    adopted: object
    reason: str


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _round_unit(v: int, unit: int) -> int:
    return (v + unit // 2) // unit * unit


def sg_range(profile: Optional[dict]) -> Optional[tuple[int, int, int]]:
    """機種の SG の範囲と単位 (下限, 上限, 単位)。SG を持たない機種は None。"""
    sg = ((profile or {}).get("source") or {}).get("sg")
    if not sg:
        return None
    return int(sg["min_hz"]), int(sg["max_hz"]), int(sg["unit_hz"])


def check(cfg: dict, changes: dict, rng: Optional[tuple[int, int, int]]) -> tuple[dict, list[Adjusted]]:
    """changes（渡した欄だけ）を cfg に重ねて確かめる。直した値と、直したことの一覧を返す。

    周波数は SG の単位へ丸め、範囲へ収める。刻みは単位以上。回数は範囲へ収める。読めない値は ValueError（欄の名前）。
    """
    out = dict(cfg)
    adj: list[Adjusted] = []
    lo, hi, unit = rng if rng is not None else (None, None, 1)
    for name in ("start_hz", "stop_hz"):
        if name not in changes:
            continue
        v = changes[name]
        if not _is_int(v):
            raise ValueError(name)
        r = _round_unit(v, unit)
        reason = "freq_unit"
        if lo is not None and not lo <= r <= hi:
            r, reason = min(max(r, lo), hi), "sg_range"
        if r != v:
            adj.append(Adjusted(name, v, r, reason))
        out[name] = r
    if "step_hz" in changes:
        v = changes["step_hz"]
        if not _is_int(v):
            raise ValueError("step_hz")
        r = max(_round_unit(v, unit), unit)
        if r != v:
            adj.append(Adjusted("step_hz", v, r, "step_min" if v < unit else "freq_unit"))
        out["step_hz"] = r
    if "per_step" in changes:
        v = changes["per_step"]
        if not _is_int(v):
            raise ValueError("per_step")
        r = min(max(v, PER_STEP_MIN), PER_STEP_MAX)
        if r != v:
            adj.append(Adjusted("per_step", v, r, "per_step_min" if v < PER_STEP_MIN else "per_step_max"))
        out["per_step"] = r
    if "combine" in changes:
        if changes["combine"] not in COMBINES:
            raise ValueError("combine")
        out["combine"] = changes["combine"]
    if "trace" in changes:
        v = changes["trace"]
        if not _is_int(v) or not 1 <= v <= 4:
            raise ValueError("trace")
        out["trace"] = v
    return out, adj


def restore(d, rng: Optional[tuple[int, int, int]] = None) -> dict:
    """保存した形から戻す。読めない欄は初期値（範囲・単位は確かめ直す）。"""
    out = dict(DEFAULTS)
    if not isinstance(d, dict):
        return out
    for k in FIELDS:
        if k in d:
            try:
                out, _adj = check(out, {k: d[k]}, rng)
            except ValueError:
                pass
    return out


def count(cfg: dict) -> int:
    """段の数（終了を越えない所まで）。"""
    step = max(1, int(cfg["step_hz"]))
    return abs(int(cfg["stop_hz"]) - int(cfg["start_hz"])) // step + 1


def plan(cfg: dict) -> list[int]:
    """段ごとの SG の周波数（Hz の整数）。"""
    start, step = int(cfg["start_hz"]), max(1, int(cfg["step_hz"]))
    sign = 1 if int(cfg["stop_hz"]) >= start else -1
    return [start + sign * step * i for i in range(count(cfg))]


def start_problem(cfg: dict) -> Optional[str]:
    """設定だけで決まる始められない理由のコード（段が多すぎる）。無ければ None。"""
    n = count(cfg)
    if n > STEPS_MAX:
        return "too_many_steps"
    if cfg["combine"] == ALL and n > ALL_STEPS_MAX:
        return "too_many_steps_all"
    return None


def _or_flags(acc: dict, new: dict) -> dict:
    """印の和（蓄積に1回でも印の付いた掃引が入った点は印を持つ。トレースの蓄積と同じ: 設計F）。"""
    out = {k: np.asarray(v, dtype=bool).copy() for k, v in acc.items()}
    for k, v in new.items():
        v = np.asarray(v, dtype=bool)
        out[k] = out[k] | v if k in out and out[k].shape == v.shape else v.copy()
    return out


class Combiner:
    """重ね方に従って掃引を重ねる。値はリニア（電力は W）の1次元の配列。"""

    def __init__(self, combine: str, points: int) -> None:
        if combine not in COMBINES:
            raise ValueError(combine)
        self.combine = combine
        self.points = int(points)
        self.sweeps = 0
        self.flags: dict = {}
        self._max: Optional[np.ndarray] = None
        self._sum: Optional[np.ndarray] = None
        self._n: Optional[np.ndarray] = None
        # 全部重ねる: 段ごとの和と数（段の番号の順）
        self._step_sum: list[np.ndarray] = []
        self._step_n: list[np.ndarray] = []
        self._step_index: list[int] = []

    def add(self, step: int, values, flags: Optional[dict] = None) -> None:
        v = np.asarray(values, dtype=np.float64)
        if v.shape != (self.points,):
            raise ValueError("値の長さが点数と違う")
        ok = np.isfinite(v)
        self.sweeps += 1
        self.flags = _or_flags(self.flags, flags or {})
        if self.combine == MAX:
            self._max = v.copy() if self._max is None else np.fmax(self._max, v)
        elif self.combine == AVERAGE:
            if self._sum is None:
                self._sum, self._n = np.zeros(self.points), np.zeros(self.points)
            self._sum += np.where(ok, v, 0.0)
            self._n += ok
        else:
            if not self._step_index or self._step_index[-1] != step:
                self._step_index.append(step)
                self._step_sum.append(np.zeros(self.points))
                self._step_n.append(np.zeros(self.points))
            self._step_sum[-1] += np.where(ok, v, 0.0)
            self._step_n[-1] += ok

    @staticmethod
    def _mean(s: np.ndarray, n: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n > 0, s / np.maximum(n, 1), np.nan)

    def step_values(self) -> Optional[np.ndarray]:
        """全部重ねるときの段ごとの値 [段, 点]（重ねた段の順）。ほかの重ね方は None。"""
        if self.combine != ALL or not self._step_sum:
            return None
        return np.vstack([self._mean(s, n) for s, n in zip(self._step_sum, self._step_n)])

    def result(self) -> Optional[np.ndarray]:
        """主の値（最大保持・平均・全部重ねるの包絡線）。まだ1回も重ねていなければ None。"""
        if self.sweeps == 0:
            return None
        if self.combine == MAX:
            return self._max.copy()
        if self.combine == AVERAGE:
            return self._mean(self._sum, self._n)
        steps = self.step_values()
        with np.errstate(invalid="ignore"):
            all_nan = np.all(np.isnan(steps), axis=0)
            env = np.where(all_nan, np.nan, np.nanmax(np.where(np.isnan(steps), -np.inf, steps), axis=0))
        return env


def decimate_rows(rows: np.ndarray, max_points: int = DRAW_POINTS_MAX) -> tuple[np.ndarray, int]:
    """[段, 点] の値を、1本 max_points 点以下に続く点の最大で間引く。(間引いた値, まとめた点の数 k) を返す。

    k 点ずつの最大（値の無い点は除く。全部無ければ値なし）。k = 1 ならそのまま。
    """
    a = np.asarray(rows, dtype=np.float64)
    n = a.shape[1] if a.ndim == 2 else 0
    if n <= max_points:
        return a, 1
    k = math.ceil(n / max_points)
    m = math.ceil(n / k)
    pad = np.full((a.shape[0], m * k - n), np.nan)
    b = np.concatenate([a, pad], axis=1).reshape(a.shape[0], m, k)
    all_nan = np.all(np.isnan(b), axis=2)
    out = np.where(all_nan, np.nan, np.max(np.where(np.isnan(b), -np.inf, b), axis=2))
    return out, k
