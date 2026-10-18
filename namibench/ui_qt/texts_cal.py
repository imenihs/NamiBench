"""校正の画面の語（設計K §4.1〜§4.5）。Qt の cal_dialog.py と Web の app_cal.js が同じ表を使う（J §5）。

窓の題と「校正するもの」の名前は、開いたメニューの項目の名前（MENU_ITEMS の cal_*。「…」を除く: U-24）。
画面に置くのは名前と、手順の指示（「SG を … にしてください」など、押す前の行動を決める文）だけ（U-06・U-26）。
`{名前}` は差し込み（texts.tpl・GSTexts と同じ置き換え）。Qt を import しない。
"""
from __future__ import annotations

# 何を校正するか → 開くメニューの項目のコード（窓の題と選択肢の名前はその項目の名前）
CAL_MENU_OF_KIND = {"freq": "cal_freq", "rbw": "cal_rbw", "nf": "cal_nf", "pluto_gain": "cal_pluto_gain",
                    "legacy": "cal_legacy"}

CAL_TEXTS = {
    # ---- 手順（K §4.1）
    "step_prepare": "準備",
    "step_measure": "測定",
    "step_load": "読み込み",
    "step_check": "確認",
    "step_save": "保存",
    "back": "戻る",
    "next": "次へ",
    "close": "閉じる",
    # ---- 準備
    "device": "機器",
    "device_name": "{name}（{serial}）",
    "current_cal": "現在の校正データ",
    "cal_none": "なし（補正なしの値）",
    "cal_legacy": "旧校正ファイル {name}・{at}",
    "cal_work": "校正作業・{at}",
    "kind_group": "校正するもの",
    "need": "必要な機器",
    "wiring": "接続",
    "need_freq": "外部 SG・ケーブル",
    "need_rbw": "ケーブル 1 本",
    "need_nf": "ノイズソース・6dB のアッテネータ・80dB 程度のアンプ・イメージ除去の BPF",
    "need_legacy": "旧校正ファイル（gigast.txt / ba205.txt）",
    "need_pluto_gain": "30〜40dB のアッテネータ・ケーブル 2 本",
    "wiring_freq": "外部 SG の出力を SP 入力に接続",
    "wiring_rbw": "TG 出力と SP 入力を直結",
    "wiring_nf": "ノイズソースからアンプと BPF を通して SP 入力に接続",
    "wiring_pluto_gain": "PlutoSDR の TX からアッテネータを通して RX に接続",
    # ---- 周波数特性（K §4.2）
    "sg_level": "SG のレベル",
    "cable_loss": "ケーブルの損失",
    "set_sg": "SG を {freq} にしてください",
    "measure": "測定",
    "skip": "スキップ",
    "measuring": "測定中…",
    "col_freq": "周波数 (MHz)",
    "col_band": "帯",
    "col_sg_freq": "SG の周波数 (MHz)",
    "band_lower": "下の帯",
    "band_upper": "上の帯",
    "col_before": "変更前 (dB)",
    "col_after": "変更後 (dB)",
    "col_diff": "差 (dB)",
    "col_state": "",
    "st_measured": "測定済み",
    "st_skipped": "スキップ",
    "st_pending": "確認待ち",
    "big_mark": "⚠",
    "big_tip": "変更前の値との差が {limit} dB を超えます",
    "confirm_title": "確認",
    "confirm_big": "現在の補正との差が {diff} dB あります。この値を適用しますか？",
    "do_put": "適用",
    # ---- RBW（K §4.3）
    "rbw_extra": "100・1500・3000MHz でも測定",
    "start": "開始",
    "col_rbw_peak": "{rbw} (dBm)",
    "col_rbw_offset": "{rbw} の補正 (dB)",
    # ---- NF の利得（K §4.4）
    "nf_freq": "周波数",
    "nf_with_att": "6dB のアッテネータを入れてください",
    "nf_without_att": "アッテネータを外してください",
    "nf_y": "HOT−COLD {db} dB",
    "nf_diff": "差 {db} dB",
    # ---- PlutoSDR の利得の補正（設計Q §7.6）: 全自動
    "ext_atten": "アッテネータ",
    "col_gain_range": "利得 (dB)",
    "col_tx_atten": "送信の減衰 (dB)",
    "col_corr": "補正 (dB)",
    "range": "{lo}〜{hi}",
    "item_pluto_gain_max": "補正の最大 (dB)",
    # ---- 旧校正ファイル（K §4.5・設計D §5.6）
    "choose_file": "ファイルを選択…",
    "file": "元のファイル",
    "file_none": "未選択",
    "file_title": "旧校正ファイル（gigast.txt / ba205.txt）",
    "col_measured": "校正時の測定値",
    "measured_warn": "校正時の測定値と {diff} 異なります",
    # ---- 確認
    "col_item": "項目",
    "value_none": "なし",
    "item_level": "全体のレベル (dB)",
    "item_rbw": "RBW {rbw} (dB)",
    "item_nf_amp": "NF の利得（倍率）",
    "item_nf_enr": "ENR (dB)",
    "graph_x": "周波数 (MHz)",
    "graph_y": "補正 (dB)",
    "graph_before": "変更前",
    "graph_after": "変更後",
    "save": "保存",
    "saved": "保存し、使用する校正データに設定しました（{file}）",
    "again": "続けて校正",
    # ---- 閉じる・切り替える（U-18）
    "lost_points": "測定した {n} 点は保存されません。閉じますか？",
    "lost_value": "測定値は保存されません。閉じますか？",
    "lost_legacy": "読み込んだ値は保存されません。閉じますか？",
    "lost_freqs": "測定した {n} 周波数は保存されません。閉じますか？",
    "switch_points": "測定した {n} 点は保存されません。切り替えますか？",
    "switch_value": "測定値は保存されません。切り替えますか？",
    "switch_legacy": "読み込んだ値は保存されません。切り替えますか？",
    "switch_freqs": "測定した {n} 周波数は保存されません。切り替えますか？",
    "lost_main_points": "・校正: 測定した {n} 点は保存されません",
    "lost_main_value": "・校正: 測定値は保存されません",
    "lost_main_legacy": "・校正: 読み込んだ値は保存されません",
    "lost_main_freqs": "・校正: 測定した {n} 周波数は保存されません",
    "do_close_discard": "保存せずに閉じる",
    "do_close_save_points": "測定した点のみ保存して閉じる",
    "do_close_save_freqs": "測定した周波数のみ保存して閉じる",
    "do_close_save": "保存して閉じる",
    "do_switch": "切り替え",
    # ---- 使えない理由
    "need_file": "ファイルを選択してください",
    "need_points": "測定した点がありません",
    "need_rbw": "RBW が未測定です",
    "need_nf": "アッテネータあり・なしの両方を測定してください",
    "need_pluto": "利得の補正が未測定です",
    "need_answer": "確認待ちの点に回答してください",
    "device_other": "測定値は別の機器（{serial}）のものです",
}

# 校正の操作を断った理由（ERRORS へ足す）
CAL_ERRORS = {
    "calibration_in_progress": "校正の測定中は使用できません",
    "calibration_not_open": "校正の画面が未表示です",
    "calibration_unsaved": "未保存の校正の値があります",
    "calibration_wrong_kind": "現在の校正の手順では使用できません",
    "calibration_confirm_pending": "確認待ちの点に回答してください",
    "calibration_no_pending": "確認待ちの点がありません",
    "calibration_nothing_to_save": "保存する値がありません",
    "calibration_not_for_device": "この機器にはこの校正を使用できません",
    "calibration_device_changed": "測定値は別の機器のものです",
    "cal_no_signal": "信号が見つかりません。SG の周波数と接続を確認してください",
    "cal_no_signal_direct": "信号が見つかりません。TG 出力と SP 入力の直結を確認してください",
    "cal_nf_no_difference": "ノイズソースの ON・OFF で差がありません。ノイズソースと接続を確認してください",
    "cal_nf_att_no_difference": "アッテネータの有無で差がありません。アッテネータの取り付け・取り外しを確認してください",
    "cal_pluto_no_signal": "送信のトーンを検出できません。TX・アッテネータ・RX の接続を確認してください",
    "cal_pluto_overload": "送信を最小にしても受信が過大入力になります。アッテネータと、外部からの信号の混入を確認してください",
    "disconnected": "機器との接続が切れました",
    "invalid_sweep": "機器が受け付けない条件のため、送信しませんでした",
}

# 長い処理（TASK_NAMES など texts.py の表へ足す）
CAL_TASK_NAMES = {
    "cal_point": "校正点を測定中",
    "cal_rbw": "RBW の校正を測定中",
    "cal_nf": "NF の利得を測定中",
    "cal_pluto_gain": "PlutoSDR の利得の補正を測定中",
}
CAL_TASK_DONE = {
    "cal_point": "校正点を測定しました",
    "cal_rbw": "RBW の補正を計算しました",
    "cal_nf": "NF の利得の測定が完了しました",
    "cal_pluto_gain": "PlutoSDR の利得の補正を測定しました",
}
CAL_TASK_FAILED = {
    "cal_point": "校正点を測定できませんでした",
    "cal_rbw": "RBW の校正を測定できませんでした",
    "cal_nf": "NF の利得を測定できませんでした",
    "cal_pluto_gain": "PlutoSDR の利得の補正を測定できませんでした（測定した周波数は保存できます）",
}
CAL_TASK_CANCELLED = {
    "cal_point": "測定を中断しました（この点は変更前の値のまま）",
    "cal_rbw": "RBW の校正を中断しました（RBW の補正は変更前の値のまま）",
    "cal_nf": "測定を中断しました（利得は変更前の値のまま）",
    "cal_pluto_gain": "測定を中断しました（測定した周波数は保存できます）",
}

# つなぎ方の図の箱（左から右へ矢印でつなぐ）。旧校正ファイルは機器をつながない
CAL_WIRING = {
    "freq": ("外部 SG", "ケーブル", "SP 入力"),
    "rbw": ("TG 出力", "ケーブル", "SP 入力"),
    "nf": ("ノイズソース", "6dB のアッテネータ", "アンプ", "BPF", "SP 入力"),
    "pluto_gain": ("TX", "アッテネータ", "RX"),
}
