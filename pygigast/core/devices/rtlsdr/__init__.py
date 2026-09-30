"""RTL-SDR（RTL2832U＋R820T）の機器の層（設計Q §6）。

`rtlsdr.dll` を ctypes で直接呼び（dll.py）、IQ からスペクトルを作って（spectrum.py）、
機器の口（driver.py）から GigaSt と同じ形の生の値を返す。IQ はこの層の外に出さない（設計Q §3）。
"""
