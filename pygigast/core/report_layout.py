"""画像出力（設計I）の配置・帯・マーカー表の規則。Qt と Web が同じものを使う（I §3.1・§3.5・J §5）。

I §3.1 の「配置（帯の項目・順・文字の大きさ）はコアのデータとして1つ持ち、両方の描画がそれを使う」を
実装したもの。Qt（ui_qt/png_export.py）は直接呼び、Web（ui_web/png.js）は Web の口の
`POST /api/v1/png_layout` でここの結果を受け取る。

- ここにあるのは「データ → 行の並び・配置の数」だけ。文字の幅を測って折り返すのと、線や文字を描くのは描く側
  （書体の幅は描く部品でしか測れないため）
- 帯とマーカー表の文字（「校正データ: なし」など）は画像の配置の一部（I §3.2）なのでここに置く。
  画面の文言（ui_qt/texts.py）とは別に、画像にだけ焼き込む語
- Qt を import しない（Web の口が画面なしで使う）

データ（build_data の戻り値・png_export.render_png の data）の形::

    {
      "measured_at": "2026-10-01T09:15:30+09:00",   # ISO 8601。時差なしならこの PC の時差とみなす
      "device": {"model": "GigaSt v5", "serial": "..."},
      "app_version": "0.1.0",
      "mode": "スペクトラムアナライザ",
      "conditions": {"start_hz", "stop_hz", "rbw_hz", "points", "step_hz",
                     "image_reject" ("accurate"・"fast"・"off"。実際に使ったもの。None なら出さない),
                     "ref_level", "db_per_div", "unit",
                     "x_axis" (ゼロスパンだけ: {"kind": "time", "x0", "x1"（μs）, "factor", "unit", "length_s",
                               "point_time_s"}。横軸・トレース・マーカーの位置は μs: 設計N §1)},
      "calibration": {"source": "...", "at": "..."} | None,      # None は「校正データ: なし」
      "normalization": {"name", "at", "applied", "before_power_cycle", "reason"} | None,
      "traces": [{"number", "action", "average", "count", "count_target", "label", "color", "visible",
                  "line",                                    # 線（TRACE_LINE_STYLES の名前・参照トレースは reference）
                  "start_hz", "step_hz", "points", "values_db", "flags"}],   # 値は Qt が描くときだけ
      "markers": [{"name", "kind", "trace", "freq_hz", "value", "ref", "delta_freq_hz", "delta_value",
                   "child", "note"}],
      "warnings": ["..."],
      "note": "",
    }
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Iterable, Optional, Union

from . import zero_span as zs
from .trace import formats
from .units import format_freq, level_text, minus_sign, time_axis_unit

META_KEY = "pygigast"

# 条件の行のイメージ除去（設計F §7.3。画像にだけ焼き込む語: 画面の選択肢と同じ名前）
IMAGE_REJECT_WORDS = {"accurate": "正確", "fast": "スピード", "off": "なし"}
META_FORMAT = 1
DIVISIONS = 10
MIN_SIZE = (320, 240)

# 配色の初期値（設計J §4.4 の表）。Qt の画面・PNG と Web の画面・PNG が同じ値を使う。
# J §4.4 は「マーカー」の色を名前だけ挙げて値を決めていないので、文字の色と同じにしている（I §3.5）
DARK_DEFAULT: dict[str, str] = {
    "graph_bg": "#15181d",
    "panel_bg": "#1e232b",
    "text": "#e6e9ee",
    "text_dim": "#aab3c0",
    "accent": "#56a7ff",
    "warn": "#ffcc66",
    "grid": "#2a313c",
    "axis": "#3a4452",
    "trace1": "#ffd24a",
    "trace2": "#4fd1c5",
    "trace3": "#ff7aa8",
    "trace4": "#9fe870",
    "zero_db": "#7fb3ff",
    "running": "#18572e",
    "marker": "#e6e9ee",
}
LIGHT_DEFAULT: dict[str, str] = {
    "graph_bg": "#ffffff",
    "panel_bg": "#f4f6f9",
    "text": "#1b1f24",
    "text_dim": "#4a5562",
    "accent": "#1d58a2",
    "warn": "#6f4600",
    "grid": "#d5dbe3",
    "axis": "#5a6573",
    "trace1": "#d18a00",
    "trace2": "#00897b",
    "trace3": "#c2185b",
    "trace4": "#2e7d32",
    "zero_db": "#1f5fae",
    "running": "#52bf57",
    "marker": "#1b1f24",
}

# トレースの線の描き方（設計F §9）。Qt の画面・PNG と Web の画面・PNG が同じ値を使う（Web は /api/v1/info と
# png_layout で受け取る）。名前は core/trace/trace.TraceLine のコード。
# - width: 描く所の基準の幅に掛ける倍率。基準は Qt の画面 1 画素・Web の画面 1.25 画素（今までの線）・PNG は
#   文字の大きさの 1/12（下限 1 画素）。実線（倍率 1）が今までの線
# - dash: 線と隙間の長さの並び（その線の幅に対する倍率。端は切りっぱなし）。None は切れ目なし
# - z: 重なりの段。大きいほど上に描く（太線 > 実線・破線・点線 > 細線）。同じ段は番号の大きいトレースが上（今までと同じ）
# 1 画素より細い線（細線）は、ぼかして描く（Web の canvas・PNG）か、1 画素の線を幅の割合だけ薄く描く（Qt の画面:
# ui_qt/plot_layers.line_pen）。どちらでもなく描くと 1 画素の線になり、実線と同じに見える
TRACE_LINE_STYLES: dict[str, dict] = {
    "thin": {"width": 0.5, "dash": None, "z": 0},
    "solid": {"width": 1.0, "dash": None, "z": 1},
    "thick": {"width": 2.0, "dash": None, "z": 2},
    "dash": {"width": 1.0, "dash": [8.0, 4.0], "z": 1},
    "dot": {"width": 1.0, "dash": [2.0, 3.0], "z": 1},
}
TRACE_LINE_DEFAULT = "solid"
# 参照トレース（開いたファイル）の線。選べない（設計F §9: 今のまま点線）。測っているトレースより下に描く
REFERENCE_LINE = "reference"
REFERENCE_LINE_STYLE = {"width": 1.0, "dash": [4.0, 2.0], "z": -1}
LINE_STYLES: dict[str, dict] = {**TRACE_LINE_STYLES, REFERENCE_LINE: REFERENCE_LINE_STYLE}


def line_style(code: Optional[str]) -> dict:
    """線の名前から描き方。読めない名前は実線。"""
    return LINE_STYLES.get(code or "", TRACE_LINE_STYLES[TRACE_LINE_DEFAULT])


def stroke(code: Optional[str], base_px: float) -> dict:
    """描く線の幅と切れ目（画素）。base_px は描く所の基準の幅（TRACE_LINE_STYLES の説明）。
    Web の graph.js の lineStroke と同じ計算。"""
    s = line_style(code)
    w = base_px * s["width"]
    return {"width": w, "dash": [v * w for v in s["dash"]] if s["dash"] else None}


def png_line_base(px: float) -> float:
    """PNG のトレースの基準の線の幅（画素）: 文字の大きさの 1/12、下限 1 画素（Qt と Web の PNG で同じ）。"""
    return max(1.0, px / 12.0)


def legend_stroke(code: Optional[str], base_px: float) -> dict:
    """PNG の凡例の線の見本: トレースの線の倍の太さで、切れ目の長さはトレースの線と同じ（見本は短いので、太さに
    合わせて伸ばすと切れ目が見本に入らない）。"""
    s = stroke(code, base_px)
    return {"width": s["width"] * LAYOUT["legend_line"], "dash": s["dash"]}


def draw_order_key(code: Optional[str], number: Any) -> tuple:
    """重ねて描く順（小さいほど先 = 下）: 線の段、同じ段なら番号の小さい順。参照トレースは一番下。"""
    try:
        n = int(number)
    except (TypeError, ValueError):
        n = 0
    return (line_style(code)["z"], n)


# 配置（I §3.1: 帯の項目・順・文字の大きさ）。文字の大きさは画像の大きさに比例させる（1600×1000 で 16 画素）。
# 長さはどれも文字の大きさ（px）に対する倍率
LAYOUT = {
    "base_px_at_1600x1000": 16.0,
    "min_px": 11.0,
    "line_height": 1.4,       # 1行の高さ。Qt と Web で同じ行の数が収まるよう、書体の測り方に頼らず決める
    "margin": 1.4,            # 画像の外周の余白
    "gap": 0.84,              # 区画どうしの間
    "item_gap": 1.6,          # 帯の項目どうしの間（全角1字の幅に対する倍率）
    "table_max_ratio": 0.30,  # マーカー表に使ってよい高さ（画像の高さに対する割合）
    "table_two_columns_from": 7,  # この行数を超えたら2列に（I §3.2）
    "legend_line": 2.0,       # 凡例の線の見本の太さ（そのトレースの線の幅に対する倍率）
    # 凡例の1項目の見本の場所（全角1字の幅に対する倍率。見本の線はその 0.8）。破線・点線の切れ目が見本に2つ以上入る長さ
    "legend_sample": 2.0,
    # 帯の段（I §3.2 の 1〜3・6・5 の順）。マーカー表（4）はグラフのすぐ下に置く
    "band_rows": ("when", "conditions", "correction", "warnings", "note"),
}

JP_FONT_FALLBACKS = ("Yu Gothic UI", "Meiryo UI", "Meiryo", "Noto Sans CJK JP", "Noto Sans JP", "Hiragino Sans")

MARKER_COLUMNS = ("番号", "トレース", "周波数 (MHz)", "値", "基準", "Δ周波数 (MHz)", "Δ値 (dB)", "")
# 数える列は右寄せ（U-17）
MARKER_RIGHT = (False, True, True, True, False, True, True, False)
X_AXIS_TITLE = "周波数 (MHz)"
# ゼロスパンの横軸（設計N §1。機器は時刻を返さないので推定）とマーカー表の位置の列
X_AXIS_TITLE_TIME = "時間（推定） ({unit})"
MARKER_COLUMNS_TIME = ("番号", "トレース", "時間 (ms)", "値", "基準", "Δ時間 (ms)", "Δ値 (dB)", "")
X_AXIS_TITLE_OFFSET = "周波数 (MHz)・換算後（測った周波数に {off} を足した値）"


# ---------------------------------------------------------------- 配色

# PNG の配色の選び方（I §3.6）: "screen" = ユーザー指定（画面で使っている配色そのもの。利用者が変えた色を含む）、
# "light" / "dark" = その初期値。既定はユーザー指定: 画面で見ている色のまま残す（違う色で残すのは選んだときだけ）。
# Web（ui_web/app_settings.js の PNG_DEFAULTS・pngColors）も同じ選び方と既定を持つ
PNG_PALETTE_CHOICES = ("screen", "light", "dark")
PNG_PALETTE_DEFAULT = "screen"


def png_palette(choice: Optional[str], user_colors: Optional[dict]) -> dict[str, str]:
    """PNG の配色の選び方（PNG_PALETTE_CHOICES）から、描く配色を返す。読めない選び方は既定（ユーザー指定）。"""
    if choice not in PNG_PALETTE_CHOICES:
        choice = PNG_PALETTE_DEFAULT
    if choice == "screen":
        return resolve_palette(dict(user_colors or {}))
    return resolve_palette(choice)


def resolve_palette(palette: Union[str, dict, None]) -> dict[str, str]:
    """"light" / "dark" / 色の組（ユーザー設定）から、全部の名前が揃った配色を返す。

    ユーザー設定に欠けた名前があれば、ライトの初期値で埋める（画像が描けなくなるのを避ける）。
    """
    if palette is None or palette == "light":
        return dict(LIGHT_DEFAULT)
    if palette == "dark":
        return dict(DARK_DEFAULT)
    if isinstance(palette, dict):
        out = dict(LIGHT_DEFAULT)
        out.update({k: v for k, v in palette.items() if isinstance(v, str)})
        return out
    raise ValueError(f"palette は 'light'・'dark'・dict のどれか: {palette!r}")


# ---------------------------------------------------------------- 大きさ

def font_px(width: int, height: int) -> float:
    """文字の大きさ（画素）。画像の大きさに比例させ、下限を持つ。"""
    px = LAYOUT["base_px_at_1600x1000"] * min(width / 1600.0, height / 1000.0)
    return max(LAYOUT["min_px"], px)


def line_px(px: float) -> float:
    return px * LAYOUT["line_height"]


def check_size(width: int, height: int) -> None:
    if width < MIN_SIZE[0] or height < MIN_SIZE[1]:
        raise ValueError(f"画像が小さすぎる: {width}x{height}")


# ---------------------------------------------------------------- 帯の文字

def format_measured_at(value: Union[str, _dt.datetime, None]) -> str:
    """`2026-10-01 09:15:30 +09:00` の形（I §3.2）。時差の無い値はこの PC の時差とみなす。"""
    if value is None:
        dt = _dt.datetime.now().astimezone()
    elif isinstance(value, _dt.datetime):
        dt = value
    else:
        dt = _dt.datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    off = dt.strftime("%z")  # +0900
    return dt.strftime("%Y-%m-%d %H:%M:%S ") + f"{off[:3]}:{off[3:]}"


def _hz(v: Any) -> str:
    return format_freq(int(round(float(v))))


def _num(v: float) -> str:
    return f"{float(v):g}"


def band_rows(data: dict, note: str = "", app_version: str = "") -> list[tuple[str, list[str]]]:
    """帯の段ごとの項目（(色の名前, 項目の並び)）。空の段は出さない（U-15）。"""
    c = data.get("conditions") or {}
    unit = c.get("unit", "dBm")
    rows: dict[str, tuple[str, list[str]]] = {}

    dev = data.get("device") or {}
    dev_text = " ".join(str(x) for x in (dev.get("model"), f"S/N {dev['serial']}" if dev.get("serial") else None) if x)
    when = [format_measured_at(data.get("measured_at"))]
    if dev_text:
        when.append(dev_text)
    when.append(f"pyGigaSt {data.get('app_version') or app_version}".strip())
    rows["when"] = ("text", when)

    cond: list[str] = []
    if data.get("mode"):
        cond.append(str(data["mode"]))
    off = int(c.get("freq_offset_hz") or 0)
    if c.get("start_hz") is not None and c.get("stop_hz") is not None:
        # 周波数オフセットがあれば換算後の周波数（画面の横軸と同じ: 設計O）
        start, stop = float(c["start_hz"]) + off, float(c["stop_hz"]) + off
        cond += [f"Start {_hz(start)}", f"Stop {_hz(stop)}",
                 f"Center {_hz((start + stop) / 2)}", f"Span {_hz(stop - start)}"]
    if off:
        cond.append(f"周波数オフセット {'+' if off > 0 else '−'}{_hz(abs(off))}（周波数は測った周波数に足した値）")
    if c.get("rbw_hz") is not None:
        cond.append(f"RBW {_hz(c['rbw_hz'])}")
    if c.get("points") is not None:
        s = f"{int(c['points'])} 点"
        if c.get("step_hz") is not None:
            s += f"（Step {_hz(c['step_hz'])}）"
        cond.append(s)
    if c.get("image_reject") is not None:
        cond.append("イメージ除去 " + IMAGE_REJECT_WORDS.get(c["image_reject"], str(c["image_reject"])))
    xa = c.get("x_axis") or {}
    if xa.get("kind") == "time":
        # ゼロスパンの表示する長さと、時間の推定に使った1点あたりの時間（設計N §1）
        length = float(xa.get("length_s") or 0)
        shown = "掃引1回" if not length else f"表示 {length:g} 秒（掃引の間は空白）"
        cond.append(f"ゼロスパン {shown}・1点 {float(xa.get('point_time_s') or 0) * 1e3:.3f} ms（推定）")
    if formats.is_linear_unit(unit) and c.get("db_per_div") is not None:
        # |Γ|・SWR の縦軸は下端が決まっていて REF を持たない（設計R §3）: 範囲と1目盛を書く
        div = float(c["db_per_div"])
        bottom = formats.LINEAR_AXES[unit]["bottom"]
        cond.append(f"{unit} {_num(bottom)}〜{_num(bottom + DIVISIONS * div)}（{_num(div)} {unit}/div）")
    else:
        if c.get("ref_level") is not None:
            cond.append(f"REF {level_text(c['ref_level'])} {unit}")
        if c.get("db_per_div") is not None:
            cond.append(f"{_num(c['db_per_div'])} dB/div")
    rows["conditions"] = ("text", cond)

    corr: list[str] = []
    cal = data.get("calibration")
    if cal:
        s = f"校正データ: {cal.get('source', '')}"
        if cal.get("at"):
            s += f"（{cal['at']}）"
        corr.append(s)
    else:
        # 無いこと自体が情報（U-15）
        corr.append("校正データ: なし")
    nz = data.get("normalization")
    if nz:
        s = f"ノーマライズ: {nz.get('name', '')}"
        if nz.get("at"):
            s += f"（{nz['at']}）"
        if nz.get("before_power_cycle"):
            s += " 電源の入れ直し前の基準"
        if nz.get("applied") is False:
            s += " 当てていない"
            if nz.get("reason"):
                s += f": {nz['reason']}"
        corr.append(s)
    ext = float(c.get("ext_db") or 0.0)
    if ext:
        corr.append(f"外部補正 {ext:+g} dB")
    rows["correction"] = ("text", corr)

    warns = [f"注意: {w}" for w in (data.get("warnings") or []) if w]
    rows["warnings"] = ("warn", warns)
    memo = (note or data.get("note") or "").strip()
    rows["note"] = ("text", [f"メモ: {memo}"] if memo else [])
    return [rows[k] for k in LAYOUT["band_rows"] if rows[k][1]]


def legend_text(tr: dict) -> str:
    """凡例の1項目: トレースの番号・動作・平均の種類・回数（U-14: 動作は凡例の隣）。"""
    parts = [str(tr.get("number", "")), str(tr.get("action") or "")]
    if tr.get("average"):
        parts.append(str(tr["average"]))
    if tr.get("count") is not None:
        cnt = str(tr["count"])
        if tr.get("count_target"):
            cnt += f"/{tr['count_target']}"
        parts.append(cnt)
    if tr.get("label"):
        parts.append(str(tr["label"]))
    return " ".join(p for p in parts if p)


def freq_decimals(values_hz: Iterable[Optional[float]]) -> int:
    """MHz で出すときの小数の桁。全部の値が桁を落とさずに出る最小の桁数（最大 6 = 1Hz）。"""
    vals = [float(v) for v in values_hz if v is not None]
    for d in range(7):
        if all(abs(round(v / 1e6, d) - v / 1e6) < 1e-9 for v in vals):
            return d
    return 6


# ---------------------------------------------------------------- マーカー表

def marker_table_rows(data: dict) -> list[list[str]]:
    """マーカー表の行の文字（列は MARKER_COLUMNS）。コピー用の表と同じ中身。"""
    ms = data.get("markers") or []
    time = is_time_axis(data)
    off = 0 if time else int((data.get("conditions") or {}).get("freq_offset_hz") or 0)
    ms = [{**m, "freq_hz": m["freq_hz"] + off} if m.get("freq_hz") is not None and off else m for m in ms]
    freqs = [m.get("freq_hz") for m in ms] + [m.get("delta_freq_hz") for m in ms]
    d = freq_decimals(f for f in freqs if f is not None)
    # 位置の列: 周波数は MHz、ゼロスパンの時間は ms の小数3桁（画面のマーカー表と同じ: 設計N §1）
    scale, d = (1e3, 3) if time else (1e6, d)
    unit = (data.get("conditions") or {}).get("unit", "dBm")
    out = []
    for m in ms:
        name = str(m.get("name", ""))
        if m.get("child"):
            name = "  " + name
        f, v = m.get("freq_hz"), m.get("value")
        df, dv = m.get("delta_freq_hz"), m.get("delta_value")
        u = m.get("unit") or unit
        out.append([
            name,
            str(m["trace"]) if m.get("trace") is not None else "",
            f"{f / scale:.{d}f}" if f is not None else "",
            # 値と Δ値の書き方は画面の表と同じ（|Γ|・SWR: 設計R §3。formats.value_cell・delta_cell）
            formats.value_cell(v, u),
            str(m.get("ref") or ""),
            f"{df / scale:+.{d}f}" if df is not None else "",
            formats.delta_cell(dv, u, db_offset=m.get("kind") == "level_cursor" and bool(m.get("child"))),
            str(m.get("note") or ""),
        ])
    return out


def fit_table(rows: list[list[str]], height: float, line_h: float) -> tuple[list[list[str]], int, int]:
    """表を画像の高さの上限（table_max_ratio）に収める。(行, 列の数, 1列あたりの行数) を返す。

    行が table_two_columns_from を超えたら2列。収まらない分は最後の行を「ほか N 行」にする
    （黙って切らない。全行は tEXt と保存に残る: I §3.5）。見出しの1行は行数に含めない。
    """
    if not rows:
        return [], 1, 0
    cols = 2 if len(rows) > LAYOUT["table_two_columns_from"] else 1
    rows_per_col = math.ceil(len(rows) / cols)
    max_rows = max(1, int((height * LAYOUT["table_max_ratio"]) / line_h) - 1)
    if rows_per_col > max_rows:
        keep = max_rows * cols - 1
        rest = len(rows) - keep
        rows = rows[:keep] + [[f"ほか {rest} 行"] + [""] * (len(MARKER_COLUMNS) - 1)]
        rows_per_col = max_rows
    return rows, cols, rows_per_col


# ---------------------------------------------------------------- 目盛

def y_labels(ref: float, div: float, unit: Optional[str] = None) -> list[str]:
    """縦軸の数字（上から。REF が一番上: J §4.1）。画面の目盛・REF の欄と同じ形（units.level_text）。
    |Γ|・SWR は1目盛を書き分けられる桁（設計R §3。formats.tick_decimals）。"""
    if formats.is_linear_unit(unit):
        d = formats.tick_decimals(unit, div)
        return [minus_sign(f"{ref - i * div:.{d}f}") for i in range(DIVISIONS + 1)]
    return [level_text(ref - i * div) for i in range(DIVISIONS + 1)]


def x_labels(start: float, stop: float) -> list[str]:
    """横軸の数字（MHz。全部の目盛で桁をそろえる）。目盛は格子と同じ 10 区画の線の上（AXES の決まり）。"""
    ticks = [start + (stop - start) * i / DIVISIONS for i in range(DIVISIONS + 1)]
    d = freq_decimals(ticks)
    return [f"{t / 1e6:.{d}f}" for t in ticks]


def is_time_axis(data: dict) -> bool:
    return ((data.get("conditions") or {}).get("x_axis") or {}).get("kind") == "time"


def time_x_labels(x0: float, x1: float, factor: float) -> list[str]:
    """ゼロスパンの横軸の数字（目盛の単位で。全部の目盛で桁をそろえる）。"""
    ticks = [(x0 + (x1 - x0) * i / DIVISIONS) / factor for i in range(DIVISIONS + 1)]
    for d in range(7):
        if all(abs(round(t, d) - t) < 1e-9 for t in ticks):
            break
    return [f"{t:.{d}f}" for t in ticks]


def x_label_every(step_px: float, widest_px: float) -> int:
    """横軸の数字を何目盛ごとに出すか（重ならない最小の間隔）。"""
    return next((n for n in (1, 2, 5, 10) if step_px * n >= widest_px), 10)


# ---------------------------------------------------------------- 軸の置き場所（Qt と Web で同じ: I §3.1）

# 軸の目盛と単位の決まり。長さは文字の大きさ（px）に対する倍率。目盛は格子と同じ 10 区画の線の上に置き（縦も横も）、
# 横軸の数字は重ならない最小の間隔（x_label_every）で間引く。縦軸の単位は縦軸の数字の上（凡例と同じ段の左端）、横軸の
# 単位は横軸の数字の下の見出し（「周波数 (MHz)」）。
# 置き場所と間引きは描く側の書体の測り方に頼らず、文字数からの幅の見積もり（TEXT_EM）で決める——測ると Qt とブラウザで
# 書体の幅が違い、間引きと枠の位置が画像ごとに変わる。見積もりは広めにとる（狭く見積もると数字が重なる）
AXES = {
    "unit_gap": 0.6,          # 縦軸の数字（と単位）の右からグラフの枠までの余白
    "right_room": 3.0,        # グラフの枠の右の余白（数字の字の幅の倍率）
    "top_room": 1.6,          # 凡例と単位の段（1行）＋一番上の目盛の数字の半分（行の高さの倍率: J §4.3）
    "bottom_room": 2.3,       # 横軸の数字と見出しの2行（行の高さの倍率）
    "min_plot_rows": 3.0,     # グラフの枠の高さの下限（行の高さの倍率）
    "y_label_gap": 0.5,       # 縦軸の数字の右端から枠まで（数字の字の幅の倍率）
    "x_label_gap": 1.5,       # 横軸の数字どうしの最小の間（数字の字の幅の倍率）
    "x_label_top": 0.15,      # 枠の下端から横軸の数字の段の上端まで（行の高さの倍率）
    "edge_room": 0.3,         # 右端の数字を画像の外に出さない余白（外周の余白の倍率）
}
# 文字の幅の見積もり（文字の大きさに対する倍率）。数字・記号は 0.62（Yu Gothic UI 0.56・Meiryo UI 0.63）、m・w は 1.0
# （同 0.88・0.94）、ほかの英字は 0.8、ASCII でない字（μ・−・かな）は 1.0
TEXT_EM = {"narrow": 0.62, "letter": 0.8, "wide": 1.0}
_WIDE_LETTERS = frozenset("mMwW")


def text_width_px(text: str, px: float) -> float:
    """文字の幅の見積もり（画素）。AXES の置き場所を決めるためだけに使う（描く位置の寄せは描く側が測る）。"""
    em = 0.0
    for ch in str(text):
        if ord(ch) >= 0x80 or ch in _WIDE_LETTERS:
            em += TEXT_EM["wide"]
        elif ch.isalpha():
            em += TEXT_EM["letter"]
        else:
            em += TEXT_EM["narrow"]
    return em * px


def graph_axes(width: float, px: float, line: float, margin: float, ylabels: list[str], unit: str,
               xlabels: list[str]) -> dict:
    """グラフの枠と軸の文字の置き場所（AXES の決まり）。縦の位置は枠の下端（描く側が区画から決める）からの距離で返す。

    返すもの: plot_left・plot_right（枠の左右）・plot_top（枠の上）・bottom_room（枠の下に空ける高さ）・min_plot_h・
    y_label_right（縦軸の数字の右端）・unit_left（縦軸の単位の左端。凡例と同じ段）・legend_left（凡例の左端）・
    x_every（横軸の数字を何目盛ごとに出すか）・x_label_top・x_title_top（枠の下端から各段の上端まで）・
    x_label_max_right（横軸の数字の右端の上限）。段の中の文字は段の高さの真ん中に置く。
    """
    digit = px * TEXT_EM["narrow"]
    axis_w = max(text_width_px(s, px) for s in list(ylabels) + [unit]) + px * AXES["unit_gap"]
    left = margin + axis_w
    right = width - margin - digit * AXES["right_room"]
    widest = max(text_width_px(s, px) for s in xlabels) + digit * AXES["x_label_gap"]
    return {
        "plot_left": left,
        "plot_right": right,
        "plot_top": margin + line * AXES["top_room"],
        "bottom_room": line * AXES["bottom_room"],
        "min_plot_h": line * AXES["min_plot_rows"],
        "y_label_right": left - digit * AXES["y_label_gap"],
        "unit_left": margin,
        "legend_left": left,
        "x_every": x_label_every((right - left) / DIVISIONS, widest),
        "x_label_top": line * AXES["x_label_top"],
        "x_title_top": line * (AXES["x_label_top"] + 1.0),
        "x_label_max_right": width - margin * AXES["edge_room"],
    }


# ---------------------------------------------------------------- メタデータ

def png_metadata(data: dict, note: str = "", app_version: str = "") -> dict:
    """tEXt に入れる JSON（I §3.3: 帯と同じ内容。トレースの値は入れない——データは保存で残す: I §2）。"""
    keys = ("device", "mode", "conditions", "calibration", "normalization", "markers", "warnings")
    meta: dict[str, Any] = {"format": META_FORMAT,
                            "measured_at": format_measured_at(data.get("measured_at")),
                            "app_version": data.get("app_version") or app_version}
    for k in keys:
        if data.get(k) is not None:
            meta[k] = data[k]
    meta["traces"] = [{k: t.get(k) for k in ("number", "action", "average", "count", "count_target", "label")}
                      for t in data.get("traces") or [] if t.get("visible", True)]
    memo = (note or data.get("note") or "").strip()
    if memo:
        meta["note"] = memo
    return meta


def layout(data: dict, size: tuple[int, int], note: str = "", app_version: str = "") -> dict:
    """描く側（Web の canvas）へ渡す配置の一式。Qt の png_export も同じ関数の結果で描く。

    返すもの: 文字の大きさ・行の高さ・余白・帯の段・マーカー表（見出し・行・右寄せ・列の数）・凡例・
    縦軸と横軸の数字・枠と軸の文字の置き場所（axes）・PNG の tEXt の中身。帯の折り返し（文字の幅で決まる）は描く側が行う。
    """
    w, h = int(size[0]), int(size[1])
    check_size(w, h)
    px = font_px(w, h)
    lh = line_px(px)
    c = data.get("conditions") or {}
    rows, cols, per_col = fit_table(marker_table_rows(data), h, lh)
    xa = c.get("x_axis") or {}
    time = xa.get("kind") == "time"
    start = float(c.get("start_hz") or 0.0)
    stop = float(c.get("stop_hz") or start + 1.0)
    if stop <= start:
        stop = start + 1.0
    off = int(c.get("freq_offset_hz") or 0)
    ylabels = y_labels(float(c.get("ref_level", 0.0)), float(c.get("db_per_div", 10.0)), c.get("unit"))
    unit = c.get("unit", "dBm")
    if time:
        # ゼロスパン: 横軸は時間（推定。画面と同じ: 設計N §1）。周波数オフセットは横軸に出さない（設計O）
        xlabels = time_x_labels(float(xa["x0"]), float(xa["x1"]), float(xa["factor"]))
        xtitle = X_AXIS_TITLE_TIME.replace("{unit}", str(xa.get("unit")))
    else:
        # 横軸の数字は換算後の周波数、見出しに換算中であることを書く（設計O: 軸の名前で示す）
        xlabels = x_labels(start + off, stop + off)
        xtitle = X_AXIS_TITLE if not off else X_AXIS_TITLE_OFFSET.replace("{off}", ("+" if off > 0 else "−") + _hz(abs(off)))
    return {
        "size": [w, h],
        "font_px": px,
        "line_px": lh,
        "margin_px": px * LAYOUT["margin"],
        "gap_px": px * LAYOUT["gap"],
        "item_gap": LAYOUT["item_gap"],
        "fonts": list(JP_FONT_FALLBACKS),
        "band": [[color, items] for color, items in band_rows(data, note, app_version)],
        "table": {"header": list(MARKER_COLUMNS_TIME if time else MARKER_COLUMNS), "right": list(MARKER_RIGHT),
                  "rows": rows, "cols": cols, "rows_per_col": per_col},
        # 凡例の線の見本は、そのトレースの線（設計F §9）。stroke は見本の太さと切れ目（画素）
        "legend": [{"number": t.get("number"), "color": t.get("color") or f"trace{t.get('number', 1)}",
                    "text": legend_text(t), "line": t.get("line") or TRACE_LINE_DEFAULT,
                    "stroke": legend_stroke(t.get("line"), png_line_base(px))}
                   for t in data.get("traces") or [] if t.get("visible", True)],
        "legend_sample": LAYOUT["legend_sample"],
        # トレースの線の基準の幅（画素）。線ごとの倍率は line_styles（Web は画面と同じ部品でグラフを描くので渡す）
        "line_base_px": png_line_base(px),
        "line_styles": LINE_STYLES,
        "y_labels": ylabels,
        "x_labels": xlabels,
        "x_title": xtitle,
        "unit": unit,
        # 枠と軸の文字の置き場所・横軸の数字の間引き（AXES の決まり。Qt と Web が同じ値で描く）
        "axes": graph_axes(w, px, lh, px * LAYOUT["margin"], ylabels, unit, xlabels),
        "meta": png_metadata(data, note, app_version),
        "meta_key": META_KEY,
    }


# ---------------------------------------------------------------- 状態から中身を作る

def _when(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


def calibration_of(state: dict) -> Optional[dict]:
    """帯の「校正データ」。GigaSt は校正ファイル、RTL-SDR は個体の校正（ppm・レベル: 設計Q §6.6）。"""
    conn = state.get("connection") or {}
    rtl = state.get("rtlsdr") or {}
    if rtl.get("connected") or conn.get("sweep_kind") == "fft":
        cal = rtl.get("calibration")
        if not cal:
            return None
        parts = [f"ppm {cal.get('ppm', 0):+d}"]
        if cal.get("level_offset_db"):
            parts.append("レベルの補正あり")
        return {"source": "RTL-SDR 個体の校正（" + "・".join(parts) + "）", "at": cal.get("created_at")}
    cal = state.get("calibration")
    if not cal:
        return None
    return {"source": (cal.get("source") or {}).get("file_name") or "校正データ", "at": cal.get("created_at")}


def normalization_of(state: dict, reasons: Optional[dict] = None) -> Optional[dict]:
    norm = state.get("normalization") or {}
    if not norm.get("used"):
        return None
    sel = norm.get("selected") or {}
    reason = norm.get("reason") or ""
    return {"name": sel.get("name", ""), "at": _when(sel.get("taken_at")),
            "applied": bool(norm.get("applied")), "before_power_cycle": bool(norm.get("power_cycled")),
            "reason": (reasons or {}).get(reason, reason) if not norm.get("applied") else ""}


def build_data(state: dict, *, ref_level: float, db_per_div: float, unit: str, mode_name: str,
               traces: list[dict], markers: list[dict], warnings: list[str], note: str = "",
               measured_at: Optional[str] = None, app_version: str = "",
               norm_reasons: Optional[dict] = None) -> dict:
    """画面の状態と、画面が決める中身（縦軸・モードの名前・凡例・マーカー表の行・注意）から画像の中身を作る。"""
    sw = state.get("sweep") or {}
    conn = state.get("connection") or {}
    ident = conn.get("identity") or {}
    view = state.get("view") or {}
    x_axis = None
    if sw.get("step_hz") == 0:
        # ゼロスパン: 横軸は時間（推定）。範囲と目盛の単位は画面と同じ決め方（core/zero_span.py・units）
        length = float(view.get("zero_span_s", 10) or 0)
        pt = float(sw.get("point_time_s") or 0.0)
        x0, x1 = zs.x_range_us(length, int(sw.get("points") or 1), pt)
        x1 = max(x1, x0 + 1)
        factor, tunit = time_axis_unit(x1 - x0)
        x_axis = {"kind": "time", "x0": x0, "x1": x1, "factor": factor, "unit": tunit, "length_s": length,
                  "point_time_s": pt}
    if measured_at is None:
        latest = max((t.get("last_update_time") or 0 for t in state.get("traces") or []
                      if t.get("visible") and t.get("has_values")), default=0)
        when = _dt.datetime.fromtimestamp(latest).astimezone() if latest else _dt.datetime.now().astimezone()
        measured_at = when.isoformat(timespec="seconds")
    return {
        "measured_at": measured_at,
        "device": {"model": ident.get("model") or conn.get("display_name") or conn.get("profile_model"),
                   "serial": ident.get("serial")},
        "app_version": app_version,
        "mode": mode_name,
        "conditions": {"start_hz": sw.get("start_hz"), "stop_hz": sw.get("stop_hz"), "rbw_hz": sw.get("rbw_hz"),
                       "points": sw.get("points"), "step_hz": sw.get("step_hz"),
                       # 実際に使ったもの（NF・ゼロスパンは選択によらず「なし」: 設計F §7.1）。RTL-SDR は持たない
                       "image_reject": None if conn.get("sweep_kind") == "fft" else sw.get("image_reject_used"),
                       "ref_level": ref_level, "db_per_div": db_per_div, "unit": unit,
                       # 表示の設定（設計O）: 周波数オフセット・外部補正。帯と横軸に出す
                       "freq_offset_hz": int(view.get("freq_offset_hz") or 0),
                       "ext_db": float(view.get("ext_db") or 0.0),
                       **({"x_axis": x_axis} if x_axis else {})},
        "calibration": calibration_of(state),
        "normalization": normalization_of(state, norm_reasons),
        "traces": traces,
        "markers": markers,
        "warnings": list(warnings),
        "note": note,
    }
