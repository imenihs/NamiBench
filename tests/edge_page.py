"""試験で使うヘッドレスの Edge（DevTools の口で動かす1枚のページ）。試験は持たない。

Edge は1つ開くと、ブラウザの本体のほかに描画・GPU・クラッシュの記録などの子のプロセスを十数個作る。閉じ方が
足りないと、それが試験の後も残って PC を重くする（2026-10-09: 全体の試験の最中に msedge が 347 個あり、Web の
試験が「描画が回らない（visibilityState=hidden）」で落ちた。残っていたのは 6 日前の試験の Edge 13 個・229 プロセス）。

そこで、開いた Edge の木（本体と、その子・孫）を Windows のジョブ（入れたプロセスと子を1つにまとめるもの）に入れる:
- 閉じるときは、ブラウザに閉じるよう頼んだ後、ジョブごと止める（本体が先に終わって親を失った子も残らない）
- ジョブは「最後の持ち手が閉じたら中を全部止める」にしてある。試験が途中で落ちても、時間切れで Python ごと
  止められても、Python が終われば Edge の木は残らない
- 止めるのはこのジョブに入れたものだけ（利用者が開いている Edge の窓には触らない）
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
# 試験の Edge の利用者のフォルダの名前の頭（試験が開いた Edge かを見分ける印）
PROFILE_PREFIX = "gs_edge_"


def edge_available() -> bool:
    return EDGE.exists()


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                  "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
_BASIC_ACCOUNTING_INFORMATION = 1
_CREATE_SUSPENDED = 0x4


class _Job:
    """Windows のジョブ。入れたプロセスの子・孫も自動で入り、止めると全部止まる。最後の持ち手が閉じても全部止まる。"""

    def __init__(self) -> None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.c_void_p)
        k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._k = k
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                         ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            k.CloseHandle(self.handle)
            raise ctypes.WinError(err)

    def add(self, process_handle: int) -> None:
        if not self._k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def active(self) -> int:
        """ジョブの中でまだ動いているプロセスの数。"""
        info = _BasicAccounting()
        if not self._k.QueryInformationJobObject(self.handle, _BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
                                                 ctypes.sizeof(info), None):
            return -1
        return int(info.ActiveProcesses)

    def close(self) -> None:
        """中を全部止めて閉じる。"""
        if self.handle:
            self._k.TerminateJobObject(self.handle, 1)
            self._k.CloseHandle(self.handle)
            self.handle = None


def _start_in_job(args: list[str]) -> tuple[subprocess.Popen, "_Job | None"]:
    """プロセスを止めた形で作ってジョブに入れてから動かす（動き出してから入れると、その前に作った子がジョブの外に出る）。"""
    if os.name != "nt":
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), None
    job = _Job()
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=_CREATE_SUSPENDED)
    except Exception:
        job.close()
        raise
    try:
        job.add(int(proc._handle))  # noqa: SLF001 - Popen が持つプロセスの持ち手（Windows）
    finally:
        # 止めた形で作ったプロセスを動かす（ジョブに入れられなかったときも動かし、閉じるときに木ごと止める）
        ctypes.WinDLL("ntdll").NtResumeProcess(wintypes.HANDLE(int(proc._handle)))  # noqa: SLF001
    return proc, job


def edge_processes(marker: str = PROFILE_PREFIX) -> list[int]:
    """コマンド行に marker（試験の Edge の利用者のフォルダ）を含む msedge のプロセスの番号。利用者が開いている Edge は
    含まない。数えるだけ（止めない）。"""
    if os.name != "nt":
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like '*"
          + marker.replace("'", "''") + "*' } | ForEach-Object { $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       timeout=60)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


class EdgePage:
    """ヘッドレスの Edge の1枚のページ（DevTools の口で動かす）。閉じたら（落ちても）Edge の木を残さない。"""

    def __init__(self) -> None:
        from websockets.sync.client import connect

        self.profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
        self.proc = None
        self.job = None
        self.ws = None
        self._ws_cm = None
        self._id = 0
        self.errors: list[str] = []
        try:
            self.proc, self.job = _start_in_job(
                [str(EDGE), "--headless=new", "--disable-gpu", "--remote-debugging-port=0",
                 f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                 # 本番のブラウザの画面は見えているタブで、描画（requestAnimationFrame）が回る。ヘッドレスの窓は、PC が
                 # 重いと隠れた窓・裏のタブとみなされて描画と時計を止められることがある（全体の試験で settle が 60 秒
                 # 返らなかった）。本番と同じ「見えているタブ」の前提にそろえる
                 "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                 "--disable-renderer-backgrounding", "--disable-features=CalculateNativeWinOcclusion",
                 # Edge に入っている拡張機能はページに入り込んで自分の誤り（「message channel closed」）をページの誤りとして
                 # 出し、画面の誤りを数える試験を落とした（2026-10-09）。数えたいのはこの画面の誤りだけなので止める
                 "--disable-extensions", "--disable-component-extensions-with-background-pages",
                 "about:blank"])
            port_file = Path(self.profile) / "DevToolsActivePort"
            end = time.monotonic() + 30
            page = None
            while page is None:
                if time.monotonic() > end:
                    raise RuntimeError("Edge の DevTools の口が開かない")
                try:
                    port = int(port_file.read_text().split()[0])
                    tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5).read())
                    page = next(t for t in tabs if t["type"] == "page")
                except (OSError, ValueError, StopIteration, IndexError):
                    time.sleep(0.1)
            self._ws_cm = connect(page["webSocketDebuggerUrl"], max_size=None, open_timeout=10)
            self.ws = self._ws_cm.__enter__()
            self.send("Runtime.enable")
            self.send("Page.enable")
            self.send("Network.setCacheDisabled", cacheDisabled=True)
            # 本番の画面は利用者が見ている（前にあり、フォーカスを持つ）タブ。ヘッドレスの窓は PC が重いと隠れた窓と
            # みなされ、描画（requestAnimationFrame）を止められた（全体の試験で visibilityState=hidden になり settle が
            # 落ちた）。前に出し、フォーカスを持つ前提にそろえる
            self.send("Emulation.setFocusEmulationEnabled", enabled=True)
            self.send("Page.bringToFront")
        except BaseException:
            # 開く途中で失敗しても、作った Edge の木と利用者のフォルダを残さない（呼んだ側は close を呼べない）
            self.close()
            raise

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=60))
            if m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(json.dumps(m["params"]["exceptionDetails"], ensure_ascii=False)[:500])
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result")

    def ev(self, js: str):
        r = self.send("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
        return r.get("result", {}).get("value")

    def size(self, w: int, h: int) -> None:
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)

    def wait(self, js: str, what: str, timeout: float = 30) -> None:
        end = time.monotonic() + timeout
        while not self.ev(js):
            if time.monotonic() > end:
                raise AssertionError(f"{what} にならない")
            time.sleep(0.1)

    def settle(self) -> None:
        """Vue が描き直し、2回描画が回るまで待つ。描画が 10 秒回らなければ、そのときのページの見え方を添えて落とす
        （黙って 60 秒待って通信の時間切れにしない: 原因が分かるように）。"""
        js = ("new Promise((r) => { const t = setTimeout(() => r(`描画が回らない（visibilityState=${document.visibilityState}"
              ", hasFocus=${document.hasFocus()}）`), 10000); requestAnimationFrame(() => requestAnimationFrame("
              "() => setTimeout(() => { clearTimeout(t); r('ok'); }, 50))); })")
        r = self.ev(js)
        if r != "ok" and "visibilityState=hidden" in r:
            # ブラウザが窓を隠れたものにした（本番の見ているタブと違う前提）。前に出し直してから待ち直す
            self.send("Page.bringToFront")
            r = self.ev(js)
        if r != "ok":
            raise AssertionError(r)

    def shot(self, path: Path) -> None:
        s = self.send("Page.captureScreenshot", format="png")
        Path(path).write_bytes(base64.b64decode(s["data"]))

    def close(self) -> None:
        """ブラウザに閉じるよう頼み（ページの接続を閉じさせる）、それからジョブごと止める（残った子も止まる）。"""
        asked = self.ws is not None  # ブラウザに閉じるよう頼めた（子が自分で終わるのを待つ）
        if self.ws is not None:
            try:
                self._id += 1
                self.ws.send(json.dumps({"id": self._id, "method": "Browser.close", "params": {}}))
                if self.proc is not None:
                    self.proc.wait(10)
            except Exception:  # noqa: BLE001 - 閉じるときの失敗は試験の結果に関係しない（この後で木ごと止める）
                pass
            try:
                self._ws_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self.ws = None
        if self.job is not None:
            # 子が自分で終わるのを少し待ってから止める（描画のプロセスを途中で止めると、ページが繋いでいた Web の口に
            # 「接続を切られた」の記録が出る）
            end = time.monotonic() + (5 if asked else 0)
            while self.job.active() > 0 and time.monotonic() < end:
                time.sleep(0.05)
            self.job.close()
            self.job = None
        elif self.proc is not None and self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
        if self.proc is not None:
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
        # 止めたプロセスが利用者のフォルダを放すまで少し待ってから消す
        for _ in range(20):
            shutil.rmtree(self.profile, ignore_errors=True)
            if not os.path.exists(self.profile):
                break
            time.sleep(0.1)
