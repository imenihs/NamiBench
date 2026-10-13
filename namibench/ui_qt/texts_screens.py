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
POLARITY_TIPS = {"peak": "周りより盛り上がった点（山）を探す", "dip": "周りよりへこんだ点（谷）を探す"}
# 探すボタンの語（キーはメニューの項目のコード）。「次に高い」だけは −ピークで「次に深い」（逆の意味になるため）
SEARCH_BUTTONS = {
    "peak": {"marker_peak": "ピーク", "marker_peak_left": "◀ 次のピーク", "marker_peak_right": "次のピーク ▶",
             "marker_peak_lower": "次に高い", "marker_add": "マーカー追加"},
    "dip": {"marker_peak": "ピーク", "marker_peak_left": "◀ 次のピーク", "marker_peak_right": "次のピーク ▶",
            "marker_peak_lower": "次に深い", "marker_add": "マーカー追加"},
}
SEARCH_TIPS = {
    "peak": {"marker_peak": "選んでいるマーカーを最大の点へ（無ければ M を置く）", "marker_peak_left": "左の次のピークへ",
             "marker_peak_right": "右の次のピークへ", "marker_peak_lower": "いまより低いピークのうち最も高いものへ",
             "marker_add": "新しい M をピークに置く"},
    "dip": {"marker_peak": "選んでいるマーカーを一番深い点へ（無ければ M を置く）", "marker_peak_left": "左の次の谷へ",
            "marker_peak_right": "右の次の谷へ", "marker_peak_lower": "いまより浅い谷のうち最も深いものへ",
            "marker_add": "新しい M を谷に置く"},
}
# −ピークのときのメニューの項目の名前（＋ピークのときは MENU_ITEMS のまま: G §3.8）
DIP_MENU_ITEMS = {
    "marker_peak": "選んでいるマーカーを −ピークへ", "marker_peak_left": "次の −ピーク（左）",
    "marker_peak_right": "次の −ピーク（右）", "marker_peak_lower": "次に深い −ピーク", "marker_add": "マーカーを追加（−ピークに M）",
}

# PNG の配色の選び方（I §3.6）。並びは選ぶ欄の並び（Qt・Web）。"screen" は画面で使っている配色（変えた色を含む）
PNG_PALETTES = {"screen": "ユーザー指定", "light": "ライト", "dark": "ダーク"}
# 選択肢の意味（ツールチップ。名前に説明を付けない: U-24）
PNG_PALETTE_TIPS = {"screen": "画面と同じ色（自分で変えた色を含む）", "light": "ライトの初期値の色",
                    "dark": "ダークの初期値の色"}

REASON_TEXTS.update({
    "track_p_only": "ピーク追従は P（ピーク）のマーカーだけが持てます（M は周波数を固定）",
    "needs_connection": "機器に繋ぐと使えます",
    "taking_reference": "基準を取っている間は使えません",
    "select_reference": "一覧から基準を選んでください",
    "no_reference_selected": "選んでいる基準がありません",
    "count_only": "回数は平均・最大保持・最小保持で使います",
    "average_type_only": "平均の種類は動作が「平均」のときに使います",
    "after_only": "N 回の後の動きは、回数を持つ平均・最大保持・最小保持で使います",
    "vector_only": "ベクトル平均は位相を測れる機器だけで使えます",
    # ピークの一覧・上位 N 個は、電力のスペクトルでは谷を探さない（P §9.7。マーカーの谷は探せる）
    "dips_on_power": "電力のスペクトルでは谷を探しません",
})

LABELS.update({
    # ---- グラフの軸（ツールチップは軸の上の操作: U-12）
    "axis_x_tip": "ホイール: カーソルの位置を中心に拡大・縮小\nグラフの中を横にドラッグ: その範囲を Start/Stop に\n"
                  "グラフの中をクリック: 近いトレースに M を置く\n右クリック: この位置への操作（Center に・マーカー・REF に）",
    "axis_y_tip": "ホイール: REF レベルを1目盛ずつ\nCtrl+ホイール: dB/div を1段\nドラッグ: REF レベルを連続で動かす",
    "level_no_ref": "レベル（基準なし・受けた電力）",
    # ---- マーカー表（グラフの下）。ピーク・谷を探すボタンの語は SEARCH_BUTTONS（山／谷の選択で変わる）
    "btn_copy_tip": "表をタブ区切りでクリップボードへ",
    # 谷に置いたマーカーの注意の列（N dB 帯域幅は谷から上がる点を探す・追従は谷を追う: G §3.8）
    "row_dip": "−ピーク",
    "row_ops": "操作",
    "ref_combo_tip": "デルタの基準（この行に Δ周波数・Δ値を出す相手）",
    "row_center": "この周波数を Center に",
    "row_ref": "この値を REF レベルに",
    "row_remove": "{m} を消す",
    "row_remove_cursor": "このカーソルを消す",
    "row_remove_function": "この機能を止める",
    "cell_edit_tip": "ダブルクリックで直せます（Enter で決定・Esc でやめる）",
    "row_unset": "{m} を外す",
    "row_track": "追従",
    "band_center": "中心 {v}",
    "band_width": "幅 {v}",
    "row_bw": "{m} {n}dB 帯域幅",
    "row_sf": "{m} シェープファクタ",
    "row_th": "{m} しきい値 {level} {side}",
    "marker_clear_confirm": "マーカー {n} 本（オフセット・機能の設定を含む）を消します。",
    # ---- マーカーのタブ
    "trace_n": "トレース {n}",
    "delta_ref": "デルタの基準",
    "to_ref_level": "REF レベルに",
    "erase": "消す",
    "offset_value_tip": "周波数は 10M・500k のように単位付き、レベルは dB の数",
    "bad_offset_value": "オフセットの値が読めません（例: 10M・500k・3）",
    "second_n": "シェープファクタ",
    "threshold_cross": "しきい値との交点",
    # マーカーの機能の結果をグラフに出す薄い線（G §3.6）
    "function_lines": "交点の線をグラフに出す",
    "function_lines_tip": "N dB 帯域幅・しきい値との交点のレベル（横線）と交点の周波数（縦線）を、マーカーの色の薄い点線で出す。"
                          "掃引ごとに動く（つまんで動かす XY カーソルとは別）",
    # dB の打てるドロップダウンで範囲の外の値を打ったとき（G §3.6）
    "db_range_note": "{lo}〜{hi} {unit} の値を入れてください",
    # ピークの一覧の基準（P §9.7）: 電力はノイズフロアから、基準との比（TG・S11）は最大から。欄の名前とツールチップ
    "floor_tip": "ノイズフロアよりこれだけ高い山を並べる",
    "max_tip_peak": "最大からこれだけ下までの山を並べる",
    "max_tip_dip": "最大よりこれだけ低い谷を並べる",
    "find": "探す",
    "col_freq_mhz": "周波数 (MHz)",
    "col_height_db": "高さ (dB)",
    "col_below_max_db": "最大からの差 (dB)",
    "count_suffix": " 個",
    "place_p_after": "に P を置く",
    "no_marker_selected": "マーカーを選んでいません",
    "selected_text": "{id}（{kind}・トレース {trace}）  {value}",
    "marker_snapped": "{freq} の点に置きました（トレースの点に載る）",
    "peaks_found": "{n} 個",
    "peaks_found_floor": "{n} 個・ノイズフロア {level} {unit}",
    "peaks_found_max": "{n} 個・最大 {level} {unit}",
    # ---- トレース・掃引のタブ
    "no_count": "回数なし",
    "bad_count": "回数は 1〜10000 の整数で入れてください（回数なしは一覧から選ぶか、欄を空にする）",
    "times_suffix": " 回",
    "clear_tip": "このトレースの蓄積を捨てる",
    "default": "既定",
    "default_with": "既定（{type}）",
    "count_n": "回数 N",
    "no_value_paren": "（値なし）",
    "no_value": "値なし",
    "freq_offset": "周波数オフセット",
    "no_files_open": "開いたファイルはありません",
    "close_file_tip": "このファイルの参照トレースをグラフから外す（ファイルは消さない）",
    "file_trace_check": "#{number}  {unit}  {points} 点",
    "file_unit_differs": "単位が縦軸（{axis}）と違う（{unit}）ので重ねません",
    # ---- 表示のタブ
    "draw_style": "描き方",
    "draw_line": "線",
    "draw_fill": "塗りつぶし",
    "unit_auto": "自動（電力は dBm・比は dB）",
    "unit_tip": "縦軸の単位",
    "ext_corr": "外部補正",
    "png_group": "PNG",
    "png_size": "大きさ",
    "png_memo_placeholder": "画像の下端に焼き込む1行",
    "change": "変える…",
    "png_dir": "保存先",
    "png_dir_title": "PNG の保存先",
    "colors_bandplan": "配色・バンドプラン",
    "reset_colors": "ライト・ダークの初期値に戻す…",
    "show_bandplan": "バンドプランを表示",
    # ---- 信号源のタブ
    "sg_group": "SG",
    "take_name_placeholder": "空なら「TG 日時」",
    "average": "平均",
    "s11_open": "S11: オープンを測る",
    "s11_short": "S11: ショートを測る",
    "use": "使う",
    "not_use": "使わない",
    "rename_dots": "名前…",
    "erase_dots": "消す…",
    # SG 周波数の欄のそばの状態。出していないときは、なぜ出ていないかを原因ごとに言う（U-08。打った値が出ない理由）
    "sg_off_mode": "SG モードではないので出力していません",
    "sg_off_stopped": "掃引を止めているので出力していません",
    "ref_selected": "選んでいる基準: {name}（{when}）",
    "ref_none": "ノーマライズ: 未取得",
    "new_name": "新しい名前",
    "ref_delete_title": "基準を消す",
    "ref_delete_confirm": "基準「{name}」を消します。取り直すまで戻せません。",
    "ref_delete_in_use": "\nいま使っている基準なので、TG の表示は受けた電力（dBm）に戻ります。",
    # ---- ファイル（Qt の窓。Web はダウンロード・アップロード）
    "filter_h5": "NamiBench の保存ファイル (*.h5);;すべて (*)",
    "filter_csv": "CSV (*.csv);;すべて (*)",
    "filter_txt": "テキスト (*.txt);;すべて (*)",
    "open_dialog": "開く",
    "export_dialog": "CSV に書き出す",
    "png_save_failed": "PNG を保存できませんでした: {why}",
    "png_copy_failed": "PNG をコピーできませんでした: {why}",
    "record_started": "記録を始めました",
    "record_stopped": "記録を止めました。「記録をまとめる…」で保存ファイルにします",
    "discard_title": "記録を捨てる",
    "discard_confirm": "作業ファイルを消します。{n} 回の掃引の記録が失われ、戻せません。",
    "discarded": "まとめ終わっていない記録を捨てました",
    "export_trace_item": "トレース {n}（{action}・{unit}）",
    "export_file_item": "{name} #{number}（{unit}）",
    "export_none": "書き出せるトレースがありません（掃引するか、ファイルを開いてください）",
    "export_range": "周波数の範囲",
    "export_view": "いまの表示（{start}〜{stop}）",
    "export_custom": "数値で指定",
    "export_all": "トレースの全部の点",
    "export_ok": "書き出す…",
    "unfinished_title": "まとめ終わっていない記録があります",
    "unfinished_note": "前回、保存ファイルへまとめる前に終わった記録です。まとめるか、捨てるかを選んでください。",
    "unfinished_row": "{started}  {n} 回の掃引",
    "unfinished_minutes": "・{m} 分",
    "unfinished_unreadable": "・読めない所あり（{why}）",
    "unfinished_finish": "まとめる…",
    "unfinished_discard": "捨てる…",
    "unfinished_later": "あとで",
    # ---- 閉じる（H §4.1・U-18: 何が失われるかを言って聞く）
    "close_title": "閉じる",
    "close_confirm": "閉じると次のようになります。閉じますか？",
    "close_lost_task": "・{task}: やめます（{what}）",
    "close_lost_record": "・記録: 保存ファイルにまとめていません。作業ファイルは残り、次の起動でまとめるか聞きます",
    # ---- 状態バー
    "open_folder": "フォルダを開く",
})
