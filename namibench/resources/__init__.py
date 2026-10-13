"""アプリに同梱する絵（Qt の窓のアイコンと Web の favicon が同じものを使う）。

namibench.svg が元の絵。namibench.png は同じ絵を 256×256 で描いたもの（SVG を読めない Qt で使う）。
"""
from __future__ import annotations

from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent
ICON_SVG = RESOURCES_DIR / "namibench.svg"
ICON_PNG = RESOURCES_DIR / "namibench.png"
