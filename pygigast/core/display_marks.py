"""機器の範囲の外・意味の無い点の印と、REF レベルの「自動」に使う最大値（設計J §4.8・§4.1）。

- 点ごとの印（Frame.flags のキー。bool の配列）
  - lo_leak: SA・SG で、機種のプロファイル `lo_leak_band_hz` の帯の中（0Hz の局発の漏れ。入力の信号ではない）
  - negative_mirror: SA・SG で、負の周波数（正の周波数 |f| の信号の鏡で、約 1〜2dB 低く読む）
  - outside_tg_range: TG・S11 で、プロファイル `source.tg_range_hz` の外（値は通過特性ではない）
  プロファイルに帯の無い機種（RTL-SDR など）は何も付けない。マーカーの読み値は点の印をそのまま注意にする
  （設計G §3.3。marker/analysis.py の point_flags）
- 画面が描く帯（display_marks）: 同じ規則を、いまの表示範囲に切った区間で返す。画面は計算し直さずに描く
- REF の自動（usable_max）: 1本のトレースのうち、使ってよい点（値がある・表示範囲の中・下の印が無い）の最大値
- 内部の漏れ（internal_leaks・設計L §1.4）: SG・TG を使っているとき、機種のプロファイルの漏れの周波数のうち表示範囲に
  入るもの。グラフの上端の印・ピークの一覧の印・マウスの位置の読み値に使う。測った山を漏れとみなす幅は leak_tolerance_hz
- 純粋な計算だけ。Qt・スレッド・入出力を持たない
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .types import Mode

LO_LEAK = "lo_leak"
NEGATIVE_MIRROR = "negative_mirror"
OUTSIDE_TG_RANGE = "outside_tg_range"
MARK_KINDS = (OUTSIDE_TG_RANGE, LO_LEAK, NEGATIVE_MIRROR)

# 受けた信号を見るモード（0Hz の局発の漏れと負の側の鏡が出る）と、TG を出して通過・反射を測るモード
_RECEIVE_MODES = (Mode.SA, Mode.SG)
_TG_MODES = (Mode.TG, Mode.S11)

# REF の自動に使わない点の印（J §4.8: 局発の漏れ・前置き不足・値の無い点・TG の範囲の外）。
# no_reference はノーマライズの基準が無い点（値なし: 設計E §6.4）、no_value は RTL-SDR の値の無い点
AUTO_REF_EXCLUDE = (LO_LEAK, "transient", "no_value", "no_reference", OUTSIDE_TG_RANGE)


def _mode(mode) -> Optional[Mode]:
    if mode is None:
        return None
    try:
        return Mode(mode)
    except ValueError:
        return None


def _pair(v) -> Optional[tuple[int, int]]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    lo, hi = int(v[0]), int(v[1])
    return (lo, hi) if lo <= hi else None


def lo_leak_band(profile: Optional[dict]) -> Optional[tuple[int, int]]:
    return _pair((profile or {}).get("lo_leak_band_hz"))


def tg_range(profile: Optional[dict]) -> Optional[tuple[int, int]]:
    return _pair(((profile or {}).get("source") or {}).get("tg_range_hz"))


def point_marks(freqs: np.ndarray, mode, profile: Optional[dict]) -> dict[str, np.ndarray]:
    """点ごとの印。そのモードで意味を持つ印だけを返す（関係の無い印のキーは作らない）。"""
    m = _mode(mode)
    f = np.asarray(freqs, dtype=np.int64)
    out: dict[str, np.ndarray] = {}
    if m in _RECEIVE_MODES:
        band = lo_leak_band(profile)
        if band is not None:
            out[LO_LEAK] = (f >= band[0]) & (f <= band[1])
        if f.size and int(f.min()) < 0:
            out[NEGATIVE_MIRROR] = f < 0
    elif m in _TG_MODES:
        rng = tg_range(profile)
        if rng is not None:
            out[OUTSIDE_TG_RANGE] = (f < rng[0]) | (f > rng[1])
    return out


def display_marks(mode, profile: Optional[dict], start_hz: int, stop_hz: int) -> list[dict]:
    """表示範囲 start_hz〜stop_hz に掛かる帯 [{kind, start_hz, stop_hz}]（周波数の順）。

    負の側の帯は局発の漏れの帯の下端で止める（2つの帯を重ねて描かない。点の印はどちらも付く）。
    """
    m = _mode(mode)
    if stop_hz <= start_hz:
        return []  # ゼロスパン（横軸が時間）には周波数の帯を描かない
    raw: list[tuple[str, int, int]] = []
    if m in _RECEIVE_MODES:
        band = lo_leak_band(profile)
        neg_top = band[0] if band is not None and band[0] < 0 else 0
        raw.append((NEGATIVE_MIRROR, start_hz, min(stop_hz, neg_top)))
        if band is not None:
            raw.append((LO_LEAK, band[0], band[1]))
    elif m in _TG_MODES:
        rng = tg_range(profile)
        if rng is not None:
            raw.append((OUTSIDE_TG_RANGE, start_hz, rng[0]))
            raw.append((OUTSIDE_TG_RANGE, rng[1], stop_hz))
    out = []
    for kind, lo, hi in raw:
        lo, hi = max(lo, start_hz), min(hi, stop_hz)
        if hi > lo:
            out.append({"kind": kind, "start_hz": int(lo), "stop_hz": int(hi)})
    return sorted(out, key=lambda d: d["start_hz"])


# 内部の漏れの周波数を示すモード（設計L §1.4: SG・TG を使っているとき）。S11 は機器から見れば TG と同じ掃引
_LEAK_MODES = (Mode.SG, Mode.TG, Mode.S11)


def internal_leaks(mode, profile: Optional[dict], sg_hz: Optional[int], start_hz: int, stop_hz: int) -> list[dict]:
    """内部の漏れの周波数のうち、表示範囲 start_hz〜stop_hz に入るもの [{freq_hz, kind, offset_hz}]（周波数の順）。

    機種のプロファイルの internal_spurs_hz（T2）: fixed はそのままの周波数、sg_offset は SG の周波数からのずれ。
    SG・TG・S11 のときだけ（SA・NF では空）。sg_offset は SG モードだけ（TG は掃引に同期して動くので、受けている
    周波数から常にずれだけ離れていて掃引に乗らない）。ゼロスパン（横軸が時間）では空。
    """
    m = _mode(mode)
    if m not in _LEAK_MODES or stop_hz <= start_hz:
        return []
    spurs = (profile or {}).get("internal_spurs_hz") or {}
    found: dict[int, dict] = {}
    for f in spurs.get("fixed") or []:
        found.setdefault(int(f), {"freq_hz": int(f), "kind": "fixed", "offset_hz": None})
    if m == Mode.SG and sg_hz is not None:
        for off in spurs.get("sg_offset") or []:
            f = int(sg_hz) + int(off)
            found.setdefault(f, {"freq_hz": f, "kind": "sg_offset", "offset_hz": int(off)})
    return [found[f] for f in sorted(found) if start_hz <= f <= stop_hz]


def leak_tolerance_hz(rbw_hz: int, step_hz: int) -> int:
    """測った山を内部の漏れとみなす周波数の幅（±）。RBW と点の間隔の和。

    山の点は格子の点なので本当の周波数から最大で点の間隔の半分ずれ、RBW 180kHz ではフィルタの形が非対称で山が
    +50〜100kHz ずれて見える（T2）。両方の半分ずつに、漏れの発振器そのものの周波数のずれ（未測定）の分を足して、
    RBW と点の間隔の和にした（設計に値は無い）。
    """
    return int(rbw_hz) + max(0, int(step_hz))


def leak_at(freq_hz: float, leaks: list[dict], tolerance_hz: float) -> Optional[dict]:
    """freq_hz から tolerance_hz 以内にある一番近い漏れ（無ければ None）。"""
    best = None
    for lk in leaks or []:
        d = abs(float(freq_hz) - float(lk["freq_hz"]))
        if d <= tolerance_hz and (best is None or d < best[0]):
            best = (d, lk)
    return best[1] if best else None


def usable_max(values_db, freqs, flags: Optional[dict], start_hz: Optional[int] = None,
               stop_hz: Optional[int] = None) -> Optional[float]:
    """REF の自動に使う最大値（設計J §4.8）。使える点が無ければ None。

    values_db は画面の単位の値（NaN は値なし）。start_hz・stop_hz を渡せばその範囲の外の点も使わない
    （Stop の先に出た最後の点: 設計C §5.3、表示範囲より広い参照トレース）。
    """
    y = np.asarray(values_db, dtype=np.float64)
    ok = np.isfinite(y)
    f = np.asarray(freqs, dtype=np.int64)
    if f.shape == y.shape:
        if start_hz is not None:
            ok &= f >= start_hz
        if stop_hz is not None:
            ok &= f <= stop_hz
    for name in AUTO_REF_EXCLUDE:
        a = (flags or {}).get(name)
        if a is not None and np.shape(a) == y.shape:
            ok &= ~np.asarray(a, dtype=bool)
    return float(y[ok].max()) if ok.any() else None


def auto_ref_level(peaks: list[Optional[float]], db_per_div: float) -> Optional[float]:
    """表示中のトレースの最大値（usable_max）から REF レベル: 最大値が上から1目盛下に来る値（J §4.1）。

    表示の桁（0.1dB）へ切り上げる。切り捨てると最大値が1目盛の線をわずかに超える。
    """
    vals = [p for p in peaks if p is not None and math.isfinite(p)]
    if not vals:
        return None
    return math.ceil((max(vals) + float(db_per_div)) * 10 - 1e-9) / 10
