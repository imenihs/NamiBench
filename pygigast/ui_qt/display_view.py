"""表示（設計P）の画面の中身。Qt と Web が同じものを使う（J §5。Web へは view.py が状態と一緒に送る）。

- 周波数の表示の範囲（freq_view）: ライブは掃引の Start・Stop、再生中は再生の表示の範囲（測り直さない拡大: P §5）。
  上段の欄・横軸・グラフの上の操作が同じ範囲を使う（スペクトルとウォーターフォールの横軸の連動: P §2）
- 表示のタブの欄（display_tab_view）・再生の帯（playback_view）・バンドプランとブックマーク（bandplan_view）・
  別ウィンドウにできるか（part_reasons）・ウォーターフォールの読み値（waterfall_reading）・条件の境目（boundary_label）
Qt を import しない。
"""
from __future__ import annotations

from typing import Optional

from ..core import colormaps
from ..core.units import format_freq, level_text
from ..core.view_units import DENSITY_HALF_CHOICES, WF_ROWS_CHOICES
from . import texts

D = texts.DISPLAY_TEXTS


def _t(key: str, **kw) -> str:
    s = D[key]
    for k, v in kw.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def freq_view(st: dict) -> dict:
    """横軸・上段の欄に出す周波数の範囲。{start_hz, stop_hz, center_hz, span_hz, step_hz, points, playback}。"""
    pb = st.get("playback") or {}
    if pb.get("active") and pb.get("view"):
        lo, hi = int(pb["view"]["start_hz"]), int(pb["view"]["stop_hz"])
        step = int(pb.get("step_hz") or 0)
        return {"start_hz": lo, "stop_hz": hi, "center_hz": lo + (hi - lo) // 2, "span_hz": hi - lo, "step_hz": step,
                "points": int(pb.get("points") or 0), "playback": True}
    sw = st.get("sweep") or {}
    if "start_hz" not in sw or "stop_hz" not in sw:
        return {}
    lo, hi = int(sw["start_hz"]), int(sw["stop_hz"])
    return {"start_hz": lo, "stop_hz": hi, "center_hz": sw.get("center_hz", lo + (hi - lo) // 2),
            "span_hz": sw.get("span_hz", hi - lo), "step_hz": sw.get("step_hz", 1), "points": sw.get("points", 0),
            "playback": False}


def layout_of(st: dict) -> str:
    return (st.get("view") or {}).get("layout") or "spectrum"


def part_reasons(st: dict) -> dict:
    """ウォーターフォール・密度表示の部分が使えない理由（表示の組み合わせで選んでいないとき: U-08）。"""
    lay = layout_of(st)
    return {"waterfall": None if lay == "waterfall" else texts.REASON_TEXTS["layout_waterfall"],
            "density": None if lay == "density" else texts.REASON_TEXTS["layout_density"]}


def display_tab_view(st: dict) -> dict:
    """表示のタブのウォーターフォール・密度の欄: 選択肢の文字・いまの値・使えない理由。"""
    v = st.get("view") or {}
    pal = st.get("palette") or {}
    auto = bool(v.get("wf_color_auto", True))
    wf = st.get("waterfall") or {}
    rng = wf.get("range") or {}
    unit = wf.get("unit") or texts.level_unit_label(v.get("level_unit"))
    return {
        "layout": layout_of(st),
        "trace": int(v.get("wf_trace") or 1),
        "traces": [{"code": n, "text": texts.w("trace_n", n=n)} for n in (1, 2, 3, 4)],
        "auto": auto,
        "lo": float(rng.get("lo") if auto and rng.get("lo") is not None else v.get("wf_color_lo", -110.0)),
        "hi": float(rng.get("hi") if auto and rng.get("hi") is not None else v.get("wf_color_hi", -20.0)),
        "range_reason": texts.REASON_TEXTS["wf_auto_on"] if auto else None,
        "unit": unit,
        "colormap": pal.get("colormap") or colormaps.DEFAULT,
        "colormaps": [{"code": c, "text": texts.COLORMAP_NAMES.get(c, c)} for c in colormaps.CHOICES],
        "rows": int(v.get("wf_rows") or 2000),
        "rows_choices": [{"code": n, "text": _t("rows_item", n=n)} for n in WF_ROWS_CHOICES],
        "half": int(v.get("density_half") or 20),
        "half_choices": [{"code": n, "text": _t("times_item", n=n)} for n in DENSITY_HALF_CHOICES],
        "bandplan_shown": bool(v.get("bandplan_shown", True)),
        "bookmarks_shown": bool(v.get("bookmarks_shown", True)),
    }


def density_seconds_text(seconds: Optional[float]) -> str:
    """何秒分の密度か（N × 掃引の間隔: P §4）。まだ分からなければ空。"""
    if seconds is None:
        return ""
    return _t("density_seconds", t=texts.span_text(seconds))


def playback_view(st: dict) -> dict:
    """再生の帯（P §9.4）: 出すか・再生／一時停止の文字・速さの選択肢・位置の文字・保存時の分解能。"""
    pb = st.get("playback") or {}
    if not pb.get("active"):
        return {"visible": False}
    step = int(pb.get("step_hz") or 0)
    return {
        "visible": True, "playing": bool(pb.get("playing")),
        "play_label": D["pause"] if pb.get("playing") else D["play"],
        "speed": int(pb.get("speed") or 1),
        "speeds": [{"code": s, "text": texts.PLAYBACK_SPEED_NAMES.get(str(s), f"{s}")} for s in pb.get("speeds") or []],
        "index": int(pb.get("index") or 0), "total": int(pb.get("total") or 0),
        "t": pb.get("t"), "t_first": pb.get("t_first"), "t_last": pb.get("t_last"),
        "position": _t("position", time=texts.clock_text(pb.get("t"), tenths=True), i=int(pb.get("index") or 0) + 1,
                       n=int(pb.get("total") or 0)),
        "resolution": _t("resolution", step=format_freq(step)) if step else "",
        "name": pb.get("name") or "",
    }


def bandplan_view(st: dict) -> dict:
    """バンドプランの帯・ブックマークの印と一覧（周波数は表示の周波数オフセットを足した文字: 設計O）。"""
    bpl = st.get("bandplan") or {}
    v = st.get("view") or {}
    off = texts.freq_offset_of(st)
    bands = [dict(b) for b in bpl.get("bands") or []]
    marks = [{**m, "freq": format_freq(int(m["hz"]) + off)} for m in bpl.get("bookmarks") or []]
    return {
        "bands": bands if v.get("bandplan_shown", True) else [],
        "bookmarks": marks if v.get("bookmarks_shown", True) else [],
        "list": marks,
        "count": _t("bands_count", n=len(bands)),
        "file": _t("bandplan_file", name=bpl["source"]) if bpl.get("source") else "",
    }


def boundary_label(cond: dict, freq_offset_hz: int = 0) -> str:
    """条件が変わった境目に出す条件（P §3）: 範囲・RBW・モード。"""
    mode = texts.MODE_LABELS.get(cond.get("mode"), cond.get("mode") or "")
    rbw = format_freq(int(cond["rbw_hz"])) if cond.get("rbw_hz") else ""
    lo, hi = cond.get("start_hz"), cond.get("stop_hz")
    off = int(freq_offset_hz or 0)
    if lo is None or hi is None:
        return ""
    if lo == hi:
        return _t("boundary_zero", start=format_freq(int(lo) + off), rbw=rbw, mode=mode)
    return _t("boundary", start=format_freq(int(lo) + off), stop=format_freq(int(hi) + off), rbw=rbw, mode=mode)


def waterfall_reading(t: float, sweeps: int, freq_hz: Optional[int], level: Optional[float], unit: str,
                      freq_offset_hz: int = 0, point: Optional[int] = None) -> list[str]:
    """ウォーターフォールの行の上のマウスの読み値（P §3・§9.2）: 時刻・周波数・レベル（まとめた行はその数）。"""
    lines = [texts.clock_text(t, tenths=True)]
    if int(sweeps or 1) > 1:
        lines[0] += "　" + _t("reading_merged", n=int(sweeps))
    if freq_hz is not None:
        lines.append(format_freq(int(freq_hz) + int(freq_offset_hz or 0)))
    elif point is not None:
        lines.append(texts.tpl("mouse_point", n=int(point)))
    lines.append(f"{level_text(level)} {unit}".strip() if level is not None else texts.MARKER_STATUS["no_value"])
    return lines


def ui(st: dict) -> dict:
    """Web へ状態と一緒に送るもの（Qt も同じ関数を使う）。"""
    return {
        "freq_view": freq_view(st),
        "parts": part_reasons(st),
        "tab": display_tab_view(st),
        "playback": playback_view(st),
        "bandplan": bandplan_view(st),
        "density_seconds": density_seconds_text(((st.get("waterfall") or {}).get("density") or {}).get("seconds")),
    }
