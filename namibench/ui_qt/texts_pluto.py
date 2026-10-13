"""PlutoSDR（設計Q §7）の語。texts.py が取り込み、/api/v1/texts で Web へも同じ表を配る。Qt を import しない。"""
from __future__ import annotations

from .texts_ui import TEMPLATES

PLUTO_LABELS = {
    "group": "受信（PlutoSDR）",
    "gain": "受信の利得",
    "auto": "自動",
    "gain_tip": "利得を変えると値の意味が変わるので、トレースの蓄積を捨てます。REF レベルとは連動しません。"
                "使える範囲は周波数で変わります（一覧はいまの範囲で使える値）",
    "fit_gain": "利得を合わせる",
    "fit_gain_tip": "いまの条件で、見えている信号が振り切れないいちばん大きい利得を選びます。選んだらトレースの蓄積を捨てます",
    "fit_needs_manual": "自動の間は使えません（利得を手動の値にしてから押します）",
    "fit_running": "利得を合わせています",
    "tx_group": "送信（PlutoSDR）",
    "tx_atten": "送信の減衰",
    "tx_atten_tip": "SG・TG・S11 の出力のレベル（0dB がいちばん大きい。0.25dB 刻み）。SG・TG・S11 の間に変えるとトレースの"
                   "蓄積を捨てます",
    "not_pluto": "PlutoSDR に繋ぐと使えます",
    # TG 高調波の平均除去（設計Q §7.7。名前はユーザー指定）
    "tg_harmonic": "TG 高調波の平均除去",
    "tg_harmonic_tip": "1000MHz より下の位置で、送信と受信の局部発振器を合わせ直して読むのを回数分くり返し、電力を平均します"
                       "（送信の高調波で値が揺れるのを減らします。掃引の時間は延びます）。回数を変えてもトレースの蓄積と"
                       "基準はそのまま使います",
    "tg_harmonic_not_tg": "TG・S11 のときだけ使います",
    # 受信の利得の補正（設計Q §7.6）
    "gain_cal_none": "利得の補正なし",
    "gain_cal_none_tip": "この PlutoSDR の受信の利得の補正がありません。利得を変えると、同じ信号の値が動きます"
                         "（校正の「PlutoSDR の利得の補正…」で測ります）",
    # TG で基準を取り始めてから、基準を捨てるか TG を出るまで（受信の利得と送信の減衰: 設計Q §7.9）
    "tg_locked": "TG で基準を取り始めてから、基準を捨てるか TG を出るまでは変えられません（基準と同じ条件で測ります）",
    # S11 でオープンを測った時点から、基準を捨てるか S11 を出るまで（受信の利得と送信の減衰: 設計Q §7.8）
    "s11_locked": "S11 でオープンを測ってから、基準を捨てるか S11 を出るまでは変えられません（オープン・ショートと同じ"
                  "条件で測ります）",
    "calibrating": "利得の補正を測っています",
    # 進み具合の行: TG の基準の前の利得合わせ（設計Q §7.9。同じ作業の最初の段）
    "ref_fit_task": "基準の前に受信の利得を合わせています",
}

# TG 高調波の平均除去の選択肢（設計Q §7.7。キーは回数: 1 はなし。並びは なし → 4回 → 8回）
TG_HARMONIC_CHOICES = {
    "1": "なし",
    "4": "4回",
    "8": "8回",
}

PLUTO_ERRORS = {
    "plutosdr_dll_not_found": "PlutoSDR の DLL（libiio.dll）が見つかりません",
    "plutosdr_not_connected": "PlutoSDR に繋がっていません",
    "reference_wide_fft": "この機器の基準はいまの条件で取ります（RBW が違う基準は当たらないため）",
    "plutosdr_tg_conditions_locked": "TG で基準を取り始めてから、基準を捨てるか TG を出るまでは受信の利得と送信の減衰を"
                                     "変えられません（基準と同じ条件で測ります）",
    "plutosdr_s11_conditions_locked": "S11 でオープンを測ってから、基準を捨てるか S11 を出るまでは受信の利得と送信の減衰を"
                                      "変えられません（オープン・ショートと同じ条件で測ります）",
    # 「オープンを測る」の前の利得合わせ（設計Q §7.8 の決定②）。合わせられなければオープンは測らない
    "s11_open_fit_failed": "受信の利得を合わせられなかったので、オープンを測っていません",
    "s11_open_overload": "いちばん小さい受信の利得でも振り切れるので、オープンを測っていません（送信の減衰で出力を"
                         "下げてください）",
    # TG の「基準を取る」の前の利得合わせ（設計Q §7.9）。合わせられなければ基準は取らない
    "tg_ref_fit_failed": "受信の利得を合わせられなかったので、基準を取っていません",
    "tg_ref_overload": "いちばん小さい受信の利得でも振り切れるので、基準を取っていません（送信の減衰で出力を下げてください）",
    # 基準の補正（設計Q §7.10）
    "plutosdr_xo_busy": "掃引している間は基準の補正を変えられません（掃引を止めてから変えます）",
    "plutosdr_xo_unsupported": "この PlutoSDR は基準の補正（xo_correction）を持っていません",
}

PLUTO_NOTICES = {
    "plutosdr_setting_failed": "PlutoSDR に利得・送信の減衰を入れられませんでした（測定は続けます）",
    "s11_open_gain_fitted": "オープンを測る前に受信の利得を合わせました",
    "tg_ref_gain_fitted": "基準を取る前に受信の利得を合わせました",
    "plutosdr_xo_refused": "PlutoSDR の基準の補正が機器の範囲の外なので、書いていません（機器の基準の値のまま測ります）",
}

PLUTO_SETTINGS = {
    "plutosdr_dll": "libiio.dll",
    "plutosdr_32bit": "64 ビット版の libiio が要ります",
    "plutosdr_choose_title": "libiio.dll（64 ビット版）のあるフォルダ",
    "plutosdr_uri": "URI",
    "plutosdr_uri_tip": "USB の RNDIS で繋ぐ PlutoSDR は ip:192.168.2.1。USB で見つかる機器は一覧に自動で出ます",
    # 基準の補正（設計Q §7.10。名前はユーザー指定）
    "plutosdr_xo": "基準の補正",
    "plutosdr_xo_tip": "基準の水晶（40MHz）のずれ（ppm）。送信・受信の周波数とサンプリング周波数が同じ割合で合います。"
                       "PlutoSDR ごとに覚え、繋いだときに書きます。0 は書きません（機器の値のまま）",
}

TEMPLATES.update({
    "pluto_gain_used": "この範囲では {db} dB を使います",
    "pluto_tx_on": "出力中: {what}",
    "s11_open_gain_fitted": "オープンを測る前に受信の利得を {gain} dB に合わせました（前は {before}）",
    "tg_ref_gain_fitted": "基準を取る前に受信の利得を {gain} dB に合わせました（前は {before}）",
    "ref_taken_fitted": "受信の利得を {gain} dB に合わせて（前は {before}）基準を取りました: {name}",
    "ref_open_measured_fitted": "受信の利得を {gain} dB に合わせて（前は {before}）オープンを測りました。ショートを繋いで"
                                "「ショートを測る」を押してください",
    "status_cal_pluto": "補正: PlutoSDR 未校正・単位 {unit}",
    "status_cal_pluto_gain": "補正: PlutoSDR 利得の補正 {at}・単位 {unit}",
    # 基準の補正（設計Q §7.10）: 機器から読み戻した基準の周波数・繋いでいないとき・繋いだときに書けなかった補正
    "pluto_xo_hz": "機器の基準 {hz} Hz",
    "pluto_xo_next": "{serial} に繋いだときに書きます",
    "pluto_xo_refused": "{ppm} ppm は機器の範囲（{min}〜{max} ppm）の外なので書いていません",
})
