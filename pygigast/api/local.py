"""同じプロセスの口（設計A §4.3）。Qt 画面はこれだけを使い、コアの中身を直接触らない。

命令は Future を返す。イベントは購読した関数へ制御スレッドから届くので、
画面スレッドへ渡し直すのは画面側の仕事（Qt は QueuedConnection のシグナル）。
Web の口（⑤）も同じ命令の名前・引数・戻り値を使う。
"""
from __future__ import annotations

import concurrent.futures
from typing import Callable, Optional

from ..core.instrument import CommandError, DeviceBackend, Instrument

__all__ = ["LocalApi", "CommandError", "create_default"]


class LocalApi:
    def __init__(self, instrument: Instrument) -> None:
        self._inst = instrument

    # 命令（名前と引数は Web の `POST /api/v1/{命令名}` と同じ）
    def call(self, name: str, /, **kwargs) -> concurrent.futures.Future:
        if name.startswith("_"):
            raise CommandError("unknown_command", None, {"name": name})
        return self._inst.submit(name, **kwargs)

    def get_state(self) -> concurrent.futures.Future:
        return self.call("get_state")

    @property
    def data_root(self):
        """アプリのデータのフォルダ（Web の口が作るファイルの置き場 web_files の親: 設計A §4.3.2 の 3）。"""
        return self._inst._root

    def subscribe(self, cb: Callable[[str, dict], None]) -> Callable[[], None]:
        return self._inst.subscribe(cb)

    def close(self) -> None:
        self._inst.shutdown()


class _GigaStBackend(DeviceBackend):
    def __init__(self, profile: dict, traffic_log_dir=None) -> None:
        self._profile = profile
        self._log = None
        if traffic_log_dir:
            import time
            from pathlib import Path

            from ..core.devices.gigast.traffic_log import TrafficLog

            Path(traffic_log_dir).mkdir(parents=True, exist_ok=True)
            self._log = TrafficLog(Path(traffic_log_dir) / time.strftime("traffic_%Y%m%d_%H%M%S"))

    def discover(self) -> list[dict]:
        from ..core.devices.gigast.transport import FTDI_PID, FTDI_VID, discover

        usb_id = f"{FTDI_VID:04X}:{FTDI_PID:04X}"
        return [
            {"port": p.port, "serial": p.serial, "description": p.description, "usb_id": usb_id}
            for p in discover()
        ]

    def probe(self, port: str) -> bool:
        from ..core.devices.gigast.driver import GigaStDevice

        return GigaStDevice.probe(port, self._profile, self._log)

    def open(self, serial: Optional[str], port: Optional[str]):
        from ..core.devices.gigast.driver import GigaStDevice

        dev = GigaStDevice(self._profile, traffic_log=self._log)
        # シリアル番号が分かっていればそれで開く（COM 番号が変わっても同じ機器: 設計B §2 の3）
        dev.open(serial=serial or None, port=None if serial else port)
        return dev


class _SimulatorBackend(DeviceBackend):
    """シミュレータ。実機が無いときの開発と画面の確認に使う（設計B §7.4）。"""

    SERIAL = "SIM00001"

    def __init__(self, profile: dict) -> None:
        self._profile = profile

    def discover(self) -> list[dict]:
        return [{"port": "simulator", "serial": self.SERIAL, "description": "Simulator"}]

    def probe(self, port: str) -> bool:
        return True

    def open(self, serial: Optional[str], port: Optional[str]):
        from ..core.devices.simulator.device import SimulatorDevice, SynthConfig

        # 画面の確認用に、SG 直結に近い −30dBm の信号を 100MHz と 1500MHz に置く
        cfg = SynthConfig(signals=[(100_000_000, -30.0), (1_500_000_000, -30.0)])
        return SimulatorDevice(self._profile, config=cfg, speed=self.speed, serial_number=self.SERIAL)

    speed = 1.0


class _RtlSdrBackend(DeviceBackend):
    """RTL-SDR（設計Q §6.2・§6.6）。DLL は設定で選んだフォルダから読む（本体が set_dll_folder で渡す）。

    ポートは番号付き（"rtlsdr:N"）。シリアル番号が全製品共通のことがあるので、一覧の行に同じシリアル番号の
    数（shared_serial）と、別のアプリが開いていて使えないか（available）を付ける。
    確認の掃引は要らない（DLL が RTL-SDR と分かった機器だけを数える。知らない機器へは何も送らない）。
    lib_factory は試験で DLL の偽物を渡すためのもの。
    """

    def __init__(self, profile: Optional[dict] = None, lib_factory: Optional[Callable[[], object]] = None) -> None:
        self._profile = profile
        self._lib_factory = lib_factory
        self._folder: Optional[str] = None

    def set_dll_folder(self, folder: Optional[str]) -> None:
        self._folder = folder

    def _lib(self):
        if self._lib_factory is not None:
            return self._lib_factory()
        from ..core.devices.rtlsdr.dll import RtlSdrLib

        return RtlSdrLib.load(self._folder)  # 見つからなければ DllNotFound

    def discover(self) -> list[dict]:
        from ..core.devices.rtlsdr.driver import discover

        return [
            {"port": r.port, "serial": r.serial,
             "description": " ".join(x for x in (r.manufacturer, r.product) if x) or r.name,
             "available": r.available, "shared_serial": r.shared_serial}
            for r in discover(lib=self._lib())
        ]

    def probe(self, port: str) -> bool:
        return True

    def open(self, serial: Optional[str], port: Optional[str]):
        from ..core.instrument_rtlsdr import open_rtlsdr_device

        # 番号付きのポートがあればそれで開く（同じシリアル番号が複数でも選んだ1本を開く: 設計Q §6.6）
        return open_rtlsdr_device(self._lib(), port=port, serial=None if port else serial, profile=self._profile)


class _PlutoSdrBackend(DeviceBackend):
    """PlutoSDR（設計Q §7.2）。libiio の DLL は設定で選んだフォルダから読む（本体が set_dll_folder で渡す）。

    ポートは URI。一覧は USB で見える機器と、設定の URI（既定 ip:192.168.2.1）で繋がる機器を、シリアル番号で1行に
    まとめる（devices/plutosdr/driver.discover）。確認の掃引は要らない（libiio が PlutoSDR と答えた機器だけを並べる）。
    lib_factory は試験で libiio の偽物（devices/plutosdr/fake.py）を渡すためのもの。
    """

    def __init__(self, profile: Optional[dict] = None, lib_factory: Optional[Callable[[], object]] = None) -> None:
        self._profile = profile
        self._lib_factory = lib_factory
        self._folder: Optional[str] = None
        self._uri: Optional[str] = None

    def set_dll_folder(self, folder: Optional[str]) -> None:
        self._folder = folder

    def set_uri(self, uri: Optional[str]) -> None:
        self._uri = uri

    def _lib(self):
        if self._lib_factory is not None:
            return self._lib_factory()
        from ..core.devices.plutosdr.iio import IioLib

        return IioLib.load(self._folder)  # 見つからなければ IioNotFound

    def discover(self) -> list[dict]:
        from ..core.devices.plutosdr.driver import DEFAULT_URI, discover

        return [{"port": r.port, "serial": r.serial, "description": r.description, "available": r.available}
                for r in discover(self._lib(), (self._uri or DEFAULT_URI,))]

    def probe(self, port: str) -> bool:
        return True

    def open(self, serial: Optional[str], port: Optional[str]):
        from ..core.instrument_plutosdr import open_plutosdr_device

        if port is None:
            rows = [r for r in self.discover() if r["serial"] == serial]
            if not rows:
                from ..core.types import DeviceDisconnected

                raise DeviceDisconnected(f"シリアル番号 {serial} の PlutoSDR が見つからない")
            port = rows[0]["port"]
        return open_plutosdr_device(self._lib(), port, profile=self._profile)


def create_default(traffic_log_dir=None) -> LocalApi:
    from ..core.devices.base import load_profile
    from ..core.instrument import GIGAST_PROFILE_PATH

    profile = load_profile(GIGAST_PROFILE_PATH)
    backends = {
        "gigast": _GigaStBackend(profile, traffic_log_dir),
        "simulator": _SimulatorBackend(profile),
        "rtlsdr": _RtlSdrBackend(),
        "plutosdr": _PlutoSdrBackend(),
    }
    return LocalApi(Instrument(backends, profile=profile))
