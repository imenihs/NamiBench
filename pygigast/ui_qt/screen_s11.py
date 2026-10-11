"""反射（S11）の基準の手順・縦軸の表示の形式・|Γ|・SWR の縦軸について、コアの状態から画面に出すものを作る
（設計E §6.5・設計R §3・§5.1・設計J §4.1）。

Qt と Web が同じものを使う（J §5: 同じ構成・同じ語。同じ処理を2回書かない）。Qt はここを直接呼び、Web の口は
screen_model.ui_view が state に `ui` として付けて送る。Qt を import しない。

- 表示の形式: 縦軸に1つ（表示の設定 view.s11_format。単位と同じく「振幅の詳細」の欄: 2026-10-09 ユーザー決定）。
  S11 で測った比のトレース（測定中・固定・参照）は全部この形式で出る。使えない形式は理由付きで無効（U-08。位相系は
  「この機器は振幅だけ」）
- 縦軸の単位: 表示している測定中のトレースのうち番号の一番小さいものの単位（無ければ表示している固定のトレース）。
  1つの目盛りに違う単位を混ぜない。単位の違う固定・参照トレースは描かず、「表示」のチェックを無効にして理由は
  ツールチップだけに出す（行に文を書かない）
- |Γ|・SWR の縦軸: 下端が決まっていて、1目盛の大きさだけを選ぶ（core/trace/formats.py）
- S11 の基準: オープン → ショートを1段ずつ（次に押すボタンを強調し、測ったオープンの時刻と範囲を出す）
"""
from __future__ import annotations

from typing import Optional

from ..core.instrument_display import DB_PER_DIV_CHOICES
from ..core.report_layout import DIVISIONS
from ..core.trace import formats
from . import texts
from .texts import LABELS, REASON_TEXTS, TEMPLATES, tpl


def _r(key: str, **kw) -> str:
    text = REASON_TEXTS[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


# ---------------------------------------------------------------- 表示の形式（設計R §3）

def format_name(code: str) -> str:
    """縦軸の形式の名前。形式が当たるのは S11 の比だけなので、dB の形式は「リターンロス（dB）」。"""
    return texts.AXIS_FORMAT_NAMES.get(code, code)


def _has_s11_traces(st: dict) -> bool:
    """S11 で測った比の値を持つトレース（測定中・固定・開いたファイルの参照トレース）があるか。"""
    for t in st.get("traces") or []:
        if t.get("has_values") and t.get("quantity") == "ratio" and t.get("measurement") == "s11":
            return True
    for f in st.get("files") or []:
        for t in f.get("traces") or []:
            if t.get("quantity") == "ratio" and (t.get("info") or {}).get("measurement") == "s11":
                return True
    return False


def axis_format_view(st: dict) -> dict:
    """「振幅の詳細」の「S11 の表示」の欄（縦軸の表示の形式: 表示の設定 view.s11_format）。

    形: {"value": 選んでいる形式, "items": [{"code", "text", "enabled", "tip"}], "reason": 欄ごと使えない理由 or None,
         "tip": 欄のツールチップ}
    - 選べるのは S11 モードのとき（基準を取る前に選んでおける）と、S11 で測った比のトレースがあるとき（固定・参照も
      この形式で出る）。ほかのときは選んでも何も変わらないので、理由付きで無効（U-08）
    - 位相系は位相を測れる機器だけ（GigaSt は振幅だけ: 設計R §2）。一覧に残して無効にし理由
    """
    mode = (st.get("sweep") or {}).get("mode")
    # 位相を測れる機器か（トレースの値の種類。値の無いトレースはつないでいる機器のプロファイルの値の種類: コア）
    phase = any(t.get("value_kind") == "complex" for t in st.get("traces") or [])
    items = []
    for code in formats.FORMATS:
        if code in formats.VECTOR_FORMATS:
            why = texts.S11_ERRORS["format_not_yet"] if phase else _r("format_vector")
        else:
            why = None
        items.append({"code": code, "text": format_name(code), "enabled": why is None, "tip": why or ""})
    value = (st.get("view") or {}).get("s11_format")
    value = value if value in formats.AXIS_FORMATS else formats.DB
    reason = None if mode == "s11" or _has_s11_traces(st) else _r("format_s11_context")
    tip = LABELS["axis_format_tip"] + (f" — {reason}" if reason else "")
    return {"value": value, "items": items, "reason": reason, "tip": tip}


# ---------------------------------------------------------------- 縦軸（設計J §4.1・設計R §3）

def axis_unit(st: dict) -> str:
    """縦軸の単位: 表示していて値のある測定中（固定でない）のトレース（番号の小さい順）の最初の単位。無ければ表示して
    いて値のある固定のトレースの最初の単位。どれも無ければ振幅の単位か dBm（コアの _axis_trace と同じ決まり）。"""
    shown = [t for t in st.get("traces") or [] if t.get("visible") and t.get("has_values") and t.get("unit")]
    live = [t for t in shown if t.get("action") != "fixed"]
    if live or shown:
        return (live or shown)[0]["unit"]
    return (st.get("amplitude") or {}).get("unit") or "dBm"


def trace_drawn_reason(t: dict, axis: str) -> Optional[str]:
    """トレースを描かない理由（縦軸と単位が違う）。描くなら None。画面は「表示」のチェックを無効にし、理由はツールチップ
    に出す（行に文を書かない）。測定中のトレースは縦軸を決める側なので、ふつうは固定のトレースだけが当たる。

    チェックが入っているかに関係なく、値を持ち縦軸と単位が違う間は理由を返す（設計E §6.5.1）。チェックの外れた
    トレースだけ有効にすると、入れたとたんに描かれずチェックが無効になり、外せなくなる。ただしチェックの外れた測定中の
    トレースは、入れればそれが縦軸を決める（axis_unit: 測定中のトレースが先）ので描ける。"""
    unit = t.get("unit")
    if not unit or unit == axis or not t.get("has_values"):
        return None
    if not t.get("visible") and t.get("action") != "fixed":
        return None
    return _r("trace_unit_differs", axis=axis, unit=unit)


def axis_view(st: dict, unit: str) -> dict:
    """縦軸の決め方。dB 系は REF レベル（一番上）と dB/div（値は画面が振幅の状態から持つ）、|Γ|・SWR は下端が決まり
    1目盛の大きさだけを選ぶ（top・div はここで決まる）。

    形: {"linear", "unit", "top", "div", "bottom", "div_label", "div_key", "div_choices": [{"value", "text"}],
         "ref_reason": REF の欄・自動・REF を動かす操作が使えない理由, "decimals": 目盛の数字の小数の桁}
    """
    if formats.is_linear_unit(unit):
        ax = formats.LINEAR_AXES[unit]
        per = formats.per_div_of(unit, st.get("amplitude"))
        return {"linear": True, "unit": unit, "top": formats.linear_top(unit, per, DIVISIONS), "div": per,
                "bottom": ax["bottom"], "div_label": tpl_label("per_div", unit=unit), "div_key": ax["key"],
                "div_choices": [{"value": float(v), "text": f"{v:g}"} for v in ax["choices"]],
                "ref_reason": _r("ref_linear", unit=unit, bottom=f"{ax['bottom']:g}"),
                "decimals": formats.tick_decimals(unit, per)}
    return {"linear": False, "unit": unit, "top": None, "div": None, "bottom": None, "div_label": "dB/div",
            "div_key": "db_per_div", "div_choices": [{"value": v, "text": f"{v:g} dB"} for v in DB_PER_DIV_CHOICES],
            "ref_reason": None, "decimals": 1}


def threshold_spec(unit: Optional[str]) -> dict:
    """しきい値との交点の欄の決まり（texts.THRESHOLD_SPECS。dB 系の単位（dBm・dBμV など）は dB と同じ）。"""
    return {**texts.THRESHOLD_SPECS[unit if formats.is_linear_unit(unit) else "dB"], "unit": unit or ""}


def div_width_samples() -> list[str]:
    """1目盛の欄の一番長い文字の見本（単位が変わっても欄の幅を動かさない: S-02）。"""
    out = [f"{v:g} dB" for v in DB_PER_DIV_CHOICES]
    for ax in formats.LINEAR_AXES.values():
        out += [f"{v:g}" for v in ax["choices"]]
    return out


def tpl_label(key: str, **kw) -> str:
    text = LABELS[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def tick_text(v: float, decimals: int) -> str:
    """縦軸の目盛の数字（負号は「−」。dB 系は REF の欄と同じ 0.1 まで: units.level_text と同じ形）。"""
    s = f"{float(v):.{decimals}f}"
    if s.startswith("-") and float(s) == 0.0:
        s = s[1:]
    return s.replace("-", "−")


def value_text(v: Optional[float], unit: Optional[str]) -> str:
    """読み値の文字（マーカー表・マウスの位置・PNG の表で同じ）。|Γ| は小数3桁、ほかは2桁。"""
    return formats.value_cell(v, unit)


# ---------------------------------------------------------------- S11 の基準（設計E §6.5）

def _range_name(kind: Optional[str]) -> str:
    return LABELS["s11_range_fine"] if kind == "fine" else LABELS["s11_range_wide"]


def _common_reason(st: dict) -> Optional[str]:
    if (st.get("connection") or {}).get("state") != "connected":
        return REASON_TEXTS["needs_connection"]
    if (st.get("sweep") or {}).get("mode") != "s11":
        return _r("s11_mode_only")
    if any(t.get("kind") == "reference_take" for t in st.get("tasks") or []):
        return REASON_TEXTS["taking_reference"]
    return texts.live_lock_reason(st)  # 再生中は基準を取らない（設計P §5）


def s11_view(st: dict) -> dict:
    """信号源のタブの S11 の手順: ボタンの使えない理由・次に押すボタン・測ったオープンの文・ツールチップ。

    TG の「広く取る」「いまの条件で細かく取る」は S11 では使えない（基準はオープン・ショートの2段で取る）。
    """
    from .texts_devices import hhmm

    o = (st.get("normalization") or {}).get("s11_open")
    common = _common_reason(st)
    open_kind = (o or {}).get("kind")
    return {
        "open_reason": common,
        "short_reason": common or (None if o else _r("s11_needs_open")),
        "range_reason": common,
        "next": "short" if o else "open",
        "state_text": tpl_label("s11_open_done", time=hhmm(o.get("taken_at")), range=_range_name(open_kind)) if o
        else "",
        "short_tip": tpl_label("s11_short_tip", range=_range_name(open_kind)),
        "open_kind": open_kind,
    }


def ref_state_text(st: dict) -> str:
    """信号源のタブの基準の状態の文（状態バーの「基準: …」と同じ。使わないモードでは選んでいる基準か「未取得」）。"""
    import datetime

    n = st.get("normalization") or {}
    text = texts.normalization_text(n)
    if text:
        return text
    sel = n.get("selected")
    if not sel:
        return LABELS["ref_none"]  # 無いこと自体が情報（U-15）
    ts = sel.get("taken_at")
    when = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M") if ts else ""
    value = texts.ref_value_text(sel.get("value_db"))  # 基準の値（設計E §6.7。0 dB なら出さない）
    if value:
        return tpl("ref_selected_value", name=sel.get("name", ""), when=when, value=value)
    return tpl_label("ref_selected", name=sel.get("name", ""), when=when)


def ref_value_view(st: dict) -> dict:
    """信号源のタブの「基準の値」の欄（設計E §6.7）: 使えない理由とツールチップ。S11 の基準には使わない（全反射 = 0dB）。
    欄の値は画面ごとに持つ（名前・平均の欄と同じく、取るときに送る値）。"""
    reason = REASON_TEXTS["ref_value_s11"] if (st.get("sweep") or {}).get("mode") == "s11" else None
    return {"reason": reason, "tip": LABELS["ref_value_tip"]}


def ref_row(r: dict) -> dict:
    """基準の一覧の1行（Qt の一覧と Web の表で同じ）: 文と「基準の値…」の使えない理由（S11 の基準には無い）。

    文は「● 名前  TG  取得日時」に、基準の値が 0 dB でなければ「  +40.0 dB」を足す（U-15）。
    """
    import datetime

    ts = r.get("taken_at")
    when = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M") if ts else ""
    mark = "● " if r.get("selected") else "   "
    text = f"{mark}{r.get('name', '')}  {str(r.get('mode') or '').upper()}  {when}"
    value = texts.ref_value_text(r.get("value_db"))
    if value:
        text += f"  {value}"
    return {"text": text, "value_reason": REASON_TEXTS["ref_value_s11"] if r.get("mode") == "s11" else None}


def tg_take_reason(st: dict) -> Optional[str]:
    """TG の「広く取る」「いまの条件で細かく取る」の使えない理由（S11 では2段の手順で取る）。"""
    sw = st.get("sweep") or {}
    if (st.get("connection") or {}).get("state") != "connected":
        return REASON_TEXTS["needs_connection"]
    if sw.get("mode") == "s11":
        return _r("tg_take_in_s11")
    if sw.get("mode") != "tg":
        return texts.ERRORS["mode_not_normalizable"]
    if any(t.get("kind") == "reference_take" for t in st.get("tasks") or []):
        return REASON_TEXTS["taking_reference"]
    return texts.live_lock_reason(st)  # 再生中は基準を取らない（設計P §5）


def tg_wide_reason(st: dict) -> Optional[str]:
    """TG の「広く取る」の使えない理由。FFT の機器（PlutoSDR）は RBW が同じでないと基準が当たらないので、
    いまの条件で取る（設計E §6.4 の rbw_mismatch）。"""
    if (st.get("connection") or {}).get("sweep_kind") == "fft" and (st.get("sweep") or {}).get("mode") == "tg":
        return tg_take_reason(st) or texts.ERRORS["reference_wide_fft"]
    return tg_take_reason(st)


def s11_quick(st: dict) -> dict:
    """上段の S11 のボタン（「基準を取る」の場所: J §4.2）: 次の1段の名前・ツールチップ・送る中身・状態の文。"""
    from .texts_devices import hhmm

    o = (st.get("normalization") or {}).get("s11_open")
    step = "short" if o else "open"
    q = texts.S11_QUICK_LABELS
    # 「広く」で取る（TG の「基準を取る」と同じ）。ショートはオープンと同じ範囲（コアが合わせる）
    take = {"standard": step, "kind": None if o else "wide", "show_zero": step == "short"}
    return {"label": q[step], "tip": q[step + "_tip"], "take": take,
            "open_text": tpl("s11_open_quick", time=hhmm(o.get("taken_at"))) if o else ""}


def s11_ref_name(time_text: str) -> str:
    return TEMPLATES["ref_s11_name"].replace("{time}", time_text)
