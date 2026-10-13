"""NF 測定の語（設計M・J §4.5 の信号源のタブの NF）。Qt の tab_source.py・nf_dialog.py と Web の app_nf.js が同じ表を使う。

texts.py が `from .texts_nf import *` で取り込み、/api/v1/texts で Web へも同じ表で配る（J §5）。
画面に置くのは名前と、手順（要る機器・つなぎ方）と、押す前の行動を変える知らせだけ（U-06・U-24・U-26）。
`{名前}` は差し込み（texts.tpl・GSTexts と同じ置き換え）。Qt を import しない。
"""
from __future__ import annotations

NF_TEXTS = {
    "group": "NF",
    "enr": "ENR",
    "enr_table": "ENR の表",
    "amp": "NF の増幅度（倍率）",
    "value_none": "なし",
    "load_table": "CSV を読み込む…",
    "clear_table": "表を外す",
    "table_none": "読み込んでいません",
    "table_line": "{name}・{start}〜{stop}・{rows} 行",
    # ENR が無く NF を出せないとき（ENR の欄に付ける: U-25）
    "enr_needed": "ENR を入れてください",
    # 入れていないので校正データの ENR を使っているとき（欄のツールチップ）
    "enr_from_calibration": "校正データの値",
    # 表を使っている間は値の欄を使えない理由（U-08）
    "enr_table_in_use": "ENR の表を使っています",
    "no_table": "ENR の表を読み込んでいません",
    # いまの Start〜Stop の一部が表の外（表の行に付ける: U-25）
    "table_outside": "いまの範囲の一部が表の外です",
    "table_file_title": "ENR の表（CSV）",
    # 表が読めないときの行（enr_table_unreadable の detail: 何行目の何が読めないか）
    "table_error_line": "{line} 行目: {why}",
    "table_file_filter": "CSV (*.csv *.txt);;すべてのファイル (*)",
    # 必要な外部機器とつなぎ方（NF モードに初めて入ったときに1回だけ出す。信号源のタブのボタンからも開く: 設計M）
    "guide_button": "NF 測定のつなぎ方…",
    "guide_title": "NF 測定のつなぎ方",
    "need": "要る機器",
    "wiring": "つなぎ方",
    "need_text": "ノイズ源・80dB 程度のアンプ・イメージ除去の BPF",
    "wiring_text": "ノイズ源から被測定物・アンプ・BPF を通して SP 入力へつなぐ",
    "close": "閉じる",
    # トレースの平均の種類を NF では選べない理由（U-08。HOT と COLD をパワー平均してから NF を出す: 設計M §1）
    "average_type_nf": "NF は HOT と COLD をパワー平均します",
    # 単位の欄（J §4.10）: NF を出している間の単位と、電力の単位を選べない理由
    "level_unit_nf_item": "dB（NF）",
    "level_unit_nf_item_tip": "NF モードの間だけ、この単位で出します",
    "level_unit_nf_reason": "NF を出している間は電力の単位を使いません。ほかのモードに戻すと {unit} で出します",
}

# つなぎ方の図の箱（左から右へ矢印でつなぐ。校正の画面の図と同じ描き方: K §4.1）
NF_WIRING = ("ノイズ源", "被測定物", "アンプ", "BPF", "SP 入力")

# NF の命令が断った理由（ERRORS へ足す）
NF_ERRORS = {
    "enr_table_in_use": "ENR の表を使っています。表を外してから入れてください",
    "enr_table_unreadable": "ENR の表が読めません",
    "enr_no_table": "ENR の表を読み込んでいません",
}

# ENR の表が読めない理由（enr_table_unreadable の detail.reason）。行の番号と一緒に出す
ENR_TABLE_REASONS = {
    "columns": "周波数と ENR の2列が要ります",
    "frequency": "周波数が読めません",
    "enr": "ENR が読めません",
    "enr_range": "ENR が {min}〜{max} dB の外です",
    "duplicate": "同じ周波数が2回あります",
    "empty": "周波数と ENR の行がありません",
    "too_large": "ファイルが大きすぎます",
}

# マーカーの読み値に添える注意（G §3.3）。点の印の名前（core/level/nf.py の FLAG_*）
NF_MARKER_NOTES = {
    "nf_small_difference": "HOT と COLD の差が 0.5dB 未満",
    "enr_outside_table": "ENR の表の外（端の ENR）",
}
