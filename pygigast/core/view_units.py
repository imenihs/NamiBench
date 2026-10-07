"""表示の単位・外部補正・周波数オフセット（設計O・J §4.5 の「振幅の詳細」・旧 dB Value と OffSet）。

測った値（トレースはリニアの W、周波数は機器の Hz）は変えない。画面・マーカー・CSV・PNG・REF に出すときだけ、
ここで決めた分だけずらす。機器へ送るものは何も変わらない。

- 単位: dBm／dBμV／dBmV。50Ω で dBμV = dBm + 106.99、dBmV = dBm + 46.99（20·log10(√(1mW·50Ω)/1μV) など）
- 外部補正（dB）: 外付けのアッテネータ・アンプの分。電力の読み値に足す（減衰 10dB を戻すなら +10）
- 単位を換えるのは dBm で測る機器のトレースだけ。校正していない機器の「dB（未校正）」は単位を換えずに外部補正だけ足す
- 比（ノーマライズ中の dB・差）は単位も外部補正も当てない（被測定物の通過の比で、絶対のレベルではないため）
- 周波数オフセット: 表示する周波数 = 機器の周波数 + オフセット（負も可。変換器の観測用）

Qt を import しない。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

# 単位のコード → 画面に出す名前
LEVEL_UNITS = {"dBm": "dBm", "dBuV": "dBμV", "dBmV": "dBmV"}
# 50Ω で 0dBm を各単位で言った値。0dBm = √(1e-3 W × 50Ω) = 0.2236V
R_OHM = 50.0
_V0 = math.sqrt(1e-3 * R_OHM)
OFFSET_FROM_DBM = {"dBm": 0.0, "dBuV": 20.0 * math.log10(_V0 / 1e-6), "dBmV": 20.0 * math.log10(_V0 / 1e-3)}

# 外部補正と周波数オフセットの上限（打ち間違いで画面が使えなくなるのを防ぐ。設計に値は無い）
EXT_DB_LIMIT = 200.0
FREQ_OFFSET_LIMIT_HZ = 1_000_000_000_000  # 1THz

DRAW_STYLES = ("line", "fill")
# 表示の組み合わせ（設計P §2）: スペクトルのみ／スペクトル＋ウォーターフォール／スペクトル＋密度
LAYOUTS = ("spectrum", "waterfall", "density")

# ウォーターフォールの行数の上限（画面に持つ行: 設計P §3・§9.2 の「例 2000行」）と、密度表示の「N が半分になるまでの
# 回数」（設計P §9.3）の選択肢。値の並びは設計に無い（2000行と、数十回の目安から決めた）
WF_ROWS_CHOICES = (500, 1000, 2000, 4000)
DENSITY_HALF_CHOICES = (5, 10, 20, 50, 100, 200)
# ウォーターフォールの色の範囲（手で決めるときの下限・上限。表示の単位）。幅は 1dB 以上。打ち間違いの上限は ±400dB
WF_LEVEL_LIMIT = 400.0
WF_MIN_SPAN_DB = 1.0

# function_lines: マーカーの機能（N dB 帯域幅・しきい値との交点）の結果をグラフに薄い線で出すか（G §3.6）
# wf_*: ウォーターフォール（設計P §3・§9.2）。wf_trace は行にするトレースの番号、wf_color_auto は色の範囲を自動で追うか、
# wf_color_lo・wf_color_hi は手で決めた色の範囲（表示の単位）、wf_rows は画面に持つ行数の上限
# density_half: 密度表示で重みが半分になるまでの掃引の回数（設計P §9.3）
# bandplan_shown・bookmarks_shown: バンドプランの帯・ブックマークの印を出すか（設計P §6）
DEFAULTS = {"level_unit": "dBm", "ext_db": 0.0, "freq_offset_hz": 0, "draw_style": "line", "layout": "spectrum",
            "function_lines": True, "wf_trace": 1, "wf_color_auto": True, "wf_color_lo": -110.0, "wf_color_hi": -20.0,
            "wf_rows": 2000, "density_half": 20, "bandplan_shown": True, "bookmarks_shown": True}
# float で持つもの（戻すときに float にする）
FLOAT_KEYS = ("ext_db", "wf_color_lo", "wf_color_hi")


def level_shift(base_unit: Optional[str], opts: dict) -> tuple[float, Optional[str]]:
    """測った値の単位（base_unit: "dBm"・"dB"・"dB（未校正）"など）から、(足す dB, 出す単位の名前)。

    比（"dB"）は 0 と "dB"。dBm は単位の換算＋外部補正。そのほかの電力（未校正）は外部補正だけ。
    """
    if base_unit is None:
        return 0.0, None
    if base_unit == "dB":
        return 0.0, "dB"
    ext = float(opts.get("ext_db") or 0.0)
    if base_unit == "dBm":
        code = opts.get("level_unit") or "dBm"
        return OFFSET_FROM_DBM.get(code, 0.0) + ext, LEVEL_UNITS.get(code, "dBm")
    return ext, base_unit


def shift_values(values_db: np.ndarray, shift: float) -> np.ndarray:
    if not shift:
        return values_db
    return (np.asarray(values_db, dtype=np.float64) + shift).astype(np.asarray(values_db).dtype)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_level(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
            and abs(v) <= WF_LEVEL_LIMIT)


def check_options(level_unit=None, ext_db=None, freq_offset_hz=None, draw_style=None, layout=None,
                  function_lines=None, wf_trace=None, wf_color_auto=None, wf_color_lo=None, wf_color_hi=None,
                  wf_rows=None, density_half=None, bandplan_shown=None, bookmarks_shown=None) -> Optional[str]:
    """命令の値を確かめる。だめなら欄の名前を返す（None は全部よい）。"""
    for name, v in (("function_lines", function_lines), ("wf_color_auto", wf_color_auto),
                    ("bandplan_shown", bandplan_shown), ("bookmarks_shown", bookmarks_shown)):
        if v is not None and not isinstance(v, bool):
            return name
    if wf_trace is not None and not (_is_int(wf_trace) and 1 <= wf_trace <= 4):
        return "wf_trace"
    if wf_rows is not None and not (_is_int(wf_rows) and wf_rows in WF_ROWS_CHOICES):
        return "wf_rows"
    if density_half is not None and not (_is_int(density_half) and density_half in DENSITY_HALF_CHOICES):
        return "density_half"
    for name, v in (("wf_color_lo", wf_color_lo), ("wf_color_hi", wf_color_hi)):
        if v is not None and not _is_level(v):
            return name
    if level_unit is not None and level_unit not in LEVEL_UNITS:
        return "level_unit"
    if ext_db is not None and (isinstance(ext_db, bool) or not isinstance(ext_db, (int, float))
                               or not math.isfinite(ext_db) or abs(ext_db) > EXT_DB_LIMIT):
        return "ext_db"
    if freq_offset_hz is not None and (isinstance(freq_offset_hz, bool) or not isinstance(freq_offset_hz, int)
                                       or abs(freq_offset_hz) > FREQ_OFFSET_LIMIT_HZ):
        return "freq_offset_hz"
    if draw_style is not None and draw_style not in DRAW_STYLES:
        return "draw_style"
    if layout is not None and layout not in LAYOUTS:
        return "layout"
    return None


def restore(d) -> dict:
    """保存した形から戻す。読めない値は初期値。"""
    out = dict(DEFAULTS)
    if isinstance(d, dict):
        for k in DEFAULTS:
            v = d.get(k)
            if v is not None and check_options(**{k: v}) is None:
                out[k] = float(v) if k in FLOAT_KEYS else v
    if out["wf_color_hi"] - out["wf_color_lo"] < WF_MIN_SPAN_DB:
        out["wf_color_lo"], out["wf_color_hi"] = DEFAULTS["wf_color_lo"], DEFAULTS["wf_color_hi"]
    return out
