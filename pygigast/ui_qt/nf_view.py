"""NF の欄（信号源のタブ: J §4.5・設計M）と、NF 測定のつなぎ方の窓に出すもの。

Qt の tab_source.py・nf_dialog.py と Web の app_nf.js が同じものを使う（J §5: 同じ構成・同じ語）。Web へは
screen_model.ui_view が `ui.nf` として送る。Qt を import しない。
"""
from __future__ import annotations

from typing import Optional

from .texts_nf import NF_TEXTS as N
from .texts_nf import NF_WIRING


def enr_text(db: Optional[float]) -> str:
    """ENR の欄の文字（0.01dB まで）。"""
    return "" if db is None else f"{float(db):.2f} dB"


def amp_text(amp_raw: Optional[float]) -> str:
    """増幅度を倍率で出す（内部の値 amp_raw を出さない: U-22。校正の画面と同じ cal_view.amp_text）。"""
    if amp_raw is None:
        return N["value_none"]
    from .cal_view import amp_text as cal_amp_text

    return cal_amp_text(amp_raw)


def guide_view() -> dict:
    """必要な外部機器とつなぎ方（NF モードに初めて入ったときの窓。信号源のタブのボタンからも開く: 設計M）。"""
    return {"title": N["guide_title"], "need_label": N["need"], "need": N["need_text"],
            "wiring_label": N["wiring"], "wiring": N["wiring_text"], "diagram": list(NF_WIRING), "close": N["close"]}


def nf_view(st: dict) -> dict:
    """NF の欄の中身。

    - reason: 欄全体を使えない理由（繋いでいる機器が NF を測れない: Q §2.1。U-08 で消さずに無効）
    - enr_text・enr_value: ENR の欄（いま使う ENR。表を使っている間は空）。enr_tip は校正データの値を使っているとき
    - enr_reason: ENR の欄を使えない理由（表を使っている間）。enr_warn: ENR が無く NF を出せないとき（欄に付ける: U-25）
    - table_text: 表の名前・範囲・行の数（無ければ「読み込んでいません」: U-15）。table_warn: いまの範囲の一部が表の外
    - clear_reason: 「表を外す」を使えない理由
    - amp_text: 増幅度（倍率。校正していなければ「なし」）
    """
    from .screen_model import mode_reasons

    nf = st.get("nf") or {}
    reason = mode_reasons(st).get("nf")
    enr = nf.get("enr") or None
    table = nf.get("table") or None
    using_table = bool(enr and enr.get("kind") == "table")
    value = None if using_table or not enr else enr.get("db")
    if table:
        table_text = N["table_line"].replace("{name}", str(table.get("name") or "")).replace(
            "{start}", str(table.get("start_text") or "")).replace("{stop}", str(table.get("stop_text") or "")).replace(
            "{rows}", str(table.get("rows") or 0))
    else:
        table_text = N["table_none"]
    return {
        "reason": reason,
        "enr_text": enr_text(value),
        "enr_value": value,
        "enr_tip": N["enr_from_calibration"] if enr and enr.get("kind") == "calibration" else "",
        "enr_reason": reason or (N["enr_table_in_use"] if using_table else None),
        "enr_warn": N["enr_needed"] if not enr else "",
        "table_text": table_text,
        "table_loaded": bool(table),
        "table_warn": N["table_outside"] if table and nf.get("table_covers") is False else "",
        "load_reason": reason,
        "clear_reason": reason or (None if table else N["no_table"]),
        "amp_text": amp_text(nf.get("amp_raw")),
        "guide": guide_view(),
    }


def average_type_reason(st: dict) -> Optional[str]:
    """トレースの平均の種類を選べない理由（NF は HOT と COLD をパワー平均: 設計M §1）。NF でなければ None。"""
    return N["average_type_nf"] if (st.get("sweep") or {}).get("mode") == "nf" else None


