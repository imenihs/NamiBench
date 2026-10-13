"""信号源の画面の中身（設計L）: SG のステップ掃引の欄（§1.3）と内部の漏れの印（§1.4）。Qt と Web が同じものを使う（J §5）。

- ステップ掃引の欄: 設定の値・重ね方とトレースの選択肢・見積もり（「40 段 × 約 0.6 秒 × 4 回」: 時間を約束しない
  U-20）・始めるボタンの使えない理由・欄の使えない理由（している間は変えられない）。値はコアの状態 step_sweep
- 内部の漏れの印: コアの状態 internal_leaks（SG・TG のときだけ・表示範囲の中）を、グラフの上端の印と説明にする。
  表示の設定 view.leak_marks が OFF なら出さない。マウスの位置の読み値にも同じ説明を添える（screen_model.mouse_reading）
- Qt を import しない（Web の口が画面なしで使う）
"""
from __future__ import annotations

from typing import Optional

from ..core import step_sweep as ss
from . import texts
from .texts import STEP_COMBINES, leak_tip, step_seconds_text

L = texts.LABELS


def step_view(st: Optional[dict]) -> dict:
    """ステップ掃引の欄の中身。

    形: {"start_hz", "stop_hz", "step_hz", "per_step", "combine", "trace", "running", "estimate",
         "combines": [{"code", "text"}], "traces": [{"value", "text"}], "reason": 始めるボタンの使えない理由 or None,
         "field_reason": 欄の使えない理由 or None, "per_step_min", "per_step_max"}
    """
    s = (st or {}).get("step_sweep") or {}
    cfg = {**ss.DEFAULTS, **{k: s[k] for k in ss.FIELDS if k in s}}
    n = s.get("steps") if s.get("steps") is not None else ss.count(cfg)
    sec = s.get("sweep_s")
    if sec is None:
        estimate = texts.w("step_estimate_none", n=n, m=cfg["per_step"])
    else:
        estimate = texts.w("step_estimate", n=n, s=step_seconds_text(float(sec)), m=cfg["per_step"])
    code = s.get("reason")
    reason = None if not code else texts.ERRORS.get(code, code)
    running = bool(s.get("running"))
    return {
        **cfg,
        "running": running,
        "estimate": estimate,
        "combines": [{"code": c, "text": STEP_COMBINES[c]} for c in ss.COMBINES],
        "traces": [{"value": i, "text": texts.w("trace_n", n=i)} for i in (1, 2, 3, 4)],
        "reason": reason,
        "field_reason": texts.REASON_TEXTS["step_running"] if running else None,
        "per_step_min": ss.PER_STEP_MIN,
        "per_step_max": ss.PER_STEP_MAX,
    }


def adjusted_notes(result: dict) -> list[dict]:
    """命令 set_step_sweep の戻り値の adjusted を、欄ごとの一度きりの知らせにする [{"field", "text"}]。
    field は画面の欄の名前（step_start・step_stop・step_step・step_per_step）。"""
    out = []
    for a in (result or {}).get("adjusted") or []:
        f = texts.STEP_FIELDS.get(a.get("field"))
        if f:
            out.append({"field": f, "text": texts.step_adjust_text(a)})
    return out


def leaks_on(st: Optional[dict]) -> bool:
    """内部の漏れの印をグラフに出すか（表示の設定 view.leak_marks。無ければ出す）。"""
    return ((st or {}).get("view") or {}).get("leak_marks", True) is not False


def leak_marks_view(st: Optional[dict]) -> list[dict]:
    """グラフの上端の印 [{freq_hz, tip, near_hz}]（周波数の順）。印を出さない設定・SG・TG でないときは空。

    near_hz は、測った山・マウスの位置をその漏れとみなす幅（コアの tolerance_hz）。
    """
    if not leaks_on(st):
        return []
    lk = (st or {}).get("internal_leaks") or {}
    off = texts.freq_offset_of(st)
    tol = int(lk.get("tolerance_hz") or 0)
    return [{"freq_hz": int(x["freq_hz"]), "tip": leak_tip(x, off), "near_hz": tol} for x in lk.get("leaks") or []]


def peak_leak(leak: Optional[dict], freq_offset_hz: int = 0) -> tuple[str, str]:
    """ピークの一覧の行の印（設計P §9.6）: (列の文字, ツールチップ)。漏れでなければ ("", "")。"""
    if not leak:
        return "", ""
    return L["peak_leak"], leak_tip(leak, freq_offset_hz)


def leak_near(marks: list[dict], x_hz: float, px_hz: float = 0.0) -> Optional[dict]:
    """マウスの位置 x_hz の近くの印（印の幅か、画素で 5 個分の広い方の内で一番近いもの）。無ければ None。"""
    best = None
    for m in marks or []:
        d = abs(float(x_hz) - float(m["freq_hz"]))
        if d <= max(float(m.get("near_hz") or 0), 5.0 * max(0.0, float(px_hz or 0.0))) \
                and (best is None or d < best[0]):
            best = (d, m)
    return best[1] if best else None
