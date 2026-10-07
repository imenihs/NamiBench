"""探すもの（山・谷）とピークの一覧の画面の中身（設計G §3.8・P §9.7）。Qt と Web が同じものを使う（J §5）。

- 山／谷の選択は画面ごとの覚え書き（設計O §1.1: Qt は画面の覚え書き、Web はブラウザ）。マーカー表の上の切り替えと
  ピークの一覧が同じ選択を使う（2か所で別々に選ばせない: U-09）。既定は山
- 探すボタン・メニューの項目は場所を変えず、選んだほうで名前が変わる
- ピークの一覧の基準は、電力（SA）ではノイズフロア、基準との比（TG・S11）では表示範囲の最大。どちらかは最後に画面へ
  出した掃引の量（状態の display_quantity: J §4.10 と同じ）で決める。電力では一覧の谷を探さない（無効にして理由）
- Qt を import しない（Web の口が画面なしで使う）
"""
from __future__ import annotations

from typing import Optional

from ..core.marker.analysis import DIP, PEAK, POLARITIES
from ..core.units import level_text
from . import texts

# 名前が選択で変わるボタン・メニューの項目（マーカー表の上の並び。この順で並べる: Qt と Web で同じ）
SEARCH_CODES = ("marker_peak", "marker_peak_left", "marker_peak_right", "marker_peak_lower", "marker_add")
# 覚え書きの名前（Qt の画面の覚え書き。Web はブラウザの pygigast.polarity）
PREF_KEY = "marker_polarity"


def polarity_of(value) -> str:
    """覚え書きから読んだ値を山・谷にする（読めない値・無いときは山）。"""
    return value if value in POLARITIES else PEAK


def other(polarity: str) -> str:
    """「山／谷を切り替える」の行き先。"""
    return DIP if polarity == PEAK else PEAK


def menu_label(code: str, polarity: str) -> str:
    """メニューの項目の名前。谷のときは探す項目の名前を谷の語にする（G §3.8）。"""
    if polarity == DIP and code in texts.DIP_MENU_ITEMS:
        return texts.DIP_MENU_ITEMS[code]
    return texts.MENU_ITEMS[code]


def ratio_shown(st: Optional[dict]) -> bool:
    """最後に画面へ出した掃引が基準との比か（TG・S11 で基準を使っている）。まだ掃引が無ければ電力として扱う。"""
    return (st or {}).get("display_quantity") == "ratio"


def list_view(st: Optional[dict], polarity: str) -> dict:
    """ピークの一覧の欄（P §9.7）: 基準・N dB の欄の名前とツールチップ・差の列の名前・探せないときの理由。

    形: {"base": "noise_floor" | "max", "label", "tip", "diff_header", "reason": None | 理由}
    """
    L = texts.LABELS
    if ratio_shown(st):
        return {"base": "max", "label": L["from_max"], "tip": L["max_tip_dip" if polarity == DIP else "max_tip_peak"],
                "diff_header": L["col_below_max_db"], "reason": None}
    return {"base": "noise_floor", "label": L["from_floor"], "tip": L["floor_tip"], "diff_header": L["col_height_db"],
            "reason": texts.REASON_TEXTS["dips_on_power"] if polarity == DIP else None}


def list_views(st: Optional[dict]) -> dict:
    """Web へ状態と一緒に送る形（選択はブラウザが持つので両方）。"""
    return {p: list_view(st, p) for p in POLARITIES}


def _num(v: float, digits: int) -> str:
    s = f"{v:.{digits}f}"
    return "0." + "0" * digits if s.lstrip("-").strip("0.") == "" else s  # −0.0 を出さない


def result_view(r: dict, freq_offset_hz: int = 0) -> dict:
    """ピークの一覧の結果（命令 peak_list の戻り値）の文字: 見つけた数の行・差の列の名前・行。

    行: {"freq_hz", "freq"（MHz。周波数オフセット込み: 設計O）, "level"（単位付き）, "diff"（基準からの差 dB）}
    見つけた数の行: 「1 個・ノイズフロア −95.2 dBm」／「1 個・最大 −0.4 dB」（P §9.7）
    """
    peaks = r.get("peaks") or []
    unit = r.get("unit") or ""
    base_level = r.get("base_level")
    by_max = r.get("base") == "max"
    if base_level is None:
        found = texts.w("peaks_found", n=len(peaks))
    else:
        found = texts.w("peaks_found_max" if by_max else "peaks_found_floor", n=len(peaks),
                        level=level_text(float(base_level)), unit=unit)
    rows = []
    for p in peaks:
        hz = int(p["freq_hz"])
        rows.append({
            "freq_hz": hz,
            "freq": f"{(hz + int(freq_offset_hz or 0)) / 1e6:.6f}".rstrip("0").rstrip("."),
            "level": f"{_num(float(p['level']), 2)} {unit}".strip(),
            "diff": _num(float(p["diff_db"]), 1) if p.get("diff_db") is not None else "",
            "leak": leak_text(p.get("leak")),
        })
    header = texts.LABELS["col_below_max_db" if by_max else "col_height_db"]
    # 内部の漏れの可能性の印（P §9.6・設計L §1.4）。印のある行が1つも無ければ列を出さない（空の列を出さない: U-15）
    leak_header = texts.LABELS.get("col_leak", "") if any(r["leak"] for r in rows) else ""
    return {"found": found, "diff_header": header, "rows": rows, "leak_header": leak_header}


def leak_text(mark) -> str:
    """コアの「内部の漏れの可能性の印」（_peak_leak_mark: 設計L §1.4 で決める）を一覧の文字にする。印が無ければ空。"""
    if not mark:
        return ""
    if isinstance(mark, dict) and mark.get("text"):
        return str(mark["text"])
    return texts.LABELS.get("leak_mark", "")
