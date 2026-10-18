"""画面の部品の語の表（メニュー・モード・まだ作っていない機能の理由・RTL-SDR・聞く・rtl_tcp・文の型）。

texts.py が `from .texts_ui import *` で取り込み、Web へは texts.py の表として一緒に配る
（GET /api/v1/texts）。Qt と Web はどちらもここの語で部品を作る（J §5: 同じ語）。
Qt を import しない。
"""
from __future__ import annotations

# ---------------------------------------------------------------- モード

# モード（コード・ボタンの文字・名前・使えない理由）。並びが Ctrl+1〜5（Web は Alt+1〜5）の順（J §4.6）
MODES = (
    ("sa", "SA", "スペクトラムアナライザ", None),
    ("tg", "TG", "伝送特性", None),
    ("s11", "S11", "反射", None),
    ("sg", "SG", "信号源", None),
    ("nf", "NF", "NF 測定", None),
)
MODE_NAMES = {code: name for code, _l, name, _w in MODES}
MODE_LABELS = {code: label for code, label, _n, _w in MODES}
# 機器が測れないモードの理由（機種のプロファイルの measurements に無い: Q §2.1・U-08）
MODE_UNSUPPORTED = "この機器はこのモードに対応していません（受信専用・信号源なし）"

# ---------------------------------------------------------------- まだ作っていない機能の理由（U-08）

NOT_YET = {
    "file": "保存・読み込み・書き出しは未対応です",
    "view": "表示の切り替えは未対応です",
    "settings": "設定の画面は未対応です",
    "settings_keys": "キー操作の割り当ての画面は未対応です",
    "diag": "自己診断の結果の画面は未対応です",
    "colors": "配色の編集は未対応です",
    "waterfall": "スペクトログラムは未対応です",
    "density": "密度表示は未対応です",
    "unit": "dBμV・dBmV への切り替えは未対応です",
    "ext": "外部補正（アッテネータ・アンプ）は未対応です",
    "bandplan": "バンドプラン・ブックマークは未対応です",
    "freq_offset": "周波数オフセットは未対応です",
    "generic": "未対応です",
}

# ---------------------------------------------------------------- メニュー（J §4.3.1。Qt のメニューバーと Web の「≡」は同じ並び・同じ語）

MENU_TITLES = {
    "file": "ファイル", "edit": "編集", "device": "機器", "sweep": "掃引", "mode": "モード", "amplitude": "振幅",
    "marker": "マーカー", "calibration": "校正", "view": "表示", "settings": "設定", "help": "ヘルプ",
}
# メニューのアクセスキー（Alt+文字）。Qt のメニューバーと Web のメニューバーで同じ（Web はブラウザが先に取るキーを除いて
# 開く: app_menu.js の menuKey）。どちらもキーの割り当てに使えない（QT_RESERVED_KEYS・WEB_RESERVED_KEYS）
MENU_MNEMONICS = {
    "file": "F", "edit": "E", "device": "D", "sweep": "S", "mode": "M", "amplitude": "A", "marker": "K",
    "calibration": "C", "view": "V", "settings": "O", "help": "H",
}

MENU_ITEMS = {
    "open": "開く…",
    "save": "保存…",
    "export": "CSV に書き出す…",
    "png_save": "PNG を保存",
    "png_copy": "PNG をクリップボードにコピー",
    "record_start": "記録を開始",
    "record_stop": "記録を停止",
    "record_finish": "記録を保存…",
    "quit": "終了",
    "undo": "元に戻す",
    "redo": "やり直し",
    "connect": "機器に接続…",
    "disconnect": "切断",
    "self_test": "自己診断の結果",
    "run_cont": "連続掃引",
    "run_single": "シングル掃引",
    "stop": "停止",
    "pause": "連続掃引 ⇔ 停止",
    "ref_auto": "REF レベルを自動調整",
    # 探すもの（山・谷）の切り替え（G §3.8）。キーの初期値は無い（キー操作の画面で割り当てられる）
    "marker_polarity": "＋ピーク／−ピークを切り替え",
    "marker_peak": "ピークサーチ",
    "marker_peak_left": "ネクストピーク（左）",
    "marker_peak_right": "ネクストピーク（右）",
    "marker_peak_lower": "ネクストピーク（レベル順・高い）",
    "marker_left": "マーカーを左へ 1 点移動",
    "marker_right": "マーカーを右へ 1 点移動",
    "marker_here": "マウスの位置に M を配置",
    "marker_add": "マーカーを追加",
    "marker_center": "マーカーの周波数を Center に",
    "marker_ref": "マーカーの値を REF レベルに",
    "marker_span": "デルタマーカーの 2 点を Start・Stop に",
    "marker_listen": "選択中のマーカーで音声モニタ",
    "listen_stop": "音声モニタを停止",
    "marker_remove": "選択中のマーカーを削除",
    "marker_clear": "すべてのマーカーを削除",
    "marker_copy": "マーカー表をコピー（タブ区切り）",
    "cal_freq": "周波数特性と全体のレベル…",
    "cal_rbw": "RBW…",
    "cal_nf": "NF の利得…",
    "cal_pluto_gain": "PlutoSDR の利得の補正…",
    "cal_legacy": "旧校正ファイルを取り込む…",
    "view_colors": "配色…",
    "view_waterfall": "スペクトログラム",
    "view_density": "密度表示",
    "settings": "設定…",
    "settings_keys": "キー操作…",
    "web_connect": "ブラウザからの接続方法…",
    "help_keys": "キー操作の一覧",
    "help_about": "NamiBench について",
}

# メニューの並び（None は区切り線）。mode は MODES から作る
MENU_LAYOUT = (
    ("file", ("open", "save", "export", None, "png_save", "png_copy", None, "presets", "preset_save", None,
              "record_start", "record_stop", "record_finish", None, "quit")),
    ("edit", ("undo", "redo")),
    ("device", ("connect", "disconnect", None, "self_test")),
    ("sweep", ("run_cont", "run_single", "stop", "pause")),
    ("mode", ()),
    ("amplitude", ("ref_auto",)),
    ("marker", ("marker_polarity", "marker_peak", "marker_peak_left", "marker_peak_right", "marker_peak_lower",
                "marker_left", "marker_right", "marker_here", "marker_add", None, "marker_center", "marker_ref",
                "marker_span", None, "marker_listen", "listen_stop", None, "marker_remove", "marker_clear",
                "marker_copy")),
    ("calibration", ("cal_freq", "cal_rbw", "cal_nf", None, "cal_pluto_gain", None, "cal_legacy")),
    ("view", ("view_colors", "view_waterfall", "view_density", None, "layout_reset")),
    ("settings", ("settings", "settings_keys", None, "web_connect")),
    ("help", ("help_keys", "help_about")),
)

# まだ作っていない項目と、その理由（NOT_YET のキー）
MENU_NOT_YET = {
    "self_test": "diag",
    "view_waterfall": "waterfall", "view_density": "density",
}

# 操作が使えない理由（メニューとボタンで同じ文）
REASON_TEXTS = {
    "select_marker": "マーカーが未選択です",
    "delta_ref": "基準マーカーを選択すると使用できます",
    "no_wave": "波形がありません。掃引すると使用できます",
    "no_auto_points": "調整に使用できる点がありません",
    "no_state": "準備中です",
    "no_undo": "元に戻す操作がありません",
    "no_redo": "やり直す操作がありません",
    "no_sweep": "掃引条件が未設定です",
    "zero_span_axis": "ゼロスパンの横軸は周波数ではありません",
    "not_listening": "音声モニタ中ではありません",
    "no_data_to_save": "保存する値がありません。掃引してください",
    "part_window": "別ウィンドウの中からは開けません",
    # イメージ除去を選べないとき（設計F §7.1。NF は HOT/COLD の2回だけ・ゼロスパンは点が時刻の並び）
    "image_reject_nf": "NF ではイメージ除去を使用できません",
    "image_reject_zero_span": "ゼロスパンではイメージ除去を使用できません",
}

# 機器の範囲の外・意味の無い区間の帯の説明（設計J §4.8）。グラフの帯・PNG の注意・マーカーの読み値の注意で同じ文
DISPLAY_MARKS = {
    "outside_tg_range": "TG の範囲外（値は通過特性ではありません）",
    "lo_leak": "局部発振器の漏れ（入力の信号ではありません）",
    "negative_mirror": "正の周波数のイメージ（約 1〜2dB 低く表示）",
}

# ---------------------------------------------------------------- 文の型（`{名前}` を差し込む。texts.tpl・GSTexts.fmt）

TEMPLATES = {
    "mark_range": "{label}: {start}〜{stop}",
    "adjusted_with_reason": "{value} に合わせました（{reason}）",
    "adjusted": "{value} に合わせました",
    "alt_keep_span": "Span {span} を維持して Center を {center} に変更",
    "alt_keep_center": "Center {center} を維持して Span を {span} に変更",
    "alt_keep_typed": "Span を {span} に縮めて {start}〜{stop} に変更",
    "alt_done": "Center {center}・Span {span} に置き換えました（この 1 回のみ）",
    "points_auto": "自動",
    "on": "ON",
    "off": "OFF",
    "paren": "（{text}）",
    "port": "ポート {port}",
    "max_span": "最大 {span}",
    "range_min_max": "{min}〜{max}",
    "legacy_line": "（{line} 行目の {field} 番目の欄）",
    "age_s": "{n} 秒前",
    "age_m": "{n} 分前",
    "age_h": "{n} 時間前",
    "age_d": "{n} 日前",
    "range": "{lo}〜{hi}",
    "trace_reset_cause": "条件変更（{cause}）でリセット",
    "trace_prefix": "トレース {n}: ",
    "axis_differs": "（現在の軸と異なる）",
    "norm_none_reason": "基準なし（受信電力）: {reason}",
    "norm_none": "基準なし（受信電力）",
    "norm_ref": "基準: {name}",
    "norm_ref_noname": "基準",
    "norm_power_cycled": "⚠ 電源再投入前の基準（再取得を推奨）",
    "norm_out_of_range": "基準の範囲外: {ranges}",
    "task_progress": "  {done}/{total}",
    "task_elapsed": "  経過時間 {s} 秒",
    "task_cancelling": "（中断処理中）",
    "cond_tail_gap": "Stop の手前 1 Step 未満はデータがありません（機器の上限）",
    "cond_fixed_center": "{cause}のため、チューナーの中心周波数を {center} に固定中です。中心の DC の点は使用できません（値なし）",
    "span_limited": "{cause}のため、表示範囲を {start}〜{stop} に制限しました。終了すると元の範囲に戻ります",
    "rtltcp_freq_refused": "rtl_tcp のクライアントが要求した周波数 {freq} は使用できませんでした（{reason}）",
    "rtltcp_port_in_use": "ポートは使用中です",
    "gain_agc": "AGC",
    "status_conn": "● {state}{who}",
    "status_run": "掃引: {state}",
    "status_single_count": " {done}/{target} 回",
    "status_sweep_time": "掃引時間 {s} 秒",
    "status_sweep_time_none": "掃引時間 —",
    "status_discard": "破棄した掃引 {n} 回",
    "status_cal": "補正: 校正データ {at}",
    "status_cal_none": "補正: 校正データなし（補正なしの値）",
    "status_cal_rtl": "補正: RTL-SDR ppm {ppm}{level}（{at}）・単位 {unit}",
    "status_cal_rtl_none": "補正: RTL-SDR 未校正（ppm {ppm}）・単位 {unit}",
    "rtl_level_cal": "・レベル",
    "status_rec_active": "● 記録中 {clock}・{sweeps} 回",
    "status_rec_stopped": "記録停止中（未保存）",
    "status_rtltcp": "rtl_tcp 送出中 :{port}・接続 {clients}",
    "status_rtltcp_lan": "（同じネットワークのほかの PC からも接続可）",
    "status_rtltcp_error": "rtl_tcp: ポート {port} を開けません（{reason}）",
    "status_listen": "音声モニタ中: {freq}・{mode}",
    "status_tasks": "処理中 {n} 件",
    "status_ws_down": "NamiBench の PC との接続が切れました（再接続中）",
    "source_sg": "SG 出力中 {freq}",
    "source_tg": "TG 出力中",
    "waiting_sweep_end": "現在の掃引の終了待ち（残り約 {s} 秒）",
    # 上段の掃引の進み具合（設計J §4.13）
    "sweep_progress": "{percent}%",
    "rtl_gain_step": "{db} dB",
    "rtl_ppm_done": "ppm を {old} → {new} に変更しました（{measured} の信号を {true} として校正）",
    "rtl_level_done": "利得 {gain} dB のレベルの補正を {offset} dB に変更しました（{measured} の信号を {true} dBm として校正）",
    "rtl_cal_none": "個体の校正: なし（ppm 0・レベルは dB（未校正））",
    "rtl_cal_at": "個体の校正: {at}（ppm {ppm}{level}）",
    "rtl_unit": "現在の単位: {unit}",
    "rtl_shared": "同じシリアル番号の機器の接続数 {n}（校正は共有）",
    "rtl_dll": "DLL のフォルダ: {folder}",
    "rtl_dll_default": "DLL のフォルダ: 自動検出",
    "rtl_fit_done": "利得を {gain} dB に調整しました（変更前 {before}）",
    "rtl_fit_overload": "。最小の利得でも過大入力です。入力レベルを下げてください",
    "rtl_fit_reset": "。トレース {traces} をクリアしました",
    "listen_now": "音声モニタ中: {marker}（{freq}）・{mode}",
    "audio_none": "音声の出力デバイスが見つかりません（音声を再生できません）",
    "audio_blocked": "ブラウザが音声の再生をブロックしました。画面をクリックすると再生されます",
    "rtltcp_running": "送出中: ポート {port}・接続 {clients}（{bind}）",
    "rtltcp_stopped_state": "停止中",
    "rtltcp_dropped": "破棄したバイト数（クライアントの受信遅れ）: {n}",
    "saved": "保存しました: {name}",
    "trace_note": "トレース {n}: {text}",
    # グラフの上のマウスの位置の読み値（G §2）。ゼロスパンの横軸は点の番号
    "task_done_path": "{done}: {name}",
    "open_done": "開きました: {n} ファイル",
    "open_errors": "・開けなかったファイル: {list}",
    "open_error_item": "{name}（{reason}）",
    "export_rows": "（{rows} 行）",
    "ref_open_measured": "オープンを測定しました。ショートを接続して「ショートを測定」を押してください",
    "ref_taken": "基準を取得しました: {name}",
    "cancelled_generic": "中断しました",
    "task_failed": "{failed}: {error}",
    "work_file_kept": "。記録の作業ファイルは保持されるため、後で保存できます",
    "norm_changed": "ノーマライズが変更されました",
    "file_trace_label": "{name} #{number}",
    "marker_copied": "マーカー表をコピーしました（{n} 行）",
}

# ---------------------------------------------------------------- RTL-SDR（設計Q §6）

RBW_WINDOWS = {"hann": "Hann", "flattop": "Flat top"}
# RBW の欄の注（窓ごとの並び: Q §6.4）
RBW_WINDOW_NOTES = {
    "hann": "Hann: 周波数分解能を優先（ビンの間の信号は最大約 1.4dB 低く表示）",
    "flattop": "Flat top: 振幅確度を優先（ピークの幅が広い）",
}

RTL_LABELS = {
    "group": "受信（RTL-SDR）",
    "gain": "受信の利得",
    "agc": "AGC",
    "gain_tip": "利得を変更すると、トレースをクリアします。REF レベルとは連動しません。",
    "ppm": "周波数の補正（ppm）",
    "ppm_tip": "水晶発振子の周波数偏差の補正。シリアル番号ごとに保存します。",
    "cal_marker": "マーカー",
    "cal_true_freq": "既知の周波数",
    "cal_ppm_button": "ppm をこの信号で校正",
    "cal_ppm_tip": "マーカーを既知の周波数の信号（FM 放送・GigaSt の SG など）に配置し、既知の周波数を入力して押します。",
    "cal_true_dbm": "既知のレベル（dBm）",
    "cal_level_button": "レベルをこの信号で校正",
    "cal_level_tip": "マーカーを既知のレベルの信号（GigaSt の SG など）に配置し、既知のレベルを入力して押します。現在の利得の段の補正として保存し、以後その段は dBm で表示します。",
    "fit_gain": "利得を自動調整",
    "fit_gain_tip": "現在の条件で、表示中の信号が過大入力にならない最大の利得の段を選択します。選択後、トレースをクリアします。",
    "fit_needs_manual": "AGC の間は使用できません。利得を手動に設定してください",
    "fit_running": "利得を調整中",
    "dll_button": "DLL のフォルダ…",
    "not_rtlsdr": "RTL-SDR を接続すると使用できます",
    "no_marker": "マーカーを配置すると使用できます",
    "needs_manual_gain": "AGC の間はレベルを校正できません。利得を手動に設定してください",
    "points_fft": "この機器のトレースポイント数は RBW と Span で決まります",
    "zero_span_fft": "この機器はゼロスパンに対応していません",
    "image_reject_fft": "この機器はイメージ除去に対応していません",
}

FIXED_CENTER_CAUSES = {"rtltcp_clients": "rtl_tcp のクライアントの接続中", "listening": "音声モニタ中"}

# ---------------------------------------------------------------- 音声モニタ（設計Q §6.8・設計T）

LISTEN_MODES = {"am": "AM", "fm_narrow": "狭帯域 FM", "fm_broadcast": "FM 放送", "usb": "USB", "lsb": "LSB"}
LISTEN_STOP_REASONS = {
    "user": "手動で停止",
    "marker_removed": "音声モニタ中のマーカーを削除したため",
    "device_lost": "機器の接続が切れたため",
    "device_changed": "機器を変更したため",
    "ext_tx": "外部 IQ の送信でサンプリング周波数が変わるため",
}
LISTEN_LABELS = {
    "group": "音声モニタ",
    "mode": "方式",
    "start": "音声モニタを開始",
    "stop": "停止",
    "volume": "音量",
    "not_iq": "音声モニタは RTL-SDR の接続中のみ使用できます",
}

# ---------------------------------------------------------------- 設定の画面（設計O §1.3）

RTLTCP_BINDS = {"local": "この PC のみ", "lan": "同じネットワークのほかの PC からも"}
SETTINGS_LABELS = {
    "title": "設定",
    "rtltcp_enable": "送り出す",
    "rtltcp_port": "ポート番号",
    "rtltcp_bind": "接続元",
    "rtltcp_stop": "停止",
    "rtlsdr_dll": "rtlsdr.dll",
    "rtlsdr_missing": "見つかりません",
    "rtlsdr_32bit": "32 ビット版のため使用できません",
    "rtlsdr_choose": "フォルダを選択…",
    "rtlsdr_choose_title": "rtlsdr.dll（64 ビット版）のあるフォルダ",
    "rtlsdr_default_tip": "初期値: 自動検出",
    "close": "閉じる",
}
