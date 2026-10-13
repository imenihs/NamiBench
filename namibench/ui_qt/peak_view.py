"""探すもの（山・谷）とピークの一覧の画面の中身（設計G §3.8・P §9.7）。Qt と Web が同じものを使う（J §5）。

- 山／谷の選択は画面ごとの覚え書き（設計O §1.1: Qt は画面の覚え書き、Web はブラウザ）。マーカー表の上の切り替えと
  ピークの一覧が同じ選択を使う（2か所で別々に選ばせない: U-09）。既定は山
- 探すボタン・メニューの項目は場所を変えず、選んだほうで名前が変わる
- ピークの一覧の基準は、電力（SA）ではノイズフロア、基準との比（TG・S11）と NF では表示範囲の最大。どちらかは最後に画面へ
  出した掃引の量（状態の display_quantity: J §4.10 と同じ）で決める。電力では一覧の谷を探さない（無効にして理由）
- Qt を import しない（Web の口が画面なしで使う）
"""
from __future__ import annotations

from typing import Optional

from ..core.marker.analysis import DIP, PEAK, POLARITIES
from ..core.trace.formats import is_linear_unit, value_decimals
from ..core.units import level_text
from . import source_view, texts

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


def base_is_max(st: Optional[dict]) -> bool:
    """ピークの一覧の基準が表示範囲の最大か。最後に画面へ出した掃引が電力でなければ最大（基準との比の TG・S11 と NF:
    どちらもノイズフロアが無い。コアの marker.analysis.list_base と同じ決まり: 設計P §9.7）。まだ掃引が無ければ電力
    として扱う。"""
    q = (st or {}).get("display_quantity")
    return q is not None and q != "power"


def list_view(st: Optional[dict], polarity: str) -> dict:
    """ピークの一覧の欄（P §9.7）: 基準・N dB の欄の名前とツールチップ・差の列の名前・探せないときの理由。

    形: {"base": "noise_floor" | "max", "label", "tip", "diff_header", "reason": None | 理由}
    """
    L = texts.LABELS
    # ゼロスパンは横軸が時間なので、周波数のピークの一覧は使わない（設計N §1。理由: U-08）
    zero = ((st or {}).get("sweep") or {}).get("step_hz") == 0
    freq_only = texts.ZERO_SPAN_TEXTS["freq_only"] if zero else None
    if base_is_max(st):
        return {"base": "max", "label": L["from_max"], "tip": L["max_tip_dip" if polarity == DIP else "max_tip_peak"],
                "diff_header": L["col_below_max_db"], "reason": freq_only}
    return {"base": "noise_floor", "label": L["from_floor"], "tip": L["floor_tip"], "diff_header": L["col_height_db"],
            "reason": freq_only or (texts.REASON_TEXTS["dips_on_power"] if polarity == DIP else None)}


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
    # |Γ|・SWR のトレース（設計R §3）は、その値の桁（|Γ| は3桁）。差の列は dB のまま（dB で探すため）
    digits = value_decimals(unit)
    linear = is_linear_unit(unit)
    if base_level is None:
        found = texts.w("peaks_found", n=len(peaks))
    else:
        found = texts.w("peaks_found_max" if by_max else "peaks_found_floor", n=len(peaks),
                        level=_num(float(base_level), digits) if linear else level_text(float(base_level)), unit=unit)
    rows = []
    for p in peaks:
        hz = int(p["freq_hz"])
        # 内部の漏れの可能性の印（設計P §9.6・L §1.4）: 列の文字とツールチップ（漏れでなければ空）
        leak, leak_tip = source_view.peak_leak(p.get("leak"), freq_offset_hz)
        rows.append({
            "freq_hz": hz,
            "freq": f"{(hz + int(freq_offset_hz or 0)) / 1e6:.6f}".rstrip("0").rstrip("."),
            "level": f"{_num(float(p['level']), digits)} {unit}".strip(),
            "diff": _num(float(p["diff_db"]), 1) if p.get("diff_db") is not None else "",
            "leak": leak,
            "leak_tip": leak_tip,
        })
    header = texts.LABELS["col_below_max_db" if by_max else "col_height_db"]
    return {"found": found, "diff_header": header, "rows": rows}
