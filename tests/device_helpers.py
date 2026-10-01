"""機器の一覧の試験の道具: GigaSt の口を持つ偽の種類（ポートの挿し抜き・使用中・確認の掃引の結果を決められる）。

実機のポートは開かない。開くとシミュレータの機器を返し、名乗り（identity）のポートだけを一覧の行に合わせる。
"""
import threading
from typing import Optional

from pygigast.core.devices.simulator.device import SimulatorDevice
from pygigast.core.instrument import DeviceBackend
from pygigast.core.types import DeviceIdentity, PortInUse

USB_ID = "0403:6001"


class FakeGigaBackend(DeviceBackend):
    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.ports: list[dict] = []
        self.probe_result = True
        self.busy: set[str] = set()  # ほかのアプリが開いているポート
        self.discover_calls = 0
        self.probe_calls: list[str] = []
        self.gate: Optional[threading.Event] = None  # 開くのを待たせる（「繋いでいます…」を見る）

    def plug(self, port: str, serial: str) -> None:
        self.ports.append({"port": port, "serial": serial, "description": "USB Serial Port", "usb_id": USB_ID})

    def unplug(self, port: str) -> None:
        self.ports = [p for p in self.ports if p["port"] != port]

    def discover(self) -> list[dict]:
        self.discover_calls += 1
        return [dict(p) for p in self.ports]

    def probe(self, port: str) -> bool:
        self.probe_calls.append(port)
        if port in self.busy:
            raise PortInUse(f"could not open port '{port}': PermissionError(13, 'Access is denied.', None, 5)")
        return self.probe_result

    def open(self, serial: Optional[str], port: Optional[str]):
        if self.gate is not None:
            self.gate.wait(10)
        row = next((p for p in self.ports if p["port"] == port or (serial and p["serial"] == serial)), None)
        if row is None:
            from pygigast.core.types import DeviceDisconnected

            raise DeviceDisconnected("見つからない")
        if row["port"] in self.busy:
            raise PortInUse(f"could not open port '{row['port']}': PermissionError(13, 'Access is denied.', None, 5)")
        dev = SimulatorDevice(self.profile, speed=0.05, serial_number=row["serial"])
        dev._identity = DeviceIdentity(model=self.profile["model"], serial=row["serial"], port=row["port"])
        return dev
