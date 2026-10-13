"""単位付きの数値の読み書き（設計J §3: `1.2G`・`300k`・`1500` の自由入力）。

周波数は Hz の int に直す。float を通さない（設計A §2.1）ので Decimal で読む。
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Optional

_PREFIX = {"": 0, "k": 3, "K": 3, "m": 6, "M": 6, "g": 9, "G": 9}
_FREQ_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([kKmMgG]?)\s*(?:[hH][zZ])?\s*$")


# 表示に使うマイナスの字形（ハイフンより見間違えにくい: J §4.1）
MINUS = "−"


def minus_sign(text: str) -> str:
    """表示の文字列の負号を「−」にする。"""
    return text.replace("-", MINUS)


def level_text(v: float) -> str:
    """レベル（dB の値）の表示: 0.1 dB まで・負号は「−」。REF レベルの欄と縦軸の目盛で同じ形にする。

    単位を換えた REF（例 0 dBm → 106.99 dBμV）を足し引きした目盛の値は 116.99 や 106.98999… になるので、
    欄と同じ桁に丸めて、欄（117.0）と目盛（116.99）の数字が食い違わないようにする。-0.0 は 0.0 と書く
    """
    s = f"{float(v):.1f}"
    return minus_sign("0.0" if s == "-0.0" else s)


# 縦軸のドラッグで、REF が1目盛（dB/div）の倍数にこの割合（目盛の幅に対して）まで近ければ、その倍数に吸い付く
REF_DRAG_SNAP_DIV = 0.15


def drag_ref_level(ref0: float, frac: float, db_per_div: float, divisions: int = 10) -> float:
    """縦軸をドラッグしたときの REF レベル（設計J §4.1.1。Qt と Web で同じ: ui_web/units.js の dragRefLevel）。

    frac はつかんでからの移動（グラフの高さに対する割合。下へ +）。下へ引くとより上の値が見える（REF が上がる）。
    REF が1目盛（dB/div）の倍数に近いときはその倍数に吸い付き、目盛の数字がきりのよい値にそろう（-5dB ずらしたいのに
    -5.1dB になって使いにくかった: 2026-10-03 ユーザー指摘。吸わせるのは REF そのもの: ユーザー決定）。
    動かす量は刻まない（細かく動かせる。ユーザー決定）。値は欄と目盛と同じ 0.1 dB に丸める。
    """
    ref = ref0 + frac * divisions * db_per_div
    k = round(ref / db_per_div)
    if abs(ref - k * db_per_div) <= REF_DRAG_SNAP_DIV * db_per_div:
        ref = k * db_per_div
    return round(ref, 1) + 0.0


class ParseError(ValueError):
    pass


def parse_freq_hz(text: str, default_exp: int = 6) -> int:
    """周波数の文字列を Hz の int にする。単位を省いたら default_exp（既定は MHz）。

    Hz より細かい端数は偶数丸めで Hz にする（機器の単位への丸めは掃引条件モデルが行い、知らせる）。
    'm' は周波数の欄では mHz ではなく MHz と読む（周波数の欄で mHz を打つ用事が無く、打ち間違いが多いため）。
    """
    m = _FREQ_RE.match(text)
    if not m:
        raise ParseError(text)
    num, prefix = m.group(1), m.group(2)
    exp = _PREFIX[prefix] if prefix else default_exp
    try:
        value = Decimal(num).scaleb(exp)
    except InvalidOperation as e:
        raise ParseError(text) from e
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def format_freq(hz: int, unit_exp: Optional[int] = None) -> str:
    """Hz の int を、桁を落とさずに単位付きの文字列にする（例 1234567890 → '1234.56789 MHz'）。"""
    if unit_exp is None:
        a = abs(hz)
        unit_exp = 9 if a >= 10**10 else 6 if a >= 10**6 else 3 if a >= 10**3 else 0
    unit = {0: "Hz", 3: "kHz", 6: "MHz", 9: "GHz"}[unit_exp]
    d = Decimal(hz).scaleb(-unit_exp)
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s} {unit}"


# ゼロスパンの時間（設計N §1）。横軸・マーカー・XY カーソルの位置はマイクロ秒の整数で持つ（周波数と同じく整数で
# 持ち、マーカーの仕組みをそのまま使う）。欄に打つときの単位は s・ms・μs（省いたら ms）
_TIME_EXP = {"s": 6, "ms": 3, "us": 0, "µs": 0, "μs": 0}
_TIME_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(s|ms|us|µs|μs)?\s*$")
TIME_UNIT = "ms"  # 欄・マーカー表・マウスの位置の読み値の時間の単位（全部同じ単位で桁をそろえる: U-17）


def parse_time_us(text: str, default_unit: str = TIME_UNIT) -> int:
    """時間の文字列をマイクロ秒の int にする（例 '12.5' → 12500、'-1.2 s' → -1200000）。負号は「-」と「−」。"""
    m = _TIME_RE.match(text.replace(MINUS, "-"))
    if not m:
        raise ParseError(text)
    try:
        value = Decimal(m.group(1)).scaleb(_TIME_EXP[m.group(2) or default_unit])
    except InvalidOperation as e:
        raise ParseError(text) from e
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def format_time_us(us: float) -> str:
    """マイクロ秒を ms の小数3桁で書く（例 -5234123 → '-5234.123 ms'）。端数のマイクロ秒は偶数丸め。"""
    v = Decimal(int(round(us))).scaleb(-3)
    return f"{format(v, '.3f')} {TIME_UNIT}"


def time_axis_unit(span_us: float) -> tuple[float, str]:
    """時間軸の目盛の単位（1単位が何マイクロ秒か, 単位の名前）。幅が 2 s 以上は s、2 ms 以上は ms、それより狭いと μs。"""
    a = abs(float(span_us))
    if a >= 2e6:
        return 1e6, "s"
    if a >= 2e3:
        return 1e3, "ms"
    return 1.0, "μs"


def parse_db(text: str) -> float:
    """dB・dBm の欄。単位の文字は読み飛ばす。マイナスは「-」と「−」（U+2212。表示に使う字形）の両方を読む。"""
    t = text.strip().replace(MINUS, "-")
    for suffix in ("dBm", "dB", "dbm", "db"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
            break
    try:
        return float(t)
    except ValueError as e:
        raise ParseError(text) from e
