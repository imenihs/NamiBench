"""コアの設定ファイル（設計O §1.1）。

覚えた機器・いまの状態（掃引条件など）・配色・rtl_tcp の送出の設定・RTL-SDR の設定を1つの JSON に持つ。
- 版を持つ
- 読めない・壊れているときは初期値で始め、壊れたファイルは別名で残し、何を初期値にしたかを返す（黙って消さない）
- 書くときは一時ファイルから置き換える（途中で落ちても前の内容が残る）
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

FORMAT = "pygigast-settings"
VERSION = 1

DEFAULTS: dict[str, Any] = {
    "known_devices": {"serials": [], "last": None},
    "state": {},
    "theme": {},
    "rtltcp": {"enabled": False, "port": 1234, "bind": "local"},
    # RTL-SDR（設計Q §6.2・§6.5）: DLL のフォルダ（None は既定の探し方）・受信の利得（None は機種の既定）・AGC
    "rtlsdr": {"dll_folder": None, "gain_db": None, "agc": False},
    # PlutoSDR（設計Q §7）: libiio の DLL のフォルダ・URI・受信の利得（None は機種の既定）・自動利得・送信の減衰（dB）・
    # TG 高調波の平均除去の回数（1 はなし: 設計Q §7.7）
    "plutosdr": {"dll_folder": None, "uri": "ip:192.168.2.1", "gain_db": None, "agc": False, "tx_atten_db": None,
                 "tg_harmonic_avg": 1},
    # 外部 IQ 送信の口（設計S §1）: 受け付けるか・ポート・外部の送信の減衰（dB。None は最小の出力）
    "ext_tx": {"enabled": False, "port": 1235, "atten_db": None},
    # 利用者が機器に付けた名前（設計J §4.7）。キーは "種類:シリアル番号"
    "device_names": {},
}


class Settings:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        # 読み込みで初期値に戻したもの（画面へ1回知らせる）。None は問題なし
        self.load_problem: Optional[dict] = None
        self._load()

    def _load(self) -> None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as e:
            self.load_problem = {"reason": "unreadable", "message": str(e)}
            return
        try:
            d = json.loads(raw.decode("utf-8"))
            if not isinstance(d, dict) or d.get("format") != FORMAT:
                raise ValueError("format")
            if int(d.get("version", 0)) > VERSION:
                raise ValueError("newer_version")
        except (ValueError, UnicodeDecodeError) as e:
            kept = self.path.with_name(self.path.stem + time.strftime(".broken-%Y%m%d-%H%M%S") + self.path.suffix)
            try:
                os.replace(self.path, kept)
            except OSError:
                kept = None
            self.load_problem = {"reason": "broken", "message": str(e), "kept_as": str(kept) if kept else None}
            return
        for key, default in DEFAULTS.items():
            v = d.get(key)
            if isinstance(v, type(default)):
                if isinstance(default, dict):
                    merged = copy.deepcopy(default)
                    merged.update(v)
                    self.data[key] = merged
                else:
                    self.data[key] = v

    def get(self, key: str) -> Any:
        return copy.deepcopy(self.data.get(key))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = copy.deepcopy(value)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        out = {"format": FORMAT, "version": VERSION, **self.data}
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # 覚えた機器（設計B §7.3）
    def known_serials(self) -> list[str]:
        return list(self.data["known_devices"].get("serials", []))

    def last_serial(self) -> Optional[str]:
        return self.data["known_devices"].get("last")

    def device_kind(self, serial: str) -> Optional[str]:
        """覚えた機器の種類（gigast・rtlsdr など。前の版で覚えた機器は分からないので None）。"""
        kinds = self.data["known_devices"].get("kinds")
        v = kinds.get(serial) if isinstance(kinds, dict) else None
        return v if isinstance(v, str) and v else None

    def last_backend(self) -> Optional[str]:
        return self.data["known_devices"].get("last_backend")

    def remember_device(self, serial: str, backend: Optional[str] = None) -> None:
        kd = self.data["known_devices"]
        if serial not in kd["serials"]:
            kd["serials"].append(serial)
        kd["last"] = serial
        if backend:
            kd["last_backend"] = backend
            # 機器の種類も覚える（覚えた機器の一覧で、機器の一覧と同じ名前を出すため: 設計J §4.7）
            kinds = kd.get("kinds") if isinstance(kd.get("kinds"), dict) else {}
            kinds[serial] = backend
            kd["kinds"] = kinds
        self.save()

    def forget_device(self, serial: str) -> None:
        """覚えた機器から外す（設定の画面の「接続」）。前回の機器だったら前回も外す。"""
        kd = self.data["known_devices"]
        kd["serials"] = [s for s in kd.get("serials", []) if s != serial]
        if isinstance(kd.get("kinds"), dict):
            kd["kinds"].pop(serial, None)
        if kd.get("last") == serial:
            kd["last"] = None
            kd.pop("last_backend", None)
        self.save()

    # 機器の名前（設計J §4.7）
    def device_name(self, key: str) -> Optional[str]:
        v = self.data["device_names"].get(key)
        return v if isinstance(v, str) and v else None

    def set_device_name(self, key: str, name: Optional[str]) -> None:
        if name:
            self.data["device_names"][key] = name
        else:
            self.data["device_names"].pop(key, None)
        self.save()
