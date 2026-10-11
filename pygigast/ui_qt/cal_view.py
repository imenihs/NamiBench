"""校正の画面に出すもの（設計K §4.1〜§4.5）。Qt の cal_dialog.py と Web の app_cal.js が同じ中身を映す（J §5）。

状態の `calibration_work`（コアの作業の値。数のまま）から、画面の文字・表の行・使えない理由を作る。Web へは
状態の ui.cal で届く（api/web/view.py）。Qt を import しない（Web の画面のサーバーが Qt なしで使う）。
"""
from __future__ import annotations

import math
from typing import Optional

from ..core.level import cal_work as cw
from ..core.level import pluto_gain as pg
from ..core.units import MINUS, format_freq, minus_sign
from . import texts
from .texts_cal import CAL_MENU_OF_KIND, CAL_TEXTS, CAL_WIRING

C = CAL_TEXTS

# 確かめるグラフの横軸（周波数特性の表の範囲）。PlutoSDR の利得の補正は測る周波数の上端（6000MHz）
GRAPH_MAX_MHZ = 12000
PLUTO_GRAPH_MAX_MHZ = 6000


def _suffix(kind: str) -> str:
    """閉じる・切り替えるときに聞く文の種類（測った点・周波数・読み込んだ値・測った値）。"""
    return {cw.KIND_FREQ: "points", cw.KIND_LEGACY: "legacy", cw.KIND_PLUTO_GAIN: "freqs"}.get(kind, "value")


def w(key: str, **kw) -> str:
    text = C[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def kind_label(kind: str) -> str:
    """何を校正するかの名前 = 開いたメニューの項目の名前（「…」を除く。窓の題も同じ: U-24）。"""
    return texts.MENU_ITEMS[CAL_MENU_OF_KIND[kind]].rstrip("…")


def db_text(v: Optional[float], signed: bool = True) -> str:
    """dB の値（0.01dB まで・負号は「−」）。値が無ければ空。"""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    s = f"{float(v):+.2f}" if signed else f"{float(v):.2f}"
    s = minus_sign(s)
    return "0.00" if s in ("+0.00", MINUS + "0.00") else s


def amp_text(raw: Optional[float]) -> str:
    """NF の増幅度の値（旧アプリと同じ amp_raw）を、画面では倍率で出す（×0.917。内部の値を出さない: U-22）。"""
    if raw is None:
        return ""
    return f"×{10.0 ** (float(raw) / 200.0):.3f}"


def mhz_text(hz: int) -> str:
    """表の周波数（MHz。100MHz おきなので整数）。"""
    return f"{int(hz) // 1_000_000}" if int(hz) % 1_000_000 == 0 else format_freq(int(hz), 6).replace(" MHz", "")


def freq_field_text(hz: int) -> str:
    """SG に合わせる周波数の書き方（「1500.000MHz」: K §4.2 の画面の例と同じ）。"""
    return f"{int(hz) / 1_000_000:.3f}MHz"


def _item_name(item: str) -> str:
    if item == "level":
        return C["item_level"]
    if item == cw.PLUTO_ITEM:
        return C["item_pluto_gain_max"]
    if item.startswith("rbw:"):
        return w("item_rbw", rbw=format_freq(int(item[4:])))
    return C["item_nf_amp"] if item == "nf_amp" else C["item_nf_enr"]


def _value_text(item: str, v: Optional[float]) -> str:
    if v is None:
        return C["value_none"]
    if item == "nf_amp":
        return amp_text(v)
    return db_text(v, signed=item != "nf_enr")


def _diff_text(item: str, before: Optional[float], after: Optional[float]) -> str:
    if before is None or after is None or item == "nf_amp":
        return ""
    return db_text(after - before)


def _base_text(base: Optional[dict]) -> str:
    if not base:
        return C["cal_none"]
    at = (base.get("created_at") or "").replace("T", " ")[:16]
    src = base.get("source") or {}
    if src.get("kind") == cw.SOURCE_LEGACY:
        return w("cal_legacy", name=src.get("file_name") or "", at=at)
    return w("cal_work", at=at)


def _device_text(st: dict, wk: dict) -> str:
    """校正を当てる機器: 名前（シリアル番号）（K §4.1 の 1）。"""
    conn = st.get("connection") or {}
    serial = wk.get("serial")
    if not serial:
        return ""
    name = conn.get("name") if (conn.get("identity") or {}).get("serial") == serial else None
    return w("device_name", name=name, serial=serial) if name and name != serial else serial


def _reason(code: Optional[str], wk: dict) -> Optional[str]:
    if code is None:
        return None
    if code == "calibration_device_changed":
        return w("device_other", serial=wk.get("serial") or "")
    return texts.ERRORS.get(code, code)


def _message_text(m: Optional[dict]) -> Optional[str]:
    """やめた・測れなかったこと（その手順のそばに出す: U-25）。"""
    if not m:
        return None
    if m.get("code") == "cancelled":
        return texts.TASK_CANCELLED.get(m.get("task") or "", texts.TEMPLATES["cancelled_generic"])
    return texts.ERRORS.get(m.get("code") or "", m.get("code") or "")


def view(st: dict) -> Optional[dict]:
    """校正の画面の中身。作業が無ければ None（画面は閉じる）。"""
    wk = (st or {}).get("calibration_work")
    if not wk:
        return None
    kind = wk["kind"]
    measuring = bool(wk.get("task_id"))
    device_reason = _reason(wk.get("device_reason"), wk)
    tasks = {t.get("task_id"): t for t in st.get("tasks") or []}
    task = tasks.get(wk.get("task_id"))
    out: dict = {
        "kind": kind,
        "title": kind_label(kind),
        "steps": [C["step_prepare"], C["step_load"] if kind == cw.KIND_LEGACY else C["step_measure"],
                  C["step_check"], C["step_save"]],
        "kinds": [{"code": k, "label": kind_label(k)} for k in cw.KINDS],
        "device": _device_text(st, wk),
        "device_reason": device_reason,
        "base": _base_text(wk.get("base")),
        "need": C["need_" + kind],
        "wiring": C.get("wiring_" + kind, ""),
        "diagram": list(CAL_WIRING.get(kind, ())),
        "measuring": measuring,
        "task_id": wk.get("task_id"),
        "progress": None if task is None else {"done": task.get("done", 0), "total": task.get("total", 0),
                                                "text": texts.task_label(task)},
        "message": _message_text(wk.get("message")),
        "unsaved": int(wk.get("unsaved") or 0),
        "savable": bool(wk.get("savable")),
        "saved_text": w("saved", file=wk["saved"]["file"]) if wk.get("saved") else "",
    }
    # 次へ（準備 → 測る）を押せない理由（入口で止める: U-02）。旧ファイルの取り込みは機器の選択だけ要る
    out["next_reason"] = device_reason
    inputs = wk.get("inputs") or {}
    out["inputs"] = {
        "sg_level_dbm": float(inputs.get("sg_level_dbm", cw.SG_LEVEL_DEFAULT_DBM)),
        "cable_loss_db": float(inputs.get("cable_loss_db", cw.CABLE_LOSS_DEFAULT_DB)),
        "rbw_extra": bool(inputs.get("rbw_extra")),
        "nf_freq_hz": int(inputs.get("nf_freq_hz", cw.NF_FREQ_DEFAULT_HZ)),
        "ext_atten_db": float(inputs.get("ext_atten_db", 40.0)),
    }
    out.update(_measure_view(wk, kind, measuring, device_reason))
    out.update(_check_view(wk, kind))
    # 閉じる・切り替えるときに聞くこと（U-18）。保存していない作業が無ければ聞かない
    # 閉じるときの「保存して閉じる」を押せるか（保存できる値があり、確かめ待ちが無い）
    out["close_savable"] = out["savable"] and not wk.get("pending")
    n = out["unsaved"]
    if n:
        suffix = _suffix(kind)
        out["close_question"] = w("lost_" + suffix, n=n)
        out["switch_question"] = w("switch_" + suffix, n=n)
        out["close_save_label"] = C["do_close_save_" + suffix] if suffix in ("points", "freqs") else C["do_close_save"]
    else:
        out["close_question"] = out["switch_question"] = out["close_save_label"] = None
    return out


def _measure_view(wk: dict, kind: str, measuring: bool, device_reason: Optional[str]) -> dict:
    """測る（読み込む）の中身。"""
    out: dict = {}
    pend = wk.get("pending")
    busy = C["measuring"] if measuring else None
    if kind == cw.KIND_FREQ:
        pts = wk.get("points") or []
        cur = pts[wk.get("current", 0)] if pts else None
        out["instruction"] = w("set_sg", freq=freq_field_text(cur["measure_hz"])) if cur else ""
        why = device_reason or busy or (C["need_answer"] if pend else None)
        out["measure_reason"] = why
        out["skip_reason"] = busy or (C["need_answer"] if pend else None)
        out["pending"] = None if not pend else {
            "index": pend["index"], "title": C["confirm_title"],
            "text": w("confirm_big", diff=db_text(pend["diff_db"])),
            "ok": C["do_put"]}
    elif kind == cw.KIND_RBW:
        out["measure_reason"] = device_reason or busy
        out["rbw_headers"] = [C["col_freq"]] + [w("col_rbw_peak", rbw=format_freq(r)) for r in cw.RBW_LIST_HZ] + \
            [w("col_rbw_offset", rbw=format_freq(r)) for r in cw.RBW_LIST_HZ if r != cw.RBW_BASE_HZ]
        rows = []
        for f in wk.get("rbw") or []:
            cells = [mhz_text(f["freq_hz"])]
            cells += [db_text(f["peaks_dbm"].get(str(r)), signed=False) for r in cw.RBW_LIST_HZ]
            cells += [db_text(f["offsets_db"].get(str(r))) for r in cw.RBW_LIST_HZ if r != cw.RBW_BASE_HZ]
            rows.append({"freq_hz": f["freq_hz"], "cells": cells, "used": f["freq_hz"] == cw.RBW_FREQ_HZ})
        out["rbw_rows"] = rows
    elif kind == cw.KIND_NF:
        nf = wk.get("nf") or {}
        out["measure_reason"] = device_reason or busy
        out["nf_rows"] = [
            {"state": cw.NF_WITH_ATT, "text": C["nf_with_att"],
             "value": w("nf_y", db=db_text(nf.get("with_att_db"), signed=False)) if nf.get("with_att_db") is not None
             else ""},
            {"state": cw.NF_WITHOUT_ATT, "text": C["nf_without_att"],
             "value": w("nf_y", db=db_text(nf.get("without_att_db"), signed=False))
             if nf.get("without_att_db") is not None else ""},
        ]
        out["nf_result"] = "" if nf.get("diff_db") is None else (
            w("nf_diff", db=db_text(nf["diff_db"])) + ("  " + C["item_nf_amp"] + " " + amp_text(nf["amp_raw"])
                                                       if nf.get("amp_raw") is not None else ""))
    elif kind == cw.KIND_PLUTO_GAIN:
        out["measure_reason"] = device_reason or busy
        out["pluto_headers"] = [C["col_freq"], C["col_gain_range"], C["col_tx_atten"], C["col_corr"], C["col_state"]]
        rows = []
        for r in (wk.get("pluto") or {}).get("rows") or []:
            done = r["status"] == cw.POINT_MEASURED
            cells = [mhz_text(r["freq_hz"]), "", "", "", ""]
            if done:
                cells[1] = w("range", lo=_int_text(r["gain_lo_db"]), hi=_int_text(r["gain_hi_db"]))
                cells[2] = " / ".join(db_text(a, signed=False) for a in r["tx_atten_db"])
                cells[3] = w("range", lo=db_text(r["min_db"]), hi=db_text(r["max_db"]))
                cells[4] = C["st_measured"]
            rows.append({"index": r["index"], "cells": cells, "measured": done})
        out["pluto_rows"] = rows
    else:
        lg = wk.get("legacy")
        out["measure_reason"] = device_reason or busy
        out["legacy_file"] = lg["file_name"] if lg else C["file_none"]
        out["file_title"] = C["file_title"]
    return out


def _int_text(v) -> str:
    return minus_sign(str(int(v)))


def _check_view(wk: dict, kind: str) -> dict:
    """確かめる: 前の値と新しい値の表と、周波数のグラフ（差が大きい点に印: K §4.1 の 3）。"""
    legacy = kind == cw.KIND_LEGACY
    rows = []
    for it in wk.get("items") or []:
        item = it["item"]
        warn = None
        if legacy and it.get("warn"):
            warn = w("measured_warn", diff=_measured_diff(item, it))
        rows.append({"item": item, "name": _item_name(item), "before": _value_text(item, it.get("before")),
                     "after": _value_text(item, it.get("after")), "diff": _diff_text(item, it.get("before"), it.get("after")),
                     "big": bool(it.get("big")),
                     "measured": _value_text(item, it.get("measured")) if legacy and it.get("measured") is not None
                     else "",
                     "warn": warn})
    points = []
    pend = wk.get("pending") or {}
    pluto = kind == cw.KIND_PLUTO_GAIN
    raw_points = ((wk.get("pluto") or {}).get("rows") or []) if pluto else (wk.get("points") or [])
    for p in raw_points:
        state = {cw.POINT_MEASURED: C["st_measured"], cw.POINT_SKIPPED: C["st_skipped"]}.get(p["status"], "")
        if pend and pend.get("index") == p["index"]:
            state = C["st_pending"]
        warn = None
        if legacy and p.get("warn"):
            warn = w("measured_warn", diff=_measured_diff(f"freq:{p['freq_hz']}", {"after": p["after_db"], **p}))
        points.append({"index": p["index"], "freq": mhz_text(p["freq_hz"]), "before": db_text(p["before_db"]),
                       "after": db_text(p["after_db"]), "diff": db_text(p["after_db"] - p["before_db"]),
                       "state": state, "big": bool(p.get("big")), "current": p["index"] == wk.get("current"),
                       "measured": db_text(p.get("measured")) if legacy else "", "warn": warn,
                       "status": p["status"]})
    limit = pg.BIG_DIFF_DB if pluto else cw.CONFIRM_DIFF_DB
    out: dict = {"items": rows, "points": points, "legacy": legacy,
                 "big_tip": w("big_tip", limit=f"{limit:g}"),
                 "headers": {"freq": C["col_freq"], "before": C["col_before"], "after": C["col_after"],
                             "diff": C["col_diff"], "state": C["col_state"], "item": C["col_item"],
                             "measured": C["col_measured"]}}
    if kind in (cw.KIND_FREQ, cw.KIND_LEGACY, cw.KIND_PLUTO_GAIN) and points:
        raw = raw_points
        out["graph"] = {
            "x_label": C["graph_x"], "y_label": C["graph_y"], "before_label": C["graph_before"],
            "after_label": C["graph_after"], "x_max": PLUTO_GRAPH_MAX_MHZ if pluto else GRAPH_MAX_MHZ,
            "freqs_mhz": [p["freq_hz"] / 1e6 for p in raw],
            "before": [p["before_db"] for p in raw],
            "after": [p["after_db"] for p in raw],
            # 印: 前との差が大きい点（周波数特性）・校正で測った値と大きく違う点（旧ファイル）
            "marks": [p["index"] for p in raw if p.get("big") or (legacy and p.get("warn"))],
        }
    else:
        out["graph"] = None
    # 確かめ待ちの点がある間は保存できない（答えてから: K §4.2）
    out["save_reason"] = None if wk.get("savable") and not wk.get("pending") else _save_reason(wk, kind)
    return out


def _measured_diff(item: str, it: dict) -> str:
    m, v = it.get("measured"), it.get("after")
    if m is None or v is None:
        return ""
    if item == "nf_amp":
        # 増幅度は倍率で比べる（内部の値の差を出さない: U-22）
        return f"{abs(10.0 ** ((v - m) / 200.0) - 1.0) * 100.0:.1f} %"
    return db_text(abs(v - m), signed=False) + " dB"


def main_close_line(st: dict) -> Optional[str]:
    """主の窓を閉じるときに言う、校正の画面の保存していない作業（U-18）。無ければ None。"""
    wk = (st or {}).get("calibration_work") or {}
    n = int(wk.get("unsaved") or 0)
    if not n:
        return None
    return w("lost_main_" + _suffix(wk.get("kind")), n=n)


def _save_reason(wk: dict, kind: str) -> str:
    if wk.get("pending"):
        return C["need_answer"]
    if kind == cw.KIND_FREQ:
        return C["need_points"]
    if kind == cw.KIND_RBW:
        return C["need_rbw"]
    if kind == cw.KIND_NF:
        return C["need_nf"]
    if kind == cw.KIND_PLUTO_GAIN:
        return C["need_pluto"]
    return C["need_file"]
