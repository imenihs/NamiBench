"""画面の文言の正本（U-22: 画面の言い方と記録の言い方を分ける。J §5: Qt と Web は同じ語）。

- コアが返す理由のコードを画面の文言にする表と、文を組み立てる型（TEMPLATES）を持つ
- Web は同じ表を `GET /api/v1/texts` で受け取る（大文字の名前の表がそのまま JSON になる）。
  Web の texts.js・app.js は文言を持たず、ここの表と TEMPLATES を組み合わせるだけ
- TEMPLATES の差し込みは `{名前}` だけ（書式の指定を使わない）。Python と JavaScript の両方で
  同じ置き換えで作れるようにするため。数値の桁は差し込む前にそろえる
- Qt を import しない（Web の画面のサーバーが Qt なしで使う）
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Optional

from ..core.units import format_freq
from .texts_ui import *  # noqa: F401,F403 — 画面の部品の語の表（メニュー・RTL-SDR など）。同じ texts として配る
from .texts_ui import DISPLAY_MARKS, TEMPLATES
from .texts_labels import *  # noqa: F401,F403 — 部品の名前・ボタンの語（LABELS）。Web へも同じ表で配る
from .texts_labels import LABELS
from .texts_devices import *  # noqa: F401,F403 — 機器の一覧と「基準を取る」の語（J §4.7・§4.2）
from .texts_devices import device_name, port_shown
from .texts_web import *  # noqa: F401,F403 — ブラウザから使うときの語（A §4.3.2）
from .texts_web import WEB_ERRORS
from .texts_screens import *  # noqa: F401,F403 — 右のタブ・マーカー表・ファイルの窓などの語（LABELS・REASON_TEXTS へ足す）
from .texts_settings import *  # noqa: F401,F403 — 設定の画面・配色・キー・プリセット・表示の単位（設計O・J §4.4・§4.6）
from .texts_settings import SETTINGS_ERRORS, VIEW_TEXTS
from .texts_web_settings import *  # noqa: F401,F403 — ブラウザだけの設定の画面・キーの割り当ての語
from .texts_cal import *  # noqa: F401,F403 — 校正の画面の語（設計K）
from .texts_cal import CAL_ERRORS, CAL_TASK_CANCELLED, CAL_TASK_DONE, CAL_TASK_FAILED, CAL_TASK_NAMES
from .texts_zerospan import *  # noqa: F401,F403 — ゼロスパンの時間軸と流す表示の語（設計N §1）
from .texts_zerospan import ZERO_SPAN_ERRORS


def tpl(key: str, **kw) -> str:
    """TEMPLATES の `{名前}` を差し込む（JavaScript の GSTexts.fmt と同じ動き）。"""
    text = TEMPLATES[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def w(key: str, **kw) -> str:
    """LABELS の `{名前}` を差し込む（JavaScript の GSTexts.w と同じ動き）。"""
    text = LABELS[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


FIELD_NAMES = {
    "start_hz": "Start",
    "stop_hz": "Stop",
    "center_hz": "Center",
    "span_hz": "Span",
    "points": "点数",
    "rbw_hz": "RBW",
    "sg_hz": "SG 周波数",
    "step_hz": "Step",
    "mode": "モード",
    "image_reject": "イメージ除去",
    "span_lock": "Span 固定",
}

FREQ_FIELDS = {"start_hz", "stop_hz", "center_hz", "span_hz", "sg_hz", "step_hz", "rbw_hz"}


# 周波数オフセット（設計O）で換算して出す欄（欄の表示と同じ: Span・SG・Step・RBW は換算しない）
OFFSET_FREQ_FIELDS = {"start_hz", "stop_hz", "center_hz"}


def adjustment_text(adj: dict, freq_offset_hz: int = 0) -> str:
    """調整1件を「◯◯ に合わせました（理由）」にする。周波数は欄と同じく換算後の周波数で出す。

    値ではなく状態が変わった調整（Span 固定を外した: 設計C §5.13）は、その文だけを出す（SENTENCE_REASONS）。
    """
    if adj.get("reason") in SENTENCE_REASONS:
        return SENTENCE_REASONS[adj["reason"]]
    field = adj.get("field", "")
    v = adj.get("adopted")
    if field in FREQ_FIELDS and isinstance(v, int):
        value = format_freq(v + (int(freq_offset_hz or 0) if field in OFFSET_FREQ_FIELDS else 0))
    elif field == "points" and v is None:
        value = TEMPLATES["points_auto"]
    elif field == "mode":
        value = MODE_NAMES.get(v, str(v))
    elif field == "image_reject":
        value = IMAGE_REJECT_CHOICES.get(v, str(v))
    elif isinstance(v, bool):
        value = TEMPLATES["on"] if v else TEMPLATES["off"]
    else:
        value = str(v)
    reason = REASONS.get(adj.get("reason", ""), adj.get("reason", ""))
    return tpl("adjusted_with_reason", value=value, reason=reason) if reason else tpl("adjusted", value=value)


REASONS = {
    "upper_limit": "上限",
    "lower_limit": "下限",
    "freq_unit": "機器の周波数の単位",
    "min_span": "この点数で作れる最小の Span",
    "step_max": "Step は 1MHz まで",
    "points_max": "点数の上限",
    "points_min": "点数の下限",
    "sg_range": "SG の範囲",
    "rbw_fixed": "TG・S11・NF と Span 50MHz 超では RBW は 180kHz だけ",
    "rbw_restored": "条件から出たので、選んでいた RBW に戻しました",
    "keep_center": "Center を保つため、範囲に収まる最大の Span",
    "keep_span": "Span 固定のため、Span を保って Center を移動",
    "span_lock": "Span 固定のため",
    "zero_span_unsupported": "この機器はゼロスパンを持ちません",
    "points_by_rbw": "この機器の点数は RBW と Span で決まります",
    "rbw_points_max": "点数の上限を超えるため RBW を広げました",
    "mode_unsupported": "この機器はこのモードで測れません",
    "image_reject_unsupported": "この機器はイメージ除去を持ちません",
    "device_default_range": "機器の範囲の外なので、その機器の既定の範囲",
    "device_changed": "機器が変わったため既定",
}

# 値ではなく状態が変わった調整の文（「◯◯ に合わせました」の形にしない）。欄は調整の field で決まる
SENTENCE_REASONS = {"span_lock_released": LABELS["span_lock_released"]}

# 範囲の規則で値を動かした理由（設計C §5.11・§5.12）。この知らせには反対の規則のボタンを添える
RULE_REASONS = {"keep_center", "keep_span"}


def alternative_label(alt: dict) -> str:
    """反対の規則の結果のボタンの文字（設計C §5.11 の 3）。"""
    span = alt["stop_hz"] - alt["start_hz"]
    center = alt["start_hz"] + span // 2
    if alt.get("rule") == "keep_typed":
        # Span 固定中に打った Start・Stop を守り、Span を縮める結果（C §5.12）
        return tpl("alt_keep_typed", span=format_freq(span), start=format_freq(alt["start_hz"]),
                   stop=format_freq(alt["stop_hz"]))
    key = "alt_keep_span" if alt.get("rule") == "keep_span" else "alt_keep_center"
    return tpl(key, span=format_freq(span), center=format_freq(center))


def alternative_done_text(center_hz: int, span_hz: int) -> str:
    return tpl("alt_done", center=format_freq(center_hz), span=format_freq(span_hz))


WARNINGS = {
    "rbw180k_narrow_span": "RBW 180kHz で Span 0.3MHz 未満: 平らな上面しか見えません（レベルは正しい）",
    "rbw30k_fine_step": "RBW 30kHz で Step 2kHz 未満: 形が狭く出ます（レベルは正しい）",
    "rbw15k_unstable": "RBW 15kHz は1回の掃引ではレベルと形が揺れます。平均を使ってください",
    # ゼロスパンで RBW 15kHz（設計N §1・C §5.3。D4: 15〜30dB の落ち込みが散発する）
    "rbw15k_zero_span": "RBW 15kHz のゼロスパンは 15〜30dB の落ち込みが散発します（RBW 180kHz なら安定）",
}


def offset_text(off_hz) -> str:
    """周波数オフセットの符号付きの文字（+10GHz・−1.2GHz）。負号は見間違えない字形（J §4.1）。"""
    off = int(off_hz or 0)
    return ("+" if off >= 0 else "−") + format_freq(abs(off))


def freq_offset_of(st: Optional[dict]) -> int:
    """表示の周波数オフセット（設計O）。表示する周波数 = 測った周波数 + これ。"""
    return int(((st or {}).get("view") or {}).get("freq_offset_hz") or 0)


# いまの条件で選べない RBW の理由（設計C §5.14。RBW の一覧で無効にした行と欄のツールチップに出す）。{rbw} は選べる1つの RBW
RBW_LIMITS = {
    "mode_fixed": "TG・S11・NF では RBW は {rbw} だけです",
    "span_wide": "Span が 50MHz を超えると RBW は {rbw} だけです（Span を 50MHz 以下にすると選べます）",
    "points_max": "この Span では点数が上限を超えるので選べません（Span を狭くすると選べます）",
}


def condition_lines(st: dict) -> list[str]:
    """上段の下に出し続ける条件の注意（U-02・U-08）。PNG の帯の注意と同じ文（I §3.2 の 6）。"""
    sw = st.get("sweep") or {}
    if not sw:
        return []
    out = []
    off = freq_offset_of(st)
    if off:
        # 周波数オフセットで換算中であることを出し続ける（設計O: 換算中であることを示す）
        out.append(VIEW_TEXTS["freq_offset_on"].replace("{off}", offset_text(off)))
    out += [WARNINGS.get(w, w) for w in sw.get("warnings", [])]
    if sw.get("tail_gap"):
        out.append(TEMPLATES["cond_tail_gap"])
    rtl = st.get("rtlsdr") or {}
    if rtl.get("connected") and rtl.get("overload"):
        out.append(NOTICES["rtlsdr_overload"])
    fixed = rtl.get("fixed_center") if rtl.get("connected") else None
    if fixed:
        out.append(tpl("cond_fixed_center", cause=FIXED_CENTER_CAUSES.get(fixed.get("cause"), fixed.get("cause")),
                       center=format_freq(int(fixed["tuner_center_hz"]))))
    return out


NOTICES = {
    "sweep_discarded": "掃引を1回捨てました",
    "disconnected": "機器が切れました。同じ機器が戻るのを待っています",
    "reconnected": "機器が戻りました。掃引を再開します",
    "no_calibration": "校正データがありません（補正なしの値）",
    "calibration_unreadable": "校正データが読めません（補正なしの値）",
    "invalid_sweep": "機器が受け付けない条件のため送りませんでした",
    "device_error": "機器との通信で想定外の失敗がありました",
    "internal_error": "内部の失敗がありました",
    "record_error": "記録の書き込みで失敗がありました",
    "reference_unreadable": "前回使っていた基準が読めません（選び直してください）",
    "settings_reset": "設定ファイルが読めないため初期値で起動しました",
    "settings_unwritable": "設定ファイルに書けません",
    "device_profile_changed": "機器が変わったので、掃引条件をその機器に合わせました",
    "rtlsdr_overload": "入力が振り切れています。利得を下げてください（振り切れた間の値は正しくありません）",
    "rtlsdr_setting_failed": "RTL-SDR に利得・ppm を入れられませんでした（測定は続けます）",
    "rtlsdr_shared_serial": "同じシリアル番号の RTL-SDR が複数つながっています。校正はシリアル番号ごとなので共有になります",
    "rtlsdr_span_limited": "チューナーの中心を動かせない間は、表示の範囲を使える幅に限りました",
    "rtlsdr_span_limit_failed": "表示の範囲をチューナーの使える幅に合わせられませんでした",
    "rtltcp_freq_from_client": "rtl_tcp から周波数が変わりました",
    "rtltcp_gain_from_client": "rtl_tcp から利得が変わりました",
    "rtltcp_port_in_use": "rtl_tcp: そのポートは使われています。別のポートを選んでください",
    "rtltcp_start_failed": "rtl_tcp の送出を始められませんでした",
    "rtltcp_sample_rate_unsupported": "rtl_tcp から来たサンプリング速度は使えないので、使える速さで送ります",
    "rtltcp_started": "rtl_tcp の送出を始めました",
    "rtltcp_stopped": "rtl_tcp の送出を止めました",
    "listen_stopped": "聞くのをやめました",
    "auto_connected": "前回の機器に繋ぎました",
}

DISCARD_REASONS = {
    "byte_count": "応答の長さが違う",
    "first_byte_timeout": "応答が始まらない",
    "silence_timeout": "応答が途切れた",
    "total_timeout": "掃引が長すぎる",
}

ERRORS = {
    "not_connected": "機器に繋がっていません",
    "device_not_selected": "機器を選んでください",
    "device_not_found": "その機器が見つかりません",
    "device_ambiguous": "同じシリアル番号の機器が複数あります。一覧から番号付きのポートを選んでください",
    "probe_failed": "GigaSt の応答が返りませんでした（別の機器かもしれません）",
    "open_failed": "ポートを開けませんでした",
    "port_in_use": "ポートを開けません。GS5.exe など別のアプリケーションが使っていないか確かめてください",
    "legacy_unreadable": "旧校正ファイルが読めません",
    "legacy_calibration_not_for_device": "この機器には旧校正ファイルを使えません",
    "file_unreadable": "ファイルが読めません",
    "not_integer": "数値が正しくありません",
    "unknown_backend": "その種類の機器は使えません",
    "unknown_mode": "そのモードはありません",
    "bad_arguments": "操作の値が正しくありません",
    "server_unreachable": "pyGigaSt の PC に繋がりません",
    "rtlsdr_dll_not_found": "RTL-SDR の DLL（rtlsdr.dll）が見つかりません",
    "rtlsdr_not_connected": "RTL-SDR に繋がっていません",
    "rtlsdr_level_needs_manual_gain": "レベルの校正は利得を手動にしてから行います（AGC の間は利得が分からないため）",
    "rtlsdr_stitching_blocked": "チューナーの中心を動かせない間（聞いている・rtl_tcp の相手がつながっている）は、使える幅より広い Span にできません",
    "listen_needs_iq_device": "聞く機能は RTL-SDR に繋いでいるときだけ使えます",
    "listen_rtltcp_active": "rtl_tcp の相手がつながっている間は、マーカーの位置へチューナーを動かせません",
    "marker_no_value": "マーカーの点に値がありません",
    "marker_not_on_rtlsdr_trace": "RTL-SDR で測ったトレースに載ったマーカーを選んでください",
    "calibration_unwritable": "校正を保存できません",
}

CONN_STATES = {
    "disconnected": "未接続",
    "connected": "接続",
    "reconnecting": "切断・再接続待ち",
}

RUN_STATES = {
    "stopped": "停止",
    "continuous": "連続",
    "single": "単発",
    "stopping": "停止中（掃引の終わりを待っています）",
    "reference": "基準を取っています",
    "calibration": "校正で測っています",
}


def notice_text(n: dict) -> str:
    code = n.get("code", "")
    text = NOTICES.get(code, code)
    d = n.get("detail") or {}
    if code == "sweep_discarded":
        r = d.get("reason", "")
        text += tpl("paren", text=DISCARD_REASONS.get(r, r))
    elif code == "device_profile_changed" and d.get("model"):
        # 機種のプロファイルの名前（rtlsdr-r820t など）はコードなので、表示の名前で言う（J §4.9）
        from .screen_model import model_display_name

        name = model_display_name(d["model"])
        if name:
            text += tpl("paren", text=name)
    elif code in ("rtltcp_started", "rtltcp_stopped", "rtltcp_port_in_use") and d.get("port"):
        text += tpl("paren", text=tpl("port", port=d["port"]))
    elif code == "rtltcp_start_failed" and d.get("message"):
        text += tpl("paren", text=d["message"])
    elif code == "rtltcp_freq_from_client" and d.get("freq_hz") is not None:
        f = format_freq(int(d["freq_hz"]))
        if d.get("applied"):
            text += tpl("paren", text=f)
        else:
            why = ERRORS.get(d.get("code") or "", d.get("code") or "")
            text = tpl("rtltcp_freq_refused", freq=f, reason=why)
    elif code == "rtltcp_gain_from_client":
        g = TEMPLATES["gain_agc"] if d.get("agc") else (f"{d['gain_db']:g} dB" if d.get("gain_db") is not None else "")
        if g:
            text += tpl("paren", text=g)
    elif code == "rtltcp_sample_rate_unsupported" and d.get("used"):
        text += tpl("paren", text=f"{d.get('requested')} → {d['used']}")
    elif code == "rtlsdr_span_limited" and d.get("start_hz") is not None:
        cause = FIXED_CENTER_CAUSES.get(d.get("cause"), d.get("cause") or "")
        text = tpl("span_limited", cause=cause, start=format_freq(int(d["start_hz"])),
                   stop=format_freq(int(d["stop_hz"])))
    elif code == "rtlsdr_overload" and d.get("ratio") is not None:
        text += tpl("paren", text=f"{d['ratio'] * 100:.2f} %")
    elif code == "auto_connected" and d.get("name"):
        text = tpl("auto_connected", name=d["name"])
    elif code == "listen_stopped":
        r = LISTEN_STOP_REASONS.get(d.get("reason"), d.get("reason") or "")
        if r:
            text += tpl("paren", text=r)
    return text


def error_text(e) -> str:
    code = getattr(e, "code", None)
    if code is None:
        return str(e)
    detail = getattr(e, "detail", {}) or {}
    if code == "no_peak" and detail.get("polarity") == "dip":
        code = "no_dip"
    text = ERRORS.get(code) or STORAGE_ERRORS.get(code) or code
    if code == "legacy_unreadable" and "line" in detail:
        text += tpl("legacy_line", line=detail["line"], field=detail["field"])
    elif code == "rtlsdr_stitching_blocked" and detail.get("max_span_hz"):
        text += tpl("paren", text=tpl("max_span", span=format_freq(int(detail["max_span_hz"]))))
    elif code == "internal_error" and detail.get("message"):
        # どこで何が起きたかを出す（「値が正しくない」と取り違えさせない）
        text += tpl("paren", text=str(detail["message"]))
    elif code == "bad_value" and "min" in detail and "max" in detail:
        text += tpl("paren", text=tpl("range_min_max", min=detail["min"], max=detail["max"]))
    return text


# ---------------------------------------------------------------- トレース（設計F）

TRACE_ACTIONS = {
    "update": "更新",
    "max_hold": "最大保持",
    "min_hold": "最小保持",
    "average": "平均",
    "fixed": "固定",
}

AVERAGE_TYPES = {
    "power": "パワー",
    "log": "対数",
    "voltage": "電圧",
    "vector": "ベクトル",
}

AVERAGE_AFTER = {
    "exponential": "続ける（指数平均）",
    "restart": "N 回ごとにやり直す",
}

HOLD_AFTER = {
    "continue": "保持し続ける",
    "stop": "N 回で止める",
}

# トレースの線（設計F §9。並びは細線 → 実線 → 太線 → 破線 → 点線。コードは core の TraceLine）
TRACE_LINES = {
    "thin": "細線",
    "solid": "実線",
    "thick": "太線",
    "dash": "破線",
    "dot": "点線",
}

# イメージ除去の3つ（設計F §7.4。並びは正確 → スピード → なし）
IMAGE_REJECT_CHOICES = {
    "accurate": "正確",
    "fast": "スピード",
    "off": "なし",
}


def seconds_text(s) -> str:
    """選択肢に出す1画面の見積もりの秒（小数1桁。1 秒未満も 0.4 のように出す: 設計F §7.4）。"""
    return f"{float(s):.1f}"


def image_reject_item(code: str, seconds=None) -> str:
    """イメージ除去の選択肢の文字「正確（約 3.8 秒）」。見積もりが無い（選べない機器）ときは名前だけ。"""
    name = IMAGE_REJECT_CHOICES.get(code, code)
    if seconds is None:
        return name
    return w("image_reject_item", name=name, s=seconds_text(seconds))

# 蓄積を捨てた理由（設計F §5.3 の条件）
RESET_CAUSES = {
    "axis": "周波数軸",
    "rbw": "RBW",
    "mode": "モード",
    "image_reject": "イメージ除去",
    "calibration": "校正データ",
    "reference": "ノーマライズの基準",
    "sg_freq": "SG の周波数",
    "gain": "受信の利得",
    "ppm": "周波数の補正（ppm）",
    "level": "レベルの補正",
    "level_calibration": "レベルの補正",
}

TRACE_NOTICES = {
    "reset_by_condition": "条件変更でリセット",
    "reset_by_frame_mismatch": "軸の違う掃引が来たのでリセット",
    "average_type_reset": "この値では選べない平均の種類なので既定に戻しました",
    "log_average_reads_low": "対数平均: ノイズの電力は約 2.5dB 低く読めます",
}


def trace_reset_text(detail: dict) -> str:
    c = detail.get("cause", "")
    cause = RESET_CAUSES.get(c, c)
    return tpl("trace_reset_cause", cause=cause) if cause else TRACE_NOTICES["reset_by_condition"]


def legend_parts(t: dict) -> dict:
    """凡例の1項目を report_layout.legend_text の形にする（動作は凡例の隣: U-14）。

    t はトレースのイベント（count が今の回数）か状態の traces の1つ（current_count が今の回数）。
    """
    action = t.get("action")
    out = {"number": t.get("trace", t.get("number")), "action": TRACE_ACTIONS.get(action, action or "")}
    if action == "average" and (t.get("average_type") or t.get("average_type_effective")):
        at = t.get("average_type") or t.get("average_type_effective")
        out["average"] = AVERAGE_TYPES.get(at, at)
    if action in ("average", "max_hold", "min_hold"):
        out["count"] = t.get("count") if "trace" in t else t.get("current_count")
        out["count_target"] = t.get("count_target")
    else:
        out["count"] = None
    if t.get("axis_differs"):
        out["label"] = TEMPLATES["axis_differs"]
    return out


def file_trace_label(f: dict, tid: str) -> str:
    """参照トレース（開いたファイル）の凡例の文字: 名前・番号・日時・機器。"""
    num = str(tid).split(".", 1)[-1]
    when = str(f.get("created_at") or "")[:16].replace("T", " ")
    dev = f.get("device_serial") or ""
    return " ".join(p for p in (f.get("name", tid), f"#{num}", when, dev) if p)


# ---------------------------------------------------------------- マーカー（設計G）

MARKER_STATUS = {
    "ok": "",
    "out_of_range": "範囲外",
    "no_value": "値なし",
    "no_trace": "トレースに値がない",
    "parent_unavailable": "元のマーカーが読めない",
    "no_ref": "基準がない",
    "ref_unavailable": "基準が読めない",
    "unit_mismatch": "dBm と dB の差は出さない",
}

# 読み値に添える注意（G §3.3）。点の印と、掃引条件の信用できない条件
MARKER_NOTES = {
    "transient": "掃引の始めの不安定な点",
    "out_of_cal_range": "校正の範囲外",
    "no_reference": "基準の範囲外",
    "no_reference_band_edge": "基準の帯の境目",
    "ref_coarse": "基準が粗い（±0.5dB 程度）",
    "ref_power_cycled": "電源の入れ直し前の基準",
    "reference_coarse": "基準が粗い（±0.5dB 程度）",
    "reference_power_cycled": "電源の入れ直し前の基準",
    "rbw15k_unstable": "RBW 15kHz の1回の掃引",
    "rbw15k_zero_span": "RBW 15kHz のゼロスパン（落ち込みが散発）",
    "rbw180k_narrow_span": "RBW 180kHz で狭い Span",
    "rbw30k_fine_step": "RBW 30kHz で細かい Step",
    "no_value": "値なし",
    # FFT の機器を Hann 窓で測った値（設計Q §6.4）。格子の間の信号は低く読めることがある
    "hann_scalloping": "Hann 窓: 最大約 1.4dB 低く読めることがある",
    # 機器の範囲の外・意味の無い点（設計J §4.8）。グラフの帯と同じ文
    **DISPLAY_MARKS,
}

# 印のうち読み値の注意にしないもの（細かい基準は正常）
# ic_noise_floor は前の版の印（雑音の床が低く読めるという注意。実測で真ん中はほぼ動かず誤りだった: T18）
MARKER_NOTES_SILENT = {"ref_fine", "ic_noise_floor"}

BAND_STATUS = {
    "ok": "",
    "edge": "表示範囲の端まで交点がない",
    "no_value_region": "基準の無い区間に達したため求められません",
    "no_marker_value": "マーカーの点に値がない",
    "not_in_region": "マーカーがしきい値の指定の側にない",
    "out_of_range": "範囲外",
    "no_value": "値なし",
    "no_trace": "トレースに値がない",
}

THRESHOLD_SIDES = {"above": "以上", "below": "以下"}

# ---------------------------------------------------------------- ノーマライズ（設計E）

NORM_REASONS = {
    "no_reference_selected": "基準を選んでいません",
    "reference_empty": "基準に区間がありません",
    "not_power": "測った値が電力ではありません",
    "unknown_conditions": "掃引条件が分かりません",
    "serial_mismatch": "別の機器で取った基準です",
    "mode_mismatch": "別のモードの基準です",
    "image_reject_mismatch": "基準とイメージ除去が違います",
    "rbw_mismatch": "基準と RBW が違います",
    "out_of_range": "表示範囲に基準がありません",
}

ACCURACY = {
    "fine": "細かい",
    "coarse": "粗い（±0.5dB 程度）",
    "mixed": "一部が粗い（±0.5dB 程度）",
    "none": "",
}


def age_text(seconds) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    if s < 60:
        return tpl("age_s", n=s)
    if s < 3600:
        return tpl("age_m", n=s // 60)
    if s < 86400:
        return tpl("age_h", n=s // 3600)
    return tpl("age_d", n=s // 86400)


def ranges_text(ranges) -> str:
    return "・".join(tpl("range", lo=format_freq(lo), hi=format_freq(hi)) for lo, hi in ranges)


def normalization_text(n: dict) -> str:
    """状態バーの「基準: …」（E §6.3・§6.4）。使わないモードでは空。"""
    if not n or not n.get("used"):
        return ""
    sel = n.get("selected")
    if not n.get("applied"):
        reason = NORM_REASONS.get(n.get("reason") or "", n.get("reason") or "")
        return tpl("norm_none_reason", reason=reason) if reason else TEMPLATES["norm_none"]
    parts = [tpl("norm_ref", name=sel["name"]) if sel else TEMPLATES["norm_ref_noname"]]
    acc = ACCURACY.get(n.get("accuracy") or "", "")
    if acc:
        parts.append(acc)
    age = age_text(n.get("age_s"))
    if age:
        parts.append(age)
    if n.get("power_cycled"):
        parts.append(TEMPLATES["norm_power_cycled"])
    if n.get("out_of_range_hz"):
        parts.append(tpl("norm_out_of_range", ranges=ranges_text(n["out_of_range_hz"])))
    return "・".join(parts)


# ---------------------------------------------------------------- 長い処理（設計H §4.1）

TASK_NAMES = {
    "save": "保存しています",
    "open": "読み込んでいます",
    "export_csv": "書き出しています",
    "record_finish": "記録をまとめています",
    "finish_unfinished": "記録をまとめています",
    "rtlsdr_fit_gain": "利得を合わせています",
    "reference_take": "基準を取っています",
}

TASK_DONE = {
    "save": "保存しました",
    "open": "開きました",
    "export_csv": "書き出しました",
    "record_finish": "記録をまとめました",
    "finish_unfinished": "記録をまとめました",
    "rtlsdr_fit_gain": "利得を合わせました",
    "reference_take": "基準を取りました",
}

TASK_FAILED = {
    "save": "保存できませんでした",
    "open": "開けませんでした",
    "export_csv": "書き出せませんでした",
    "record_finish": "記録をまとめられませんでした",
    "finish_unfinished": "記録をまとめられませんでした",
    "rtlsdr_fit_gain": "利得を合わせられませんでした",
    "reference_take": "基準を取れませんでした",
}

# やめたときに何が残るか（U-19: やめたら何が残るか言う）
TASK_CANCELLED = {
    "save": "保存をやめました（ファイルは作っていません）",
    "open": "読み込みをやめました",
    "export_csv": "書き出しをやめました（書きかけのファイルは消しました）",
    "record_finish": "まとめるのをやめました。記録の作業ファイルは残っていて、あとでまとめられます",
    "finish_unfinished": "まとめるのをやめました。作業ファイルは残っています",
    "rtlsdr_fit_gain": "利得を合わせるのをやめました（利得は押す前の段に戻しました）",
    "reference_take": "基準を取るのをやめました（基準は変わりません）",
}

STORAGE_ERRORS = {
    "cannot_open": "ファイルを開けません",
    "not_pygigast_file": "pyGigaSt の保存ファイルではありません",
    "unsupported_version": "知らない版のファイルなので開きません",
    "axis_mismatch": "周波数の点が違うトレースは同じ表に並べられません",
    "unit_mismatch": "電力と比のトレースは同じ単位で書き出せません",
    "range_empty": "その範囲に点がありません",
    "no_data": "書き出すトレースがありません",
    "os_error": "ファイルに書けません",
    "reference_build_failed": "基準を作れませんでした",
    "reference_unreadable": "基準のファイルが読めません",
    "corrupt_sweep": "記録の掃引が壊れています",
}

EXPORT_WARNINGS = {"exceeds_spreadsheet_rows": "表計算ソフトの行数の上限を超えています"}


def storage_error_text(err: dict) -> str:
    code = (err or {}).get("code", "")
    return STORAGE_ERRORS.get(code) or ERRORS.get(code) or code


# 校正の測定（設計K）の語は texts_cal.py に置き、ここで同じ表へ足す（Web へも同じ表で配る）
TASK_NAMES.update(CAL_TASK_NAMES)
TASK_DONE.update(CAL_TASK_DONE)
TASK_FAILED.update(CAL_TASK_FAILED)
TASK_CANCELLED.update(CAL_TASK_CANCELLED)


def task_label(t: dict) -> str:
    """進み具合の1行（何件目/全体・経過時間。時間は約束しない: U-20）。"""
    kind = t.get("task") or t.get("kind") or ""
    text = TASK_NAMES.get(kind, kind)
    if t.get("total"):
        text += tpl("task_progress", done=t.get("done", 0), total=t["total"])
    text += tpl("task_elapsed", s=int(t.get("elapsed_s") or 0))
    if t.get("cancelling"):
        text += TEMPLATES["task_cancelling"]
    return text


ERRORS.update(ZERO_SPAN_ERRORS)
ERRORS.update({
    "no_trace_data": "そのトレースにまだ値がありません",
    "table_full": "マーカーはそれぞれ 8 本までです",
    "no_peak": "ピークが見つかりません",
    # 谷を探したとき（G §3.8）。コアは no_peak に探した向きを添えて返す（error_text が言い換える）
    "no_dip": "谷が見つかりません",
    "bad_polarity": "探すもの（山・谷）の指定が正しくありません",
    "dips_on_power": "電力のスペクトルでは谷を探しません",
    "same_frequency": "デルタの2点が同じ周波数です",
    "unknown_marker": "そのマーカーはもうありません",
    "bad_marker_setting": "そのマーカーでは使えない設定です",
    "bad_offset": "オフセットは正の値で入れてください",
    "bad_marker_function": "マーカーの機能の値が正しくありません",
    "out_of_range": "マーカーが表示範囲の外です",
    "no_value": "マーカーの点に値がありません",
    "no_ref": "デルタの基準を選んでください",
    "ref_unavailable": "デルタの基準が読めません",
    "reference_in_progress": "基準を取っている間はできません",
    "mode_not_normalizable": "基準は TG・S11 モードで取ります",
    "s11_open_missing": "先にオープンを測ってください",
    "already_recording": "もう記録しています",
    "not_recording": "記録していません",
    "no_data": "保存する値がありません（掃引してください）",
    "bad_value": "値が正しくありません",
    "bad_trace_setting": "そのトレースでは使えない設定です",
    "bad_name": "名前を入れてください",
    "not_a_work_file": "記録の作業ファイルではありません",
    "unknown_task": "その処理はもう終わっています",
    "rtlsdr_fit_needs_manual_gain": "AGC の間は利得を合わせられません（利得を手動の段にしてから押します）",
    "rtlsdr_fit_running": "いま利得を合わせています",
    **WEB_ERRORS,
    **SETTINGS_ERRORS,
    "unknown_file": "そのファイルはもう外しています",
})
# 校正の画面（設計K）が断った理由。すでにある語は変えない
ERRORS.update({k: v for k, v in CAL_ERRORS.items() if k not in ERRORS})


# ---------------------------------------------------------------- 状態バー（J §2・M-04）。Qt と Web で同じ項目・同じ語

def fmt_clock(seconds) -> str:
    s = int(seconds or 0)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def fmt_bytes(n) -> str:
    n = float(n or 0)
    units = ("B", "KB", "MB", "GB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{int(n)} {units[i]}"


def calibration_status(st: dict) -> str:
    """「補正: …」。RTL-SDR は個体の校正（ppm・レベル: 設計Q §6.6）と、単位（dB（未校正）など）。"""
    rtl = st.get("rtlsdr") or {}
    if rtl.get("connected"):
        cal = rtl.get("calibration")
        ppm = rtl.get("ppm") if rtl.get("ppm") is not None else (cal or {}).get("ppm", 0)
        unit = rtl.get("level_unit") or ""
        level = TEMPLATES["rtl_level_cal"] if cal and cal.get("level_offset_db") else ""
        if cal:
            return tpl("status_cal_rtl", ppm=f"{int(ppm):+d}", level=level, at=cal.get("created_at") or "",
                       unit=unit)
        return tpl("status_cal_rtl_none", ppm=f"{int(ppm or 0):+d}", unit=unit)
    cal = st.get("calibration")
    return tpl("status_cal", at=cal["created_at"]) if cal else TEMPLATES["status_cal_none"]


def recording_text(rec: dict) -> str:
    if rec.get("active"):
        text = tpl("status_rec_active", clock=fmt_clock(rec.get("elapsed_s")), sweeps=rec.get("sweeps", 0))
    elif rec.get("stopped"):
        text = TEMPLATES["status_rec_stopped"]
    else:
        return ""
    if rec.get("file_bytes"):
        text += "・" + fmt_bytes(rec["file_bytes"])
    return text


def source_text(st: dict) -> str:
    """信号源が出ていることの表示（U-05: 状態は画面が示す）。掃引している間だけ出す。"""
    if not st or not st.get("run") or not st.get("sweep"):
        return ""
    running = st["run"]["state"] in ("continuous", "single", "stopping", "reference")
    if st["run"]["state"] == "calibration" and (st.get("connection") or {}).get("state") == "connected":
        # 校正の掃引は、いまの条件とは別の条件（RBW の校正は SG で 500MHz: 設計K §4.3）。機器へ渡した方を出す
        src = st.get("source") or {}
        if src.get("output") == "sg" and src.get("freq_hz") is not None:
            return tpl("source_sg", freq=format_freq(src["freq_hz"]))
        return TEMPLATES["source_tg"] if src.get("output") == "tg" else ""
    if (st.get("connection") or {}).get("state") != "connected" or not running:
        return ""
    sw = st["sweep"]
    if sw["mode"] == "sg" and sw.get("sg_hz") is not None:
        return tpl("source_sg", freq=format_freq(sw["sg_hz"]))
    if sw["mode"] in ("tg", "s11"):
        return TEMPLATES["source_tg"]
    return ""


def sg_state_text(st: dict) -> str:
    """SG 周波数の欄のそばの状態（Qt・Web 同じ）。出していないときは原因（未接続・モード・止めている）を言う
    （設計J §4.12: 「出力していません」だけでは、打った値が出ない理由が分からない）。"""
    text = source_text(st)
    if text and (st.get("sweep") or {}).get("mode") == "sg":
        return text
    if (st.get("connection") or {}).get("state") != "connected":
        return ERRORS["not_connected"]
    if (st.get("sweep") or {}).get("mode") != "sg":
        return LABELS["sg_off_mode"]
    return LABELS["sg_off_stopped"]


def rtltcp_status(r: dict) -> tuple[str, str]:
    """(文字, 種類)。出している間は「rtl_tcp 送出中 :1234・接続 N」（ポートを開けていることを見せる: 設計Q §4.3）。"""
    if not r:
        return "", ""
    if r.get("running"):
        text = tpl("status_rtltcp", port=r.get("port"), clients=r.get("clients", 0))
        if r.get("bind") == "lan":
            text += TEMPLATES["status_rtltcp_lan"]
        return text, "warn"
    err = r.get("error")
    if r.get("enabled") and err:
        why = TEMPLATES["rtltcp_port_in_use"] if err.get("code") == "port_in_use" else (err.get("message") or "")
        return tpl("status_rtltcp_error", port=err.get("port", r.get("port")), reason=why), "warn"
    return "", ""


def listen_status(lst: dict) -> str:
    """「聞いている: 84.7MHz・FM放送」（M-04: 動作中の機能を出す）。"""
    if not lst or not lst.get("active"):
        return ""
    return tpl("status_listen", freq=format_freq(int(lst["freq_hz"])),
               mode=LISTEN_MODES.get(lst.get("mode"), lst.get("mode") or ""))


def status_items(st: dict) -> list[dict]:
    """状態バーの項目（key・text・cls）。Qt と Web が同じ並び・同じ文字で出す。空の text は出さない。"""
    conn = st.get("connection") or {}
    ident = conn.get("identity") or {}
    # 名前（利用者が付けた名前か、機種名＋シリアル番号の末尾4桁）とポート（J §4.7: いま使っている機器は状態バーで分かる）。
    # ポートは COM 番号を持つ機器（GigaSt）だけ。RTL-SDR・シミュレータのポートは内部の名前なので出さない（J §4.9）
    name = conn.get("name") or device_name({"kind": conn.get("backend")})
    port = ident.get("port", "") if port_shown(conn.get("backend")) else ""
    who = (tpl("status_conn_who", name=name, port=port) if port else tpl("status_conn_who_name", name=name)) \
        if ident else ""
    run = st.get("run") or {}
    rs = RUN_STATES.get(run.get("state"), run.get("state"))
    if run.get("state") == "single" or (run.get("state") == "stopped" and run.get("last_stop_reason") == "single_done"):
        rs += tpl("status_single_count", done=run.get("single_done"), target=run.get("single_target"))
    nt = normalization_text(st.get("normalization") or {})
    norm = st.get("normalization") or {}
    rt_text, rt_cls = rtltcp_status(st.get("rtltcp") or {})
    tasks = st.get("tasks") or []
    ext = float((st.get("view") or {}).get("ext_db") or 0.0)
    off = freq_offset_of(st)
    items = [
        {"key": "conn", "text": TEMPLATES["status_conn_none"] if conn.get("state") == "disconnected" else
         tpl("status_conn", state=CONN_STATES.get(conn.get("state"), conn.get("state")), who=who),
         "cls": "ok" if conn.get("state") == "connected" else "warn"},
        {"key": "run", "text": tpl("status_run", state=rs), "cls": ""},
        {"key": "time", "text": tpl("status_sweep_time", s=f"{run['last_sweep_s']:.2f}") if run.get("last_sweep_s")
         else TEMPLATES["status_sweep_time_none"], "cls": ""},
        {"key": "discard", "text": tpl("status_discard", n=run.get("discard_count", 0)), "cls": ""},
        {"key": "cal", "text": calibration_status(st), "cls": ""},
        # 外部補正・周波数オフセット（設計O: 読み値がずれていることを常に見せる）
        {"key": "ext", "text": VIEW_TEXTS["status_ext"].replace("{db}", f"{ext:+g}") if ext else "", "cls": "warn"},
        {"key": "offset", "text": VIEW_TEXTS["status_offset"].replace("{off}", offset_text(off)) if off else "",
         "cls": "warn"},
        {"key": "norm", "text": nt, "cls": "" if norm.get("applied") else "warn"},
        {"key": "src", "text": source_text(st), "cls": "warn"},
        {"key": "listen", "text": listen_status(st.get("listen") or {}), "cls": "warn"},
        {"key": "rtltcp", "text": rt_text, "cls": rt_cls},
        {"key": "rec", "text": recording_text(st.get("recording") or {}), "cls": "warn"},
        {"key": "tasks", "text": tpl("status_tasks", n=len(tasks)) if tasks else "", "cls": ""},
    ]
    return [i for i in items if i["text"]]


# ---------------------------------------------------------------- 一度きりの知らせを画面に出す形（Qt と Web で同じ）

def task_end_text(code: str, d: dict) -> tuple[str, Optional[str]]:
    """長い処理の終わりの知らせ（H §4.1）。(文, 保存したファイルのパス or None)。"""
    kind = d.get("task", "")
    if code == "task_done":
        res = d.get("result") or {}
        text = TASK_DONE.get(kind, kind)
        path = res.get("path")
        if path:
            text = tpl("task_done_path", done=text, name=PurePath(path).name)
        if kind == "open":
            text = tpl("open_done", n=len(res.get("files") or []))
            errs = res.get("errors") or []
            if errs:
                text += tpl("open_errors", list="、".join(
                    tpl("open_error_item", name=PurePath(e["path"]).name, reason=storage_error_text(e)) for e in errs))
        elif kind == "export_csv":
            text += tpl("export_rows", rows=res.get("rows", 0))
            warns = [EXPORT_WARNINGS.get(w, w) for w in res.get("warnings") or []]
            if warns:
                text += "・" + "・".join(warns)
        elif kind == "rtlsdr_fit_gain":
            text = fit_gain_text(res)
        elif kind == "reference_take":
            text = TEMPLATES["ref_open_measured"] if res.get("next") == "short" else tpl("ref_taken",
                                                                                         name=res.get("name", ""))
            if res.get("ref_level") is not None:
                # 0dB が見えるよう REF レベルを動かした（上段の「基準を取る」: 黙って変えない J §4）
                text += tpl("ref_zero_shown", ref=f"{res['ref_level']:+g}".replace("-", "−"))
        return text, path
    if code == "task_cancelled":
        return TASK_CANCELLED.get(kind, TEMPLATES["cancelled_generic"]), None
    err = d.get("error") or {}
    text = tpl("task_failed", failed=TASK_FAILED.get(kind, kind), error=storage_error_text(err))
    detail = err.get("detail") or {}
    if detail.get("message"):
        text += tpl("paren", text=detail["message"])
    if kind in ("record_finish", "finish_unfinished"):
        text += TEMPLATES["work_file_kept"]
    return text, None


def fit_gain_text(res: dict) -> str:
    """「利得を合わせる」の終わりの1件（設計Q §6.5: 選んだ段と、蓄積を捨てたこと）。"""
    before = res.get("previous_gain_db")
    text = tpl("rtl_fit_done", gain=f"{float(res.get('gain_db', 0.0)):g}",
               before=TEMPLATES["gain_agc"] if before is None else f"{float(before):g} dB")
    if res.get("overload"):
        text += TEMPLATES["rtl_fit_overload"]
    if res.get("traces_reset"):
        text += tpl("rtl_fit_reset", traces="・".join(str(x) for x in res["traces_reset"]))
    return text


def level_unit_label(code) -> str:
    """単位のコード（dBm・dBuV・dBmV）を画面の名前にする。"""
    from ..core.view_units import LEVEL_UNITS

    return LEVEL_UNITS.get(code or "dBm", str(code))


# 条件が基準と合わなくなって電力に戻ったとき（比で見る手立てを添える）。基準を外した・モードを変えたときは添えない
POWER_HINT_REASONS = ("image_reject_mismatch", "rbw_mismatch", "out_of_range", "serial_mismatch", "mode_mismatch")


def notice_view(n: dict, state: Optional[dict] = None) -> dict:
    """知らせ1件の出し方: {"text": 状態バーの文, "traces": {番号: トレースの欄の文}, "path": 保存先 or None,
    "field": 欄の下にも出すときの欄の名前 or None, "modal": 窓で知らせるときの中身 or None}。

    modal は {"key": 「次から出さない」の覚え書きの名前, "title", "text", "check": チェックの文}。
    出すかどうかは画面ごとの覚え書きで画面が決める（設計O §1.1）。
    """
    code = n.get("code", "")
    d = n.get("detail") or {}
    traces: dict = {}
    path = None
    if code == "level_unit_changed":
        # 単位の設定が変わった（どこから変わっても: J §4.10 の 4）。単位の欄の下と状態バーに出す
        return {"text": VIEW_TEXTS["level_unit_changed"].replace("{unit}", level_unit_label(d.get("unit"))),
                "traces": {}, "path": None, "field": "unit", "modal": None}
    if code == "ratio_display":
        # 電力 → 基準との比に変わった（J §4.10 の 3）
        name, unit = str(d.get("name") or ""), level_unit_label(d.get("unit"))
        modal = {"key": "ratio_display", "title": VIEW_TEXTS["ratio_title"], "check": VIEW_TEXTS["dont_show_again"],
                 "text": VIEW_TEXTS["ratio_text"].replace("{name}", name).replace("{unit}", unit)}
        return {"text": VIEW_TEXTS["ratio_status"].replace("{name}", name), "traces": {}, "path": None,
                "field": None, "modal": modal}
    if code == "power_display":
        # 基準との比 → 電力に戻った（J §4.10 の 3。窓は比に変わったときと同じ覚え書き ratio_display で出すかを決める）
        unit = level_unit_label(d.get("unit"))
        r = d.get("reason") or ""
        if r == "mode":
            reason = VIEW_TEXTS["power_reason_mode"].replace("{mode}", MODE_LABELS.get(d.get("mode"), d.get("mode") or ""))
        else:
            reason = NORM_REASONS.get(r, r)
        text = VIEW_TEXTS["power_text"].replace("{reason}", reason).replace("{unit}", unit)
        if r in POWER_HINT_REASONS:
            text += "\n" + VIEW_TEXTS["power_hint"]
        modal = {"key": "ratio_display", "title": VIEW_TEXTS["power_title"], "check": VIEW_TEXTS["dont_show_again"],
                 "text": text}
        return {"text": VIEW_TEXTS["power_status"].replace("{unit}", unit).replace("{reason}", reason), "traces": {},
                "path": None, "field": None, "modal": modal}
    if code in ("task_done", "task_failed", "task_cancelled"):
        text, path = task_end_text(code, d)
        res = d.get("result") or {}
        if code == "task_done" and d.get("task") == "rtlsdr_fit_gain" and res.get("traces_reset"):
            # 利得を合わせて捨てたトレースは、トレースの欄にも出す（知らせは1件のまま: 設計Q §6.5）
            t = trace_reset_text({"cause": "gain"})
            traces = {num: t for num in res["traces_reset"]}
    elif code == "trace":
        t = "・".join(TRACE_NOTICES.get(c, c) for c in d.get("codes") or [])
        traces[d.get("trace")] = t
        text = tpl("trace_note", n=d.get("trace"), text=t)
    elif code == "trace_reset":
        t = trace_reset_text(d)
        nums = list(d.get("traces") or [])
        traces = {num: t for num in nums}
        text = tpl("trace_note", n="・".join(str(x) for x in nums), text=t)
    elif code in TRACE_NOTICES:
        t = TRACE_NOTICES[code]
        if d.get("trace"):
            traces[d["trace"]] = t
            text = tpl("trace_note", n=d["trace"], text=t)
        else:
            text = t
    elif code == "normalization":
        text = normalization_text({**((state or {}).get("normalization") or {}), **d, "used": True})             or TEMPLATES["norm_changed"]
    else:
        text = notice_text(n)
    return {"text": text, "traces": {str(k): v for k, v in traces.items() if k is not None}, "path": path,
            "field": None, "modal": None}


def waiting_text(remaining_s: float) -> str:
    return tpl("waiting_sweep_end", s=f"{float(remaining_s):.1f}")
