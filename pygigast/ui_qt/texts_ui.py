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
    ("s11", "S11", "反射", "反射（方向性結合器）の測定はまだ使えません"),
    ("sg", "SG", "信号源", None),
    ("nf", "NF", "NF 測定", None),
)
MODE_NAMES = {code: name for code, _l, name, _w in MODES}
MODE_LABELS = {code: label for code, label, _n, _w in MODES}
# 機器が測れないモードの理由（機種のプロファイルの measurements に無い: Q §2.1・U-08）
MODE_UNSUPPORTED = "この機器はこのモードで測れません（受信だけの機器・信号源なし）"

# ---------------------------------------------------------------- まだ作っていない機能の理由（U-08）

NOT_YET = {
    "file": "保存・読込・書き出しはまだ使えません",
    "view": "表示の切り替えはまだ使えません",
    "settings": "設定の画面はまだ使えません",
    "settings_keys": "キー操作の割り当ての画面はまだ使えません",
    "diag": "自己診断の結果を見る画面はまだ使えません",
    "colors": "配色の編集はまだ使えません",
    "waterfall": "ウォーターフォールはまだ使えません",
    "density": "密度表示はまだ使えません",
    "unit": "dBμV・dBmV への切り替えはまだ使えません",
    "ext": "外部補正（アッテネータ・アンプ）はまだ使えません",
    "bandplan": "バンドプラン・ブックマークはまだ使えません",
    "s11": "S11（反射）モードはまだ使えません",
    "step": "ステップ掃引はまだ使えません",
    "nf": "NF 測定はまだ使えません",
    "freq_offset": "周波数オフセット（変換器の観測用）はまだ使えません",
    "generic": "まだ使えません",
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
    "png_copy": "PNG をクリップボードへ",
    "record_start": "記録を開始",
    "record_stop": "記録を止める",
    "record_finish": "記録をまとめる…",
    "quit": "終了",
    "undo": "条件の変更を戻す",
    "redo": "条件の変更をやり直す",
    "connect": "機器に接続…",
    "disconnect": "切断",
    "self_test": "自己診断の結果",
    "run_cont": "連続",
    "run_single": "単発",
    "stop": "停止",
    "pause": "連続 ⇔ 停止",
    "ref_auto": "REF レベルを波形に合わせる（自動）",
    # 探すもの（山・谷）の切り替え（G §3.8）。キーの初期値は無い（キー操作の画面で割り当てられる）
    "marker_polarity": "＋ピーク／−ピークを切り替える",
    "marker_peak": "選んでいるマーカーをピークへ",
    "marker_peak_left": "次のピーク（左）",
    "marker_peak_right": "次のピーク（右）",
    "marker_peak_lower": "次に高いピーク",
    "marker_left": "マーカーを左へ1点（Shift で10点）",
    "marker_right": "マーカーを右へ1点（Shift で10点）",
    "marker_here": "マウスの位置に M を置く",
    "marker_add": "マーカーを追加（ピークに M）",
    "marker_center": "マーカーの周波数を Center に",
    "marker_ref": "マーカーの値を REF レベルに",
    "marker_span": "デルタの2点を Start・Stop に",
    "marker_listen": "選んでいるマーカーの位置を聞く",
    "listen_stop": "聞くのをやめる",
    "marker_remove": "選んでいるマーカーを消す",
    "marker_clear": "マーカーを全部消す",
    "marker_copy": "マーカー表をコピー（タブ区切り）",
    "cal_freq": "周波数特性と全体のレベル…",
    "cal_rbw": "RBW…",
    "cal_nf": "NF の増幅度…",
    "cal_legacy": "旧校正ファイルを読み込む…",
    "view_colors": "配色…",
    "view_waterfall": "ウォーターフォール",
    "view_density": "密度表示",
    "settings": "設定…",
    "settings_keys": "キー操作…",
    "web_connect": "ブラウザからの接続のしかた…",
    "help_keys": "キー操作の一覧",
    "help_about": "pyGigaSt について",
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
    ("calibration", ("cal_freq", "cal_rbw", "cal_nf", None, "cal_legacy")),
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
    "select_marker": "マーカーを選んでいません",
    "delta_ref": "デルタの基準を選ぶと使えます",
    "no_wave": "波形がまだありません（掃引すると使えます）",
    "no_auto_points": "合わせる点がありません（局発の漏れ・TG の範囲外・掃引の始めの不安定な点・値の無い点は使いません）",
    "no_state": "まだ準備ができていません",
    "no_undo": "戻す変更がありません",
    "no_redo": "やり直す変更がありません",
    "no_sweep": "掃引条件がまだありません",
    "zero_span_axis": "ゼロスパンの横軸は周波数ではありません",
    "not_listening": "聞いていません",
    "no_data_to_save": "保存する値がありません（掃引してください）",
    "part_window": "別ウィンドウの中からは開けません",
    # イメージ除去を選べないとき（設計F §7.1。NF は HOT/COLD の2回だけ・ゼロスパンは点が時刻の並び）
    "image_reject_nf": "NF ではイメージ除去を使いません",
    "image_reject_zero_span": "ゼロスパンではイメージ除去を使いません",
}

# 機器の範囲の外・意味の無い区間の帯の説明（設計J §4.8）。グラフの帯・PNG の注意・マーカーの読み値の注意で同じ文
DISPLAY_MARKS = {
    "outside_tg_range": "TG の範囲外（値は通過特性ではありません）",
    "lo_leak": "局発の漏れ（入力の信号ではありません）",
    "negative_mirror": "正の周波数の鏡（約 1〜2dB 低く読む）",
}

# ---------------------------------------------------------------- 文の型（`{名前}` を差し込む。texts.tpl・GSTexts.fmt）

TEMPLATES = {
    "mark_range": "{label}: {start}〜{stop}",
    "adjusted_with_reason": "{value} に合わせました（{reason}）",
    "adjusted": "{value} に合わせました",
    "alt_keep_span": "Span {span} を保って Center を {center} にする",
    "alt_keep_center": "Center {center} を保って Span を {span} にする",
    "alt_keep_typed": "Span を {span} に縮めて {start}〜{stop} にする",
    "alt_done": "Center {center}・Span {span} に置き換えました（この1回だけ）",
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
    "axis_differs": "（いまの軸と違う）",
    "norm_none_reason": "基準なし（受けた電力）: {reason}",
    "norm_none": "基準なし（受けた電力）",
    "norm_ref": "基準: {name}",
    "norm_ref_noname": "基準",
    "norm_power_cycled": "⚠ 電源の入れ直し前の基準（取り直しを勧めます）",
    "norm_out_of_range": "基準の範囲外: {ranges}",
    "task_progress": "  {done}/{total}",
    "task_elapsed": "  経過 {s} 秒",
    "task_cancelling": "（やめています）",
    "cond_tail_gap": "Stop の手前 1 Step 未満はデータがありません（機器の上限）",
    "cond_fixed_center": "{cause}間はチューナーの中心を {center} に止めています。中心の DC の点は使えません（値なし）",
    "span_limited": "{cause}間は表示の範囲を {start}〜{stop} に限りました（やめると戻します）",
    "rtltcp_freq_refused": "rtl_tcp から来た周波数 {freq} は使えませんでした（{reason}）",
    "rtltcp_port_in_use": "ポートが使われています",
    "gain_agc": "AGC",
    "status_conn": "● {state}{who}",
    "status_run": "掃引: {state}",
    "status_single_count": " {done}/{target} 回",
    "status_sweep_time": "掃引時間 {s} 秒",
    "status_sweep_time_none": "掃引時間 —",
    "status_discard": "捨てた掃引 {n} 回",
    "status_cal": "補正: 校正データ {at}",
    "status_cal_none": "補正: 校正データなし（補正なしの値）",
    "status_cal_rtl": "補正: RTL-SDR ppm {ppm}{level}（{at}）・単位 {unit}",
    "status_cal_rtl_none": "補正: RTL-SDR 未校正（ppm {ppm}）・単位 {unit}",
    "rtl_level_cal": "・レベル",
    "status_rec_active": "● 記録中 {clock}・{sweeps} 回",
    "status_rec_stopped": "記録を止めました（まとめていません）",
    "status_rtltcp": "rtl_tcp 送出中 :{port}・接続 {clients}",
    "status_rtltcp_lan": "（同じネットワークのほかの PC からも受けます）",
    "status_rtltcp_error": "rtl_tcp: ポート {port} を開けません（{reason}）",
    "status_listen": "聞いている: {freq}・{mode}",
    "status_tasks": "処理中 {n} 件",
    "status_ws_down": "pyGigaSt の PC と切れています（繋ぎ直しています）",
    "source_sg": "SG 出力中 {freq}",
    "source_tg": "TG 出力中",
    "waiting_sweep_end": "いまの掃引の終わりを待っています（残り約 {s} 秒）",
    "rtl_gain_step": "{db} dB",
    "rtl_ppm_done": "ppm を {old} → {new} にしました（{measured} に見えた信号を {true} として）",
    "rtl_level_done": "利得 {gain} dB のレベルの補正を {offset} dB にしました（{measured} を {true} dBm として）",
    "rtl_cal_none": "個体の校正: なし（ppm 0・レベルは dB（未校正））",
    "rtl_cal_at": "個体の校正: {at}（ppm {ppm}{level}）",
    "rtl_unit": "いまの単位: {unit}",
    "rtl_shared": "同じシリアル番号の機器が {n} 本つながっています（校正は共有）",
    "rtl_dll": "DLL のフォルダ: {folder}",
    "rtl_dll_default": "DLL のフォルダ: 自動で探す",
    "rtl_fit_done": "利得を {gain} dB に合わせました（前は {before}）",
    "rtl_fit_overload": "。いちばん小さい段でも振り切れています。入力を弱めてください",
    "rtl_fit_reset": "。トレース {traces} の蓄積を捨てました",
    "listen_now": "聞いている: {marker}（{freq}）・{mode}",
    "audio_none": "音を出す機器が見つかりません（音は流れていますが鳴らせません）",
    "audio_blocked": "ブラウザが音を止めています。画面のどこかを押すと鳴ります",
    "rtltcp_running": "送出中: ポート {port}・接続 {clients}（{bind}）",
    "rtltcp_stopped_state": "止まっています",
    "rtltcp_dropped": "遅い相手に送れず捨てたバイト: {n}",
    "saved": "保存しました: {name}",
    "trace_note": "トレース {n}: {text}",
    # グラフの上のマウスの位置の読み値（G §2）。ゼロスパンの横軸は点の番号
    "mouse_point": "点 {n}",
    "task_done_path": "{done}: {name}",
    "open_done": "開きました: {n} ファイル",
    "open_errors": "・開けなかったもの: {list}",
    "open_error_item": "{name}（{reason}）",
    "export_rows": "（{rows} 行）",
    "ref_open_measured": "オープンを測りました。ショートを繋いで「ショートを測る」を押してください",
    "ref_taken": "基準を取りました: {name}",
    "cancelled_generic": "やめました",
    "task_failed": "{failed}: {error}",
    "work_file_kept": "。記録の作業ファイルは残っていて、あとでまとめられます",
    "norm_changed": "ノーマライズが変わりました",
    "file_trace_label": "{name} #{number}",
    "marker_copied": "マーカー表をコピーしました（{n} 行）",
}

# ---------------------------------------------------------------- RTL-SDR（設計Q §6）

RBW_WINDOWS = {"hann": "Hann", "flattop": "Flat top"}
# RBW の欄の注（窓ごとの並び: Q §6.4）
RBW_WINDOW_NOTES = {
    "hann": "Hann: 細かく分けて見る（格子の間の信号は最大約 1.4dB 低く読める）",
    "flattop": "Flat top: レベルを正確に読む（山が広い）",
}

RTL_LABELS = {
    "group": "受信（RTL-SDR）",
    "gain": "受信の利得",
    "agc": "AGC（ドングルの自動）",
    "gain_tip": "利得を変えると値の意味が変わるので、トレースの蓄積を捨てます。REF レベルとは連動しません",
    "ppm": "周波数の補正（ppm）",
    "ppm_tip": "水晶のずれの補正。個体の校正としてシリアル番号ごとに保存します",
    "cal_marker": "マーカー",
    "cal_true_freq": "本当の周波数",
    "cal_ppm_button": "ppm をこの信号で合わせる",
    "cal_ppm_tip": "マーカーを既知の周波数の信号（FM 放送・GigaSt の SG など）に置き、本当の周波数を入れて押します",
    "cal_true_dbm": "本当のレベル（dBm）",
    "cal_level_button": "レベルをこの信号で合わせる",
    "cal_level_tip": "マーカーを既知のレベルの信号（GigaSt の SG など）に置き、本当のレベルを入れて押します。いまの利得の段の補正として保存し、以後その段は dBm で出ます",
    "fit_gain": "利得を合わせる",
    "fit_gain_tip": "いまの条件で、見えている信号が振り切れないいちばん大きい利得の段を選びます（段を上から短く測って試します）。選んだらトレースの蓄積を捨てます",
    "fit_needs_manual": "AGC の間は使えません（利得を手動の段にしてから押します）",
    "fit_running": "利得を合わせています",
    "dll_button": "DLL のフォルダ…",
    "not_rtlsdr": "RTL-SDR に繋ぐと使えます",
    "no_marker": "マーカーを置いてから使えます",
    "needs_manual_gain": "AGC の間は利得が分からないので、レベルの校正は利得を手動にしてから行います",
    "points_fft": "この機器の点数は RBW と Span で決まります",
    "zero_span_fft": "この機器はゼロスパンを持ちません",
    "image_reject_fft": "この機器はイメージ除去を持ちません",
}

FIXED_CENTER_CAUSES = {"rtltcp_clients": "rtl_tcp の相手がつながっている", "listening": "聞いている"}

# ---------------------------------------------------------------- 聞く（設計Q §6.8）

LISTEN_MODES = {"am": "AM", "fm_narrow": "FM狭", "fm_broadcast": "FM放送", "usb": "USB", "lsb": "LSB"}
LISTEN_STOP_REASONS = {
    "user": "止めました",
    "marker_removed": "聞いていたマーカーを消したため",
    "device_lost": "機器が切れたため",
    "device_changed": "機器が変わったため",
}
LISTEN_LABELS = {
    "group": "聞く",
    "mode": "方式",
    "start": "この位置を聞く",
    "stop": "やめる",
    "volume": "音量",
    "not_iq": "聞く機能は RTL-SDR に繋いでいるときだけ使えます",
}

# ---------------------------------------------------------------- 設定の画面（設計O §1.3）

RTLTCP_BINDS = {"local": "この PC からだけ", "lan": "同じネットワークのほかの PC からも"}
SETTINGS_LABELS = {
    "title": "設定",
    "rtltcp_enable": "送り出す",
    "rtltcp_port": "ポート番号",
    "rtltcp_bind": "受ける接続",
    "rtltcp_stop": "止める",
    "rtlsdr_dll": "rtlsdr.dll",
    "rtlsdr_missing": "見つかりません",
    "rtlsdr_32bit": "32 ビット版のため使えません",
    "rtlsdr_choose": "フォルダを選ぶ…",
    "rtlsdr_choose_title": "rtlsdr.dll（64 ビット版）のあるフォルダ",
    "rtlsdr_default_tip": "初期値は自動で探す",
    "close": "閉じる",
}
