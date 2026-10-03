"""校正の画面の語（設計K §4.1〜§4.5）。Qt の cal_dialog.py と Web の app_cal.js が同じ表を使う（J §5）。

窓の題と「校正するもの」の名前は、開いたメニューの項目の名前（MENU_ITEMS の cal_*。「…」を除く: U-24）。
画面に置くのは名前と、手順の指示（「SG を … にしてください」など、押す前の行動を決める文）だけ（U-06・U-26）。
`{名前}` は差し込み（texts.tpl・GSTexts と同じ置き換え）。Qt を import しない。
"""
from __future__ import annotations

# 何を校正するか → 開くメニューの項目のコード（窓の題と選択肢の名前はその項目の名前）
CAL_MENU_OF_KIND = {"freq": "cal_freq", "rbw": "cal_rbw", "nf": "cal_nf", "legacy": "cal_legacy"}

CAL_TEXTS = {
    # ---- 手順（K §4.1）
    "step_prepare": "準備",
    "step_measure": "測る",
    "step_load": "読み込む",
    "step_check": "確かめる",
    "step_save": "保存",
    "back": "戻る",
    "next": "次へ",
    "close": "閉じる",
    # ---- 準備
    "device": "機器",
    "device_name": "{name}（{serial}）",
    "current_cal": "いまの校正データ",
    "cal_none": "なし（補正なしの値）",
    "cal_legacy": "旧校正ファイル {name}・{at}",
    "cal_work": "校正作業・{at}",
    "kind_group": "校正するもの",
    "need": "要る機器",
    "wiring": "つなぎ方",
    "need_freq": "外部の SG・ケーブル",
    "need_rbw": "ケーブル 1 本",
    "need_nf": "ノイズ源・6dB のアッテネータ・80dB 程度のアンプ・イメージ除去の BPF",
    "need_legacy": "旧校正ファイル（gigast.txt / ba205.txt）",
    "wiring_freq": "外部の SG の出力を SP 入力へつなぐ",
    "wiring_rbw": "TG 出力と SP 入力を直結する",
    "wiring_nf": "ノイズ源からアンプと BPF を通して SP 入力へつなぐ",
    # ---- 周波数特性（K §4.2）
    "sg_level": "SG のレベル",
    "cable_loss": "ケーブルの損失",
    "set_sg": "SG を {freq} にしてください",
    "measure": "測る",
    "skip": "飛ばす",
    "measuring": "測定中…",
    "col_freq": "周波数 (MHz)",
    "col_before": "前 (dB)",
    "col_after": "新 (dB)",
    "col_diff": "差 (dB)",
    "col_state": "",
    "st_measured": "測った",
    "st_skipped": "飛ばした",
    "st_pending": "確かめ待ち",
    "big_mark": "⚠",
    "big_tip": "前の値との差が {limit} dB を超えています",
    "confirm_title": "確かめる",
    "confirm_big": "いまの補正との差が {diff} dB あります。この値を入れますか？",
    "do_put": "入れる",
    # ---- RBW（K §4.3）
    "rbw_extra": "100・1500・3000MHz でも測る",
    "start": "始める",
    "col_rbw_peak": "{rbw} (dBm)",
    "col_rbw_offset": "{rbw} の補正 (dB)",
    # ---- NF の増幅度（K §4.4）
    "nf_freq": "周波数",
    "nf_with_att": "6dB のアッテネータを入れてください",
    "nf_without_att": "アッテネータを外してください",
    "nf_y": "HOT−COLD {db} dB",
    "nf_diff": "差 {db} dB",
    # ---- 旧校正ファイル（K §4.5・設計D §5.6）
    "choose_file": "ファイルを選ぶ…",
    "file": "元のファイル",
    "file_none": "選んでいません",
    "file_title": "旧校正ファイル（gigast.txt / ba205.txt）",
    "col_measured": "校正で測った値",
    "measured_warn": "校正で測った値と {diff} 違います",
    # ---- 確かめる
    "col_item": "項目",
    "value_none": "なし",
    "item_level": "全体のレベル (dB)",
    "item_rbw": "RBW {rbw} (dB)",
    "item_nf_amp": "NF の増幅度（倍率）",
    "item_nf_enr": "ENR (dB)",
    "graph_x": "周波数 (MHz)",
    "graph_y": "補正 (dB)",
    "graph_before": "前",
    "graph_after": "新",
    "save": "保存",
    "saved": "保存して、これから使う校正データにしました（{file}）",
    "again": "続けて校正する",
    # ---- 閉じる・切り替える（U-18）
    "lost_points": "測った {n} 点は保存されません。閉じますか？",
    "lost_value": "測った値は保存されません。閉じますか？",
    "lost_legacy": "読み込んだ値は保存されません。閉じますか？",
    "switch_points": "測った {n} 点は保存されません。切り替えますか？",
    "switch_value": "測った値は保存されません。切り替えますか？",
    "switch_legacy": "読み込んだ値は保存されません。切り替えますか？",
    "lost_main_points": "・校正: 測った {n} 点は保存されません",
    "lost_main_value": "・校正: 測った値は保存されません",
    "lost_main_legacy": "・校正: 読み込んだ値は保存されません",
    "do_close_discard": "保存せずに閉じる",
    "do_close_save_points": "測った点だけ保存して閉じる",
    "do_close_save": "保存して閉じる",
    "do_switch": "切り替える",
    # ---- 使えない理由
    "need_file": "ファイルを選んでください",
    "need_points": "測った点がありません",
    "need_rbw": "RBW をまだ測っていません",
    "need_nf": "アッテネータあり・なしの両方を測ってください",
    "need_answer": "確かめ待ちの点に答えてください",
    "device_other": "測った値は別の機器（{serial}）のものです",
}

# 校正の命令が断った理由（ERRORS へ足す）
CAL_ERRORS = {
    "calibration_in_progress": "校正で測っている間は使えません",
    "calibration_not_open": "校正の画面が開いていません",
    "calibration_unsaved": "保存していない校正の値があります",
    "calibration_wrong_kind": "いまの校正の手順では使えません",
    "calibration_confirm_pending": "確かめ待ちの点に答えてください",
    "calibration_no_pending": "確かめ待ちの点がありません",
    "calibration_nothing_to_save": "保存する値がありません",
    "calibration_not_for_device": "この機器にはこの校正を使えません",
    "calibration_device_changed": "測った値は別の機器のものです",
    "cal_no_signal": "信号が見つかりません。SG の周波数と接続を確かめてください",
    "cal_no_signal_direct": "信号が見つかりません。TG 出力と SP 入力の直結を確かめてください",
    "cal_nf_no_difference": "ノイズ源の ON・OFF で差が出ていません。ノイズ源とつなぎ方を確かめてください",
    "cal_nf_att_no_difference": "アッテネータの有無で差が出ていません。入れる・外すを確かめてください",
    "disconnected": "機器が切れました",
    "invalid_sweep": "機器が受け付けない条件のため送りませんでした",
}

# 長い処理（TASK_NAMES など texts.py の表へ足す）
CAL_TASK_NAMES = {
    "cal_point": "校正の点を測っています",
    "cal_rbw": "RBW の校正を測っています",
    "cal_nf": "NF の増幅度を測っています",
}
CAL_TASK_DONE = {
    "cal_point": "校正の点を測りました",
    "cal_rbw": "RBW の補正を計算しました",
    "cal_nf": "NF の増幅度の測定が終わりました",
}
CAL_TASK_FAILED = {
    "cal_point": "校正の点を測れませんでした",
    "cal_rbw": "RBW の校正を測れませんでした",
    "cal_nf": "NF の増幅度を測れませんでした",
}
CAL_TASK_CANCELLED = {
    "cal_point": "測るのをやめました（この点は前の値のまま）",
    "cal_rbw": "RBW の校正をやめました（RBW の補正は前の値のまま）",
    "cal_nf": "測るのをやめました（増幅度は前の値のまま）",
}

# つなぎ方の図の箱（左から右へ矢印でつなぐ）。旧校正ファイルは機器をつながない
CAL_WIRING = {
    "freq": ("外部の SG", "ケーブル", "SP 入力"),
    "rbw": ("TG 出力", "ケーブル", "SP 入力"),
    "nf": ("ノイズ源", "6dB のアッテネータ", "アンプ", "BPF", "SP 入力"),
}
