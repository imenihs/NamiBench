"""マーカー表の行（設計G §3.3・§3.5）。コアのマーカー表（状態の `markers`）を、表・PNG・コピーの行にする。

行の形は png_export の `markers` と同じ（name・kind・trace・freq_hz・value・ref・delta_freq_hz・
delta_value・child・note）に、画面が使う id・parent・unit・status・x_kind を足したもの。
表示・コピー・PNG の3つが同じ行から作られるので、どこで見ても同じ中身になる（U-09）。

ゼロスパン（x_kind "time"）では、位置（freq_hz・delta_freq_hz）はマイクロ秒の時間（設計N §1）。列は「時間 (ms)」で、
区間の最大・最小・平均の行（kind "interval"）を XY カーソルの後に足す。
"""
from __future__ import annotations

from typing import Optional

from ..core.units import format_freq
from ..core.trace.formats import delta_cell, value_cell
from . import texts
from .texts_zerospan import ZERO_SPAN_TEXTS as ZT
from ..core.report_layout import freq_decimals as _freq_decimals

COLUMNS = texts.MARKER_COLUMNS
# 数える列は右寄せ（U-17）
RIGHT = (False, True, True, True, False, True, True, False)
NUMERIC = (False, True, True, True, False, True, True, False)


def note_texts(codes) -> list[str]:
    """注意のコードを画面の文にする（マーカーの読み値とマウスの位置の読み値で同じ文: G §3.3）。"""
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


def function_lines_on(st: Optional[dict]) -> bool:
    """マーカーの機能の線をグラフに出すか（G §3.6。コアの表示の設定 view.function_lines。無ければ出す）。"""
    return ((st or {}).get("view") or {}).get("function_lines", True) is not False


def function_line(level: Optional[float], b: dict) -> Optional[dict]:
    """マーカーの機能の結果をグラフに出す線（G §3.6）: 横線のレベルと、交点が見つかった側の縦線の周波数。

    level が None（マーカーに値が無く機能が結果を出していない）なら線は無い。表示範囲の端まで交点が無い側・値なしの
    区間に当たった側は縦線を出さない。Qt のグラフ・Web のグラフ・PNG が同じ線を描く（同じ行から作る: U-09）。
    """
    if level is None:
        return None
    freqs = [int(b[side + "_hz"]) for side in ("left", "right")
             if b.get(side + "_status") == "ok" and b.get(side + "_hz") is not None]
    return {"level": float(level), "freqs_hz": freqs}


def columns(x_kind: str = "freq") -> tuple:
    """マーカー表の列の名前。時間軸（ゼロスパン）は位置の列が時間（設計N §1）。"""
    if x_kind != "time":
        return COLUMNS
    cols = list(COLUMNS)
    cols[COL_FREQ], cols[COL_DFREQ] = ZT["col_time"], ZT["col_dtime"]
    return tuple(cols)


def rows_x_kind(rows: list[dict]) -> str:
    return "time" if any(r.get("x_kind") == "time" for r in rows) else "freq"


def interval_rows(iv: Optional[dict]) -> list[dict]:
    """区間の最大・最小・平均（パワー平均）の行（設計N §1）。表示中のトレースごとに3行。位置は最大・最小の点の時間。

    区間（X1〜X2・表示範囲）は「基準」の列に出す（注意の列は注意だけ: U-25。点が無いときだけ「値なし」）。
    """
    if not iv:
        return []
    rng = ZT["range_xy"] if iv.get("range") == "xy" else ZT["range_window"]
    out = []
    for r in iv.get("rows") or []:
        n = r.get("trace")
        for key, name, x, v in (("max", ZT["row_max"], r.get("max_us"), r.get("max")),
                                ("min", ZT["row_min"], r.get("min_us"), r.get("min")),
                                ("avg", ZT["row_avg"], None, r.get("avg"))):
            ok = v is not None
            out.append({"id": f"I{n}.{key}", "parent": None, "name": name, "kind": "interval", "trace": n,
                        "freq_hz": x if ok else None, "value": v, "unit": r.get("unit"), "ref": rng,
                        "delta_freq_hz": None, "delta_value": None, "child": False,
                        "note": "" if ok else texts.MARKER_STATUS["no_value"], "status": "ok" if ok else "no_value"})
    return out


def rows_from_snapshot(snap: Optional[dict]) -> list[dict]:
    """マーカー表の行（親の下に子の行: オフセットのカーソル・機能の結果）と XY カーソル、ゼロスパンなら区間の行。"""
    rows: list[dict] = []
    for m in (snap or {}).get("markers", []):
        mid = m["id"]
        delta = m.get("delta") or {}
        status = m.get("status", "ok")
        dip = m.get("polarity") == "dip"  # 谷に置いたマーカー（G §3.8）
        note = _join(texts.MARKER_STATUS.get(status, status), texts.LABELS["row_dip"] if dip else "",
                     texts.LABELS["row_track"] if m.get("peak_track") else "", *note_texts(m.get("notes")))
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
                "note": _join(texts.MARKER_STATUS.get(st, st), *note_texts(o.get("notes"))), "status": st,
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
        # 機能の線（G §3.6）はマーカーが値を持つ間だけ。N dB 帯域幅の横線は マーカーの値 − N（谷に置いたマーカーは
        # 谷から N 上がる点を探すので + N: G §3.8）
        level = m.get("level") if status == "ok" else None
        for b in m.get("bandwidth") or []:
            n_db = float(b["n_db"])
            # 横線の高さはコアが表示の形式の値で出す（|Γ|・SWR でも探した dB の高さ: 設計R §3）。無ければ値 ∓ N
            line_level = b["line_level"] if "line_level" in b else (
                None if level is None else level + (n_db if dip else -n_db))
            rows.append({
                "id": f"{mid}.bw{b['n_db']:g}", "parent": mid, "name": texts.w("row_bw", m=mid, n=f"{b['n_db']:g}"),
                "kind": "function", "trace": m.get("trace"), "freq_hz": None, "value": None,
                "unit": m.get("unit"), "ref": None, "delta_freq_hz": None, "delta_value": None, "child": True,
                "note": _band_note(b), "status": b.get("status"),
                "line": function_line(None if level is None else line_level, b),
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
                "line": function_line(None if level is None else th["level"], th),
            })
    rows += xy_rows((snap or {}).get("xy") or {})
    xk = (snap or {}).get("x_kind") or "freq"
    if xk == "time":
        rows += interval_rows((snap or {}).get("interval"))
    for r in rows:
        r["x_kind"] = xk
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


def format_rows(rows: list[dict], default_unit: str = "dBm", freq_offset_hz: int = 0) -> list[list[str]]:
    """表の文字（列は COLUMNS）。周波数の小数の桁は全部の行でそろえる（桁を縦にそろえる: U-17）。

    freq_offset_hz は表示の周波数オフセット（設計O）。周波数の列は測った周波数に足した値（Δ周波数は変わらない）。
    """
    time = rows_x_kind(rows) == "time"
    off = 0 if time else int(freq_offset_hz or 0)  # 時間軸に周波数オフセットは当てない（設計O・N §1）
    if off:
        rows = [{**r, "freq_hz": r["freq_hz"] + off} if r.get("freq_hz") is not None else r for r in rows]
    freqs = [r.get("freq_hz") for r in rows] + [r.get("delta_freq_hz") for r in rows]
    d = _freq_decimals(f for f in freqs if f is not None)
    # 位置の列: 周波数は MHz（全部の行で桁をそろえる）、時間は ms の小数3桁（マイクロ秒まで）
    scale, d = (1e3, 3) if time else (1e6, d)
    out = []
    for r in rows:
        f, v, df, dv = r.get("freq_hz"), r.get("value"), r.get("delta_freq_hz"), r.get("delta_value")
        unit = r.get("unit") or default_unit
        # 値の桁と Δ値の書き方は単位で決める（|Γ| は小数3桁・|Γ|・SWR の差は単位を添える: 設計R §3。PNG の表と同じ関数）
        out.append([
            ("  " if r.get("child") else "") + str(r.get("name", "")),
            str(r["trace"]) if r.get("trace") is not None else "",
            f"{f / scale:.{d}f}" if f is not None else "",
            value_cell(v, unit),
            str(r.get("ref") or ""),
            f"{df / scale:+.{d}f}" if df is not None else "",
            delta_cell(dv, unit, db_offset=r.get("kind") == "level_cursor" and bool(r.get("child"))),
            str(r.get("note") or ""),
        ])
    return out


def to_tsv(rows: list[dict], default_unit: str = "dBm", freq_offset_hz: int = 0) -> str:
    """タブ区切りの表（見出し付き。子の行と機能の結果も含める: G §3.3・§3.5）。"""
    lines = ["\t".join(columns(rows_x_kind(rows)))]
    for cells in format_rows(rows, default_unit, freq_offset_hz):
        cells = [c.strip() for c in cells]
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def png_rows(rows: list[dict], lines: bool = True, axis_unit: Optional[str] = None) -> list[dict]:
    """PNG に渡す行。lines は機能の線（G §3.6）を描くか（画面で出していないなら PNG にも描かない）。

    axis_unit を渡すと、縦軸と単位の違う（描いていない）トレースの行に hidden を付ける（表には出し、グラフには
    描かない: 画面と同じ。設計R §3）。
    """
    keys = ("id", "name", "kind", "trace", "freq_hz", "value", "unit", "ref", "delta_freq_hz", "delta_value", "child",
            "note", "x_kind")
    return [{k: r.get(k) for k in keys} | {"line": r.get("line") if lines else None,
                                           "hidden": bool(axis_unit and r.get("unit") and r["unit"] != axis_unit)}
            for r in rows]


# ------------------------------------------------------------------ 行を消す・セルを直す（G §3.7。Qt と Web で同じ決まり）

XY_KEYS = {"X1": "x1_hz", "X2": "x2_hz", "Y1": "y1", "Y2": "y2"}
# 直せる列（COLUMNS の並び）
COL_FREQ, COL_VALUE, COL_DFREQ, COL_DVALUE = 2, 3, 5, 6


def remove_command(r: dict, snap: Optional[dict]) -> Optional[tuple[str, dict]]:
    """その行だけを消す命令（子の行を消しても親のマーカーは消さない）。消せない行は None。

    マーカー → マーカー／オフセットのカーソル → そのカーソル／帯域幅 → その N だけ止める／シェープファクタ → 2つ目の
    N を止める（2つの N があるときだけ出る行のため）／しきい値 → 交点を止める／X1〜Y2 → そのカーソルを外す
    """
    rid, kind = r["id"], r.get("kind")
    if kind == "marker" and not r.get("parent"):
        return "marker_remove", {"id": rid}
    if r.get("offset"):
        typ, idx = r["offset"]
        return "marker_offset_remove", {"id": r["parent"], "type": typ, "index": idx}
    if rid in XY_KEYS:
        return "set_xy", {XY_KEYS[rid]: None}
    if kind == "function":
        mid = r["parent"]
        m = next((x for x in (snap or {}).get("markers", []) if x["id"] == mid), None)
        if m is None:
            return None
        ns = [b["n_db"] for b in m.get("bandwidth") or []]
        tail = rid[len(mid) + 1:]
        if tail == "th":
            return "marker_function", {"id": mid, "threshold": None}
        if tail == "sf" and len(ns) > 1:
            return "marker_function", {"id": mid, "bandwidth_db": ns[:-1]}
        if tail.startswith("bw"):
            return "marker_function", {"id": mid, "bandwidth_db": [n for n in ns if f"bw{n:g}" != tail]}
    return None


def row_ops(r: dict, snap: Optional[dict]) -> list[Optional[dict]]:
    """行の「操作」の項目（G §3.4・§3.7）。Qt の行のドロップダウンと右クリック、Web の行のドロップダウンが同じ並び・同じ語。

    項目: {"name"（部品の名前）, "text", "cmd", "args", "why"（使えない理由。None で使える）}。None は区切り線。
    マーカーの行: Center に・REF に・デルタの2点を Start・Stop に（デルタの基準が無ければ理由付きで無効: U-08）・
    消す。子の行・XY カーソル: その行だけを消す（remove_command）。消せない行は項目なし。
    """
    rid = r["id"]
    if r.get("kind") == "marker" and not r.get("parent"):
        # ゼロスパンの位置は時間なので、周波数の条件（Center・Start/Stop）にはできない（設計N §1。理由: U-08）
        freq_only = ZT["freq_only"] if r.get("x_kind") == "time" else None
        return [
            {"name": "row_center", "text": texts.LABELS["row_center"], "cmd": "marker_to",
             "args": {"id": rid, "target": "center"}, "why": freq_only},
            {"name": "row_ref", "text": texts.LABELS["row_ref"], "cmd": "marker_to",
             "args": {"id": rid, "target": "ref"}, "why": None},
            {"name": "row_span", "text": texts.MENU_ITEMS["marker_span"], "cmd": "marker_to",
             "args": {"id": rid, "target": "span"},
             "why": freq_only or (None if r.get("ref") else texts.REASON_TEXTS["delta_ref"])},
            None,
            {"name": "row_remove", "text": texts.w("row_remove", m=rid), "cmd": "marker_remove", "args": {"id": rid},
             "why": None},
        ]
    rm = remove_command(r, snap)
    if rm is None:
        return []
    text = (texts.LABELS["row_remove_cursor"] if r.get("offset")
            else texts.w("row_unset", m=rid) if rid in XY_KEYS else texts.LABELS["row_remove_function"])
    return [{"name": "row_remove", "text": text, "cmd": rm[0], "args": rm[1], "why": None}]


def edit_spec(r: dict, col: int, rows: list[dict], snap: Optional[dict]) -> Optional[dict]:
    """そのセルを打って直すときの命令の形。直せないセルは None。

    形: {"cmd", "args", "arg"（打った値を入れる引数）, "parse"（"freq" | "db"）, "base"（足す値）,
         "minus_offset"（表の周波数は周波数オフセット込みなので差し引く）}
    マーカー: 周波数・（デルタの相手があれば）Δ周波数（相手 + Δ）／周波数のカーソル: Δ周波数／レベルのカーソル: Δ値
    （向き付き）／X1・X2: 周波数、X2 の Δ周波数（X1 + Δ）／Y1・Y2: 値、Y2 の Δ値（Y1 + Δ）。マーカーの値は直せない
    """
    rid, kind = r["id"], r.get("kind")
    # ゼロスパンの位置は時間（ms で打つ。設計N §1）。周波数オフセットは引かない
    pos = "time" if r.get("x_kind") == "time" else "freq"
    shifted = pos == "freq"

    def spec(cmd, args, arg, parse, base=0, minus_offset=False):
        return {"cmd": cmd, "args": args, "arg": arg, "parse": parse, "base": base, "minus_offset": minus_offset}

    if kind == "marker" and not r.get("parent"):
        if col == COL_FREQ:
            return spec("marker_set", {"id": rid}, "freq_hz", pos, minus_offset=shifted)
        if col == COL_DFREQ and r.get("ref"):
            ref = next((x for x in rows if x["id"] == r["ref"]), None)
            if ref is not None and ref.get("freq_hz") is not None:
                return spec("marker_set", {"id": rid}, "freq_hz", pos, base=int(ref["freq_hz"]))
        return None
    if r.get("offset"):
        typ, idx = r["offset"]
        if (typ, col) in (("freq", COL_DFREQ), ("level", COL_DVALUE)):
            return spec("marker_offset_set", {"id": r["parent"], "type": typ, "index": idx}, "delta",
                        "freq" if typ == "freq" else "db")
        return None
    xy = (snap or {}).get("xy") or {}
    if rid in ("X1", "X2") and col == COL_FREQ:
        return spec("set_xy", {}, XY_KEYS[rid], pos, minus_offset=shifted)
    if rid == "X2" and col == COL_DFREQ and xy.get("x1_hz") is not None:
        return spec("set_xy", {}, "x2_hz", pos, base=int(xy["x1_hz"]))
    if rid in ("Y1", "Y2") and col == COL_VALUE:
        return spec("set_xy", {}, XY_KEYS[rid], "db")
    if rid == "Y2" and col == COL_DVALUE and xy.get("y1") is not None:
        return spec("set_xy", {}, "y2", "db", base=float(xy["y1"]))
    return None


def edit_command(spec: dict, text: str, freq_offset_hz: int = 0) -> tuple[str, dict]:
    """打った文字を命令にする（読めない文字は ParseError）。Web の app.js も同じ計算をする。"""
    from ..core.units import parse_db, parse_freq_hz, parse_time_us

    if spec["parse"] == "time":
        value = int(spec["base"]) + parse_time_us(text)
    elif spec["parse"] == "freq":
        v = parse_freq_hz(text) - (freq_offset_hz if spec["minus_offset"] else 0)
        value = int(spec["base"]) + v
    else:
        value = float(spec["base"]) + parse_db(text)
    return spec["cmd"], {**spec["args"], spec["arg"]: value}
