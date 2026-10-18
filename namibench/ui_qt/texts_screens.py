"""画面の部品の語のうち、右のタブ・マーカー表・ファイルの窓・閉じる確認などの語。

もとは Qt の画面のコードに直に書いていた語を、ここの表に移した（J §5: Qt と Web は同じ語の表から作る。
J §4.9: 画面の語は表だけに置き、使わない語が入っていないことを試験で確かめる）。
LABELS（texts_labels.py）と REASON_TEXTS・TEMPLATES（texts_ui.py）へ足すので、Web へも同じ表で配る
（GET /api/v1/texts）。`{名前}` は差し込み（texts.tpl・GSTexts.w と同じ置き換え）。Qt を import しない。
"""
from __future__ import annotations

from .texts_labels import LABELS
from .texts_ui import REASON_TEXTS

# マーカー表の列（表・コピー・Web の表で同じ。PNG の表の列は画像の配置: core/report_layout.py）
MARKER_COLUMNS = ("番号", "トレース", "周波数 (MHz)", "値", "基準", "Δ周波数 (MHz)", "Δ値 (dB)", "注意")

# マーカーの種類（P はピークに付いていく、M は手で置いた位置）
MARKER_KINDS = {"P": "ピーク", "M": "手動"}

# 探すもの（G §3.8）。マーカー表の上のボタンの並びの頭の切り替え。ピークの一覧とマーカーのピーク探しが同じ選択を使う
# 名前は「＋ピーク／−ピーク」（2026-10-03 ユーザー決定。「山／谷」だと、谷のとき切り替えの「谷」と探すボタンの「谷」が
# 隣に並んで見分けにくかった）。探すボタンの名前は向きによらず「ピーク」（向きは切り替えが示す）
POLARITIES = {"peak": "＋ピーク", "dip": "−ピーク"}
POLARITY_TIPS = {"peak": "極大点を検索", "dip": "極小点を検索"}
# 探すボタンの語（キーはメニューの項目のコード）。レベル順のネクストピークだけは −ピークで「深い」（逆の意味になるため）
SEARCH_BUTTONS = {
    "peak": {"marker_peak": "ピーク", "marker_peak_left": "◀ ネクストピーク", "marker_peak_right": "ネクストピーク ▶",
             "marker_peak_lower": "ネクストピーク（レベル順・高い）", "marker_add": "マーカー追加"},
    "dip": {"marker_peak": "ピーク", "marker_peak_left": "◀ ネクストピーク", "marker_peak_right": "ネクストピーク ▶",
            "marker_peak_lower": "ネクストピーク（レベル順・深い）", "marker_add": "マーカー追加"},
}
SEARCH_TIPS = {
    "peak": {"marker_peak": "選択中のマーカーを最大点に移動します。選択中のマーカーがないときは M を配置します。",
             "marker_peak_left": "左のネクストピークに移動", "marker_peak_right": "右のネクストピークに移動",
             "marker_peak_lower": "現在より低いピークのうち最も高いピークに移動", "marker_add": "新しい M をピークに配置"},
    "dip": {"marker_peak": "選択中のマーカーを最も深い点に移動します。選択中のマーカーがないときは M を配置します。",
            "marker_peak_left": "左の −ピークに移動", "marker_peak_right": "右の −ピークに移動",
            "marker_peak_lower": "現在より浅い −ピークのうち最も深い −ピークに移動", "marker_add": "新しい M を −ピークに配置"},
}
# −ピークのときのメニューの項目の名前（＋ピークのときは MENU_ITEMS のまま: G §3.8）
DIP_MENU_ITEMS = {
    "marker_peak": "−ピークサーチ", "marker_peak_left": "ネクスト −ピーク（左）",
    "marker_peak_right": "ネクスト −ピーク（右）", "marker_peak_lower": "ネクストピーク（レベル順・深い）", "marker_add": "マーカーを追加",
}

# PNG の配色の選び方（I §3.6）。並びは選ぶ欄の並び（Qt・Web）。"screen" は画面で使っている配色（変えた色を含む）
PNG_PALETTES = {"screen": "ユーザー指定", "light": "ライト", "dark": "ダーク"}
# 選択肢の意味（ツールチップ。名前に説明を付けない: U-24）
PNG_PALETTE_TIPS = {"screen": "画面と同じ色（変更した色を含む）", "light": "ライトの初期値の色",
                    "dark": "ダークの初期値の色"}

REASON_TEXTS.update({
    "track_p_only": "ピークトラックは P のマーカーでのみ使用できます",
    "needs_connection": "機器を接続すると使用できます",
    "taking_reference": "基準の取得中は使用できません",
    "select_reference": "一覧から基準を選択してください",
    "no_reference_selected": "基準が未選択です",
    "count_only": "回数は平均・マックスホールド・ミニマムホールドで使用します",
    "average_type_only": "平均の種類は動作が「平均」のときに使用します",
    "after_only": "N 回到達後の動作は、回数を設定した平均・マックスホールド・ミニマムホールドで使用します",
    "vector_only": "ベクトル平均は位相を測定できる機器でのみ使用できます",
    # ピークの一覧・上位 N は、電力のスペクトルでは谷を探さない（P §9.7。マーカーの谷は探せる）
    "dips_on_power": "電力のスペクトルでは −ピークを検索できません",
})

LABELS.update({
    # ---- グラフの軸（ツールチップは軸の上の操作: U-12）
    "axis_x_tip": "ホイール: カーソルの位置を中心に拡大・縮小\nグラフの中を横にドラッグ: その範囲を Start/Stop に\n"
                  "グラフの中をクリック: 近いトレースに M を配置\n右クリック: この位置への操作（Center に・マーカー・REF に）",
    "axis_y_tip": "ホイール: REF レベルを1目盛ずつ\nCtrl+ホイール: dB/div を変更\nドラッグ: REF レベルを連続で変更",
    "level_no_ref": "レベル（基準なし・受信電力）",
    # ---- マーカー表（グラフの下）。ピーク・谷を探すボタンの語は SEARCH_BUTTONS（山／谷の選択で変わる）
    "btn_copy_tip": "表をタブ区切りでクリップボードにコピー",
    # 谷に置いたマーカーの注意の列（N dB 帯域幅は谷から上がる点を探す・追従は谷を追う: G §3.8）
    "row_dip": "−ピーク",
    "row_ops": "操作",
    "ref_combo_tip": "基準マーカー",
    "row_center": "この周波数を Center に",
    "row_ref": "この値を REF レベルに",
    "row_remove": "{m} を削除",
    "row_remove_cursor": "このカーソルを削除",
    "row_remove_function": "この機能を解除",
    "cell_edit_tip": "ダブルクリックで編集（Enter で確定・Esc でキャンセル）",
    "row_unset": "{m} を解除",
    "row_track": "トラック",
    "band_center": "中心 {v}",
    "band_width": "幅 {v}",
    "row_bw": "{m} {n}dB 帯域幅",
    "row_sf": "{m} シェープファクタ",
    "row_th": "{m} しきい値 {level} {side}",
    "marker_clear_confirm": "すべてのマーカー（{n}）を、オフセット・機能の設定とともに削除します。",
    # ---- マーカーのタブ
    "trace_n": "トレース {n}",
    "delta_ref": "基準マーカー",
    "to_ref_level": "REF レベルに",
    "erase": "削除",
    "offset_value_tip": "周波数は 10M・500k のように単位付き、レベルは dB の値",
    "bad_offset_value": "オフセットの値の形式が正しくありません（例: 10M・500k・3）",
    "second_n": "シェープファクタ",
    "threshold_cross": "しきい値との交点",
    # マーカーの機能の結果をグラフに出す薄い線（G §3.6）
    "function_lines": "交点の線をグラフに出す",
    "function_lines_tip": "N dB 帯域幅・しきい値との交点のレベル（横線）と交点の周波数（縦線）を、マーカーの色の薄い点線で"
                          "表示します。",
    # dB の打てるドロップダウンで範囲の外の値を打ったとき（G §3.6）
    "db_range_note": "{lo}〜{hi} {unit} の値を入力してください",
    # ピークの一覧の基準（P §9.7）: 電力はノイズフロアから、基準との比（TG・S11）は最大から。欄の名前とツールチップ
    "floor_tip": "ノイズフロアからの高さがこの値以上のピークを表示します。",
    "max_tip_peak": "最大からの差がこの値以内のピークを表示します。",
    "max_tip_dip": "最大からの差がこの値以上の −ピークを表示します。",
    "find": "検索",
    "col_freq_mhz": "周波数 (MHz)",
    "col_height_db": "高さ (dB)",
    "col_below_max_db": "最大からの差 (dB)",
    "count_suffix": "",
    "place_p_after": "に P を配置",
    "no_marker_selected": "マーカーが未選択です",
    "selected_text": "{id}（{kind}・トレース {trace}）  {value}",
    "marker_snapped": "{freq} に配置しました（トレースの点に合わせました）",
    "peaks_found": "検出数 {n}",
    "peaks_found_floor": "検出数 {n}・ノイズフロア {level} {unit}",
    "peaks_found_max": "検出数 {n}・最大 {level} {unit}",
    # ---- トレース・掃引のタブ
    "no_count": "回数なし",
    "bad_count": "回数は 1〜10000 の整数で入力してください。回数なしにするときは、一覧から「回数なし」を選択するか、欄を空にしてください",
    "times_suffix": " 回",
    "clear_tip": "このトレースをクリア",
    "default": "既定",
    "default_with": "既定（{type}）",
    "count_n": "回数 N",
    "no_value_paren": "（値なし）",
    "no_value": "値なし",
    "freq_offset": "周波数オフセット",
    "no_files_open": "開いたファイルはありません",
    "close_file_tip": "このファイルの参照トレースを閉じる（ファイルは削除されません）",
    "file_trace_check": "#{number}  {unit}  {points} 点",
    "file_unit_differs": "単位が縦軸（{axis}）と異なるため（{unit}）、表示しません",
    # ---- 表示のタブ
    "draw_style": "描画方法",
    "draw_line": "線",
    "draw_fill": "塗りつぶし",
    "unit_auto": "自動（電力は dBm・比は dB）",
    "unit_tip": "縦軸の単位",
    "ext_corr": "外部補正",
    "png_group": "PNG",
    "png_size": "サイズ",
    "png_memo_placeholder": "画像の下端に焼き込む1行",
    "change": "変更…",
    "png_dir": "保存先",
    "png_dir_title": "PNG の保存先",
    "colors_bandplan": "配色・バンドプラン",
    "reset_colors": "ライト・ダークの初期値に戻す…",
    "show_bandplan": "バンドプランを表示",
    # ---- 信号源のタブ
    "sg_group": "SG",
    "take_name_placeholder": "空なら「TG 日時」",
    "average": "平均",
    "s11_open": "S11: オープンを測定",
    "s11_short": "S11: ショートを測定",
    "use": "使用",
    "not_use": "使用しない",
    "rename_dots": "名前の変更…",
    "erase_dots": "削除…",
    # SG 周波数の欄のそばの状態。出していないときは、なぜ出ていないかを原因ごとに言う（U-08。打った値が出ない理由）
    "sg_off_mode": "出力停止中（SG モード以外）",
    "sg_off_stopped": "出力停止中（掃引停止中）",
    "ref_selected": "選択中の基準: {name}（{when}）",
    "ref_none": "ノーマライズ: 未取得",
    "new_name": "新しい名前",
    "ref_delete_title": "基準の削除",
    "ref_delete_confirm": "基準「{name}」を削除します。元に戻せません。",
    "ref_delete_in_use": "\n使用中の基準のため、TG の表示は受信電力（dBm）に戻ります。",
    # ---- ファイル（Qt の窓。Web はダウンロード・アップロード）
    "filter_h5": "NamiBench の保存ファイル (*.h5);;すべて (*)",
    "filter_csv": "CSV (*.csv);;すべて (*)",
    "filter_txt": "テキスト (*.txt);;すべて (*)",
    "open_dialog": "開く",
    "export_dialog": "CSV に書き出す",
    "png_save_failed": "PNG を保存できませんでした: {why}",
    "png_copy_failed": "PNG をコピーできませんでした: {why}",
    "record_started": "記録を開始しました",
    "record_stopped": "記録を停止しました。「記録を保存…」で保存ファイルにしてください",
    "discard_title": "記録の破棄",
    "discard_confirm": "作業ファイルを削除します。{n} 回の掃引の記録が失われ、元に戻せません。",
    "discarded": "未保存の記録を破棄しました",
    "export_trace_item": "トレース {n}（{action}・{unit}）",
    "export_file_item": "{name} #{number}（{unit}）",
    "export_none": "書き出せるトレースがありません。掃引するか、ファイルを開いてください",
    "export_range": "周波数の範囲",
    "export_view": "現在の表示（{start}〜{stop}）",
    "export_custom": "数値で指定",
    "export_all": "トレースのすべての点",
    "export_ok": "書き出す…",
    "unfinished_title": "未保存の記録",
    "unfinished_note": "前回、保存ファイルにする前に終了した記録です。保存するか、破棄するかを選択してください。",
    "unfinished_row": "{started}  {n} 回の掃引",
    "unfinished_minutes": "・{m} 分",
    "unfinished_unreadable": "・読み込めない部分あり（{why}）",
    "unfinished_finish": "保存…",
    "unfinished_discard": "破棄…",
    "unfinished_later": "あとで",
    # ---- 閉じる（H §4.1・U-18: 何が失われるかを言って聞く）
    "close_title": "閉じる",
    "close_confirm": "閉じると次のようになります。閉じますか？",
    "close_lost_task": "・{task}: 中断します（{what}）",
    "close_lost_record": "・記録: 未保存です。作業ファイルは残り、次回の起動時に保存するかを確認します",
    # ---- 状態バー
    "open_folder": "フォルダを開く",
})
