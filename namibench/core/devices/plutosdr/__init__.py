"""PlutoSDR（AD9364）の機器の層（設計Q §7）。

公式の libiio（iio.py）を ctypes で直接呼び、受信の IQ をスペクトルにして（RTL-SDR と同じ部品）、機器の口（driver.py）
から RTL-SDR と同じ形の生の値を返す。SG は DDS のトーン、TG は受信の位置に追従する多トーン（tones.py）。
fake.py は試験とシミュレータ用の libiio の偽物。
"""
