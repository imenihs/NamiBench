"""計測器の本体のうち、rtl_tcp の出口（設計Q §4.3・§6.7、設定の置き場所は設計O §1.1・§1.3）。

Instrument（instrument.py）に混ぜて使う。ここの `_` で始まるメソッドはすべて制御スレッドから呼ばれる。

- サーバー（core/rtltcp/server.py）の知らせ（相手からの命令・接続数の変化）は相手のソケットのスレッドから
  来るので、必ず制御スレッドの待ち行列へ ("call", 関数) で渡す。サーバーは機器の状態に触らない
- 中身は機種のプロファイルの `rtl_tcp.mode` で決まる（設計Q §6.7）
  - "envelope_only"（GigaSt）: ゼロスパンの掃引の振幅を EnvelopeToIQ で IQ（I = 振幅、Q = 0）にして送る。
    大きさの基準はいまの REF レベル（画面で上端に見える強さが 8bit の上端になる）。掃引の間の途切れと
    ゼロスパンでない間は、刻みの仕事（fill_until）が 0 で埋めて流れを止めない
  - "iq"（RTL-SDR など）: 機器の層が読み取った IQ を push_device_iq でそのまま流す（機器スレッドから呼んでよい）
- 相手の命令（主導権は仮に「従う」: 設計Q §6.7）
  - 周波数: GigaSt はその周波数のゼロスパンに、IQ の機器はその周波数を Center に変え、条件が実際に変わったとき
    だけ「rtl_tcp から周波数が変わりました」を1回知らせる（黙って変えない: M-02）
  - サンプリング速度: GigaSt は EnvelopeToIQ の出力の速さを変える（間を埋める: 設計Q §4.3）
  - 利得など: GigaSt は無視。IQ の機器は _rtltcp_device_command へ渡す（RTL-SDR は instrument_rtlsdr.py）
- 先頭の 12 バイトのチューナーの種類・利得の段数は、つないでいる機器から読んだ値（RTL-SDR）。GigaSt は 0・0
- 既定は出さない。設定（settings.json の `rtltcp`）で出すと決めていれば、起動時に始める。終了時に止める
"""
from __future__ import annotations

import errno
import threading
import time
from typing import Optional

import numpy as np

from .instrument_common import CommandError
from .rtltcp import BIND_LAN, BIND_LOCAL, DEFAULT_PORT, TUNER_UNKNOWN, EnvelopeToIQ, RtlTcpServer
from .types import Frame, Quantity, RawSweep, SweepRequest

# 引数を渡さなかった（設定をそのまま残す）
_UNSET = object()

# 中身の質（機種のプロファイルの rtl_tcp.mode: 設計Q §6.7）
CONTENT_ENVELOPE = "envelope_only"
CONTENT_IQ = "iq"

# 相手がサンプリング速度を送ってくるまでの出力の速さ（一般知識: rtl_tcp の既定の 2.048MS/s）
DEFAULT_OUT_RATE = 2_048_000

# 0 で埋める刻みの間隔（秒）
TICK_S = 0.05
# 0 で埋めるのを「いま − 余裕」までにする余裕。遅れて届く掃引の塊を捨てないよう、1回の掃引の見積もりに足す（秒）
FILL_MARGIN_S = 0.3

# ポートが使えないときの OS のエラー番号（Windows は排他で開いたポートに 10013、使用中に 10048 を返す）
_PORT_IN_USE_ERRNO = {errno.EADDRINUSE, errno.EACCES, 10048, 10013}


class RtlTcpMixin:
    """rtl_tcp の出口（Instrument に混ぜる）。"""

    # ------------------------------------------------------------------ 初期化・終了

    def _init_rtltcp(self) -> None:
        cfg = self._settings.get("rtltcp") or {}
        self._rtl_cfg = {
            "enabled": bool(cfg.get("enabled", False)),
            "port": _valid_port(cfg.get("port"), DEFAULT_PORT),
            "bind": cfg.get("bind") if cfg.get("bind") in (BIND_LOCAL, BIND_LAN) else BIND_LOCAL,
        }
        self._rtl_server: Optional[RtlTcpServer] = None
        self._rtl_error: Optional[dict] = None
        self._rtl_env = EnvelopeToIQ(DEFAULT_OUT_RATE)
        self._rtl_ticker: Optional[threading.Thread] = None
        self._rtl_ticker_stop = threading.Event()
        self._rtl_tick_pending = False
        # 相手が最後に送った命令の値（IQ の機器をつなぐときに使う。状態には出さない）
        self._rtl_requested: dict = {}

    def _start_rtltcp_if_enabled(self) -> None:
        """起動時: 設定で出すと決めていれば始める（設計Q §4.3: 既定は出さない）。"""
        if self._rtl_cfg["enabled"]:
            self._rtltcp_start()
            self._changed()

    def _shutdown_rtltcp(self) -> None:
        self._rtltcp_stop(notify=False)

    # ------------------------------------------------------------------ 状態

    def _rtltcp_state(self) -> dict:
        s = self._rtl_server
        running = s is not None and s.running
        return {
            "enabled": self._rtl_cfg["enabled"],
            "running": running,
            # 待ち受けているときは実際のポート、止まっているときは設定のポート
            "port": s.port if running else self._rtl_cfg["port"],
            "bind": self._rtl_cfg["bind"],
            "clients": s.client_count if running else 0,
            "dropped_bytes": s.dropped_bytes if s is not None else 0,
            "error": self._rtl_error,
        }

    def _rtltcp_content(self) -> Optional[str]:
        """いまの機器の送れる中身の質。機器が無ければ接続前のプロファイル（GigaSt）の値。"""
        prof = self._profile
        dev = self._device
        if dev is not None:
            try:
                prof = dev.profile() or prof
            except Exception:  # noqa: BLE001 — 読めなければ本体のプロファイル
                prof = self._profile
        return (prof.get("rtl_tcp") or {}).get("mode")

    # ------------------------------------------------------------------ 命令

    def _cmd_rtltcp_set(self, enabled=_UNSET, port=_UNSET, bind=_UNSET) -> dict:
        """rtl_tcp の出口を出す・止める、ポート番号・どこからの接続を受けるかを変える（設計Q §4.3・設計O §1.3）。

        渡さなかった項目は変えない。設定は settings.json の `rtltcp` へすぐ書く（変えたらすぐ効く: 設計O §1.3）。
        ポートが使えないときは命令は失敗にせず、状態の error と知らせ rtltcp_port_in_use で返す
        （設定は保存したまま。次に rtltcp_set するか次の起動でもう一度試す）。
        """
        cfg = dict(self._rtl_cfg)
        if enabled is not _UNSET:
            if not isinstance(enabled, bool):
                raise CommandError("bad_value", "enabled")
            cfg["enabled"] = enabled
        if port is not _UNSET:
            if isinstance(port, bool) or not isinstance(port, int):
                raise CommandError("not_integer", "port")
            if not 1 <= port <= 65535:
                raise CommandError("bad_value", "port", {"min": 1, "max": 65535})
            cfg["port"] = port
        if bind is not _UNSET:
            if bind not in (BIND_LOCAL, BIND_LAN):
                raise CommandError("bad_value", "bind", {"choices": [BIND_LOCAL, BIND_LAN]})
            cfg["bind"] = bind
        changed_addr = (cfg["port"], cfg["bind"]) != (self._rtl_cfg["port"], self._rtl_cfg["bind"])
        self._rtl_cfg = cfg
        self._settings.set("rtltcp", dict(cfg))
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})
        running = self._rtl_server is not None and self._rtl_server.running
        if not cfg["enabled"]:
            self._rtl_error = None
            if running:
                self._rtltcp_stop()
        elif not running or changed_addr:
            if running:
                self._rtltcp_stop()
            self._rtltcp_start()
        self._changed()
        return {"rtltcp": self._rtltcp_state()}

    # ------------------------------------------------------------------ 開始・停止

    def _rtltcp_start(self) -> None:
        port, bind = self._rtl_cfg["port"], self._rtl_cfg["bind"]
        tuner, gains = self._rtltcp_header()
        server = RtlTcpServer(
            on_command=self._rtltcp_from_socket_command,
            on_clients_changed=self._rtltcp_from_socket_clients,
            tuner_type=tuner,
            gain_count=gains,
        )
        try:
            actual = server.start(port, bind)
        except OSError as e:
            code = "port_in_use" if (e.errno in _PORT_IN_USE_ERRNO or getattr(e, "winerror", None)
                                     in _PORT_IN_USE_ERRNO) else "start_failed"
            self._rtl_error = {"code": code, "port": port, "bind": bind, "message": str(e)}
            self._notice("rtltcp_port_in_use" if code == "port_in_use" else "rtltcp_start_failed",
                         dict(self._rtl_error))
            return
        self._rtl_server = server
        self._rtl_error = None
        self._rtl_env.reset()
        self._rtl_ticker_stop = threading.Event()
        self._rtl_ticker = threading.Thread(target=self._rtltcp_ticker, args=(self._rtl_ticker_stop,),
                                            name="pygigast-rtltcp-tick", daemon=True)
        self._rtl_ticker.start()
        self._notice("rtltcp_started", {"port": actual, "bind": bind})

    def _rtltcp_header(self) -> tuple[int, int]:
        """先頭の 12 バイトのチューナーの種類と利得の段数（設計Q §6.7）。

        RTL-SDR は機器から読んだ値。GigaSt はチューナーを持たないので種類 0（不明）・利得の段数 0
        （設計Q §6.8 の実装で決めたこと）。
        """
        if self._is_rtlsdr():
            return self._rtlsdr_rtltcp_header()
        if self._is_plutosdr():
            return self._plutosdr_rtltcp_header()
        return TUNER_UNKNOWN, 0

    def _rtltcp_update_header(self) -> None:
        """機器をつないだとき: 次につながる相手に送る先頭の中身を変える（つながっている相手には送り直さない）。"""
        if self._rtl_server is not None:
            self._rtl_server.set_header(*self._rtltcp_header())

    def _rtltcp_stop(self, notify: bool = True) -> None:
        server = self._rtl_server
        self._rtl_server = None
        self._rtl_ticker_stop.set()
        if self._rtl_ticker is not None:
            self._rtl_ticker.join(1.0)
            self._rtl_ticker = None
        if server is None:
            return
        port = server.port
        server.stop()
        self._rtl_env.reset()
        if notify:
            self._notice("rtltcp_stopped", {"port": port})

    # ------------------------------------------------------------------ ソケットのスレッドから（制御スレッドへ渡すだけ）

    def _rtltcp_from_socket_command(self, name: str, value) -> None:
        self._q.put(("call", lambda: self._rtltcp_on_command(name, value)))

    def _rtltcp_from_socket_clients(self, count: int) -> None:
        self._q.put(("call", self._rtltcp_on_clients))

    def _rtltcp_ticker(self, stop: threading.Event) -> None:
        """刻みのスレッド。0 で埋める仕事を制御スレッドへ渡す（前の分が残っていれば積まない）。"""
        while not stop.wait(TICK_S):
            if not self._rtl_tick_pending:
                self._rtl_tick_pending = True
                self._q.put(("call", self._rtltcp_tick))

    # ------------------------------------------------------------------ 制御スレッド

    def _rtltcp_on_clients(self) -> None:
        # 相手がいなくなったら流れの時刻を捨てる（次の相手には、つながった時点から流す）
        if self._rtl_server is not None and self._rtl_server.client_count == 0:
            self._rtl_env.reset()
        # RTL-SDR は相手がつながっている間チューナーの中心を動かさない（設計Q §6.7）
        self._rtlsdr_on_clients()
        self._changed()

    def _rtltcp_tick(self) -> None:
        self._rtl_tick_pending = False
        s = self._rtl_server
        if s is None or not s.running or s.client_count == 0:
            return
        if self._rtltcp_content() != CONTENT_ENVELOPE:
            return  # IQ の機器は機器の層が流す（push_device_iq）
        margin = FILL_MARGIN_S + float(getattr(self._adopted, "estimated_sweep_s", 0.0) or 0.0)
        iq = self._rtl_env.fill_until(time.monotonic() - margin)
        if iq.size:
            s.push_iq(iq, self._rtl_env.out_rate)

    def _rtltcp_ref_dbm(self) -> float:
        """振幅の基準（設計Q §4.3: いまの REF レベル）。REF が dBm で決まっていなければ 0dBm。"""
        amp = self._amplitude
        ref = amp.get("ref_level")
        if ref is None or amp.get("unit") not in (None, "dBm"):
            return 0.0
        return float(ref)

    def _rtltcp_on_sweep(self, raw: RawSweep, frame: Frame) -> None:
        """掃引1回ごと。GigaSt のゼロスパンなら振幅を IQ にして送る（設計Q §4.3）。"""
        s = self._rtl_server
        if s is None or not s.running or s.client_count == 0:
            return
        a = raw.adopted
        if not a.zero_span or self._rtltcp_content() != CONTENT_ENVELOPE or frame.quantity != Quantity.POWER:
            return  # 振幅にするのは電力だけ（NF の値は電力ではない: 設計M）
        with np.errstate(divide="ignore", invalid="ignore"):
            dbm = 10.0 * np.log10(np.asarray(frame.values, dtype=np.float64)) + 30.0
        if dbm.size == 0:
            return
        t_first, rate = self._rtltcp_point_times(raw, dbm.size)
        self._rtl_env.ref_dbm = self._rtltcp_ref_dbm()
        iq = self._rtl_env.push(dbm, t_first, rate)
        if iq.size:
            s.push_iq(iq, self._rtl_env.out_rate)

    def _rtltcp_point_times(self, raw: RawSweep, n: int) -> tuple[float, float]:
        """最初の点の時刻（time.monotonic の秒）と点の速さ（点/秒）。

        機器が測った1回の時間から、初バイトまでの見積もりを引いて1点の時間を出す（点の速さは機器が測った
        実際の値を使う: rtltcp/envelope.py）。何回か測る条件（イメージ除去・NF）は最後の1回を採る（イメージ除去の
        正確で区間に分けたときは、区間ごとの点数の違いを見ない近い値）。
        前置きの点（切り落とした点）の分だけ最初の点の時刻を後ろへずらす。
        """
        a = raw.adopted
        passes = max(1, int(raw.extra.get("passes", 1) or 1))
        dev_points = int(raw.extra.get("device_points", n + a.preroll_points) or (n + a.preroll_points))
        try:
            first_est, per_point_est = self._timing_fn(a.axis.step_hz, a.rbw_hz, a.mode)
        except Exception:  # noqa: BLE001 — 見積もりが無くても測った時間で足りる
            first_est, per_point_est = 0.0, 0.0
        per_pass = raw.duration_s / passes
        body = per_pass - first_est
        if body > 0 and dev_points > 0:
            period = body / dev_points
        elif per_point_est > 0:
            period = per_point_est
        else:
            period = per_pass / max(dev_points, 1)
        period = max(period, 1e-7)
        # 掃引を始めた壁時計の時刻を単調な時計へ写す
        mono_start = raw.started_at - (time.time() - time.monotonic())
        t_first = mono_start + (passes - 1) * per_pass + first_est + a.preroll_points * period
        return t_first, 1.0 / period

    def _rtltcp_on_command(self, name: str, value) -> None:
        """相手の命令（設計Q §6.7。主導権は仮に「従う」）。"""
        self._rtl_requested[name] = value
        content = self._rtltcp_content()
        if name == "set_freq":
            self._rtltcp_set_freq(int(value), content)
        elif name == "set_sample_rate":
            if content == CONTENT_ENVELOPE:
                if isinstance(value, int) and value > 0:
                    self._rtl_env.set_out_rate(value)
            else:
                self._rtltcp_device_command(name, value)
        elif content != CONTENT_ENVELOPE:
            self._rtltcp_device_command(name, value)
        # GigaSt では利得などは無視する（設計Q §4.3）

    def _rtltcp_set_freq(self, freq_hz: int, content: Optional[str]) -> None:
        """周波数の命令: GigaSt はゼロスパン、IQ の機器はその周波数を Center に。変わったときだけ1回知らせる。"""
        if self._is_fft_sdr():
            # RTL-SDR・PlutoSDR はその周波数をチューナーの中心にしたリアルタイムの動き（instrument_rtlsdr.py）
            self._rtlsdr_tcp_set_freq(freq_hz)
            return
        before = self._adopted
        if content == CONTENT_ENVELOPE:
            req = SweepRequest(center_hz=freq_hz, span_hz=0, edited=("center_hz", "span_hz"))
        else:
            req = SweepRequest(center_hz=freq_hz, edited=("center_hz",))
        try:
            out = self._apply(req)
        except CommandError as e:
            # 基準を取っている間など、条件を変えられないとき。変えなかったことを知らせる
            self._notice("rtltcp_freq_from_client", {"freq_hz": freq_hz, "applied": False, "code": e.code})
            return
        a = self._adopted
        if (a.center_hz, a.zero_span, a.span_hz) == (before.center_hz, before.zero_span, before.span_hz):
            return  # 同じ周波数の繰り返し（外のソフトは同じ値を何度も送る）は知らせない
        self._notice("rtltcp_freq_from_client", {
            "freq_hz": freq_hz, "applied": True, "center_hz": a.center_hz, "span_hz": a.span_hz,
            "adjustments": out.get("adjustments", []),
        })

    def _rtltcp_device_command(self, name: str, value) -> None:
        """IQ の機器への命令（サンプリング速度・利得など）。RTL-SDR は instrument_rtlsdr.py が従う。

        ほかの IQ の機器は、受けた値を _rtl_requested に残すだけ。
        """
        if self._is_rtlsdr():
            self._rtlsdr_tcp_command(name, value)
        elif self._is_plutosdr():
            self._plutosdr_tcp_command(name, value)

    # ------------------------------------------------------------------ 機器の層から（どのスレッドからでもよい）

    def push_device_iq(self, iq: np.ndarray, rate: int) -> None:
        """IQ の機器（RTL-SDR など）が読み取った IQ をそのまま流す口（設計Q §6.7）。

        iq は complex64 で振り切れを ±1.0、rate はサンプリング速度（Hz）。機器スレッドから呼んでよい
        （サーバーは待たない。相手がいなければ何もしない）。機器の状態には触らない。
        """
        s = self._rtl_server
        if s is None or not s.running:
            return
        s.push_iq(iq, int(rate))


def _valid_port(v, default: int) -> int:
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 65535:
        return v
    return default
