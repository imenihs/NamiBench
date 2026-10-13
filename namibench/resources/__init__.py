"""アプリに同梱する絵（Qt の窓のアイコンと Web の favicon が同じものを使う）。

icon/namibench_<大きさ>.png がアイコン（16〜512px のラスター。48px 以上は神奈川沖浪裏をなぞって色を整理した絵、
32px 以下は同じ色と構図を大きく単純にした絵）。Qt は表示する大きさに合うものを選ぶ。
"""
from __future__ import annotations

from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent
ICON_DIR = RESOURCES_DIR / "icon"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
FAVICON_SIZE = 32


def icon_png(size: int) -> Path:
    return ICON_DIR / f"namibench_{size}.png"
