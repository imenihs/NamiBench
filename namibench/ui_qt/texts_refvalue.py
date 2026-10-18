"""基準の値（設計E §6.7: 表示 = 基準との比 + 基準の値）の語と欄の決まり。

texts.py が `from .texts_refvalue import *` で取り込み、Web へも同じ表で配る（GET /api/v1/texts）。LABELS・REASON_TEXTS・
TEMPLATES へ足す語と、ERRORS・NOTICES へ足す語（texts.py が足す）を持つ。`{名前}` は差し込み。Qt を import しない。
"""
from __future__ import annotations

from ..core.normalize import VALUE_DB_LIMIT
from ..core.units import minus_sign
from .texts_labels import LABELS
from .texts_ui import REASON_TEXTS, TEMPLATES

# 信号源のタブの「基準の値」の欄（打てるドロップダウン: G §3.6 の dB の欄と同じ形）の一覧・範囲・桁（画面の語ではない
# 決まり。Qt と Web が同じ表を使う）。一覧はアッテネータ・アンプのよくある値
REF_VALUE_SPEC = {"choices": [40, 30, 20, 10, 0, -10, -20, -30, -40], "signed": True,
                  "lo": -VALUE_DB_LIMIT, "hi": VALUE_DB_LIMIT, "decimals": 1}

LABELS.update({
    "ref_value": "基準の値",
    # 符号の向きは押す前に決める値なので欄のツールチップに出す（U-06）
    "ref_value_tip": "基準との比に加算する dB（測定時のみ 40dB のアッテネータを挿入する場合は +40）",
    "ref_value_dots": "基準の値…",
    "ref_value_title": "基準の値",
    "ref_value_new": "新しい値（dB）",
})

REASON_TEXTS.update({
    "ref_value_s11": "S11 の基準には使用できません（全反射が 0 dB）",
})

TEMPLATES.update({
    # 状態バー・信号源のタブ・上段の基準の文に足す基準の値（0 dB のときは足さない）
    "norm_ref_value": "基準: {name}（{value}）",
    "ref_selected_value": "選択中の基準: {name}（{when}・{value}）",
    "ref_quick_name_value": "基準: {name}（{value}）",
    "ref_quick_now_value": "基準: {name}（{time}・{value}）",
    "ref_value_changed": "基準「{name}」の値を {value} に変更しました",
})

REF_VALUE_ERRORS = {
    "reference_value_s11": "S11 の基準には基準の値を使用できません（全反射が 0 dB）",
    "reference_value_range": "{lo}〜{hi} dB の値を入力してください",
}

REF_VALUE_NOTICES = {
    "reference_value_changed": "基準の値を変更しました",
}


def ref_value_text(v) -> str:
    """基準の値の文（「+40.0 dB」「−12.5 dB」）。0 dB と値が無いときは空（何も足さない: U-15）。"""
    if not v:
        return ""
    return minus_sign(f"{float(v):+.1f}") + " dB"


def value_range_text(lo: float, hi: float) -> str:
    """範囲の外の基準の値を送ったときの文（欄の範囲の知らせと同じ数の書き方）。"""
    return (REF_VALUE_ERRORS["reference_value_range"].replace("{lo}", minus_sign(f"{lo:g}"))
            .replace("{hi}", minus_sign(f"{hi:+g}")))
