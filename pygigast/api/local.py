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
    def call(self, name: str, **kwargs) -> concurrent.futures.Future:
        if name.startswith("_"):
            raise CommandError("unknown_command", None, {"name": name})
        return self._inst.submit(name, **kwargs)

    def get_state(self) -> concurrent.futures.Future:
        return self.call("get_state")

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
        from ..core.devices.gigast.transport import discover

        return [
            {"port": p.port, "serial": p.serial, "description": p.description}
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
        return [{"port": "SIM", "serial": self.SERIAL, "description": "Simulator"}]

    def probe(self, port: str) -> bool:
        return True

    def open(self, serial: Optional[str], port: Optional[str]):
        from ..core.devices.simulator.device import SimulatorDevice, SynthConfig

        # 画面の確認用に、SG 直結に近い −30dBm の信号を 100MHz と 1500MHz に置く
        cfg = SynthConfig(signals=[(100_000_000, -30.0), (1_500_000_000, -30.0)])
        return SimulatorDevice(self._profile, config=cfg, speed=self.speed, serial_number=self.SERIAL)

    speed = 1.0


def create_default(traffic_log_dir=None) -> LocalApi:
    from ..core.devices.base import load_profile
    from ..core.instrument import GIGAST_PROFILE_PATH

    profile = load_profile(GIGAST_PROFILE_PATH)
    backends = {
        "gigast": _GigaStBackend(profile, traffic_log_dir),
        "simulator": _SimulatorBackend(profile),
    }
    return LocalApi(Instrument(backends, profile=profile))
