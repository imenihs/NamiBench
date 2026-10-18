"""信号源のタブの SG のステップ掃引（設計L §1.3）と、内部スプリアスの印（設計L §1.4）の語。

Qt（step_panel.py・tab_display.py・tab_markers.py）と Web（app_step.js・index.html）が同じ表を使う（J §5）。
LABELS・REASON_TEXTS・TEMPLATES へ足すので、Web へも同じ表で配る（GET /api/v1/texts）。命令が断った理由の語
（STEP_ERRORS）は texts.py が ERRORS へ足す。`{名前}` は差し込み（texts.tpl・GSTexts と同じ置き換え）。
Qt を import しない。
"""
from __future__ import annotations

from typing import Optional

from ..core import step_sweep as ss
from ..core.units import format_freq
from .texts_labels import LABELS
from .texts_ui import REASON_TEXTS, TEMPLATES

__all__ = ["STEP_COMBINES", "STEP_ERRORS", "STEP_REASONS", "STEP_FIELDS", "step_adjust_text", "step_seconds_text",
           "step_task_label", "step_end_text", "step_legend", "leak_tip"]

# 合成方法（並びは マックスホールド → 平均 → すべて重ねる。コードは core/step_sweep.COMBINES）
STEP_COMBINES = {ss.MAX: "マックスホールド", ss.AVERAGE: "平均", ss.ALL: "すべて重ねる"}

LABELS.update({
    # ---- ステップ掃引（信号源のタブ。SG の欄の下）
    "step_group": "ステップ掃引",
    "step_start": "SG Start",
    "step_stop": "SG Stop",
    "step_step": "Step",
    "step_per_step": "1 ステップの掃引回数",
    "step_combine": "合成方法",
    "step_trace": "結果のトレース",
    "step_run": "ステップ掃引を開始",
    "step_run_tip": "SG の周波数を 1 ステップずつ変更して現在の掃引条件で掃引し、合成した結果を選択したトレースにビューで残します。",
    # 見積もり（時間を約束しない: U-20。段の数・1回の掃引の時間・1段の回数を並べる）
    "step_estimate": "{n} ステップ × 約 {s} 秒 × {m} 回",
    "step_estimate_none": "{n} ステップ × {m} 回",
    # ---- 内部スプリアスの印（表示のタブの「描画方法」・ピークの一覧の列）
    "leak_marks": "内部スプリアスの印",
    "leak_marks_tip": "SG・TG のとき、機器の内部スプリアスが信号として表示される周波数に、グラフの上端で ▼ を付けます。",
    "peak_leak": "内部スプリアス",
})

REASON_TEXTS.update({
    # イメージ除去を選べないとき（設計L §1.3。旧アプリも SG-SWEEP 中は使わない）
    "image_reject_step": "ステップ掃引中はイメージ除去を使用できません",
    "step_running": "ステップ掃引中",
})

TEMPLATES.update({
    "step_task_progress": "  {step}/{total} ステップ",
    "step_done": "ステップ掃引が完了しました（{done}/{total} ステップ）。結果はトレース {n}（ビュー）",
    "step_cancelled": "ステップ掃引を中断しました（{done}/{total} ステップ）。中断までの結果はトレース {n}（ビュー）",
    "step_cancelled_empty": "ステップ掃引を中断しました。結果はありません",
    "step_failed": "ステップ掃引を中止しました（{error}・{done}/{total} ステップ）。中止までの結果はトレース {n}（ビュー）",
    "step_failed_empty": "ステップ掃引を中止しました（{error}）。結果はありません",
    "legend_step": "ステップ掃引・{combine} {done}/{total} ステップ",
    "leak_tip": "内部スプリアスの可能性 {freq}",
    "leak_tip_sg": "内部スプリアスの可能性 {freq}（SG + {off}）",
    "per_step_value": "{n} 回",
})

# 命令が断った理由（texts.py が ERRORS へ足す。すでにある語は変えない）
STEP_ERRORS = {
    "step_sweep_in_progress": "ステップ掃引中は実行できません",
    "step_sweep_no_sg": "この機器は SG に対応していません",
    "step_sweep_needs_sg": "SG モードで使用します",
    "too_many_steps": f"ステップ数の上限は {ss.STEPS_MAX} です。Step を広げてください",
    "too_many_steps_all": f"すべて重ねる場合のステップ数の上限は {ss.ALL_STEPS_MAX} です。Step を広げてください",
    "step_sweep_trace": "ステップ掃引の結果を格納するトレースです。ステップ掃引の終了後に変更できます",
}

# 設定を直した理由（欄のそばに一度出す: M-02）
STEP_REASONS = {
    "freq_unit": "SG の周波数の単位",
    "sg_range": "SG の範囲",
    "step_min": "SG の周波数の単位",
    "per_step_min": "回数の下限",
    "per_step_max": "回数の上限",
}

# 直した欄 → 画面の欄の名前（Qt の step_panel・Web の notes の名前と同じ）
STEP_FIELDS = {"start_hz": "step_start", "stop_hz": "step_stop", "step_hz": "step_step", "per_step": "step_per_step"}


def _tpl(key: str, **kw) -> str:
    text = TEMPLATES[key]
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def step_adjust_text(adj: dict) -> str:
    """直した1件を「◯◯ に合わせました（理由）」にする（SG の周波数は周波数オフセットを足さない: 機器が出す周波数）。"""
    v = adj.get("adopted")
    value = _tpl("per_step_value", n=v) if adj.get("field") == "per_step" else format_freq(int(v))
    reason = STEP_REASONS.get(adj.get("reason", ""), "")
    return _tpl("adjusted_with_reason", value=value, reason=reason) if reason else _tpl("adjusted", value=value)


def step_seconds_text(s: float) -> str:
    """見積もりの1回の掃引の秒（1 秒未満は小数2桁、それ以上は1桁: 0.08 秒の掃引を「0.1」に丸めない）。"""
    return f"{s:.2f}" if s < 1 else f"{s:.1f}"


def step_task_label(t: dict) -> str:
    """進み具合の1行（いまの段/全体の段・経過時間。時間は約束しない: U-20）。"""
    total = int(t.get("total") or 0)
    text = LABELS["step_group"]
    if total:
        text += _tpl("step_task_progress", step=min(int(t.get("done") or 0) + 1, total), total=total)
    text += _tpl("task_elapsed", s=int(t.get("elapsed_s") or 0))
    if t.get("cancelling"):
        text += TEMPLATES["task_cancelling"]
    return text


def step_end_text(code: str, d: dict, error_text: str = "") -> str:
    """終わりの知らせ（U-19: 終わり方・済んだ段の数・残した結果のトレース）。d は終わりの知らせの detail。"""
    res = d.get("result") if code == "task_done" else d
    res = res or {}
    kw = {"done": res.get("steps_done", 0), "total": res.get("steps", 0), "n": res.get("trace", "")}
    if code == "task_done":
        return _tpl("step_done", **kw)
    kept = bool(res.get("kept"))
    if code == "task_cancelled":
        return _tpl("step_cancelled", **kw) if kept else TEMPLATES["step_cancelled_empty"]
    return _tpl("step_failed", error=error_text, **kw) if kept else _tpl("step_failed_empty", error=error_text)


def step_legend(info: Optional[dict]) -> str:
    """凡例に添える「ステップ掃引・マックスホールド 12/40 ステップ」。"""
    if not info:
        return ""
    return _tpl("legend_step", combine=STEP_COMBINES.get(info.get("combine"), info.get("combine") or ""),
                done=info.get("steps_done", 0), total=info.get("steps", 0))


def leak_tip(leak: dict, freq_offset_hz: int = 0) -> str:
    """内部スプリアス1つの説明「内部スプリアスの可能性 1480MHz（SG + 1480MHz）」。周波数は横軸と同じ換算後の周波数（設計O）。"""
    freq = format_freq(int(leak["freq_hz"]) + int(freq_offset_hz or 0))
    if leak.get("kind") == "sg_offset" and leak.get("offset_hz") is not None:
        return _tpl("leak_tip_sg", freq=freq, off=format_freq(int(leak["offset_hz"])))
    return _tpl("leak_tip", freq=freq)
