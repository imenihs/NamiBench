"""単位付きの数値の読み書き（設計J §3: `1.2G`・`300k`・`1500` の自由入力）。

周波数は Hz の int に直す。float を通さない（設計A §2.1）ので Decimal で読む。
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Optional

_PREFIX = {"": 0, "k": 3, "K": 3, "m": 6, "M": 6, "g": 9, "G": 9}
_FREQ_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([kKmMgG]?)\s*(?:[hH][zZ])?\s*$")


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


def parse_db(text: str) -> float:
    """dB・dBm の欄。単位の文字は読み飛ばす。"""
    t = text.strip()
    for suffix in ("dBm", "dB", "dbm", "db"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
            break
    try:
        return float(t)
    except ValueError as e:
        raise ParseError(text) from e
