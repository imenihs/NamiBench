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
    "record_error": "記録の書き込みで失敗がありました",
    "reference_unreadable": "前回使っていた基準が読めません（選び直してください）",
    "settings_reset": "設定ファイルが読めないため初期値で起動しました",
    "settings_unwritable": "設定ファイルに書けません",
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

# 蓄積を捨てた理由（設計F §5.3 の条件）
RESET_CAUSES = {
    "axis": "周波数軸",
    "rbw": "RBW",
    "mode": "モード",
    "image_cancel": "イメージキャンセル",
    "calibration": "校正データ",
    "reference": "ノーマライズの基準",
}

TRACE_NOTICES = {
    "reset_by_condition": "条件変更でリセット",
    "reset_by_frame_mismatch": "軸の違う掃引が来たのでリセット",
    "average_type_reset": "この値では選べない平均の種類なので既定に戻しました",
    "log_average_reads_low": "対数平均: ノイズの電力は約 2.5dB 低く読めます",
}


def trace_reset_text(detail: dict) -> str:
    cause = RESET_CAUSES.get(detail.get("cause", ""), detail.get("cause", ""))
    return f"条件変更（{cause}）でリセット" if cause else TRACE_NOTICES["reset_by_condition"]


def legend_parts(t: dict) -> dict:
    """凡例の1項目を png_export.legend_text の形にする（動作は凡例の隣: U-14）。"""
    action = t.get("action")
    out = {"number": t.get("trace", t.get("number")), "action": TRACE_ACTIONS.get(action, action or "")}
    if action == "average" and t.get("average_type"):
        out["average"] = AVERAGE_TYPES.get(t["average_type"], t["average_type"])
    if action in ("average", "max_hold", "min_hold"):
        out["count"] = t.get("count")
        out["count_target"] = t.get("count_target")
    else:
        out["count"] = None
    if t.get("axis_differs"):
        out["label"] = "（いまの軸と違う）"
    return out


# ---------------------------------------------------------------- マーカー（設計G）

MARKER_STATUS = {
    "ok": "",
    "out_of_range": "範囲外",
    "no_value": "値なし",
    "no_trace": "トレースに値がない",
    "parent_unavailable": "親が読めない",
    "no_ref": "基準がない",
    "ref_unavailable": "基準が読めない",
    "unit_mismatch": "dBm と dB の差は出さない",
}

# 読み値に添える注意（G §3.3）。点の印と、掃引条件の信用できない条件
MARKER_NOTES = {
    "transient": "前置き不足",
    "out_of_cal_range": "校正の範囲外",
    "no_reference": "基準の範囲外",
    "no_reference_band_edge": "基準の帯の境目",
    "ref_coarse": "基準が粗い",
    "ref_power_cycled": "電源の入れ直し前の基準",
    "reference_coarse": "基準が粗い（±0.5dB 程度）",
    "reference_power_cycled": "電源の入れ直し前の基準",
    "rbw15k_unstable": "RBW 15kHz の1回の掃引",
    "rbw180k_narrow_span": "RBW 180kHz で狭い Span",
    "rbw30k_fine_step": "RBW 30kHz で細かい Step",
}

# 印のうち読み値の注意にしないもの（細かい基準は正常）
MARKER_NOTES_SILENT = {"ref_fine"}

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

DIRECTIONS = {"highest": "ピーク", "left": "次のピーク（左）", "right": "次のピーク（右）", "lower": "次に高いピーク"}

# ---------------------------------------------------------------- ノーマライズ（設計E）

NORM_REASONS = {
    "no_reference_selected": "基準を選んでいません",
    "reference_empty": "基準に区間がありません",
    "not_power": "測った値が電力ではありません",
    "unknown_conditions": "掃引条件が分かりません",
    "serial_mismatch": "別の機器で取った基準です",
    "mode_mismatch": "別のモードの基準です",
    "image_cancel_mismatch": "基準とイメージキャンセルの有無が違います",
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
        return f"{s} 秒前"
    if s < 3600:
        return f"{s // 60} 分前"
    if s < 86400:
        return f"{s // 3600} 時間前"
    return f"{s // 86400} 日前"


def ranges_text(ranges) -> str:
    return "・".join(f"{format_freq(lo)}〜{format_freq(hi)}" for lo, hi in ranges)


def normalization_text(n: dict) -> str:
    """状態バーの「基準: …」（E §6.3・§6.4）。使わないモードでは空。"""
    if not n or not n.get("used"):
        return ""
    sel = n.get("selected")
    if not n.get("applied"):
        reason = NORM_REASONS.get(n.get("reason") or "", n.get("reason") or "")
        return f"基準なし（受けた電力）: {reason}" if reason else "基準なし（受けた電力）"
    parts = [f"基準: {sel['name']}" if sel else "基準"]
    acc = ACCURACY.get(n.get("accuracy") or "", "")
    if acc:
        parts.append(acc)
    age = age_text(n.get("age_s"))
    if age:
        parts.append(age)
    text = "・".join(parts)
    if n.get("power_cycled"):
        text += "・⚠ 電源の入れ直し前の基準（取り直しを勧めます）"
    if n.get("out_of_range_hz"):
        text += f"・基準の範囲外: {ranges_text(n['out_of_range_hz'])}"
    return text


# ---------------------------------------------------------------- 長い処理（設計H §4.1）

TASK_NAMES = {
    "save": "保存しています",
    "open": "読み込んでいます",
    "export_csv": "書き出しています",
    "record_finish": "記録をまとめています",
    "finish_unfinished": "記録をまとめています",
    "reference_take": "基準を取っています",
}

TASK_DONE = {
    "save": "保存しました",
    "open": "開きました",
    "export_csv": "書き出しました",
    "record_finish": "記録をまとめました",
    "finish_unfinished": "記録をまとめました",
    "reference_take": "基準を取りました",
}

TASK_FAILED = {
    "save": "保存できませんでした",
    "open": "開けませんでした",
    "export_csv": "書き出せませんでした",
    "record_finish": "記録をまとめられませんでした",
    "finish_unfinished": "記録をまとめられませんでした",
    "reference_take": "基準を取れませんでした",
}

# やめたときに何が残るか（U-19: やめたら何が残るか言う）
TASK_CANCELLED = {
    "save": "保存をやめました（ファイルは作っていません）",
    "open": "読み込みをやめました",
    "export_csv": "書き出しをやめました（書きかけのファイルは消しました）",
    "record_finish": "まとめるのをやめました。記録の作業ファイルは残っていて、あとでまとめられます",
    "finish_unfinished": "まとめるのをやめました。作業ファイルは残っています",
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


def task_label(t: dict) -> str:
    """進み具合の1行（何件目/全体・経過時間。時間は約束しない: U-20）。"""
    kind = t.get("task") or t.get("kind") or ""
    text = TASK_NAMES.get(kind, kind)
    if t.get("total"):
        text += f"  {t.get('done', 0)}/{t['total']}"
    text += f"  経過 {int(t.get('elapsed_s') or 0)} 秒"
    if t.get("cancelling"):
        text += "（やめています）"
    return text


ERRORS.update({
    "no_trace_data": "そのトレースにまだ値がありません",
    "table_full": "マーカーはそれぞれ 8 本までです",
    "no_peak": "ピークが見つかりません",
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
    "unknown_file": "そのファイルはもう外しています",
})
