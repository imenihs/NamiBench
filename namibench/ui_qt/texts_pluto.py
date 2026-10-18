"""PlutoSDR（設計Q §7）の語。texts.py が取り込み、/api/v1/texts で Web へも同じ表を配る。Qt を import しない。"""
from __future__ import annotations

from .texts_ui import TEMPLATES

PLUTO_LABELS = {
    "group": "受信（PlutoSDR）",
    "gain": "受信の利得",
    "auto": "自動",
    "gain_tip": "利得を変更すると、トレースをクリアします。REF レベルとは連動しません。"
                "使用可能な範囲は周波数で変わります（一覧は現在の範囲で使用可能な値）。",
    "fit_gain": "利得を自動調整",
    "fit_gain_tip": "現在の条件で、表示中の信号が過大入力にならない最大の利得を選択します。選択後、トレースをクリアします。",
    "fit_needs_manual": "自動の間は使用できません。利得を手動に設定してください",
    "fit_running": "利得を調整中",
    "tx_group": "送信（PlutoSDR）",
    "tx_atten": "送信の減衰",
    "tx_atten_tip": "SG・TG・S11 の出力のレベル（0dB が最大。0.25dB ステップ）。SG・TG・S11 の間に変更すると、トレースを"
                   "クリアします。",
    "not_pluto": "PlutoSDR を接続すると使用できます",
    # TG 高調波の平均除去（設計Q §7.7。名前はユーザー指定）
    "tg_harmonic": "TG 高調波の平均除去",
    "tg_harmonic_not_tg": "TG・S11 モードでのみ使用します",
    # 受信の利得の補正（設計Q §7.6）
    "gain_cal_none": "利得の補正なし",
    "gain_cal_none_tip": "この PlutoSDR の受信の利得の補正がありません。利得を変更すると、同じ信号の値が変わります。"
                         "校正の「PlutoSDR の利得の補正…」で測定してください。",
    # TG で基準を取り始めてから、基準を捨てるか TG を出るまで（受信の利得と送信の減衰: 設計Q §7.9）
    "tg_locked": "TG で基準を取得した後は、基準を破棄するか TG モードを終了するまで変更できません",
    # S11 でオープンを測った時点から、基準を捨てるか S11 を出るまで（受信の利得と送信の減衰: 設計Q §7.8）
    "s11_locked": "S11 でオープンを測定した後は、基準を破棄するか S11 モードを終了するまで変更できません",
    "calibrating": "利得の補正を測定中",
    # 進捗の行: TG の基準の前の利得の調整（設計Q §7.9。同じ作業の最初の段）
    "ref_fit_task": "基準の取得前に受信の利得を調整中",
}

# TG 高調波の平均除去の選択肢（設計Q §7.7。キーは回数: 1 はなし。並びは なし → 4回 → 8回）
TG_HARMONIC_CHOICES = {
    "1": "なし",
    "4": "4回",
    "8": "8回",
}

PLUTO_ERRORS = {
    "plutosdr_dll_not_found": "PlutoSDR の DLL（libiio.dll）が見つかりません",
    "plutosdr_not_connected": "PlutoSDR が未接続です",
    "reference_wide_fft": "この機器の基準は現在の条件で取得します",
    "plutosdr_tg_conditions_locked": "TG で基準を取得した後は、基準を破棄するか TG モードを終了するまで、受信の利得と"
                                     "送信の減衰を変更できません",
    "plutosdr_s11_conditions_locked": "S11 でオープンを測定した後は、基準を破棄するか S11 モードを終了するまで、受信の利得と"
                                      "送信の減衰を変更できません",
    # 「オープンを測定」の前の利得の調整（設計Q §7.8 の決定②）。調整できなければオープンは測らない
    "s11_open_fit_failed": "受信の利得を調整できなかったため、オープンを測定しませんでした",
    "s11_open_overload": "最小の受信の利得でも過大入力のため、オープンを測定しませんでした。送信の減衰で出力を"
                         "下げてください",
    # TG の「基準を取得」の前の利得の調整（設計Q §7.9）。調整できなければ基準は取らない
    "tg_ref_fit_failed": "受信の利得を調整できなかったため、基準を取得しませんでした",
    "tg_ref_overload": "最小の受信の利得でも過大入力のため、基準を取得しませんでした。送信の減衰で出力を下げてください",
    # 基準の補正（設計Q §7.10）
    "plutosdr_xo_busy": "掃引中は基準の補正を変更できません。掃引を停止してから変更してください",
    "plutosdr_xo_unsupported": "この PlutoSDR は基準の補正（xo_correction）に対応していません",
}

PLUTO_NOTICES = {
    "plutosdr_setting_failed": "PlutoSDR に利得・送信の減衰を設定できませんでした（測定は継続します）",
    "s11_open_gain_fitted": "オープンの測定前に受信の利得を調整しました",
    "tg_ref_gain_fitted": "基準の取得前に受信の利得を調整しました",
    "plutosdr_xo_refused": "PlutoSDR の基準の補正が機器の範囲外のため、書き込みませんでした（機器の基準の値で測定します）",
}

PLUTO_SETTINGS = {
    "plutosdr_dll": "libiio.dll",
    "plutosdr_32bit": "64 ビット版の libiio が必要です",
    "plutosdr_choose_title": "libiio.dll（64 ビット版）のあるフォルダ",
    "plutosdr_uri": "URI",
    "plutosdr_uri_tip": "USB の RNDIS で接続する PlutoSDR は ip:192.168.2.1 です。USB で検出される機器は一覧に自動で表示されます。",
    # 基準の補正（設計Q §7.10。名前はユーザー指定）
    "plutosdr_xo": "基準の補正",
    "plutosdr_xo_tip": "基準の水晶発振子（40MHz）の周波数偏差（ppm）",
}

TEMPLATES.update({
    "pluto_gain_used": "この範囲では {db} dB を使用します",
    "pluto_tx_on": "出力中: {what}",
    "s11_open_gain_fitted": "オープンの測定前に受信の利得を {gain} dB に調整しました（変更前 {before}）",
    "tg_ref_gain_fitted": "基準の取得前に受信の利得を {gain} dB に調整しました（変更前 {before}）",
    "ref_taken_fitted": "受信の利得を {gain} dB に調整し（変更前 {before}）、基準を取得しました: {name}",
    "ref_open_measured_fitted": "受信の利得を {gain} dB に調整し（変更前 {before}）、オープンを測定しました。ショートを接続して"
                                "「ショートを測定」を押してください",
    "status_cal_pluto": "補正: PlutoSDR 未校正・単位 {unit}",
    "status_cal_pluto_gain": "補正: PlutoSDR 利得の補正 {at}・単位 {unit}",
    # 基準の補正（設計Q §7.10）: 機器から読み戻した基準の周波数・繋いでいないとき・繋いだときに書けなかった補正
    "pluto_xo_hz": "機器の基準 {hz} Hz",
    "pluto_xo_next": "{serial} の接続時に書き込みます",
    "pluto_xo_refused": "{ppm} ppm は機器の範囲（{min}〜{max} ppm）外のため、書き込みませんでした",
})
