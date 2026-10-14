"""計測器の本体のうち、機器の切断の記録（設計B §7.7）。Instrument に混ぜる。`_` で始まるメソッドは制御スレッドから呼ぶ。

出来事（event）と理由のコード（reason）
- connected（user: 利用者が繋いだ／auto_connect: 起動時に覚えた機器へ繋いだ）
- lost（device_disconnected: 読み書きの失敗で切れ、再接続待ちに入った）
- dropped（unexpected_error: 機器スレッドが思わぬ失敗で止まり、機器を外した。再接続は待たない）
- recovered（same_serial_returned: 同じシリアル番号の機器が戻り、開き直した）
- gave_up_waiting（user_picked_device: 再接続待ちの間に利用者が別の機器を選んだ／user_disconnect: 切断を押した／
  app_exit: アプリを閉じた）
- disconnected（user: 切断を押した／switched_device: 繋いだまま別の機器を選んだ／app_exit: アプリを閉じた）
- ext_iq_started（client_start）・ext_iq_stopped（止めた理由: 外部 IQ の口の stopped.reason と同じ語）

判定（切れたとするかどうか）は変えない。ここは本体がすでに決めた出来事を書くだけ。
"""
from __future__ import annotations

import time
from typing import Optional

from .device_events import LOG_DIR, LOG_NAME, DeviceEventLog, describe_error, iso, make_record
from .devices.plutosdr import events as pluto_events
from .instrument_step import RUN_STATE as STEP_RUN_STATE


# 外部 IQ 送信が「機器が切れたため」で止まってから、切れた（lost）の行を書くまでの、同じ切断とみなす長さ。
# 送信中は掃引を止めているので、先に失敗に気づくのは押すスレッドで、その後に掃引へ戻して初めて受信が失敗する
# （libiio のコンテキストの時間切れ 3 秒と、掃引へ戻す時間を足しても数秒）
EXT_LOST_WINDOW_S = 10.0


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 3)


class DeviceEventsMixin:
    """機器の切断の記録（Instrument に混ぜる）。"""

    def _init_device_events(self) -> None:
        self._evt_log = DeviceEventLog(self._root / LOG_DIR / LOG_NAME)
        # 切れたとき（lost）の時刻と、戻ったときに比べる送信の LO（PlutoSDR が書いた値）
        self._evt_lost: Optional[dict] = None
        # 外部 IQ 送信が「機器が切れたため」で止まった時点の動作（続く lost の行は、送信を止めた後に書かれるため）
        self._evt_ext_lost: Optional[dict] = None
        # 最後に lost・dropped の行を書いた時刻（その送信の間に切断の行をもう書いたかを見る）
        self._evt_loss_written_at: Optional[float] = None

    def _evt(self, event: str, reason: Optional[str], detail: Optional[dict] = None) -> None:
        ident = self._identity or {}
        self._evt_log.write(make_record(event, reason, self._backend_name, ident.get("model"), ident.get("serial"),
                                        detail))

    def _evt_activity(self) -> dict:
        """そのときの動作（掃引・SG・TG・外部 IQ 送信）と、いまの測定が始まってからの時間・最後に掃引が返った時刻。"""
        acts = []
        if self._run in ("continuous", "single", "stopping", STEP_RUN_STATE) or self._ref_job is not None \
                or self._cal_job is not None:
            acts.append("sweep")
        out = self._source_state()["output"]
        if out in ("sg", "tg"):
            acts.append(out)
        sess = self._ext_session
        if sess is not None:
            acts.append("ext_iq")
        w = self._worker
        busy = None if w is None else w.busy_since
        return {"activity": acts, "mode": self._adopted.mode.value, "run": self._run,
                "tasks": sorted({t.kind for t in getattr(self, "_tasks", {}).values()}),
                "ext_iq_state": None if sess is None else sess["state"],
                "measuring_for_s": None if busy is None else _round(time.monotonic() - busy),
                "last_sweep_at": iso(None if w is None else w.last_ok_at)}

    def _evt_connected_for(self) -> Optional[float]:
        return None if self._connected_at is None else _round(time.time() - self._connected_at)

    def _evt_connected(self, reason: str) -> None:
        detail = {"port": (self._identity or {}).get("port")}
        if self._is_plutosdr():
            inner = self._device.inner
            # 接続した直後（送信を静める前）の送信の LO。電源を入れた直後の値を実機で確かめる材料にもなる
            detail.update({"fw_version": inner.fw_version, "tx_lo_read_hz": inner.tx_lo_at_open})
        self._evt("connected", reason, detail)

    def _evt_on_lost(self, exc: Optional[BaseException], message: str, event: str = "lost",
                     reason: str = "device_disconnected") -> None:
        """切れた（機器を外す前に呼ぶ）: 理由と、そのときの動作・時刻を書き、戻ったときに比べる値を覚える。"""
        detail = describe_error(exc) if exc is not None else {"message": message}
        detail.update(self._evt_activity())
        ext, self._evt_ext_lost = self._evt_ext_lost, None
        if ext is not None and time.monotonic() - ext["at"] <= EXT_LOST_WINDOW_S:
            # 送信中に切れて、送信を先に止めた: そのときの動作は外部 IQ 送信（いまの掃引は、止めた後に戻したもの）
            detail.update({"activity": ["ext_iq"], "ext_iq_state": ext["state"],
                           "ext_iq_stopped_before_s": _round(time.monotonic() - ext["at"])})
        detail["connected_for_s"] = self._evt_connected_for()
        written: list[int] = []
        if self._is_plutosdr():
            pd = pluto_events.lost_detail(self._device.inner)
            written = pd["tx_lo_written_hz"]
            detail.update(pd)
        if event == "lost":
            self._evt_lost = {"at": time.time(), "tx_lo_written_hz": written}
        self._evt_loss_written_at = time.time()
        self._evt(event, reason, detail)

    def _evt_on_recovered(self) -> None:
        """同じ機器が戻って開き直した（_attach の後に呼ぶ）。PlutoSDR は再起動していたかを見分ける。"""
        lost, self._evt_lost = self._evt_lost, None
        detail: dict = {"outage_s": None if lost is None else _round(time.time() - lost["at"])}
        if self._is_plutosdr():
            written = [] if lost is None else lost["tx_lo_written_hz"]
            read = self._device.inner.tx_lo_at_open
            detail.update({"rebooted": pluto_events.judge_reboot(read, written), "tx_lo_read_hz": read,
                           "tx_lo_written_hz": written, "tx_lo_power_on_hz": pluto_events.POWER_ON_TX_LO_HZ})
        self._evt("recovered", "same_serial_returned", detail)

    def _evt_on_user_drop(self, reason_connected: str, reason_waiting: str) -> None:
        """利用者の操作（切断・別の機器を選ぶ・アプリを閉じる）で機器を外す前に呼ぶ。再接続待ちなら待つのをやめた、
        繋いでいれば切断した、と書く（どちらでもなければ書かない）。"""
        if self._conn == "reconnecting":
            lost, self._evt_lost = self._evt_lost, None
            self._evt("gave_up_waiting", reason_waiting,
                      {"waited_s": None if lost is None else _round(time.time() - lost["at"])})
        elif self._device is not None:
            self._evt("disconnected", reason_connected,
                      {**self._evt_activity(), "connected_for_s": self._evt_connected_for()})

    def _evt_ext_started(self, sess: dict) -> None:
        sess["event_logged"] = True
        self._evt("ext_iq_started", "client_start", {"client": sess["client"], "sample_rate_hz": sess["rate"],
                                                     "tx_lo_hz": sess["lo"], "tx_atten_db": self._exttx_atten()})

    def _evt_ext_stopped(self, sess: dict, reason: str, detail: Optional[str]) -> None:
        if not sess.get("event_logged"):
            return  # 始める前に止まった（始めた行が無い）
        # 機器が切れたため止まり、この送信の間にまだ切断の行を書いていない（送信を先に止めた）ときだけ、続く lost の行へ
        # 動作を渡す。閉じた入口（押すスレッドの終わり・相手の停止・待ち受けの停止）は問わない。切断の行を書いた後に
        # 機器を外して止めたとき（掃引の側が先に失敗した）は渡さない（使われずに残り、次の切断を取り違えるため）
        loss = self._evt_loss_written_at
        if reason == "device_lost" and (loss is None or loss < sess["started_at"]):
            self._evt_ext_lost = {"at": time.monotonic(), "state": sess["state"]}
        st = sess.get("stream")
        self._evt("ext_iq_stopped", reason, {
            "detail": detail, "client": sess["client"],
            "consumed": None if st is None else int(st.consumed), "underrun": None if st is None else int(st.underrun),
            "on_for_s": _round(time.time() - sess["started_at"])})
