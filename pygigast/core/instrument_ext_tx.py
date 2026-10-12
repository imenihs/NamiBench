"""計測器の本体のうち、外部 IQ 送信の口（設計S §1）。Instrument に混ぜる。`_` で始まるメソッドは制御スレッドから呼ぶ。

- 口（core/exttx/server.py）は 127.0.0.1 だけで待つ。既定は待たない（設定 `ext_tx.enabled`）。相手は1つだけ
- start を受けたら: 断る条件（PlutoSDR が無い・送信できない機器・SG/TG/S11 のモード・利得の補正を測っている・範囲の外の
  速さと LO・もう始めた）を見る → 掃引の仕事を外す → 別のスレッドで機器の標本化の速さを入れて読み戻す（掃引の錠を待つので
  制御スレッドでは待たない）→ 本体のプロファイルと掃引条件モデルを新しい速さにし、条件を採り直す → 押すスレッドを始めて
  ready（クレジット付き）。速さが同じに読み戻せなければ元へ戻して sample_rate_unsupported で断る
- 止める道はどれも同じ: 押すスレッドに止める印を付け、止める順を通り終えた知らせ（end）で stopped を送って閉じ、元の速さへ
  戻す。プロファイルと掃引条件モデルは機器への書き込みの成否によらず必ず元へ戻す（P2）
- 外部の送信の間（start を受けてから閉じるまで）は SG・TG・S11 のモードと利得の補正を理由付きで断る（ユーザー決定 3）
- 外部の送信の減衰は SG・TG と別の値を覚える（初期値は最小の出力: O1）。クライアントは変えられない（画面だけ: O2）
- IQ の流れは制御スレッドを通さない（サーバーの受けのスレッドから押すスレッドの待ち行列へ直接）
"""
from __future__ import annotations

import math
import threading
import time
from typing import Optional

from .devices.plutosdr.ext_tx import ExtTxSpec
from .devices.rtlsdr.driver import fft_at_rate
from .exttx import DEFAULT_PORT, FULL_SCALE, PROTOCOL, ExtIqServer
from .instrument_common import CommandError
from .sweep.model import SweepModel
from .trace import ConditionChange
from .types import DeviceDisconnected, DeviceError, Mode, SweepRequest

_UNSET = object()
_TX_MODES = (Mode.SG, Mode.TG, Mode.S11)
# 止めたときの理由（stopped.reason）
STOP_REASONS = ("client", "user", "device_lost", "app_exit", "starved", "protocol_error", "disabled")
# アプリを閉じるとき、押すスレッドが止める順を通り終えるのを待つ上限（秒。過ぎたら機器の口が最小限を書く: 設計S §1 §5）
EXIT_WAIT_S = 0.5


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class ExtTxMixin:
    """外部 IQ 送信の口の設定・状態・命令（Instrument に混ぜる）。"""

    def _init_exttx(self) -> None:
        cfg = self._settings.get("ext_tx") or {}
        port = cfg.get("port")
        atten = cfg.get("atten_db")
        self._ext_cfg = {
            "enabled": bool(cfg.get("enabled", False)),
            "port": port if _is_int(port) and 1 <= port <= 65535 else DEFAULT_PORT,
            # 外部の送信の減衰（SG・TG と別の値。None は最小の出力: O1）
            "atten_db": float(atten) if isinstance(atten, (int, float)) and not isinstance(atten, bool)
            and math.isfinite(atten) else None,
        }
        self._ext_server: Optional[ExtIqServer] = None
        self._ext_error: Optional[dict] = None
        self._ext_client: Optional[dict] = None  # つないでいる相手（id・つないだ時刻）
        self._ext_session: Optional[dict] = None  # start を受けてから閉じるまで
        self._ext_hold = False  # 標本化の速さを切り替えている間は掃引の仕事を渡さない
        self._ext_base_profile: Optional[dict] = None  # 速さを変える前の本体のプロファイル
        self._ext_welcome: dict = {}
        self._ext_seq = 0

    # ------------------------------------------------------------------ 状態

    def _exttx_spec(self) -> ExtTxSpec:
        return ExtTxSpec.from_profile(self._plutosdr_profile())

    def _exttx_atten(self) -> float:
        a = self._ext_cfg["atten_db"]
        return float(self._plutosdr_profile()["source"]["tx_atten_db"]["min"]) if a is None else float(a)

    def _exttx_active(self) -> bool:
        """外部の送信の間（start を受けてから閉じるまで: SG・TG・S11 を断る）。"""
        return self._ext_session is not None

    def _exttx_state(self) -> dict:
        s = self._ext_server
        listening = s is not None and s.running
        sess = self._ext_session
        session = None
        if sess is not None:
            st = sess["stream"].stats() if sess.get("stream") is not None else {}
            state = sess["state"]
            if state == "waiting_data" and st.get("state") == "on":
                state = "on"
            session = {
                "state": state,
                "client": sess["client"],
                "sample_rate_hz": sess["rate"],
                "tx_lo_hz": sess["lo"],
                "tx_atten_db": self._exttx_atten(),
                "consumed": int(st.get("consumed") or 0),
                "underrun": int(st.get("underrun") or 0),
                "last_underrun_at": st.get("last_underrun_at"),
                "power_dbfs": st.get("power_dbfs"),
            }
        rng = self._plutosdr_profile()["source"]["tx_atten_db"]
        self._exttx_refresh_welcome()
        return {
            "enabled": self._ext_cfg["enabled"],
            "port": s.port if listening else self._ext_cfg["port"],
            "listening": listening,
            "error": self._ext_error,
            "client": None if self._ext_client is None else dict(self._ext_client),
            "session": session,
            "atten_db": self._exttx_atten(),
            "atten_range": {"min": float(rng["min"]), "max": float(rng["max"]), "step": float(rng["step"])},
        }

    def _exttx_refresh_welcome(self) -> None:
        """welcome の中身（サーバーの受けのスレッドが読む。作るのは制御スレッドだけ）。"""
        from .. import __version__

        prof = self._plutosdr_profile()
        spec = self._exttx_spec()
        pluto = self._conn == "connected" and self._is_plutosdr()
        model = (self._profile.get("model") if self._conn == "connected" else None)
        lo = (prof.get("source") or {}).get("tx_lo_hz") or [0, 0]
        self._ext_welcome = {
            "type": "welcome", "protocol": list(PROTOCOL), "app_version": __version__,
            "device": {"model": model, "connected": self._conn == "connected", "can_transmit": pluto},
            "tx_lo_range_hz": [int(lo[0]), int(lo[1])],
            "sample_rate_range_hz": [spec.rate_min_hz, spec.rate_max_hz],
            "sample_rate_hint": "a multiple of 1 kHz keeps the SA RBW 375-1500 Hz (Hann) usable",
            "iq_format": "ci16le", "full_scale": FULL_SCALE,
            "block_samples": spec.block_samples, "queue_samples": spec.queue_samples,
        }

    # ------------------------------------------------------------------ 起動・終了

    def _start_exttx_if_enabled(self) -> None:
        if self._ext_cfg["enabled"]:
            self._exttx_listen()
            self._changed()

    def _shutdown_exttx(self) -> None:
        """アプリを閉じる: 送信を止め（押すスレッドが止める順を通るのを最長 EXIT_WAIT_S 待つ）、stopped を送って閉じる。"""
        sess = self._ext_session
        stream = sess.get("stream") if sess is not None else None
        dev = self._device
        if stream is not None and dev is not None:
            dev.ext_tx_stop("app_exit")
            stream.join(EXIT_WAIT_S)
        if sess is not None:
            self._exttx_finish("app_exit", None, restore_device=False)
        elif self._ext_client is not None and self._ext_server is not None:
            self._ext_server.send(self._ext_client["id"], {"type": "stopped", "reason": "app_exit"})
            self._ext_server.close_client(self._ext_client["id"])
        if self._ext_server is not None:
            time.sleep(0.05)  # stopped を送り出す間（送りのスレッド）
            self._ext_server.stop()
            self._ext_server = None

    # ------------------------------------------------------------------ 命令

    def _cmd_ext_tx_set(self, enabled=_UNSET, port=_UNSET) -> dict:
        """外部 IQ 送信の口を待つ・止める、ポートを変える。設定はすぐ残す。ポートが使えないときは命令を失敗にせず、状態の
        error と知らせで返す（rtl_tcp と同じ）。切る・ポートを変えると、送信中なら止めて（disabled）相手を閉じる。"""
        cfg = dict(self._ext_cfg)
        if enabled is not _UNSET:
            if not isinstance(enabled, bool):
                raise CommandError("bad_value", "enabled")
            cfg["enabled"] = enabled
        if port is not _UNSET:
            if not _is_int(port):
                raise CommandError("not_integer", "port")
            if not 1 <= port <= 65535:
                raise CommandError("bad_value", "port", {"min": 1, "max": 65535})
            cfg["port"] = port
        changed_port = cfg["port"] != self._ext_cfg["port"]
        self._ext_cfg = cfg
        self._exttx_save()
        running = self._ext_server is not None and self._ext_server.running
        if not cfg["enabled"]:
            self._ext_error = None
            if running or self._ext_server is not None:
                self._exttx_unlisten("disabled")
        elif not running or changed_port:
            if self._ext_server is not None:
                self._exttx_unlisten("disabled")
            self._exttx_listen()
        self._changed()
        return {"ext_tx": self._exttx_state()}

    def _cmd_ext_tx_stop(self) -> dict:
        """利用者が止める（状態バー・信号源のタブの［止める］）: 送信を止めて接続を閉じる。"""
        if self._ext_session is not None or self._ext_client is not None:
            self._exttx_stop("user")
        self._changed()
        return {"ext_tx": self._exttx_state()}

    def _cmd_ext_tx_atten(self, atten_db) -> dict:
        """外部の送信の減衰（画面だけで変える: O2）。送信中ならすぐ入れる（掃引の終わりを待たない: 設計S §1 §6.1）。"""
        rng = self._plutosdr_profile()["source"]["tx_atten_db"]
        lo, hi = float(rng["min"]), float(rng["max"])
        if isinstance(atten_db, bool) or not isinstance(atten_db, (int, float)) or not math.isfinite(atten_db) \
                or not lo <= atten_db <= hi:
            raise CommandError("bad_value", "atten_db", {"min": lo, "max": hi})
        step = float(rng.get("step") or 0.25)
        v = round(round(float(atten_db) / step) * step, 2)
        self._ext_cfg["atten_db"] = v
        self._exttx_save()
        sess = self._ext_session
        dev = self._device
        if sess is not None and sess.get("stream") is not None and dev is not None:
            sess["stream"].set_atten(v)
        self._changed()
        return {"ext_tx": self._exttx_state()}

    def _exttx_save(self) -> None:
        self._settings.set("ext_tx", dict(self._ext_cfg))
        try:
            self._settings.save()
        except OSError as e:
            self._notice("settings_unwritable", {"message": str(e)})

    # ------------------------------------------------------------------ 待ち受け

    def _exttx_listen(self) -> None:
        port = self._ext_cfg["port"]
        server = ExtIqServer(self._exttx_from_socket, lambda: self._ext_welcome)
        self._exttx_refresh_welcome()
        try:
            actual = server.start(port)
        except OSError as e:
            from .instrument_rtltcp import _PORT_IN_USE_ERRNO

            code = "port_in_use" if (e.errno in _PORT_IN_USE_ERRNO or getattr(e, "winerror", None)
                                     in _PORT_IN_USE_ERRNO) else "start_failed"
            self._ext_error = {"code": code, "port": port, "message": str(e)}
            self._notice("ext_tx_port_error", dict(self._ext_error))
            return
        self._ext_server = server
        self._ext_error = None
        self._notice("ext_tx_listening", {"port": actual})

    def _exttx_unlisten(self, reason: str) -> None:
        """待ち受けを止める。送信中なら止めて（reason）stopped を送ってから閉じる。"""
        if self._ext_session is not None or self._ext_client is not None:
            self._exttx_stop(reason)
            sess = self._ext_session
            if sess is not None and sess.get("stream") is not None:
                sess["stream"].join(EXIT_WAIT_S)  # 止める順を通るのを待つ（ふつうは塊1つ分）
                self._exttx_drain()
        server, self._ext_server = self._ext_server, None
        if server is not None:
            time.sleep(0.05)
            server.stop()
            self._notice("ext_tx_closed", {"port": server.port or self._ext_cfg["port"]})
        self._ext_client = None

    def _exttx_drain(self) -> None:
        """待ち行列にある押すスレッドの終わりの知らせを、いま処理する（待ち受けを止める前に stopped を送るため）。"""
        sess = self._ext_session
        if sess is None or sess.get("stream") is None or sess["stream"].alive:
            return
        st = sess["stream"]
        self._exttx_finish(st.end_reason or "disabled", st.end_detail)

    # ------------------------------------------------------------------ ソケットのスレッドから（制御スレッドへ渡すだけ）

    def _exttx_from_socket(self, kind: str, cid: int, detail: dict) -> None:
        self._q.put(("call", lambda: self._exttx_on_socket(kind, cid, detail)))

    def _exttx_on_socket(self, kind: str, cid: int, detail: dict) -> None:
        server = self._ext_server
        if server is None:
            return
        if kind == "connected":
            if server.client_id == cid:
                self._ext_client = {"id": cid, "connected_at": time.time()}
                self._changed()
            return
        cur = self._ext_client
        if cur is None or cur["id"] != cid:
            return
        if kind == "closed":
            self._ext_client = None
            if self._ext_session is not None:
                self._exttx_stop("client", client_gone=True)
            self._changed()
        elif kind == "protocol_error":
            self._exttx_stop("protocol_error", detail.get("detail"))
        elif kind == "message":
            t = detail.get("type")
            if t == "start":
                self._exttx_on_start(cid, detail)
            elif t == "stop":
                self._exttx_stop("client")
            # 知らない種類は無視する（minor を上げて足した項目・メッセージをクライアントが送っても切らない）

    # ------------------------------------------------------------------ 始める（設計S §1 §3.3）

    def _exttx_refuse(self, cid: int, code: str, **detail) -> None:
        self._ext_server.send(cid, {"type": "error", "code": code, **detail})
        self._notice("ext_tx_refused", {"code": code, **detail})

    def _exttx_on_start(self, cid: int, msg: dict) -> None:
        if self._ext_session is not None:
            self._exttx_refuse(cid, "already_started")
            return
        if self._ext_hold:
            # 前の送信の後で元の速さへ戻している途中: 戻し終えてから受ける（戻すのと入れるのが入れ違わないように）
            threading.Timer(0.05, lambda: self._q.put(("call", lambda: self._exttx_on_start_if_current(cid, msg)))).start()
            return
        if self._conn != "connected" or self._device is None:
            self._exttx_refuse(cid, "not_connected")
            return
        if not self._is_plutosdr():
            self._exttx_refuse(cid, "device_cannot_transmit", model=self._profile.get("model"))
            return
        if self._adopted.mode in _TX_MODES:
            self._exttx_refuse(cid, "mode_conflict", mode=self._adopted.mode.value)
            return
        if self._cal_job is not None:
            self._exttx_refuse(cid, "calibrating")
            return
        spec = self._exttx_spec()
        rate = msg.get("sample_rate_hz")
        if not _is_int(rate) or not spec.rate_min_hz <= rate <= spec.rate_max_hz or not self._exttx_rate_has_rbw(rate):
            self._exttx_refuse(cid, "sample_rate_unsupported", min=spec.rate_min_hz, max=spec.rate_max_hz)
            return
        lo_rng = self._profile["source"]["tx_lo_hz"]
        lo = msg.get("tx_lo_hz")
        if not _is_int(lo) or not int(lo_rng[0]) <= lo <= int(lo_rng[1]):
            self._exttx_refuse(cid, "lo_out_of_range", min=int(lo_rng[0]), max=int(lo_rng[1]))
            return
        name = msg.get("client")
        self._ext_seq += 1
        sess = {"seq": self._ext_seq, "cid": cid, "client": str(name)[:64] if isinstance(name, str) else "",
                "state": "preparing", "rate": int(rate), "lo": int(lo), "stream": None, "started_at": time.time()}
        self._ext_session = sess
        self._listen_end("ext_tx")  # 聞く機能は読む速さで復調するので、速さを変える前にやめる
        self._ext_hold = True
        self._push_job()  # 掃引の仕事を外す（機器スレッドは位置1つ分で戻る）
        dev = self._device

        def run() -> None:
            try:
                res = ("ok", dev.ext_tx_prepare(int(rate)))
            except DeviceDisconnected as e:
                res = ("lost", str(e))
            except DeviceError as e:
                res = ("rejected", str(e))  # 機器が速さを受け付けなかった
            self._q.put(("call", lambda: self._exttx_prepared(sess, dev, res)))

        threading.Thread(target=run, name="pygigast-ext-rate", daemon=True).start()
        self._changed()

    def _exttx_on_start_if_current(self, cid: int, msg: dict) -> None:
        cur = self._ext_client
        if cur is not None and cur["id"] == cid and self._ext_server is not None:
            self._exttx_on_start(cid, msg)

    def _exttx_rate_has_rbw(self, rate: int) -> bool:
        """その速さで使える SA の RBW が1つでもあるか（FFT の長さ = 速さ ÷ 格子 が整数になる選択肢）。"""
        prof = self._plutosdr_profile()
        grid_rate = int(prof["fft"].get("grid_rate_hz") or prof["fft"]["sample_rate_hz"])
        return any(rate % (grid_rate // int(c["fft_n"])) == 0 for c in prof["rbw"]["choices"])

    def _exttx_prepared(self, sess: dict, dev, res: tuple) -> None:
        ok = res[0] == "ok" and res[1] == sess["rate"]
        if self._ext_session is not sess or dev is not self._device:
            # 準備の間に止めた・機器が変わった: 速さを入れていたら元へ戻す
            if ok and dev is self._device:
                self._exttx_restore_device(dev)
            else:
                self._exttx_release_hold()
            return
        if not ok:
            self._ext_session = None
            spec = self._exttx_spec()
            if res[0] == "lost":
                self._exttx_refuse(sess["cid"], "not_connected", detail=res[1])
            else:
                extra = {"readback": int(res[1])} if res[0] == "ok" else {"detail": res[1]}
                self._exttx_refuse(sess["cid"], "sample_rate_unsupported", min=spec.rate_min_hz, max=spec.rate_max_hz,
                                   **extra)
            self._exttx_release_hold()
            self._changed()
            return
        self._exttx_apply_rate(sess["rate"])
        server, cid = self._ext_server, sess["cid"]
        seq = sess["seq"]
        try:
            stream = dev.ext_tx_start(sess["lo"], self._exttx_atten(), lambda obj: server.send(cid, obj),
                                      lambda kind, d: self._q.put(("call", lambda: self._exttx_on_stream(seq, kind, d))))
        except DeviceError as e:
            self._ext_session = None
            self._exttx_refuse(cid, "not_connected", detail=str(e))
            self._exttx_restore_model()
            self._exttx_restore_device(dev)
            self._changed()
            return
        sess["stream"] = stream
        sess["state"] = "waiting_data"
        server.set_sink(cid, stream.feed)  # ready より先に（ready を受けたクライアントがすぐ送ってくる）
        server.send(cid, {"type": "ready", "sample_rate_hz": int(res[1]), "tx_lo_hz": sess["lo"],
                          "credit": stream.credit_initial})
        self._notice("ext_tx_started", {"client": sess["client"], "sample_rate_hz": sess["rate"], "tx_lo_hz": sess["lo"]})
        self._exttx_release_hold()
        self._changed()

    # ------------------------------------------------------------------ 速さの切り替え（本体の側）

    def _exttx_apply_rate(self, fs: int) -> None:
        """本体のプロファイルと掃引条件モデルを fs のもの（格子と RBW の値は元のまま）にし、条件を採り直す。"""
        base = self._ext_base_profile or self._profile
        self._ext_base_profile = base
        prof = dict(base)
        prof["fft"] = fft_at_rate(base["fft"], fs)
        self._exttx_set_profile(prof)

    def _exttx_restore_model(self) -> None:
        """元の速さ（4.096MS/s）のプロファイルとモデルへ戻す。機器への書き込みの成否によらず必ず戻す（P2）。"""
        base, self._ext_base_profile = self._ext_base_profile, None
        if base is not None:
            self._exttx_set_profile(base)

    def _exttx_set_profile(self, prof: dict) -> None:
        old = self._adopted
        self._profile = prof
        self._timing_fn = self._timing_for(prof)
        self._model = SweepModel(prof, self._timing_fn)
        # 範囲・RBW の好み・Span 固定は今のまま採り直す（使えない RBW は近い RBW にして欄のそばで知らせ、好みは残す:
        # 設計C §5.14・§5.1。終わったら好みに戻る）
        req = SweepRequest(start_hz=old.start_hz, stop_hz=old.stop_hz, edited=("start_hz", "stop_hz"),
                           rbw_preference_hz=old.rbw_preference_hz, one_shot=True)
        try:
            new = self._model.apply(req, old)
        except (ValueError, TypeError):
            new, _ = self._reapply_conditions(old)
        self._adopted = new
        self._last_estimate = new.estimated_sweep_s
        # 速さを変えると値の意味（帯域の形）が変わるので、蓄積を1回捨てて知らせる（受信の利得と同じ扱い: M-03）
        self._on_condition(ConditionChange.CALIBRATION, cause="sample_rate")
        self._rtlsdr_overload_key = None
        if new.adjustments:
            from .instrument_common import adjustments_to_list

            self._notice("ext_tx_rate_adjusted", {"sample_rate_hz": int(prof["fft"]["sample_rate_hz"]),
                                                  "adjustments": adjustments_to_list(new)})
        self._plutosdr_rebuild_decim(int(prof["fft"]["sample_rate_hz"]))  # rtl_tcp の出口（P3）
        tgt = self._rtlsdr_fixed_target()
        if tgt is not None:
            try:
                self._rtlsdr_fit_display(tgt[1], tgt[0], record_undo=False)  # 使える幅が変わった
            except CommandError:
                pass

    def _exttx_restore_device(self, dev) -> None:
        """機器を元の速さへ戻す（別のスレッド: 掃引の錠を待つ）。終わったら掃引の仕事を戻す。"""
        def run() -> None:
            try:
                dev.ext_tx_restore()
            except Exception:  # noqa: BLE001 — 戻せなくても本体の側は戻した（切れていれば次の open が書く）
                pass
            self._q.put(("call", self._exttx_release_hold))

        threading.Thread(target=run, name="pygigast-ext-rate", daemon=True).start()

    def _exttx_release_hold(self) -> None:
        if self._ext_hold:
            self._ext_hold = False
            self._push_job()
            self._changed()

    def _exttx_holds_job(self) -> bool:
        """速さを切り替えている間は掃引を渡さない（_push_job から呼ぶ）。"""
        return self._ext_hold

    # ------------------------------------------------------------------ 止める（設計S §1 §5）

    def _exttx_stop(self, reason: str, detail: Optional[str] = None, client_gone: bool = False) -> None:
        """止める道の入口（全部の理由で同じ）。押すスレッドがいれば止める印を付け、終わりの知らせで閉じる。"""
        sess = self._ext_session
        if sess is None:
            cur = self._ext_client
            if cur is not None and not client_gone and self._ext_server is not None:
                msg = {"type": "stopped", "reason": reason}
                if detail:
                    msg["detail"] = detail
                self._ext_server.send(cur["id"], msg)
                self._ext_server.close_client(cur["id"])
            return
        if client_gone:
            sess["client_gone"] = True
        stream = sess.get("stream")
        if stream is not None and stream.alive:
            sess["state"] = "stopping"
            dev = self._device
            if dev is not None:
                dev.ext_tx_stop(reason, detail)
            else:
                stream.request_stop(reason, detail)
            self._changed()
            return
        if stream is not None and stream.end_reason is not None:
            # 押すスレッドはもう終わった（終わりの知らせはまだ待ち行列の中）: そちらの理由で閉じる
            reason, detail = stream.end_reason, stream.end_detail
        self._exttx_finish(reason, detail)

    def _exttx_on_stream(self, seq: int, kind: str, detail: dict) -> None:
        sess = self._ext_session
        if sess is None or sess["seq"] != seq:
            return
        if kind == "on":
            sess["state"] = "on"
            self._changed()
        elif kind == "stats":
            self._changed()
        elif kind == "end":
            self._exttx_finish(detail.get("reason") or "device_lost", detail.get("detail"))

    def _exttx_finish(self, reason: str, detail: Optional[str], restore_device: bool = True) -> None:
        """送信が止まった（押すスレッドが止める順を通った・始める前・機器が抜けた）: stopped を送って閉じ、元の速さへ戻す。
        restore_device が False（機器を外す・アプリを閉じる）なら機器へは書かない（本体の側だけ戻す: P2）。"""
        sess = self._ext_session
        if sess is None:
            return
        self._ext_session = None
        server = self._ext_server
        if server is not None and not sess.get("client_gone"):
            msg = {"type": "stopped", "reason": reason}
            if detail:
                msg["detail"] = detail
            server.send(sess["cid"], msg)
            server.close_client(sess["cid"])
        if sess["state"] != "preparing":
            self._exttx_restore_model()
            dev = self._device
            if restore_device and dev is not None and self._is_plutosdr():
                self._ext_hold = True
                self._push_job()
                self._exttx_restore_device(dev)
            else:
                self._ext_hold = False
        st = sess.get("stream")
        self._notice("ext_tx_stopped", {"reason": reason, "detail": detail, "client": sess["client"],
                                        "underrun": None if st is None else int(st.underrun)})
        self._changed()

    def _exttx_device_gone(self, reason: str = "device_lost") -> None:
        """機器を外す（_drop_device・切断）: 送信を止める印を付け（機器の口が押すスレッドに任せる: P1）、待たずに閉じる。
        プロファイルと掃引条件モデルは元へ戻す（P2: 次の接続の open が元の速さを書く）。"""
        sess = self._ext_session
        if sess is None:
            return
        dev = self._device
        if dev is not None and sess.get("stream") is not None:
            dev.ext_tx_stop(reason)
        self._exttx_finish(reason, None, restore_device=False)
        self._ext_hold = False

    # ------------------------------------------------------------------ 排他（設計S §1 §6.2）

    def _exttx_check_mode(self, mode) -> None:
        """外部の送信の間は SG・TG・S11 のモードを断る（_set_adopted の入口）。"""
        if self._ext_session is not None and Mode(mode) in _TX_MODES:
            raise CommandError("ext_tx_active", "mode")

    def _exttx_check_idle(self) -> None:
        """外部の送信の間は断る処理（利得の補正・校正の測定）。"""
        if self._ext_session is not None:
            raise CommandError("ext_tx_active")
