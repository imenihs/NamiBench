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

import numpy as np

from ..core.units import format_freq
from . import marker_rows, source_view, texts
from .texts import RTL_LABELS, SETTINGS_LABELS, TEMPLATES, tpl


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


def model_display_name(model) -> str:
    """機種のプロファイルの名前（コード）を、画面に出す機種名にする。知らない名前は出さない（空）。"""
    p = _profiles().get(model) or {}
    return str(p.get("display_name") or "")


def is_fft(st: dict) -> bool:
    return (st.get("connection") or {}).get("sweep_kind") == "fft"


# ---------------------------------------------------------------- 掃引の欄

def rbw_groups(st: dict) -> list[dict]:
    """RBW の選択肢。FFT の機器は窓ごとにまとめる（Hann・Flat top: 設計Q §6.4）。

    形: [{"window": "hann" | None, "label": "Hann", "note": "...",
          "choices": [{"hz": 1500, "text": "1.5kHz", "reason": None | "選べない理由"}]}]
    いまの条件で使えない RBW は理由を持つ（画面は一覧に残して無効にする: 設計C §5.14・U-08）。
    """
    rbw = profile_of(st).get("rbw") or {}
    why = rbw_limit_text(st)
    usable = set((st.get("sweep") or {}).get("rbw_usable_hz") or [])

    def choice(hz: int) -> dict:
        return {"hz": hz, "text": format_freq(hz), "reason": why if why and usable and hz not in usable else None}

    if "choices" in rbw:
        groups: list[dict] = []
        for w in texts.RBW_WINDOWS:
            ch = sorted(c["rbw_hz"] for c in rbw["choices"] if c.get("window") == w)
            if ch:
                groups.append({"window": w, "label": texts.RBW_WINDOWS[w], "note": texts.RBW_WINDOW_NOTES.get(w, ""),
                               "choices": [choice(hz) for hz in ch]})
        return groups
    return [{"window": None, "label": "", "note": "", "choices": [choice(hz) for hz in rbw.get("choices_hz") or []]}]


def rbw_limit_text(st: dict) -> Optional[str]:
    """いまの条件で選べない RBW があるときの理由（無ければ None）。"""
    sw = st.get("sweep") or {}
    code = sw.get("rbw_limit")
    if not code:
        return None
    usable = sw.get("rbw_usable_hz") or []
    only = format_freq(usable[0]) if len(usable) == 1 else ""
    return texts.RBW_LIMITS.get(code, code).replace("{rbw}", only)


def rbw_window_of(st: dict, hz: Optional[int]) -> Optional[str]:
    for g in rbw_groups(st):
        if any(c["hz"] == hz for c in g["choices"]):
            return g["window"]
    return None


def sweep_reasons(st: dict) -> dict:
    """掃引の欄のうち、いまの機器・条件では使えないものと理由（U-08: 消さずに無効にして理由を出す）。

    イメージ除去は RTL-SDR・NF・ゼロスパンでは選べない（設計F §7.1）。
    """
    if is_fft(st):
        return {"points": RTL_LABELS["points_fft"], "zero_span": RTL_LABELS["zero_span_fft"],
                "image_reject": RTL_LABELS["image_reject_fft"]}
    sw = st.get("sweep") or {}
    reject = None
    if (st.get("step_sweep") or {}).get("running"):
        reject = texts.REASON_TEXTS["image_reject_step"]  # ステップ掃引の間は使わない（設計L §1.3）
    elif sw.get("mode") == "nf":
        reject = texts.REASON_TEXTS["image_reject_nf"]
    elif sw.get("step_hz") == 0:
        reject = texts.REASON_TEXTS["image_reject_zero_span"]
    return {"points": None, "zero_span": None, "image_reject": reject}


def image_reject_view(st: dict) -> dict:
    """イメージ除去の選択（設計F §7.4。Qt と Web が同じものを出す）: 選択肢（文字は名前と、いまの条件での1画面の
    見積もり）・いまの選択・選べないときの理由。"""
    sw = st.get("sweep") or {}
    est = sw.get("image_reject_estimates") or {}
    items = [{"code": c, "text": texts.image_reject_item(c, est.get(c))} for c in texts.IMAGE_REJECT_CHOICES]
    return {"items": items, "value": sw.get("image_reject"), "reason": sweep_reasons(st)["image_reject"]}


def image_reject_width_samples() -> list[str]:
    """選択肢の一番長い文字の見本（ドロップダウンの幅を秒の桁で動かさないため。1画面は長くても数十秒: 設計F §7.4）。"""
    return [texts.image_reject_item(c, 999.9) for c in texts.IMAGE_REJECT_CHOICES]


def mode_reasons(st: dict) -> dict:
    """モードごとの使えない理由。まだ作っていないもの → 機器が測れないもの（Q §2.1）の順。"""
    meas = profile_of(st).get("measurements") or []
    out = {}
    for code, _label, _name, why in texts.MODES:
        out[code] = why or (None if code in meas else texts.MODE_UNSUPPORTED)
    return out


# ---------------------------------------------------------------- 単位の欄（J §4.10 の 2。C §5.14 の RBW と同じ決まり）

# 単位の欄で「基準との比」を表す値（単位の設定のコードではない。選んで送るものではない）
RATIO_UNIT = "ratio"


def unit_view(st: dict) -> dict:
    """単位の欄の中身。欄は実際に使っている単位を出す。

    TG・S11 で基準との比を出している間（状態の display_quantity が "ratio"）は「dB（基準との比）」を出し、
    電力の単位は一覧に残して選べなくし、理由を出す（U-08）。選んでいた電力の単位（view.level_unit）は
    そのまま覚えていて、電力の表示に戻ったら使う。「dB（基準との比）」は選ぶものではないので、いつも選べない。
    形: {"ratio", "current": 欄に出す値, "unit": 電力の単位のコード, "label": その名前,
         "choices": [{"code", "text", "enabled", "tip"}], "tip": 欄のツールチップ}
    """
    V = texts.VIEW_TEXTS
    v = st.get("view") or {}
    code = v.get("level_unit") or "dBm"
    units = v.get("level_units") or {"dBm": "dBm"}
    label = units.get(code, code)
    ratio = st.get("display_quantity") == "ratio"
    reason = V["level_unit_ratio_reason"].replace("{unit}", label) if ratio else None
    choices = [{"code": c, "text": t, "enabled": not ratio, "tip": reason or ""} for c, t in units.items()]
    choices.append({"code": RATIO_UNIT, "text": V["level_unit_ratio_item"], "enabled": False,
                    "tip": V["level_unit_ratio_item_tip"]})
    return {"ratio": ratio, "current": RATIO_UNIT if ratio else code, "unit": code, "label": label,
            "choices": choices, "tip": "\n".join(t for t in (V["level_unit_tip"], reason) if t)}


# ---------------------------------------------------------------- 受信（RTL-SDR: 設計Q §6.5・§6.6）

def rtl_view(st: dict) -> dict:
    """受信の欄の中身。shown は欄を出すか（RTL-SDR に繋いでいるときだけ: 設計J §4.5）。

    reason（繋いでいない理由）は設定の画面など、欄の外から RTL-SDR の操作を見せるところが使う。
    """
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
        "shown": connected,
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
        # 設定の画面の rtlsdr.dll の行（設計J §4.12）: 見つけたファイルの場所。無い・32 ビット版なら注意の色で理由
        "dll_line": rtl.get("dll_path") or SETTINGS_LABELS["rtlsdr_missing"],
        "dll_reason": SETTINGS_LABELS["rtlsdr_32bit"] if rtl.get("dll_bits") == 32 else "",
        "dll_warn": not rtl.get("dll_path") or rtl.get("dll_bits") == 32,
        "ppm_reason": reason,
        "level_reason": reason or (RTL_LABELS["needs_manual_gain"] if rtl.get("agc") else None),
        # 「利得を合わせる」（設計Q §6.5）: AGC の間と、合わせている最中は押せない
        "fit_reason": reason or (RTL_LABELS["fit_needs_manual"] if rtl.get("agc") else None)
        or (RTL_LABELS["fit_running"] if rtl.get("fitting") else None),
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
    # IQ を持つ機器（RTL-SDR）に繋いでいるときだけ欄を出す（設計J §4.5: 機器に固有の欄はその機器のときだけ）
    iq = is_fft(st) and bool((st.get("rtlsdr") or {}).get("connected"))
    if not iq:
        reason = texts.LISTEN_LABELS["not_iq"]
    elif (st.get("rtlsdr") or {}).get("fixed_center", None) and \
            (st["rtlsdr"]["fixed_center"] or {}).get("cause") == "rtltcp_clients" and not lst.get("active"):
        reason = texts.ERRORS["listen_rtltcp_active"]
    else:
        reason = None
    return {"shown": iq, "active": bool(lst.get("active")), "reason": reason, "marker_id": lst.get("marker_id"),
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


# ---------------------------------------------------------------- 範囲の外・意味の無い区間の帯（設計J §4.8）

# 帯の色（配色トークン: J §4.4）。値が意味を持たない区間は「注意」、読める（低く出るだけ）負の側は控えめな色
MARK_COLORS = {"outside_tg_range": "warn", "lo_leak": "warn", "negative_mirror": "text_dim"}


def marks_view(st: dict) -> list[dict]:
    """グラフに描く帯 [{kind, start_hz, stop_hz, label, tip, color}]。区間はコアの display_marks（表示範囲で切ってある）。"""
    out = []
    off = texts.freq_offset_of(st)  # 説明の区間は横軸と同じ換算後の周波数（設計O）
    for m in st.get("display_marks") or []:
        label = texts.DISPLAY_MARKS.get(m["kind"], m["kind"])
        out.append({**m, "label": label, "color": MARK_COLORS.get(m["kind"], "warn"),
                    "tip": tpl("mark_range", label=label, start=format_freq(int(m["start_hz"]) + off),
                               stop=format_freq(int(m["stop_hz"]) + off))})
    return out


def png_warnings(st: dict) -> list[str]:
    """PNG の帯の注意（I §3.2 の 6）: 上段の条件の注意と、グラフに出ている帯の説明（範囲つき）。"""
    return texts.condition_lines(st) + [m["tip"] for m in marks_view(st)]


def ref_auto_view(st: dict, shown: list[str], unit: str, db_per_div: float,
                  has_wave: bool) -> dict:
    """REF の「自動」（J §4.1・§4.8）: 合わせる REF レベルと、使えない理由。

    shown は画面が表示しているトレースの id（"1"〜"4"・参照トレースの "F1.1"・差の "D"）。
    コアが出したトレースごとの最大値（state.ref_auto: 局発の漏れ・前置き不足・値の無い点・TG の範囲の外・
    表示範囲の外を除いたもの）のうち、縦軸と単位が同じものの最大を使う。
    """
    from ..core.display_marks import auto_ref_level

    if not has_wave:
        return {"level": None, "reason": texts.REASON_TEXTS["no_wave"]}
    peaks = (st.get("ref_auto") or {}).get("peaks") or {}
    vals = [p.get("max") for tid, p in peaks.items() if tid in shown and p.get("unit") == unit]
    level = auto_ref_level(vals, db_per_div)
    return {"level": level, "reason": None if level is not None else texts.REASON_TEXTS["no_auto_points"]}


# ---------------------------------------------------------------- 配色の編集（設計J §4.4）

def palette_view(st: dict) -> dict:
    """配色の編集の画面の中身。行ごとの名前・色・初期値・変えた印・読みやすさの知らせ（止めはしない）。

    読みやすさの知らせは、組（文字と背景・トレースどうし）のうち**利用者が変えた側の行**に出す。どちらも変えて
    いなければ両方の行に出す（警告がどの項目の話か見て分かるように: U-25。下にまとめて並べると、変えた色の ● と
    対応せず意味を成さなかった: 2026-10-03 ユーザー指摘）。行には相手の色の名前を並べ、比の値はツールチップ。
    """
    from ..core.palette import BASES, DEFAULT_BASE, NAMES

    S = texts.SETTINGS_TEXTS
    name = texts.PALETTE_NAMES.get
    p = st.get("palette") or {}
    base = p.get("base") or DEFAULT_BASE
    colors = p.get("colors") or dict(BASES[base])
    defaults = BASES.get(base, BASES[DEFAULT_BASE])
    changed = set(p.get("changed") or [])
    # 行 → {"contrast": [相手], "similar": [相手], "tips": [文]}
    hits: dict[str, dict] = {k: {"contrast": [], "similar": [], "tips": []} for k in NAMES}
    for w in p.get("warnings") or []:
        if w.get("code") == "low_contrast":
            a, b, kind = w["fg"], w["bg"], "contrast"
            tip = S["palette_contrast_tip"].replace("{fg}", name(a, a)).replace("{bg}", name(b, b)) \
                .replace("{ratio}", f"{w['ratio']:.1f}")
        elif w.get("code") == "similar_traces":
            a, b, kind = w["a"], w["b"], "similar"
            tip = S["palette_similar_tip"].replace("{a}", name(a, a)).replace("{b}", name(b, b)) \
                .replace("{d}", f"{w['delta_e']:.0f}")
        else:
            continue
        sides = [x for x in (a, b) if x in changed] or [a, b]
        for x in sides:
            other = b if x == a else a
            if x in hits and other not in hits[x][kind]:
                hits[x][kind].append(other)
                hits[x]["tips"].append(tip)
    rows = []
    for k in NAMES:
        h = hits[k]
        parts = []
        if h["contrast"]:
            parts.append(S["palette_row_contrast"].replace("{names}", "・".join(name(o, o) for o in h["contrast"])))
        if h["similar"]:
            parts.append(S["palette_row_similar"].replace("{names}", "・".join(name(o, o) for o in h["similar"])))
        rows.append({"name": k, "label": name(k, k), "color": colors.get(k, defaults[k]),
                     "default": defaults[k], "changed": k in changed,
                     "tip": S["palette_changed_tip"].replace("{default}", defaults[k]) if k in changed else "",
                     "warn": "  ".join(parts), "warn_tip": "\n".join(h["tips"])})
    # unsaved_n: 保存していない変更の数（保存した配色を読み込む前の確認: J §4.4.1）。origin_id: いまの配色を始めた配色
    return {"rows": rows, "base": base, "changed_n": len(changed), "unsaved_n": len(p.get("unsaved") or []),
            "origin_id": (p.get("origin") or {}).get("id")}


def palette_reset_question(st: dict, base: str) -> Optional[str]:
    """ライト・ダークの初期値にするときの確認の文。変えた色が無ければ None（聞かずにすぐ切り替える: J §4.4）。"""
    n = len((st.get("palette") or {}).get("changed") or [])
    if n == 0:
        return None
    return texts.SETTINGS_TEXTS["palette_reset_confirm"].replace("{n}", str(n)).replace(
        "{base}", texts.PALETTE_BASES.get(base, base))


def palette_warn_texts(warnings: list[dict]) -> tuple[str, str]:
    """保存した配色の読みやすさの知らせ（一覧の行の短い文と、組ごとの文を並べたツールチップ）。無ければ ("", "")。"""
    S = texts.SETTINGS_TEXTS
    name = texts.PALETTE_NAMES.get
    tips = []
    for w in warnings or []:
        if w.get("code") == "low_contrast":
            tips.append(S["palette_contrast_tip"].replace("{fg}", name(w["fg"], w["fg"])).replace(
                "{bg}", name(w["bg"], w["bg"])).replace("{ratio}", f"{w['ratio']:.1f}"))
        elif w.get("code") == "similar_traces":
            tips.append(S["palette_similar_tip"].replace("{a}", name(w["a"], w["a"])).replace(
                "{b}", name(w["b"], w["b"])).replace("{d}", f"{w['delta_e']:.0f}"))
    if not tips:
        return "", ""
    return S["palette_list_warn"].replace("{n}", str(len(tips))), "\n".join(tips)


def palette_rows(result: dict) -> list[dict]:
    """保存した配色の一覧の行（名前・読みやすさの知らせ・いまの配色を始めた配色か）。命令 palette_list の戻り値から。"""
    out = []
    for p in result.get("palettes") or []:
        warn, tip = palette_warn_texts(p.get("warnings") or [])
        out.append({"id": p["id"], "name": p["name"], "warn": warn, "warn_tip": tip,
                    "origin": p["id"] == result.get("origin_id")})
    return out


def palette_load_question(st: dict, name: str) -> Optional[str]:
    """保存した配色を読み込む前の確認の文。保存していない変更が無ければ None（聞かずに読み込む: J §4.4.1・U-18）。"""
    n = len((st.get("palette") or {}).get("unsaved") or [])
    if n == 0:
        return None
    return texts.SETTINGS_TEXTS["palette_load_confirm"].replace("{n}", str(n)).replace("{name}", name)


def palette_import_text(result: dict) -> str:
    """取り込んだあとの知らせ。読みにくい組があればその場で言う（J §4.4.1）。"""
    S = texts.SETTINGS_TEXTS
    warn, _tip = palette_warn_texts(result.get("warnings") or [])
    if warn:
        return S["palette_imported_warn"].replace("{name}", result.get("name", "")).replace("{warn}", warn)
    return S["palette_imported"].replace("{name}", result.get("name", ""))


def preset_rows(presets: list[dict]) -> list[dict]:
    """プリセットの一覧の行（名前と中身の要約）。"""
    out = []
    for p in presets or []:
        s = p.get("summary") or {}
        summary = ""
        if s.get("start_hz") is not None:
            summary = tpl("preset_summary", mode=texts.MODE_LABELS.get(s.get("mode"), s.get("mode") or ""),
                          start=format_freq(int(s["start_hz"])), stop=format_freq(int(s["stop_hz"])),
                          rbw=format_freq(int(s["rbw_hz"])) if s.get("rbw_hz") else "")
        out.append({"id": p["id"], "name": p["name"], "summary": summary})
    return out


# ---------------------------------------------------------------- まとめ（Web の口が state に付ける）

TRACE_COUNTED = ("average", "max_hold", "min_hold")


def trace_status(s: dict) -> dict:
    """トレースの行の2段目の様子（Qt・Web 同じ）。{"text": 1行の文, "tip": 全部の文}。

    動作の名前は隣のドロップダウンが出しているので、文はいまの回数と値の様子だけにする（同じことを2回書かない:
    U-24）。掃引ごとに長さが変わる文で列の幅や行の高さが動かないよう、画面は1行に収めて切り、全部はツールチップ
    （設計J §4.11: 同じ列の部品は同じ幅・2026-10-03 ユーザー指摘「ガタガタ」）。
    """
    a = s.get("action")
    name = texts.TRACE_ACTIONS.get(a, a or "")
    parts = []
    if a in TRACE_COUNTED:
        tgt = s.get("count_target")
        parts.append(f"{s.get('current_count', 0)}" + (f"/{tgt}" if tgt else ""))
    elif not s.get("has_values"):
        parts.append(texts.LABELS["no_value"])
    if s.get("axis_differs"):
        parts.append(TEMPLATES["axis_differs"])
    text = "".join(parts)
    tip = name + (" " + text if text else "")
    if a in TRACE_COUNTED and not s.get("has_values"):
        tip += texts.LABELS["no_value_paren"]
    return {"text": text, "tip": tip}


def ui_view(st: dict) -> dict:
    """Web の画面へ state と一緒に送るもの。Qt も同じ関数の部分を使う。"""
    return {
        "status": texts.status_items(st),
        "cond": texts.condition_lines(st),
        "rbw": rbw_groups(st),
        "sweep_reasons": sweep_reasons(st),
        "image_reject": image_reject_view(st),  # イメージ除去の選択肢（設計F §7.4）
        "mode_reasons": mode_reasons(st),
        "fft": is_fft(st),
        "rtl": rtl_view(st),
        "listen": listen_view(st),
        "rtltcp": rtltcp_view(st),
        "tasks": {t.get("task_id"): texts.task_label(t) for t in st.get("tasks") or []},
        "normalization": texts.normalization_text(st.get("normalization") or {}),
        "ref_quick": ref_quick_view(st),
        "marks": marks_view(st),
        "palette": palette_view(st),
        "sg_state": texts.sg_state_text(st),  # SG 周波数の欄のそば（J §4.12）
        "trace_status": {str(t.get("number")): trace_status(t) for t in st.get("traces") or []},
        "level": unit_view(st),  # 単位の欄（J §4.10）
        # 周波数オフセット（設計O）。横軸の名前と、欄の換算に使う
        "freq_offset_text": texts.offset_text(texts.freq_offset_of(st)) if texts.freq_offset_of(st) else "",
        # SG のステップ掃引の欄と、内部の漏れの印（設計L §1.3・§1.4）
        "step": source_view.step_view(st),
        "leaks": source_view.leak_marks_view(st),
    }


# ---------------------------------------------------------------- マウスの位置の読み値（設計G §2・§3.3）

def trace_x(payload: dict) -> np.ndarray:
    """トレースの横軸（float）。ゼロスパンは点の番号。周波数の正本は Hz の整数のまま（描くときと読むときの共通）。"""
    ax = payload["axis"]
    if "freqs_hz" in ax:
        return np.asarray(ax["freqs_hz"], dtype=np.float64)
    n = int(ax["points"])
    if ax.get("step_hz", 0) == 0:
        return np.arange(n, dtype=np.float64)
    return (ax["start_hz"] + ax["step_hz"] * np.arange(n, dtype=np.int64)).astype(np.float64)


def nearest_point(payloads: list[dict], x: float, y: float) -> Optional[tuple[int, int]]:
    """見えているトレースのうち、横の位置 x の最も近い点で縦に y へ最も近いもの（設計G §3.2 のクリックと同じ選び方）。

    戻り値は (payloads の何番目, 点の番号)。どのトレースもその点に値が無ければ、最初のトレースのその点
    （値なしと読ませる）。トレースが1本も無ければ None。縦の距離が同じなら並びの先のもの。
    """
    best = None
    fallback = None
    for k, p in enumerate(payloads):
        xs = trace_x(p)
        if xs.size == 0:
            continue
        i = int(np.clip(np.searchsorted(xs, x), 0, xs.size - 1))
        if i > 0 and abs(xs[i - 1] - x) <= abs(xs[i] - x):
            i -= 1
        if fallback is None:
            fallback = (k, i)
        v = float(np.asarray(p["values"])[i])
        if not np.isfinite(v):
            continue
        d = abs(v - y)
        if best is None or d < best[0]:
            best = (d, k, i)
    return (best[1], best[2]) if best is not None else fallback


def point_note_codes(payload: dict, i: int) -> list[str]:
    """その点に付いている印の名前（イベント trace の flags から。マーカーの読み値の注意と同じ名前: G §3.3）。"""
    fl = payload.get("flags") or {}
    codes = set()
    for name, arr in (fl.get("points") or {}).items():
        a = np.asarray(arr)
        if a.shape and i < a.shape[0] and bool(a[i]):
            codes.add(name)
    oc = fl.get("out_of_cal_range")
    if oc is not None:
        a = np.asarray(oc)
        if a.shape and i < a.shape[0] and bool(a[i]):
            codes.add("out_of_cal_range")
    if i < int(fl.get("transient_head_points") or 0):
        codes.add("transient")
    return sorted(codes)


def mouse_reading(payloads: list[dict], x: float, y: float, zero_span: bool = False,
                  freq_offset_hz: int = 0, leaks: Optional[list[dict]] = None, px_hz: float = 0.0) -> dict:
    """グラフの上のマウスの位置の読み値（設計G §2: マウスの縦の位置ではなく波形の値を出す）。

    payloads は画面が表示している測定中のトレース（イベント trace の形）、x・y はマウスの位置の軸の値。
    読むのは最も近いトレースの最も近い点（マーカーをクリックで置くときと同じ選び方）で、周波数はその点の
    周波数（値はその点のもの）。印の付いた点はマーカーと同じ注意を添える（G §3.3）。
    トレースが無いときはマウスの位置の周波数だけ。Qt はツールチップに、Web の口は `mouse_reading` で返す。
    周波数の文字は表示の周波数オフセット（設計O）を足した値。freq_hz は測った周波数のまま。
    """
    off = int(freq_offset_hz or 0)
    # 内部の漏れの印の近く（設計L §1.4）なら、その説明を添える（leaks は source_view.leak_marks_view。px_hz は
    # グラフの1画素の周波数の幅で、狭い印もマウスで拾えるようにする）
    leak = None if zero_span else source_view.leak_near(leaks or [], x, px_hz)
    hit = nearest_point(payloads, x, y)
    if hit is None:
        pos = int(round(x))
        lines = [tpl("mouse_point", n=pos) if zero_span else format_freq(pos + off)]
        if leak:
            lines.append(leak["tip"])
        return {"trace": None, "freq_hz": None if zero_span else pos, "point": pos if zero_span else None,
                "level": None, "unit": None, "notes": [], "lines": lines, "text": "\n".join(lines)}
    k, i = hit
    p = payloads[k]
    v = float(np.asarray(p["values"])[i])
    level = v if np.isfinite(v) else None
    unit = p.get("unit") or "dBm"
    ax = p["axis"]
    zero = zero_span or ("freqs_hz" not in ax and ax.get("step_hz", 0) == 0)
    freq = None if zero else int(round(float(trace_x(p)[i])))
    codes = point_note_codes(p, i)
    value_text = f"{level:.2f} {unit}" if level is not None else texts.MARKER_STATUS["no_value"]
    lines = [tpl("mouse_point", n=i) if zero else format_freq(freq + off),
             tpl("trace_note", n=p.get("trace"), text=value_text), *marker_rows.note_texts(codes)]
    if leak and not zero:
        lines.append(leak["tip"])
    return {"trace": p.get("trace"), "freq_hz": freq, "point": i if zero else None, "level": level, "unit": unit,
            "notes": codes, "lines": lines, "text": "\n".join(lines)}
