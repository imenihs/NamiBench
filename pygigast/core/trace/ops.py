"""トレースの値の表し方・差・描くときの間引き（設計F §2・§5.4・§5.5）。

- 値はリニアで持ち、dB は表示のときの表し方（設計F §2）: 電力（W）は dBm、比は dB
- 差「A − B」は dB で出す。B を A の周波数軸へ dB で線形補間し、重なる範囲だけ（設計F §5.4）
- 画素より点が多いときは画素ごとに最大値、最小保持だけは最小値で間引く（設計F §5.5・設計P §8）
周波数は Hz の int64 で持ち、補間の重みも整数の差から求める（設計A §2.1）。純粋な計算だけ。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..types import Quantity, ValueKind

# 差を作れない理由のコード（文言は画面側）
DIFF_QUANTITY_MISMATCH = "quantity_mismatch"  # 電力と比の差は作らない（設計F §5.4）
DIFF_NO_OVERLAP = "no_overlap"  # 重なる周波数の範囲が無い


def to_db(values: np.ndarray, quantity: Quantity) -> np.ndarray:
    """リニアの値を dB へ。電力（W）は dBm（10·log10(W) + 30）、比は dB（20·log10|x|）。

    複素数は振幅を使う。0 は −inf になる。
    """
    a = np.abs(np.asarray(values)).astype(np.float64)
    with np.errstate(divide="ignore"):
        if quantity == Quantity.POWER:
            return 10.0 * np.log10(a) + 30.0
        return 20.0 * np.log10(a)


def _as_freqs(freqs: np.ndarray) -> np.ndarray:
    f = np.asarray(freqs)
    if not np.issubdtype(f.dtype, np.integer):
        raise TypeError("周波数は int（Hz）の配列で持つ")
    return f.astype(np.int64)


def interp_db(freqs_src: np.ndarray, db_src: np.ndarray, freqs_dst: np.ndarray) -> np.ndarray:
    """dB の値を線形補間する。freqs_dst は freqs_src の範囲内であること。

    重みは整数の周波数の差の比で求める（周波数を float にしない）。freqs_src は昇順。
    """
    fs = _as_freqs(freqs_src)
    fd = _as_freqs(freqs_dst)
    ys = np.asarray(db_src, dtype=np.float64)
    if fs.size == 1:
        return np.full(fd.shape, ys[0])
    i = np.clip(np.searchsorted(fs, fd, side="right") - 1, 0, fs.size - 2)
    span = fs[i + 1] - fs[i]
    w = np.where(span > 0, (fd - fs[i]) / np.where(span > 0, span, 1), 0.0)
    y0 = ys[i]
    y1 = ys[i + 1]
    # 端の点でちょうど重なるとき −inf × 0 の nan を作らないよう、重み 0・1 はそのまま採る
    with np.errstate(invalid="ignore"):
        y = y0 + (y1 - y0) * w
    y = np.where(w == 0.0, y0, y)
    y = np.where(w == 1.0, y1, y)
    return y


@dataclass(frozen=True)
class TraceDifference:
    """差「A − B」の結果（設計F §5.4）。

    error が None でないときは差を作れない（理由のコード）。freqs_hz は A の軸のうち B と重なる点。
    values_db は A − B（dB。A と B の量が電力なら dBm の差、比なら dB の差）。値の種類は常に実数。
    """

    freqs_hz: np.ndarray
    values_db: np.ndarray
    value_kind: ValueKind = ValueKind.REAL
    error: Optional[str] = None


def difference(
    freqs_a: np.ndarray,
    values_a: np.ndarray,
    quantity_a: Quantity,
    freqs_b: np.ndarray,
    values_b: np.ndarray,
    quantity_b: Quantity,
) -> TraceDifference:
    """トレース A − B を dB で求める（設計F §5.4）。

    値はリニア（トレースの持ち方のまま）で受け取る。B を A の軸へ dB で線形補間し、重なる範囲だけ。
    量の種類が違うときは作らず error を返す。複素数と実数の差は実数（振幅の比）になる。
    複素数どうしも dB（振幅の比）で出す（位相の差は設計に無いので出さない）。
    """
    empty = np.zeros(0, dtype=np.int64)
    if quantity_a != quantity_b:
        return TraceDifference(empty, np.zeros(0), error=DIFF_QUANTITY_MISMATCH)
    fa = _as_freqs(freqs_a)
    fb = _as_freqs(freqs_b)
    if fb.size > 1 and np.any(np.diff(fb) < 0):
        order = np.argsort(fb, kind="stable")
        fb = fb[order]
        values_b = np.asarray(values_b)[order]
    lo = max(int(fa.min()), int(fb.min())) if fa.size and fb.size else 0
    hi = min(int(fa.max()), int(fb.max())) if fa.size and fb.size else -1
    mask = (fa >= lo) & (fa <= hi)
    if hi < lo or not np.any(mask):
        return TraceDifference(empty, np.zeros(0), error=DIFF_NO_OVERLAP)
    db_a = to_db(np.asarray(values_a)[mask], quantity_a)
    db_b = interp_db(fb, to_db(values_b, quantity_b), fa[mask])
    with np.errstate(invalid="ignore"):
        diff = db_a - db_b
    return TraceDifference(fa[mask].copy(), diff)


@dataclass(frozen=True)
class Decimated:
    """描くための値（設計F §5.5）。

    pixel: 横の画素の番号（0〜pixels−1）。freqs_hz: その画素で採った点の周波数。
    values: その点の値（入力のまま。複素数なら複素数）。
    """

    pixel: np.ndarray
    freqs_hz: np.ndarray
    values: np.ndarray


def decimate(
    freqs_hz: np.ndarray,
    values: np.ndarray,
    pixels: int,
    display_start_hz: int,
    display_stop_hz: int,
    use_min: bool = False,
) -> Decimated:
    """表示範囲の点を、横の画素ごとに1点へ間引く（設計F §5.5・設計P §8）。

    画素ごとに最大値の点を採る。use_min（最小保持のトレース）なら最小値の点。複素数は振幅で比べる。
    値は dB でもリニアでもよい（どちらでも大小の順は同じ）。1画素に1点以下なら全点をそのまま返す。
    表示範囲の外の点は捨てる。表示範囲の幅が 0（ゼロスパン）なら点の番号で画素へ割り当てる。
    nan の点は、同じ画素に nan でない点があればそちらを採る。
    """
    if not isinstance(pixels, int) or pixels < 1:
        raise ValueError("pixels は 1 以上の int")
    f = _as_freqs(freqs_hz)
    v = np.asarray(values)
    if f.shape != v.shape:
        raise ValueError("周波数と値の長さが違う")
    start = int(display_start_hz)
    stop = int(display_stop_hz)
    if stop > start:
        keep = (f >= start) & (f <= stop)
        f = f[keep]
        v = v[keep]
        px = (f - start) * pixels // (stop - start)
    else:
        idx = np.arange(f.size, dtype=np.int64)
        px = idx * pixels // max(f.size, 1)
    px = np.minimum(px, pixels - 1).astype(np.int64)
    if f.size == 0:
        return Decimated(px, f, v)

    order = np.argsort(px, kind="stable")
    px, f, v = px[order], f[order], v[order]
    key = np.abs(v).astype(np.float64)
    fill = np.inf if use_min else -np.inf
    key = np.where(np.isnan(key), fill, key)

    starts = np.flatnonzero(np.r_[True, px[1:] != px[:-1]])
    if starts.size == px.size:
        return Decimated(px, f, v)
    group = np.repeat(np.arange(starts.size), np.diff(np.r_[starts, px.size]))
    best = (np.minimum if use_min else np.maximum).reduceat(key, starts)
    hit = np.flatnonzero(key == best[group])
    _, first = np.unique(group[hit], return_index=True)
    chosen = hit[first]
    return Decimated(px[chosen], f[chosen], v[chosen])
