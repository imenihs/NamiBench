"""設定の画面（接続・表示・キー操作・保存）・配色の編集・キーの割り当て・プリセット・表示の単位・別画面の語。

texts.py が `from .texts_settings import *` で取り込み、Web へも同じ表で配る（GET /api/v1/texts。J §5）。
キーの初期値の表（QT_KEY_DEFAULTS・WEB_KEY_DEFAULTS）も、Qt と Web の両方が同じ場所から読むためにここに置く
（キーの名前は画面に出る文字なので表に持つ）。`{名前}` は差し込み（texts.tpl・GSTexts.w と同じ置き換え）。
Qt を import しない。
"""
from __future__ import annotations

from .texts_labels import LABELS
from .texts_ui import MENU_ITEMS, NOT_YET, REASON_TEXTS, TEMPLATES

# ---------------------------------------------------------------- 設定の画面（設計O §1.3）

SETTINGS_PAGES = {
    "connection": "接続",
    "display": "配色",
    "notify": "通知",
    "keys": "キー操作",
    "save": "保存",
    "web": "ブラウザから使う",
    "rtltcp": "rtl_tcp",
    "rtlsdr": "RTL-SDR",
    "plutosdr": "PlutoSDR",
}

SETTINGS_TEXTS = {
    "reset": "初期値に戻す",
    "reset_tip": "この項目を初期値に戻す",
    # 接続
    "known_group": "覚えた機器",
    "known_none": "覚えた機器はありません",
    "known_last": "前回の機器",
    "forget": "忘れる",
    "forget_title": "機器を忘れる",
    "forget_confirm": "「{name}」を覚えた機器から外します。次に起動したとき、自動では繋ぎません。いま繋いでいる機器は、このまま使えます。",
    "manual_group": "COM 番号で繋ぐ（GigaSt）",
    "manual_port": "COM 番号",
    "manual_connect": "このポートで繋ぐ",
    "manual_placeholder": "例 COM3",
    "manual_bad": "COM 番号を入れてください（例 COM3）",
    "open_devices": "機器に接続…",
    # 表示（配色）
    "palette_reset_light": "ライトの初期値にする…",
    "palette_reset_dark": "ダークの初期値にする…",
    "palette_reset_title": "配色を初期値にする",
    "palette_reset_confirm": "変えた色 {n} 個が初期値に戻ります。{base}の配色にしますか？",
    "palette_changed_mark": "●",
    "palette_changed_tip": "初期値（{default}）から変えた色",
    "palette_pick_title": "{name}の色",
    "palette_hex": "色（#rrggbb）",
    # 読みやすさの知らせは、変えた色の行に出す（U-25）。相手の色の名前を並べる
    "palette_row_contrast": "⚠ {names}とのコントラスト不足",
    "palette_row_similar": "⚠ {names}と見分けにくい",
    "palette_contrast_tip": "{fg}と{bg}のコントラスト比 {ratio}（7 未満は読みにくい）",
    "palette_similar_tip": "{a}と{b}の色の差 {d}（20 未満は見分けにくい）",
    "palette_row_reset_tip": "この色だけ初期値に戻す",
    # 保存した配色（独自の配色の保存と読み込み: J §4.4.1。プリセットの窓と同じ形の一覧）
    "palettes_group": "保存した配色",
    "palettes_none": "保存した配色はありません",
    "palettes_broken": "読めない配色のファイルが {n} 個あります（消さずに残しています）",
    "palette_load": "読み込む",
    "palette_load_tip": "いまの配色をこの配色で置き換える（元に戻す Ctrl+Z で戻せます）",
    "palette_rename": "名前…",
    "palette_delete": "消す…",
    "palette_overwrite": "いまの配色で上書き…",
    "palette_export": "ファイルに書き出す…",
    "palette_save_new": "いまの配色に名前を付けて保存…",
    "palette_import": "ファイルから取り込む…",
    "palette_name_prompt": "配色の名前",
    "palette_load_title": "配色を読み込む",
    "palette_load_confirm": "保存していない変更 {n} 個が消えます。「{name}」を読み込みますか？",
    "palette_delete_confirm": "配色「{name}」を消します。戻せません。",
    "palette_overwrite_confirm": "「{name}」の色を、いまの配色で置き換えます。",
    "palette_loaded": "配色「{name}」を読み込みました（元に戻す Ctrl+Z で戻せます）",
    "palette_saved": "配色「{name}」を保存しました",
    "palette_exported": "配色「{name}」を書き出しました: {file}",
    "palette_imported": "配色「{name}」を取り込みました",
    "palette_imported_warn": "配色「{name}」を取り込みました（{warn}）",
    # 一覧の行の読みやすさの知らせ（相手の組はツールチップ: U-25）
    "palette_list_warn": "⚠ 読みにくい組 {n}",
    "palette_export_title": "配色をファイルに書き出す",
    "palette_import_title": "配色をファイルから取り込む",
    "palette_file_filter": "配色のファイル (*.json)",
    # キー操作
    "keys_col_command": "操作",
    "keys_col_key": "いまのキー",
    "keys_col_default": "初期値",
    "keys_col_where": "効く所",
    "keys_where_graph": "グラフ",
    "keys_where_any": "どこでも",
    "keys_assign": "キーを押して割り当てる",
    "keys_waiting": "割り当てるキーを押してください（Esc でやめる）",
    "keys_clear": "キーを外す",
    "keys_reset_row": "この行を初期値に戻す",
    "keys_reset_all": "全部を初期値に戻す…",
    "keys_reset_all_title": "キーを全部初期値に戻す",
    "keys_reset_all_confirm": "変えた割り当て {n} 個が初期値に戻ります。戻しますか？",
    "keys_conflict_title": "キーがぶつかります",
    "keys_conflict": "{key} は「{other}」に使われています。「{command}」に付け替えますか？（「{other}」のキーは外れます）",
    "keys_reserved": "{key} は割り当てられません: {why}",
    "keys_none": "（なし）",
    "keys_changed_tip": "初期値から変えた割り当て",
    "keys_assigned": "{command} に {key} を割り当てました",
    # 保存
    "save_dir": "保存・開くの最初のフォルダ",
    "png_dir": "PNG の保存先",
    "png_size": "PNG の大きさ",
    "png_palette": "PNG の配色",
    "choose": "選ぶ…",
    "save_web_reason": "ブラウザではブラウザの保存先へダウンロードします",
    # ブラウザでは変えられない項目
    "web_only_pc": "pyGigaSt の PC の画面で変えます",
}

# 配色の名前（設計J §4.4 の表の名前）
PALETTE_NAMES = {
    "graph_bg": "グラフの背景",
    "panel_bg": "パネルの背景",
    "text": "文字",
    "text_dim": "控えめな文字",
    "accent": "強調（選択中・主ボタン）",
    "warn": "注意",
    "grid": "目盛線",
    "axis": "軸",
    "trace1": "トレース 1",
    "trace2": "トレース 2",
    "trace3": "トレース 3",
    "trace4": "トレース 4",
    "zero_db": "0dB の線",
    "running": "動作中（連続・単発）",
    "marker": "マーカー",
}
PALETTE_BASES = {"light": "ライト", "dark": "ダーク"}

# ---------------------------------------------------------------- 表示の単位・外部補正・周波数オフセット（設計O）

VIEW_TEXTS = {
    "level_unit": "単位",
    "level_unit_tip": "電力の読み値の単位（50Ω で換算）。グラフ・マーカー・CSV・PNG・REF レベルが同じ単位になります",
    # 単位の欄は実際に使っている単位を出す（TG・S11 で基準との比を出している間は比の dB）
    "level_unit_ratio_item": "dB（TG・S11 のノーマライズ中のみ）",
    "level_unit_ratio_item_tip": "TG・S11 で基準を使っている間だけ、この単位で出します",
    "level_unit_ratio_reason": "基準との比を出している間は電力の単位を使いません。SA・SG に戻すか基準を外すと {unit} で出します",
    "level_unit_changed": "単位を {unit} にしました",
    # 電力から基準との比の表示に変わったときの窓
    "ratio_title": "基準との比で表示します",
    "ratio_text": "基準「{name}」との比（dB）で表示します。電力（{unit}）で見るときは基準を外してください（信号源タブ）。"
                  "SA・SG に戻すと {unit} で表示します",
    "ratio_status": "基準「{name}」との比（dB）で表示します",
    # 基準との比から電力に戻ったときの窓（比に変わったときと同じ形: J §4.10 の 3）
    "power_title": "電力で表示します",
    "power_text": "{reason}。基準との比ではなく電力（{unit}）で表示します。",
    "power_hint": "比で見るときは、条件を基準と同じにするか、いまの条件で基準を取り直してください。",
    "power_status": "電力（{unit}）で表示します: {reason}",
    "power_reason_mode": "{mode} モードでは基準との比を使いません",
    "dont_show_again": "次から出さない（設定 → 通知 で戻せます）",
    # 設定の画面の「通知」: 窓で知らせるか（この画面だけの覚え書き）
    "notify_ratio": "比と電力の表示が切り替わったら知らせる",
    "notify_ratio_tip": "TG・S11 で、電力（dBm など）と基準との比（dB）の表示が入れ替わったときに窓で知らせます",
    "ext_db": "外部補正",
    "ext_db_tip": "外付けのアッテネータ・アンプの分。電力の読み値に足します（10dB の減衰なら +10）",
    "ext_db_placeholder": "例 10（dB）",
    "freq_offset": "周波数オフセット",
    "freq_offset_tip": "変換器の観測用。表示する周波数 = 測った周波数 + オフセット（負も可）。機器へ送る周波数は変わりません",
    "freq_offset_placeholder": "例 10G・−1.2G（0 で使わない）",
    "freq_offset_on": "換算中: 表示の周波数は測った周波数に {off} を足した値",
    "axis_freq_offset": "周波数（換算後: 測った周波数に {off}）",
    "status_ext": "外部補正 {db} dB",
    "status_offset": "周波数オフセット {off}",
    "draw_style": "描き方",
    "reset_amp": "単位と外部補正を初期値に戻す",
    "bad_db": "dB の数を入れてください（例 10・−6.5）",
}

# ---------------------------------------------------------------- プリセット（設計O §1.2）

PRESET_TEXTS = {
    "title": "プリセット",
    "save_new": "いまの状態を新しいプリセットに…",
    "overwrite": "いまの状態で上書き…",
    "overwrite_confirm": "「{name}」の中身を、いまの状態で置き換えます。",
    "load": "呼び出す",
    "load_tip": "いまの状態をこのプリセットで置き換える（元に戻す Ctrl+Z で戻せます）",
    "rename": "名前…",
    "delete": "消す…",
    "delete_confirm": "プリセット「{name}」を消します。戻せません。",
    "name_prompt": "プリセットの名前",
    "none": "プリセットはまだありません",
    "broken": "読めないプリセットのファイルが {n} 個あります（消さずに残しています）",
    "loaded": "プリセット「{name}」を呼び出しました（元に戻す Ctrl+Z で戻せます）",
    "saved": "プリセット「{name}」を保存しました",
}

# ---------------------------------------------------------------- 並び・別画面（J §4.3.2）

LAYOUT_TEXTS = {
    "part_peaks": "ピークの一覧",
    "layout_reset_done": "並びを初期に戻しました",
    "graph_detached": "グラフは別ウィンドウで表示中です（操作はこの画面で行えます）",
    "peaks_detached": "ピークの一覧は別ウィンドウで表示中です",
    "float_tip": "別ウィンドウにする・戻す（つまんで動かすと付け直せます）",
}
LABELS["part_peaks"] = LAYOUT_TEXTS["part_peaks"]

# 別ウィンドウで開ける部分と、メニュー「表示」の「…を別ウィンドウで開く」の並び（J §4.3.2。Qt と Web が同じ並び・
# 同じ項目のコード detach_<部分>）。名前は LABELS の part_<部分>
DETACH_PARTS = ("graph", "markers", "tab-trace", "tab-marker", "tab-view", "tab-source", "peaks")

# ---------------------------------------------------------------- キーの初期値（J §4.6）

# Qt の初期値。マーカーを動かすキーは Shift を付けると10点（Shift の分は自動で足す）
QT_KEY_DEFAULTS = {
    "open": "Ctrl+O", "save": "Ctrl+S", "export": "Ctrl+E", "png_save": "Ctrl+P", "png_copy": "Ctrl+Shift+C",
    "quit": "Ctrl+Q", "undo": "Ctrl+Z", "redo": "Ctrl+Y",
    "run_cont": "F5", "run_single": "F6", "stop": "F7", "pause": "Pause",
    "mode_sa": "Ctrl+1", "mode_tg": "Ctrl+2", "mode_s11": "Ctrl+3", "mode_sg": "Ctrl+4", "mode_nf": "Ctrl+5",
    "marker_peak": "P", "marker_peak_left": "[", "marker_peak_right": "]",
    "marker_left": "Left", "marker_right": "Right", "marker_here": "M",
    "marker_remove": "Del",
}
# Web の初期値（ブラウザが先に取るキーを避けた: F5 の再読み込み・Ctrl+数字・Ctrl+Shift+C の開発者ツール）
WEB_KEY_DEFAULTS = {
    "open": "Ctrl+O", "save": "Ctrl+S", "export": "Ctrl+E", "png_save": "Ctrl+P", "png_copy": "Ctrl+Alt+C",
    "undo": "Ctrl+Z", "redo": "Ctrl+Y",
    "run_cont": "R", "run_single": "S", "stop": "X", "pause": "Pause",
    "mode_sa": "Alt+1", "mode_tg": "Alt+2", "mode_s11": "Alt+3", "mode_sg": "Alt+4", "mode_nf": "Alt+5",
    "marker_peak": "P", "marker_peak_left": "[", "marker_peak_right": "]",
    "marker_left": "ArrowLeft", "marker_right": "ArrowRight", "marker_here": "M",
    "marker_remove": "Delete",
}
# グラフにいるときだけ効くキー（どちらの画面も同じ）
GRAPH_ONLY_KEYS = ("marker_peak", "marker_peak_left", "marker_peak_right", "marker_left", "marker_right",
                   "marker_here")
# Shift を付けたキーも同じ操作で、動く量が変わるもの
SHIFT_STEP_KEYS = ("marker_left", "marker_right")

# 割り当てられないキーの理由
KEY_RESERVED_REASONS = {
    "browser_close": "ブラウザがタブ・窓を閉じます",
    "browser_new": "ブラウザが新しいタブ・窓を開きます",
    "browser_reload": "ブラウザがページを読み直します",
    "browser_tab": "ブラウザがタブを切り替えます",
    "browser_tools": "ブラウザの開発者ツールが開きます",
    "browser_address": "ブラウザのアドレス欄へ移ります",
    "browser_history": "ブラウザが前・次のページへ移ります",
    "browser_full": "ブラウザが全画面を切り替えます",
    "app_move": "入力の欄の移動・窓を閉じるのに使います",
    "app_menu": "メニューを開くキーです（Alt と文字）",
    "modifier_only": "Ctrl・Shift・Alt だけでは割り当てられません。ほかのキーと一緒に押してください",
}
# ブラウザが先に取る・画面が使うキー（Web）。キー → 理由のコード。メニューバーのアクセスキー（Alt+文字: Qt と同じ）は
# メニューを開くのに使う。ただし Alt+D はブラウザが先に取る（アドレス欄）ので、その理由を後ろで上書きする
WEB_RESERVED_KEYS = {
    **{f"Alt+{c}": "app_menu" for c in "FEDSMAKCVOH"},
    "Ctrl+W": "browser_close", "Ctrl+Shift+W": "browser_close", "Alt+F4": "browser_close",
    "Ctrl+T": "browser_new", "Ctrl+N": "browser_new", "Ctrl+Shift+N": "browser_new", "Ctrl+Shift+T": "browser_new",
    "F5": "browser_reload", "Ctrl+R": "browser_reload", "Ctrl+Shift+R": "browser_reload", "Ctrl+F5": "browser_reload",
    "Ctrl+Tab": "browser_tab", "Ctrl+Shift+Tab": "browser_tab", "Ctrl+PageUp": "browser_tab",
    "Ctrl+PageDown": "browser_tab",
    **{f"Ctrl+{i}": "browser_tab" for i in range(1, 10)},
    "F12": "browser_tools", "Ctrl+Shift+I": "browser_tools", "Ctrl+Shift+J": "browser_tools",
    "Ctrl+Shift+C": "browser_tools",
    "Ctrl+L": "browser_address", "Alt+D": "browser_address", "F6": "browser_address",
    "Alt+ArrowLeft": "browser_history", "Alt+ArrowRight": "browser_history",
    "F11": "browser_full",
    "Tab": "app_move", "Shift+Tab": "app_move", "Escape": "app_move", "Enter": "app_move",
}
# Qt で割り当てられないキー（欄の移動・窓を閉じる・メニューのアクセスキー）
QT_RESERVED_KEYS = {
    "Tab": "app_move", "Shift+Tab": "app_move", "Backtab": "app_move", "Shift+Backtab": "app_move",
    "Esc": "app_move", "Return": "app_move", "Enter": "app_move",
    **{f"Alt+{c}": "app_menu" for c in "FEDSMAKCVOH"},
}

# ---------------------------------------------------------------- 使えない理由・失敗・メニューの項目

# 作った機能は「まだ使えません」の表から外す（U-08 の理由は作るまでのもの）
for _k in ("colors", "unit", "ext", "freq_offset", "settings_keys"):
    NOT_YET.pop(_k, None)

MENU_ITEMS.update({
    "presets": "プリセット…",
    "preset_save": "いまの状態をプリセットに保存…",
    "layout_reset": "並びを初期に戻す",
})

# 失敗の文（texts.ERRORS へ足す）
SETTINGS_ERRORS = {
    "preset_name_empty": "名前を入れてください",
    "preset_name_taken": "同じ名前のプリセットがあります（別の名前にするか、上書きを選んでください）",
    "preset_name_too_long": "名前が長すぎます",
    "unknown_preset": "そのプリセットはありません（消されたか、読めません）",
    "preset_unwritable": "プリセットを書けませんでした",
    "preset_unreadable": "プリセットを読めません（中身が壊れています）",
    "bad_color": "色を読めません（#rrggbb の形で入れてください）",
    "palette_name_empty": "名前を入れてください",
    "palette_name_taken": "同じ名前の配色があります（別の名前にするか、上書きを選んでください）",
    "palette_name_too_long": "名前が長すぎます",
    "unknown_palette": "その配色はありません（消されたか、読めません）",
    "palette_unwritable": "配色を書けませんでした",
    "palette_file_unreadable": "配色のファイルを読めません（pyGigaSt で書き出した配色のファイルではありません）",
}

REASON_TEXTS.update({
    "zero_span_offset": "ゼロスパンの横軸は周波数ではないので、周波数オフセットは横軸に出ません",
    "part_floating": "別ウィンドウで表示中です",
})

TEMPLATES.update({
    "key_row_default": "初期値 {key}",
    "preset_summary": "{mode}・{start}〜{stop}・RBW {rbw}",
})
