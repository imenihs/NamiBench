"""ウォーターフォールと密度表示の色の並び（カラーマップ。設計P §3・§9.1・J §4.4）。

- 既定は viridis（明るさが単調に変わり、色覚の違いでも順序が読める。ライト・ダークのどちらの背景でも使える）
- ほかに cividis・magma・inferno・灰色を選べる（どれも明るさが単調に変わる）
- 表は 33 点の色（0〜1 を 32 等分した位置）。間は線形に補う。viridis・cividis・magma・inferno の値は CC0（公有）で
  配られている表から取った
- Qt と Web は同じ 256 段の表（lut）を使う（Web は /api/v1/info で受け取る: J §5 の「同じ色の範囲・カラーマップ」）

Qt を import しない。
"""
from __future__ import annotations

import numpy as np

DEFAULT = "viridis"

_ANCHORS: dict[str, tuple[str, ...]] = {
    "viridis": (
        "#440154", "#470d60", "#48186a", "#482374", "#472d7b", "#453781",
        "#424086", "#3e4989", "#3b528b", "#375b8d", "#33638d", "#2f6b8e",
        "#2c728e", "#297a8e", "#26828e", "#23898e", "#21918c", "#1f978b",
        "#1f9f88", "#21a685", "#27ad81", "#31b57b", "#3dbc74", "#4cc26c",
        "#5cc863", "#6ece58", "#81d34d", "#95d840", "#aadc32", "#c0df25",
        "#d5e21a", "#eae51a", "#fde725",
    ),
    "cividis": (
        "#00204d", "#00265a", "#002c69", "#00306f", "#06366e", "#1f3c6d",
        "#2d426c", "#38486b", "#414d6b", "#4a536b", "#52596c", "#595f6d",
        "#61646f", "#686a71", "#6f7073", "#767676", "#7d7c78", "#848179",
        "#8b8779", "#938e78", "#9b9477", "#a39a76", "#aca174", "#b4a872",
        "#bcaf6f", "#c5b56c", "#cebc68", "#d7c463", "#e0cb5e", "#e9d358",
        "#f2da50", "#fbe248", "#ffea46",
    ),
    "magma": (
        "#000004", "#030312", "#0a0822", "#130d34", "#1d1147", "#29115a",
        "#36106b", "#440f76", "#51127c", "#5d177f", "#6a1c81", "#762181",
        "#832681", "#902a81", "#9c2e7f", "#aa337d", "#b73779", "#c23b75",
        "#cf4070", "#db476a", "#e55064", "#ee5b5e", "#f4695c", "#f9785d",
        "#fb8761", "#fd9668", "#fea571", "#feb47b", "#fec287", "#fed194",
        "#fde0a1", "#fceeb0", "#fcfdbf",
    ),
    "inferno": (
        "#000004", "#040312", "#0b0724", "#150b37", "#210c4a", "#2f0a5b",
        "#3d0965", "#4a0c6b", "#57106e", "#64156e", "#71196e", "#7d1e6d",
        "#8a226a", "#972766", "#a32c61", "#b0315b", "#bc3754", "#c63d4d",
        "#d04545", "#da4e3c", "#e35933", "#eb6429", "#f1711f", "#f67e14",
        "#f98c0a", "#fb9b06", "#fcaa0f", "#fbba1f", "#f9c932", "#f5d949",
        "#f2e865", "#f3f586", "#fcffa4",
    ),
    "gray": ("#000000", "#ffffff"),
}

# 選べる並び（画面の一覧の順）
CHOICES = ("viridis", "cividis", "magma", "inferno", "gray")
LUT_SIZE = 256


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def lut(name: str) -> np.ndarray:
    """256 段の色の表 [256, 3]（uint8）。知らない名前は既定の並び。"""
    anchors = np.array([_rgb(h) for h in _ANCHORS.get(name, _ANCHORS[DEFAULT])], dtype=np.float64)
    pos = np.linspace(0.0, 1.0, len(anchors))
    x = np.linspace(0.0, 1.0, LUT_SIZE)
    out = np.stack([np.interp(x, pos, anchors[:, c]) for c in range(3)], axis=1)
    return np.round(out).astype(np.uint8)


def luts_for_web() -> dict[str, list[int]]:
    """Web へ渡す形（並びごとに r, g, b, r, g, b, … の 768 個の整数）。Qt と同じ表（U-09）。"""
    return {name: lut(name).reshape(-1).astype(int).tolist() for name in CHOICES}


def valid(name) -> bool:
    return name in CHOICES
