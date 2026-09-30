"""コアが返す理由のコードを、画面の文言にする（U-22: 画面の言い方と記録の言い方を分ける）。"""
from __future__ import annotations

from ..core.units import format_freq

FIELD_NAMES = {
    "start_hz": "Start",
    "stop_hz": "Stop",
    "center_hz": "Center",
    "span_hz": "Span",
    "points": "点数",
    "rbw_hz": "RBW",
    "sg_hz": "SG 周波数",
    "step_hz": "Step",
}

FREQ_FIELDS = {"start_hz", "stop_hz", "center_hz", "span_hz", "sg_hz", "step_hz", "rbw_hz"}


def adjustment_text(adj: dict) -> str:
    """調整1件を「◯◯ に合わせました（理由）」にする。"""
    field = adj.get("field", "")
    v = adj.get("adopted")
    value = format_freq(v) if field in FREQ_FIELDS and isinstance(v, int) else str(v)
    reason = REASONS.get(adj.get("reason", ""), adj.get("reason", ""))
    return f"{value} に合わせました（{reason}）" if reason else f"{value} に合わせました"


REASONS = {
    "upper_limit": "上限",
    "lower_limit": "下限",
    "freq_unit": "機器の周波数の単位",
    "min_span": "この点数で作れる最小の Span",
    "step_max": "Step は 1MHz まで",
    "points_max": "点数の上限",
    "sg_range": "SG の範囲",
    "rbw_fixed": "この条件では RBW 180kHz 固定",
    "keep_center": "Center を保つため、範囲に収まる最大の Span",
    "keep_span": "Span 固定のため、Span を保って Center を移動",
}

# 範囲の規則で値を動かした理由（設計C §5.11）。この知らせには反対の規則のボタンを添える
RULE_REASONS = {"keep_center", "keep_span"}


def alternative_label(alt: dict) -> str:
    """反対の規則の結果のボタンの文字（設計C §5.11 の 3）。"""
    span = alt["stop_hz"] - alt["start_hz"]
    center = alt["start_hz"] + span // 2
    if alt.get("rule") == "keep_span":
        return f"Span {format_freq(span)} を保って Center を {format_freq(center)} にする"
    return f"Center {format_freq(center)} を保って Span を {format_freq(span)} にする"


def alternative_done_text(center_hz: int, span_hz: int) -> str:
    return f"Center {format_freq(center_hz)}・Span {format_freq(span_hz)} に置き換えました（この1回だけ）"

WARNINGS = {
    "rbw180k_narrow_span": "RBW 180kHz で Span 0.3MHz 未満: 平らな上面しか見えません（レベルは正しい）",
    "rbw30k_fine_step": "RBW 30kHz で Step 2kHz 未満: 形が狭く出ます（レベルは正しい）",
    "rbw15k_unstable": "RBW 15kHz は1回の掃引ではレベルと形が揺れます。平均を使ってください",
}

NOTICES = {
    "sweep_discarded": "掃引を1回捨てました",
    "disconnected": "機器が切れました。同じ機器が戻るのを待っています",
    "reconnected": "機器が戻りました。掃引を再開します",
    "no_calibration": "校正データがありません（補正なしの値）",
    "calibration_unreadable": "校正データが読めません（補正なしの値）",
    "invalid_sweep": "機器が受け付けない条件のため送りませんでした",
    "device_error": "機器との通信で想定外の失敗がありました",
    "internal_error": "内部の失敗がありました",
}

DISCARD_REASONS = {
    "byte_count": "受け取ったバイト数が違う",
    "first_byte_timeout": "応答が始まらない",
    "silence_timeout": "応答が途切れた",
    "total_timeout": "掃引が長すぎる",
}

ERRORS = {
    "not_connected": "機器に繋がっていません",
    "device_not_selected": "機器を選んでください",
    "device_not_found": "その機器が見つかりません",
    "probe_failed": "GigaSt の応答が返りませんでした（別の機器かもしれません）",
    "open_failed": "ポートを開けませんでした",
    "legacy_unreadable": "旧校正ファイルが読めません",
    "file_unreadable": "ファイルが読めません",
    "not_integer": "数値が正しくありません",
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
}


def notice_text(n: dict) -> str:
    code = n.get("code", "")
    text = NOTICES.get(code, code)
    detail = n.get("detail") or {}
    if code == "sweep_discarded":
        r = detail.get("reason", "")
        text += f"（{DISCARD_REASONS.get(r, r)}）"
    return text


def error_text(e: Exception) -> str:
    code = getattr(e, "code", None)
    if code is None:
        return str(e)
    text = ERRORS.get(code, code)
    detail = getattr(e, "detail", {}) or {}
    if code == "legacy_unreadable" and "line" in detail:
        text += f"（{detail['line']} 行目の {detail['field']} 番目の欄）"
    return text
