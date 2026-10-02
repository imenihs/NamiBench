"""配色（設計J §4.4）。使う配色は常に1つ（ユーザー設定）。ライト・ダークはその初期値。

- 色の組は名前の付いた色（配色トークン）。初期値の表は core/report_layout.py（Qt・Web・PNG が同じ値を使う）
- 持つのは「どちらの初期値から始めたか（base）」と「いまの色（colors）」。初期値から変えた色は base の初期値と
  違う色として数える（ライト・ダークのボタンの確認の「変えた色 N 個」と、編集の画面の印）
- 読めない配色を作らせない: 文字と背景のコントラスト比 7 未満・トレースどうしの色の差が小さいときは知らせる
  （止めはしない）。どちらもコードで返し、文言は画面の表（U-22）

Qt を import しない（Web の口も同じものを使う）。
"""
from __future__ import annotations

import itertools
import math
import re
from typing import Optional

from .report_layout import DARK_DEFAULT, LIGHT_DEFAULT

BASES = {"dark": DARK_DEFAULT, "light": LIGHT_DEFAULT}
DEFAULT_BASE = "dark"  # 画面はいままでダークの初期値で描いていた（設計J §4.4）
NAMES = tuple(DARK_DEFAULT)
TRACE_NAMES = ("trace1", "trace2", "trace3", "trace4")
# 重ねたファイル（参照トレース）の色の順。いまのトレース1と同じ色にしないよう 2 から始め、点線で描く
# （Qt と Web で同じ。Web は ui_web/app.js の FILE_COLORS: 試験で一致を確かめる）
FILE_TRACE_ORDER = ("trace2", "trace3", "trace4", "trace1")

# 文字と背景のコントラスト比の下限（U-16）
MIN_CONTRAST = 7.0
# トレースどうしの色の差（CIE76 の ΔE）がこれ未満なら見分けにくいと知らせる。
# 設計に値が無いので、細い線を並べて別の色と分かる目安として置いた（初期値の組の最小は 35.5）
MIN_TRACE_DELTA_E = 20.0

# 画面で実際に「文字」として重なる組（前の色, 後ろの色, コード）。コードは画面の表で文にする。
# 選択中のボタンは強調の色の上に背景の色の文字、動作中のボタンは動作中の色の上に文字の色（ui_qt/theme.py）
TEXT_PAIRS = (
    ("text", "graph_bg"), ("text", "panel_bg"),
    ("text_dim", "graph_bg"), ("text_dim", "panel_bg"),
    ("warn", "graph_bg"), ("warn", "panel_bg"),
    ("marker", "graph_bg"),
    ("graph_bg", "accent"),
    ("text", "running"),
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_color(value) -> Optional[str]:
    """`#rrggbb`（小文字）にする。読めなければ None。`#rgb` も受ける。"""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        v = "#" + "".join(c * 2 for c in v[1:])
    return v.lower() if _HEX.match(v) else None


def _rgb(color: str) -> tuple[float, float, float]:
    h = color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(color: str) -> float:
    """WCAG 2 の相対輝度。"""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 のコントラスト比（1〜21）。"""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _lab(color: str) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(color))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a: str, b: str) -> float:
    """CIE76 の色の差（L*a*b* の距離）。"""
    return math.dist(_lab(a), _lab(b))


class Palette:
    """ユーザー設定の配色（1つ）。"""

    def __init__(self, base: str = DEFAULT_BASE, colors: Optional[dict] = None) -> None:
        self.base = base if base in BASES else DEFAULT_BASE
        self.colors = dict(BASES[self.base])
        for k, v in (colors or {}).items():
            c = normalize_color(v)
            if k in self.colors and c:
                self.colors[k] = c

    @classmethod
    def from_saved(cls, d) -> "Palette":
        """設定に書いた形から作る。読めない色は初期値のまま。"""
        if not isinstance(d, dict):
            return cls()
        return cls(str(d.get("base") or DEFAULT_BASE), d.get("colors") if isinstance(d.get("colors"), dict) else None)

    def to_saved(self) -> dict:
        return {"base": self.base, "colors": dict(self.colors)}

    def changed(self) -> list[str]:
        """初期値（base）から変えた色の名前（並びは NAMES の順）。"""
        d = BASES[self.base]
        return [k for k in NAMES if self.colors[k] != d[k]]

    def warnings(self) -> list[dict]:
        """読みにくい組（止めはしない）。コントラスト比 7 未満の文字と背景・見分けにくいトレースの組。"""
        out: list[dict] = []
        for fg, bg in TEXT_PAIRS:
            r = contrast_ratio(self.colors[fg], self.colors[bg])
            if r < MIN_CONTRAST:
                out.append({"code": "low_contrast", "fg": fg, "bg": bg, "ratio": round(r, 2)})
        for a, b in itertools.combinations(TRACE_NAMES, 2):
            d = delta_e(self.colors[a], self.colors[b])
            if d < MIN_TRACE_DELTA_E:
                out.append({"code": "similar_traces", "a": a, "b": b, "delta_e": round(d, 1)})
        return out

    def state(self) -> dict:
        return {"base": self.base, "colors": dict(self.colors), "changed": self.changed(),
                "warnings": self.warnings(), "defaults": {k: dict(v) for k, v in BASES.items()}}
