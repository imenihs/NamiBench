"""マーカー表の行（設計G §3.3・§3.5）。コアのマーカー表（状態の `markers`）を、表・PNG・コピーの行にする。

行の形は png_export の `markers` と同じ（name・kind・trace・freq_hz・value・ref・delta_freq_hz・
delta_value・child・note）に、画面が使う id・parent・unit・status を足したもの。
表示・コピー・PNG の3つが同じ行から作られるので、どこで見ても同じ中身になる（U-09）。
"""
from __future__ import annotations

from typing import Optional

from ..core.units import format_freq
from . import texts
from ..core.report_layout import freq_decimals as _freq_decimals

COLUMNS = texts.MARKER_COLUMNS
# 数える列は右寄せ（U-17）
RIGHT = (False, True, True, True, False, True, True, False)
NUMERIC = (False, True, True, True, False, True, True, False)


def _notes(codes) -> list[str]:
    out = []
    for c in codes or []:
        if c in texts.MARKER_NOTES_SILENT:
            continue
        t = texts.MARKER_NOTES.get(c, c)
        if t not in out:
            out.append(t)
    return out


def _join(*parts: str) -> str:
    return "・".join(p for p in parts if p)


def _signed_hz(hz: int) -> str:
    s = format_freq(abs(int(hz)))
    return ("+" if hz >= 0 else "−") + s


def _mhz(hz) -> str:
    # 帯域の端は補間で求めた値なので、読む桁（kHz）までにする
    return f"{float(hz) / 1e6:.3f}"


def _band_note(b: dict) -> str:
    """N dB 帯域幅・しきい値の結果（下端・上端・中心・幅・Q: G §3.5）。周波数は MHz。"""
    if b.get("status") != "ok":
        return texts.BAND_STATUS.get(b.get("status"), b.get("status") or "")
    parts = []
    if b.get("left_hz") is not None and b.get("right_hz") is not None:
        parts.append(f"{_mhz(b['left_hz'])}〜{_mhz(b['right_hz'])}")
    if b.get("center_hz") is not None:
        parts.append(texts.w("band_center", v=_mhz(b["center_hz"])))
    if b.get("width_hz") is not None:
        parts.append(texts.w("band_width", v=_mhz(b["width_hz"])))
    text = "・".join(parts) + (" MHz" if parts else "")
    if b.get("q") is not None:
        text += f"・Q {b['q']:.1f}"
    return text


def rows_from_snapshot(snap: Optional[dict]) -> list[dict]:
    """マーカー表の行（親の下に子の行: オフセットのカーソル・機能の結果）と XY カーソル。"""
    rows: list[dict] = []
    for m in (snap or {}).get("markers", []):
        mid = m["id"]
        delta = m.get("delta") or {}
        status = m.get("status", "ok")
        note = _join(texts.MARKER_STATUS.get(status, status), texts.LABELS["row_track"] if m.get("peak_track") else "",
                     *_notes(m.get("notes")))
        if m.get("ref") and delta.get("status") not in (None, "ok"):
            note = _join(note, texts.MARKER_STATUS.get(delta["status"], delta["status"]))
        rows.append({
            "id": mid, "parent": None, "name": mid, "kind": "marker", "trace": m.get("trace"),
            "freq_hz": m.get("freq_hz"), "value": m.get("level"), "unit": m.get("unit"),
            "ref": m.get("ref"), "delta_freq_hz": delta.get("freq_hz"), "delta_value": delta.get("level_db"),
            "child": False, "note": note, "status": status,
        })
        for i, o in enumerate(m.get("freq_offsets") or []):
            st = o.get("status", "ok")
            rows.append({
                "id": o["id"], "parent": mid, "name": f"{mid} {_signed_hz(o['offset_hz'])}", "kind": "freq_cursor",
                "trace": m.get("trace"), "freq_hz": o.get("freq_hz"), "value": o.get("level"), "unit": o.get("unit"),
                "ref": mid, "delta_freq_hz": o.get("offset_hz") if o.get("freq_hz") is not None else None,
                "delta_value": o.get("delta_level_db"), "child": True,
                "note": _join(texts.MARKER_STATUS.get(st, st), *_notes(o.get("notes"))), "status": st,
                "offset": ("freq", i),
            })
        for i, o in enumerate(m.get("level_offsets") or []):
            st = o.get("status", "ok")
            d = o.get("offset_db", 0.0)
            rows.append({
                "id": o["id"], "parent": mid, "name": f"{mid} {'+' if d >= 0 else '−'}{abs(d):g} dB",
                "kind": "level_cursor", "trace": m.get("trace"), "freq_hz": None, "value": o.get("level"),
                "unit": o.get("unit"), "ref": mid, "delta_freq_hz": None,
                "delta_value": d if o.get("level") is not None else None, "child": True,
                "note": texts.MARKER_STATUS.get(st, st), "status": st, "offset": ("level", i),
            })
        for b in m.get("bandwidth") or []:
            rows.append({
                "id": f"{mid}.bw{b['n_db']:g}", "parent": mid, "name": texts.w("row_bw", m=mid, n=f"{b['n_db']:g}"),
                "kind": "function", "trace": m.get("trace"), "freq_hz": None, "value": None,
                "unit": m.get("unit"), "ref": None, "delta_freq_hz": None, "delta_value": None, "child": True,
                "note": _band_note(b), "status": b.get("status"),
            })
        if m.get("shape_factor") is not None:
            rows.append({
                "id": f"{mid}.sf", "parent": mid, "name": texts.w("row_sf", m=mid), "kind": "function",
                "trace": m.get("trace"), "freq_hz": None, "value": None, "unit": None, "ref": None,
                "delta_freq_hz": None, "delta_value": None, "child": True,
                "note": f"{m['shape_factor']:.2f}", "status": "ok",
            })
        th = m.get("threshold")
        if th:
            side = texts.THRESHOLD_SIDES.get(th.get("side"), th.get("side"))
            rows.append({
                "id": f"{mid}.th", "parent": mid, "name": texts.w("row_th", m=mid, level=f"{th['level']:g}", side=side),
                "kind": "function", "trace": m.get("trace"), "freq_hz": None, "value": None,
                "unit": m.get("unit"), "ref": None, "delta_freq_hz": None, "delta_value": None, "child": True,
                "note": _band_note(th), "status": th.get("status"),
            })
    rows += xy_rows((snap or {}).get("xy") or {})
    return rows


def xy_rows(xy: dict) -> list[dict]:
    """XY カーソル（G §3.5）。トレースに載らないので値は読まない。X2・Y2 の行に Δ を出す。"""
    out = []
    for k, name in (("x1_hz", "X1"), ("x2_hz", "X2")):
        if xy.get(k) is not None:
            st = xy.get(k.replace("_hz", "_status")) or "ok"
            out.append({"id": name, "parent": None, "name": name, "kind": "freq_cursor", "trace": None,
                        "freq_hz": xy[k], "value": None, "unit": None,
                        "ref": "X1" if name == "X2" and xy.get("dx_hz") is not None else None,
                        "delta_freq_hz": xy.get("dx_hz") if name == "X2" else None, "delta_value": None,
                        "child": False, "note": texts.MARKER_STATUS.get(st, st), "status": st})
    for k, name in (("y1", "Y1"), ("y2", "Y2")):
        if xy.get(k) is not None:
            out.append({"id": name, "parent": None, "name": name, "kind": "level_cursor", "trace": None,
                        "freq_hz": None, "value": xy[k], "unit": None,
                        "ref": "Y1" if name == "Y2" and xy.get("dy") is not None else None,
                        "delta_freq_hz": None, "delta_value": xy.get("dy") if name == "Y2" else None,
                        "child": False, "note": "", "status": "ok"})
    return out


def format_rows(rows: list[dict], default_unit: str = "dBm") -> list[list[str]]:
    """表の文字（列は COLUMNS）。周波数の小数の桁は全部の行でそろえる（桁を縦にそろえる: U-17）。"""
    freqs = [r.get("freq_hz") for r in rows] + [r.get("delta_freq_hz") for r in rows]
    d = _freq_decimals(f for f in freqs if f is not None)
    out = []
    for r in rows:
        f, v, df, dv = r.get("freq_hz"), r.get("value"), r.get("delta_freq_hz"), r.get("delta_value")
        unit = r.get("unit") or default_unit
        out.append([
            ("  " if r.get("child") else "") + str(r.get("name", "")),
            str(r["trace"]) if r.get("trace") is not None else "",
            f"{f / 1e6:.{d}f}" if f is not None else "",
            f"{v:.2f} {unit}" if v is not None else "",
            str(r.get("ref") or ""),
            f"{df / 1e6:+.{d}f}" if df is not None else "",
            f"{dv:+.2f}" if dv is not None else "",
            str(r.get("note") or ""),
        ])
    return out


def to_tsv(rows: list[dict], default_unit: str = "dBm") -> str:
    """タブ区切りの表（見出し付き。子の行と機能の結果も含める: G §3.3・§3.5）。"""
    lines = ["\t".join(COLUMNS)]
    for cells in format_rows(rows, default_unit):
        cells = [c.strip() for c in cells]
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def png_rows(rows: list[dict]) -> list[dict]:
    keys = ("name", "kind", "trace", "freq_hz", "value", "unit", "ref", "delta_freq_hz", "delta_value", "child", "note")
    return [{k: r.get(k) for k in keys} for r in rows]
