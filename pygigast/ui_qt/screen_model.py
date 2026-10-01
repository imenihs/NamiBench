"""コアの状態から、画面に出すもの（状態バー・条件の注意・RBW の選択肢・使えない理由・受信の欄）を作る。

Qt と Web が同じものを使う（J §5: 同じ構成・同じ語。同じ処理を2回書かない）。Qt はここを直接呼び、
Web の口（api/web/server.py）は state のイベントに `ui` として付けて送る。Web の画面はそれを映すだけ。
Qt を import しない（Web の口が画面なしで使う）。

- 機器ごとの選択肢（RBW・できる測定）は、いま繋いでいる機器の機種のプロファイルから作る（設計Q §2.1）。
  状態はプロファイルの名前（connection.profile_model）しか持たないので、同梱のプロファイルを名前で引く
- 選んでいるマーカーは画面ごとの状態なので、マーカーが要る理由（「マーカーを選んでください」）は画面が足す
"""
from __future__ import annotations

import functools
from typing import Optional

from ..core.units import format_freq
from . import texts
from .texts import RTL_LABELS, TEMPLATES, tpl


@functools.lru_cache(maxsize=None)
def _profiles() -> dict[str, dict]:
    from ..core.devices.base import load_profile
    from ..core.devices.rtlsdr.driver import PROFILE_PATH as RTL_PROFILE
    from ..core.instrument import GIGAST_PROFILE_PATH

    out = {}
    for path in (GIGAST_PROFILE_PATH, RTL_PROFILE):
        p = load_profile(path)
        out[p["model"]] = p
    return out


def profile_of(st: dict) -> dict:
    """いまの機器の機種のプロファイル。知らない名前・未接続は GigaSt（接続前の本体の既定と同じ）。"""
    profs = _profiles()
    model = (st.get("connection") or {}).get("profile_model")
    return profs.get(model) or profs["gigast-v5"]


def is_fft(st: dict) -> bool:
    return (st.get("connection") or {}).get("sweep_kind") == "fft"


# ---------------------------------------------------------------- 掃引の欄

def rbw_groups(st: dict) -> list[dict]:
    """RBW の選択肢。FFT の機器は窓ごとにまとめる（Hann・Flat top: 設計Q §6.4）。

    形: [{"window": "hann" | None, "label": "Hann", "note": "...", "choices": [{"hz": 1500, "text": "1.5kHz"}]}]
    """
    rbw = profile_of(st).get("rbw") or {}
    if "choices" in rbw:
        groups: list[dict] = []
        for w in texts.RBW_WINDOWS:
            ch = sorted(c["rbw_hz"] for c in rbw["choices"] if c.get("window") == w)
            if ch:
                groups.append({"window": w, "label": texts.RBW_WINDOWS[w], "note": texts.RBW_WINDOW_NOTES.get(w, ""),
                               "choices": [{"hz": hz, "text": format_freq(hz)} for hz in ch]})
        return groups
    return [{"window": None, "label": "", "note": "",
             "choices": [{"hz": hz, "text": format_freq(hz)} for hz in rbw.get("choices_hz") or []]}]


def rbw_window_of(st: dict, hz: Optional[int]) -> Optional[str]:
    for g in rbw_groups(st):
        if any(c["hz"] == hz for c in g["choices"]):
            return g["window"]
    return None


def sweep_reasons(st: dict) -> dict:
    """掃引の欄のうち、この機器では使えないものと理由（U-08: 消さずに無効にして理由を出す）。"""
    if is_fft(st):
        return {"points": RTL_LABELS["points_fft"], "zero_span": RTL_LABELS["zero_span_fft"],
                "image_cancel": RTL_LABELS["image_cancel_fft"]}
    return {"points": None, "zero_span": None, "image_cancel": None}


def mode_reasons(st: dict) -> dict:
    """モードごとの使えない理由。まだ作っていないもの → 機器が測れないもの（Q §2.1）の順。"""
    meas = profile_of(st).get("measurements") or []
    out = {}
    for code, _label, _name, why in texts.MODES:
        out[code] = why or (None if code in meas else texts.MODE_UNSUPPORTED)
    return out


# ---------------------------------------------------------------- 受信（RTL-SDR: 設計Q §6.5・§6.6）

def rtl_view(st: dict) -> dict:
    """受信の欄の中身。繋いでいなければ全部を無効にする理由を持つ。"""
    rtl = st.get("rtlsdr") or {}
    connected = bool(rtl.get("connected"))
    cal = rtl.get("calibration")
    gains = [{"value": None, "text": TEMPLATES["gain_agc"]}]
    gains += [{"value": g, "text": tpl("rtl_gain_step", db=f"{g:g}")} for g in rtl.get("gain_steps_db") or []]
    if cal:
        level = TEMPLATES["rtl_level_cal"] if cal.get("level_offset_db") else ""
        cal_text = tpl("rtl_cal_at", at=cal.get("created_at") or "", ppm=f"{int(cal.get('ppm', 0)):+d}", level=level)
    else:
        cal_text = TEMPLATES["rtl_cal_none"]
    reason = None if connected else RTL_LABELS["not_rtlsdr"]
    dll = rtl.get("dll_folder")
    return {
        "connected": connected,
        "reason": reason,
        "gains": gains,
        "agc": bool(rtl.get("agc")),
        # 手動のときの段。設定（gain_db）が無ければ機器の今の値
        "gain_db": rtl.get("gain_db") if rtl.get("gain_db") is not None else rtl.get("gain_db_actual"),
        "ppm": rtl.get("ppm") if rtl.get("ppm") is not None else (cal or {}).get("ppm", 0),
        "cal_text": cal_text,
        "unit_text": tpl("rtl_unit", unit=rtl.get("level_unit") or "") if connected else "",
        "shared_text": tpl("rtl_shared", n=rtl["shared_serial"]) if connected and (rtl.get("shared_serial") or 0) > 1
        else "",
        "dll_text": tpl("rtl_dll", folder=dll) if dll else TEMPLATES["rtl_dll_default"],
        "dll_found": bool(rtl.get("dll_found")),
        "ppm_reason": reason,
        "level_reason": reason or (RTL_LABELS["needs_manual_gain"] if rtl.get("agc") else None),
    }


def gain_text(gain_db) -> str:
    return TEMPLATES["gain_agc"] if gain_db is None else tpl("rtl_gain_step", db=f"{float(gain_db):g}")


def ppm_done_text(r: dict) -> str:
    return tpl("rtl_ppm_done", old=f"{r['previous_ppm']:+d}", new=f"{r['ppm']:+d}",
               measured=format_freq(int(round(r["measured_hz"]))), true=format_freq(int(r["true_freq_hz"])))


def level_done_text(r: dict) -> str:
    return tpl("rtl_level_done", gain=f"{r['gain_db']:g}", offset=f"{r['level_offset_db']:+.2f}",
               measured=f"{r['measured']:.2f}", true=f"{r['true_dbm']:g}")


# ---------------------------------------------------------------- 聞く（設計Q §6.8）

def listen_view(st: dict) -> dict:
    """聞く欄の中身。選んでいるマーカーが要る理由は画面が足す（marker_reason）。"""
    lst = st.get("listen") or {}
    if not is_fft(st) or not (st.get("rtlsdr") or {}).get("connected"):
        reason = texts.LISTEN_LABELS["not_iq"]
    elif (st.get("rtlsdr") or {}).get("fixed_center", None) and \
            (st["rtlsdr"]["fixed_center"] or {}).get("cause") == "rtltcp_clients" and not lst.get("active"):
        reason = texts.ERRORS["listen_rtltcp_active"]
    else:
        reason = None
    return {"active": bool(lst.get("active")), "reason": reason, "marker_id": lst.get("marker_id"),
            "mode": lst.get("mode"), "now_text": listen_now_text(lst),
            "stop_reason": None if lst.get("active") else texts.REASON_TEXTS["not_listening"]}


def listen_now_text(lst: dict) -> str:
    if not lst or not lst.get("active"):
        return ""
    return tpl("listen_now", marker=lst.get("marker_id") or "", freq=format_freq(int(lst["freq_hz"])),
               mode=texts.LISTEN_MODES.get(lst.get("mode"), lst.get("mode") or ""))


# ---------------------------------------------------------------- rtl_tcp（設計Q §4.3・設計O §1.3）

def rtltcp_view(st: dict) -> dict:
    r = st.get("rtltcp") or {}
    if r.get("running"):
        text = tpl("rtltcp_running", port=r.get("port"), clients=r.get("clients", 0),
                   bind=texts.RTLTCP_BINDS.get(r.get("bind"), r.get("bind") or ""))
    else:
        text = TEMPLATES["rtltcp_stopped_state"]
    err = r.get("error")
    err_text = texts.rtltcp_status(r)[0] if err else ""
    dropped = tpl("rtltcp_dropped", n=r["dropped_bytes"]) if r.get("dropped_bytes") else ""
    return {"enabled": bool(r.get("enabled")), "running": bool(r.get("running")), "port": r.get("port"),
            "bind": r.get("bind"), "text": text, "error_text": err_text, "dropped_text": dropped}


# ---------------------------------------------------------------- 上段の「直結を 0dB にする」（設計J §4.2・E §6.2）

def ref_quick_view(st: dict) -> dict:
    """TG・S11 のときだけ上段に出す。ボタンの使えない理由・取っている間の進み具合・いまの基準の状態。"""
    from .texts_devices import REF_QUICK_LABELS, hhmm

    sw = st.get("sweep") or {}
    visible = sw.get("mode") in ("tg", "s11")
    task = next((t for t in st.get("tasks") or [] if t.get("kind") == "reference_take"), None)
    if (st.get("connection") or {}).get("state") != "connected":
        reason = texts.ERRORS["not_connected"]
    elif sw.get("mode") == "s11":
        reason = REF_QUICK_LABELS["s11_reason"]
    elif task is not None:
        reason = REF_QUICK_LABELS["taking_reason"]
    else:
        reason = None
    n = st.get("normalization") or {}
    sel = n.get("selected")
    tip = ""
    if sel and (n.get("applied") or not n.get("used")):
        name, when = sel.get("name", ""), hhmm(sel.get("taken_at"))
        # 既定の名前（「直結 12:24」）は時刻を含むので、同じ時刻を重ねて出さない
        text = tpl("ref_quick_name", name=name) if when and when in name else             tpl("ref_quick_now", name=name, time=when)
        if n.get("power_cycled"):
            text += "・" + TEMPLATES["ref_quick_power_cycled"]
            tip = TEMPLATES["norm_power_cycled"]
    else:
        text = TEMPLATES["ref_quick_none"]
        if sel and n.get("reason"):
            tip = texts.NORM_REASONS.get(n["reason"], n["reason"])
    return {"visible": visible, "reason": reason, "text": text, "tip": tip,
            "taking": task is not None, "task_id": task.get("task_id") if task else None,
            "cancelling": bool(task and task.get("cancelling")),
            "progress_text": texts.task_label({**task, "task": task.get("kind")}) if task else ""}


# ---------------------------------------------------------------- まとめ（Web の口が state に付ける）

def ui_view(st: dict) -> dict:
    """Web の画面へ state と一緒に送るもの。Qt も同じ関数の部分を使う。"""
    return {
        "status": texts.status_items(st),
        "cond": texts.condition_lines(st),
        "rbw": rbw_groups(st),
        "sweep_reasons": sweep_reasons(st),
        "mode_reasons": mode_reasons(st),
        "fft": is_fft(st),
        "rtl": rtl_view(st),
        "listen": listen_view(st),
        "rtltcp": rtltcp_view(st),
        "tasks": {t.get("task_id"): texts.task_label(t) for t in st.get("tasks") or []},
        "normalization": texts.normalization_text(st.get("normalization") or {}),
        "ref_quick": ref_quick_view(st),
    }
