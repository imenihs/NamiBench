"""機器の一覧・名前・繋ぐ・切る（設計J §4.7・設計B §7.3・設計Q §6.6）。Instrument に混ぜる。

- 一覧は種類を問わず1つ（GigaSt・RTL-SDR・シミュレータ）。1行は種類・名前・状態のコード。画面の文言は
  画面の表（ui_qt/texts_devices.py）が状態のコードから作る（U-22）
- 名前は利用者が付けられる（設定に残す）。付けていなければ既定の名前（機種名＋シリアル番号の末尾4桁）
- 一覧が変わったらイベント `devices` で知らせる。ポートの一覧を見るのは、繋いでいないとき（選ぶ画面が
  開いているはず）と、画面が一覧を見ている間（list_devices の watch_s で期限を延ばす）だけ。
  期限にしたのは、Web の画面が知らせずに閉じても見続けないようにするため
- ポートをほかのアプリが使っているときは、確認の掃引の失敗（probe_failed）と分けて port_in_use にする。
  GigaSt はポートを開いてみるまで分からない（一覧を作るために知らない機器のポートを開かない: 設計B §7.3）ので、
  開けなかったポートを覚えておき、一覧から消えるか繋げるまで「ほかのアプリが使っています」と出す
"""
from __future__ import annotations

import time
from typing import Optional

from .devices.base import load_profile
from .devices.plutosdr.iio import IioNotFound
from .devices.rtlsdr.dll import DllNotFound
from .instrument_common import CommandError
from .types import DeviceError, PortInUse

# 一覧の並び（種類の順）。自動で繋ぐのは実機だけ（シミュレータへ黙って繋ぐと、作り物の値を測った値と取り違える）
LIST_ORDER = ("gigast", "rtlsdr", "plutosdr", "simulator")
AUTO_BACKENDS = ("gigast", "rtlsdr", "plutosdr")
# 画面が一覧を見ていると言える最長の期限（秒）。画面はこれより短い間隔で延ばし直す
DEVICE_WATCH_MAX_S = 30.0
# 一覧を見直す間隔（設計B §7.3 の 0.5s と同じ）
DEVICE_POLL_S = 0.5
# 繋ぎ替えるとき、前の機器が閉じるのを待つ上限（全帯域の掃引 約 8.6s の終わりまで待てる長さ）
WORKER_CLOSE_WAIT_S = 15.0


def _tail(serial: Optional[str]) -> str:
    return (serial or "")[-4:]


class DevicesMixin:
    """機器の一覧・名前・接続（Instrument に混ぜる）。"""

    def _init_devices(self) -> None:
        self._watch_until = 0.0
        self._next_device_poll = 0.0
        self._devices_last: Optional[list] = None
        # 開こうとして「ほかのアプリが使用中」だったポート（種類, ポート）
        self._busy_ports: set[tuple[str, str]] = set()
        self._model_names: dict[str, str] = {}

    # ------------------------------------------------------------------ 一覧

    def _devices_polling(self) -> bool:
        return self._conn == "disconnected" or time.monotonic() < self._watch_until

    def _poll_devices(self) -> None:
        """制御スレッドの繰り返しから呼ぶ。見ている間だけ 0.5s ごとに一覧を作り、変わったら知らせる。"""
        now = time.monotonic()
        if not self._devices_polling() or now < self._next_device_poll:
            return
        self._next_device_poll = now + DEVICE_POLL_S
        self._emit_devices()

    def _emit_devices(self, force: bool = False) -> None:
        rows = self._device_rows()
        if force or rows != self._devices_last:
            self._devices_last = rows
            self._emit("devices", {"devices": rows})

    def _devices_changed(self) -> None:
        """繋いだ・切った・名前を変えたとき。見ている画面にはすぐ知らせる（次の見直しを待たない）。"""
        if self._devices_polling():
            self._emit_devices()

    def _model_name(self, backend: str) -> str:
        """機種名（既定の名前に使う）。RTL-SDR は機種のプロファイルの名前。"""
        name = self._model_names.get(backend)
        if name is None:
            if backend in ("rtlsdr", "plutosdr"):
                if backend == "rtlsdr":
                    from .devices.rtlsdr.driver import PROFILE_PATH
                else:
                    from .devices.plutosdr.driver import PROFILE_PATH

                try:
                    name = load_profile(PROFILE_PATH).get("display_name") or "RTL-SDR"
                except (OSError, ValueError):
                    name = "RTL-SDR" if backend == "rtlsdr" else "PlutoSDR"
            else:
                name = self._base_profile.get("display_name") or self._base_profile.get("model") or backend
            self._model_names[backend] = name
        return name

    def _default_name(self, backend: str, serial: Optional[str], port: Optional[str], shared: int) -> Optional[str]:
        if backend == "simulator":
            return None  # 画面の語（「シミュレータ」）で出す
        name = f"{self._model_name(backend)} {_tail(serial)}".strip()
        if shared > 1 and port:
            name += f" ({port})"  # 同じシリアル番号が複数（設計Q §6.6）: 番号付きのポートで見分ける
        return name

    def _name_key(self, backend: str, serial: Optional[str]) -> str:
        return f"{backend}:{serial or ''}"

    def _device_name(self, backend: str, serial: Optional[str], port: Optional[str] = None,
                     shared: int = 0) -> Optional[str]:
        return self._settings.device_name(self._name_key(backend, serial)) or \
            self._default_name(backend, serial, port, shared)

    def _current_device_name(self) -> Optional[str]:
        ident = self._identity or {}
        if not self._backend_name or not ident:
            return None
        return self._device_name(self._backend_name, ident.get("serial"), ident.get("port"),
                                 self._rtlsdr_shared if self._backend_name == "rtlsdr" else 0)

    def _row_state(self, backend: str, p: dict, known: set) -> str:
        ident = self._identity or {}
        if (self._conn == "connected" and self._backend_name == backend and ident.get("port") == p.get("port")):
            return "in_use_here"
        if p.get("available", True) is False or (backend, p.get("port")) in self._busy_ports:
            return "busy_other_app"
        if backend == "gigast" and p.get("serial") not in known:
            return "first_time_needs_probe"
        if p.get("serial") in known and backend != "simulator":
            return "known"
        return "available"

    def _backend_rows(self, backend: str, b) -> list[dict]:
        try:
            ports = b.discover()
        except (DllNotFound, IioNotFound):
            return [{"id": f"{backend}:dll", "kind": backend, "port": None, "serial": None, "description": "",
                     "usb_id": None, "known": False, "shared_serial": 0, "available": False,
                     "state": "dll_not_found", "name": None, "custom_name": None,
                     "default_name": self._model_name(backend),
                     "dll_folder": self._dll_folder_of(backend)}]
        except Exception:  # noqa: BLE001 — 一覧を作れない種類は出さない（他の種類の一覧は出す）
            return []
        known = set(self._settings.known_serials())
        present = {p.get("port") for p in ports}
        self._busy_ports = {k for k in self._busy_ports if k[0] != backend or k[1] in present}
        rows = []
        for p in ports:
            serial = p.get("serial") or ""
            shared = int(p.get("shared_serial") or 0)
            custom = self._settings.device_name(self._name_key(backend, serial))
            rows.append({
                "id": f"{backend}:{p.get('port')}", "kind": backend, "port": p.get("port"), "serial": serial,
                "description": p.get("description") or "", "usb_id": p.get("usb_id"),
                "known": serial in known, "shared_serial": shared, "available": p.get("available", True),
                "state": self._row_state(backend, p, known),
                "name": custom or self._default_name(backend, serial, p.get("port"), shared),
                "custom_name": custom, "default_name": self._default_name(backend, serial, p.get("port"), shared),
            })
        return rows

    def _device_rows(self, only: Optional[str] = None) -> list[dict]:
        rows: list[dict] = []
        names = [only] if only else [n for n in LIST_ORDER if n in self._backends] + \
            [n for n in self._backends if n not in LIST_ORDER]
        for name in names:
            rows += self._backend_rows(name, self._backends[name])
        return rows

    def _discover(self, b) -> list[dict]:
        """1つの種類の一覧。RTL-SDR の DLL が無ければ理由のコードで返す（GigaSt はそのまま使える: 設計Q §6.2）。"""
        try:
            return b.discover()
        except DllNotFound as e:
            raise CommandError("rtlsdr_dll_not_found", "dll_folder",
                               {"message": str(e), "dll_folder": self._rtlsdr_cfg["dll_folder"]}) from e
        except IioNotFound as e:
            raise CommandError("plutosdr_dll_not_found", "dll_folder",
                               {"message": str(e), "dll_folder": self._pluto_cfg["dll_folder"]}) from e

    def _dll_folder_of(self, backend: str) -> Optional[str]:
        """設定で選んだ DLL のフォルダ（RTL-SDR は rtlsdr.dll、PlutoSDR は libiio.dll）。"""
        if backend == "plutosdr":
            return (getattr(self, "_pluto_cfg", None) or {}).get("dll_folder")
        return (getattr(self, "_rtlsdr_cfg", None) or {}).get("dll_folder")

    def _backend(self, backend: Optional[str]):
        b = self._backends.get(backend or "")
        if b is None:
            raise CommandError("unknown_backend", "backend", {"backend": backend})
        return b

    def _cmd_list_devices(self, backend: Optional[str] = None, watch_s: float = 0) -> dict:
        """機器の一覧（種類を問わず1つ。backend を付けるとその種類だけ）。

        watch_s を付けると、その秒数の間（最長 30s）は一覧を見続け、変わったら `devices` で知らせる。
        backend を付けたときは、RTL-SDR の DLL が無ければ失敗として返す（その種類の一覧が作れないため）。
        """
        if isinstance(watch_s, bool) or not isinstance(watch_s, (int, float)) or watch_s < 0:
            raise CommandError("bad_value", "watch_s")
        if watch_s:
            self._watch_until = max(self._watch_until, time.monotonic() + min(float(watch_s), DEVICE_WATCH_MAX_S))
        if backend is not None:
            self._discover(self._backend(backend))
            return {"devices": self._device_rows(backend)}
        rows = self._device_rows()
        self._devices_last = rows
        return {"devices": rows}

    def _cmd_rename_device(self, kind: str, serial: str, name: Optional[str] = None) -> dict:
        """機器に名前を付ける（設計J §4.7）。空・None なら既定の名前に戻す。名前はシリアル番号ごと。"""
        self._backend(kind)
        if not isinstance(serial, str) or not serial:
            raise CommandError("device_not_selected", "serial")
        if name is not None and not isinstance(name, str):
            raise CommandError("bad_name", "name")
        name = (name or "").strip()
        self._settings.set_device_name(self._name_key(kind, serial), name or None)
        self._emit_devices(force=True)
        self._changed()  # 状態バーの名前
        return {"kind": kind, "serial": serial, "name": self._device_name(kind, serial)}

    # ------------------------------------------------------------------ 繋ぐ・切る

    def _cmd_connect(self, backend: str = "gigast", serial: Optional[str] = None, port: Optional[str] = None) -> dict:
        """繋ぐ。初めての機器は確認の掃引で GigaSt と確かめてから覚える（設計B §7.3）。"""
        b = self._backend(backend)
        if self._device is not None or self._conn == "reconnecting":
            if self._ref_job is not None:
                self._abort_ref_job(CommandError("disconnected"))
            old = self._worker
            self._drop_device()
            self._conn = "disconnected"
            if old is not None:
                # 前の機器を機器スレッドが閉じるまで待つ。同じポートを開き直すとき、閉じる前に開くと
                # 「ほかのアプリが使用中」と取り違える（掃引の途中なら、その終わりまで: 設計B §3）
                old.join(WORKER_CLOSE_WAIT_S)
        known = self._settings.known_serials()
        if port is None and serial is None:
            raise CommandError("device_not_selected", "serial")
        rows = self._discover(b)
        if port is None:
            ports = [p for p in rows if p.get("serial") == serial and p.get("available", True)]
            if not ports:
                if any(p.get("serial") == serial for p in rows):
                    # 見えているが開けない（RTL-SDR をほかのアプリが開いている）
                    raise CommandError("port_in_use", "port", {"serial": serial})
                raise CommandError("device_not_found", "serial", {"serial": serial})
            if len(ports) > 1:
                # 同じシリアル番号が複数（RTL-SDR）: 番号付きのポートで選んでもらう（設計Q §6.6）
                raise CommandError("device_ambiguous", "port",
                                   {"serial": serial, "ports": [p["port"] for p in ports]})
            port = ports[0]["port"]
        else:
            match = [p for p in rows if p.get("port") == port]
            if match and match[0].get("available", True) is False:
                raise CommandError("port_in_use", "port", {"port": port})
            serial = match[0].get("serial") if match else serial
        try:
            if serial not in known and not b.probe(port):
                raise CommandError("probe_failed", "port", {"port": port})
            device = b.open(serial, port)
        except PortInUse as e:
            self._busy_ports.add((backend, port))
            self._devices_changed()
            raise CommandError("port_in_use", "port", {"port": port, "message": str(e)}) from e
        except (DeviceError, OSError) as e:
            raise CommandError("open_failed", "port", {"port": port, "message": str(e)}) from e
        self._busy_ports.discard((backend, port))
        ident = device.identity()
        if ident.serial:
            self._settings.remember_device(ident.serial, backend)
        self._attach(device, backend)
        self._devices_changed()
        return {"identity": self._identity, "name": self._current_device_name()}

    def _cmd_connect_last(self, backend: Optional[str] = None) -> dict:
        """覚えた機器が見つかれば自動で繋ぐ（設計B §7.3: 2回目から）。見つからなければ何もしない。

        backend を省くと実機の種類（GigaSt・RTL-SDR）を全部見る。前回の機器を先に探す。繋いだら
        `auto_connected` を1回知らせる（設計J §4.7 の 6）。
        """
        names = [backend] if backend else [n for n in AUTO_BACKENDS if n in self._backends]
        if backend:
            self._backend(backend)
        last, last_b = self._settings.last_serial(), self._settings.last_backend()
        order = ([last] if last else []) + self._settings.known_serials()
        names.sort(key=lambda n: n != last_b)
        for s in order:
            for name in names:
                try:
                    ports = self._discover(self._backends[name])
                except CommandError:
                    if backend:
                        raise
                    continue  # RTL-SDR の DLL が無くても GigaSt は探す
                # 同じシリアル番号が1本だけのときに限る（RTL-SDR は全製品共通のことがある: 設計Q §6.6）
                if len([p for p in ports if p.get("serial") == s and p.get("available", True)]) == 1:
                    res = self._cmd_connect(backend=name, serial=s)
                    self._notice("auto_connected", {"name": res.get("name"), "kind": name, "serial": s})
                    return res
        return {"identity": None}

    def _cmd_disconnect(self) -> dict:
        if self._ref_job is not None:
            self._abort_ref_job(CommandError("disconnected"))
        self._exttx_device_gone("user")  # 利用者が切った（外部 IQ の送信を止める: 設計S §1）
        self._drop_device()
        self._identity = None
        self._backend_name = None
        self._run = "stopped"
        self._set_conn("disconnected")
        self._devices_changed()
        return {}
