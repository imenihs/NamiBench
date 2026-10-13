"""PlutoSDR（AD9364）の機器の口（設計B §7.1・設計Q §7）。

libiio（iio.py）で受信の LO を位置ごとに合わせ、IQ を読んでスペクトルにし、RTL-SDR と同じ部品（rtlsdr/spectrum.py の
位置の決め方・継ぎ合わせ・通過帯域の割り戻し、rtlsdr/driver.py の FFT の条件）で出力の格子へ継ぎ合わせる。
外へは「振り切れの正弦波を 1.0 とするリニアの電力」（RTL-SDR と同じ形: encoding "rtlsdr_power_w"）だけを返す。
IQ はこの層の外に出さない（設計Q §3。例外の rtl_tcp の出口は iq_sink）。

実機で確かめた手順の決まり（2026-10-10。ref/capture/2026-10-10/pluto/）:
- LO の書き込みは合うまで待って戻る（近い位置 約 1.7ms、遠い位置 約 24ms）。その後に作ったバッファの最初の標本から
  新しい周波数のデータ（捨てる標本は無い）。古いデータを読まないよう、位置ごとにバッファを作り直す
- 受信の利得の範囲は LO の帯で変わる（1300MHz 未満 −1〜73dB、4000MHz 未満 −3〜71dB、それ以上 −10〜62dB）。
  範囲の外の値は機器が受け付けない。1回の測定の中で利得を混ぜないよう、全部の位置で使える値に収めて使う
- 標本は 12 ビットを int16 に入れたもの（−2048〜2047）。端に当たったら振り切れ
- 送信（SG・TG）はこのアプリが出したときだけ止める（出していない送信の設定には触らない）
- 外部 IQ の送信（設計S §1）は ext_tx.py の押すスレッドが持つ。その間、スペアナの掃引と on_idle は送信に触らない。
  押すスレッドが生きている間はコンテキストを閉じない（閉じる側は押すスレッドへ渡す: _release_ctx）
"""
from __future__ import annotations

import copy
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from ...types import (
    AdoptedSweep,
    DeviceDisconnected,
    DeviceError,
    DeviceIdentity,
    FreqAxis,
    InvalidSweep,
    Mode,
    RawSweep,
    SweepDiscarded,
)
from ..base import Device, load_profile
from ..rtlsdr import spectrum
from ..rtlsdr.driver import FftParams, fft_params, make_plan
from ..rtlsdr.levels import ENCODING
from . import tones, xo
from .discover import DEFAULT_URI, PlutoPortInfo, _SerialCache
from .discover import discover as _discover
from .iio import IioError, IioLib
from .xo import XoOutOfRange

PROFILE_PATH = Path(__file__).with_name("profile.json")
# 振り切れの判定（RTL-SDR と同じ割合: 設計Q §6.4）
OVERLOAD_RATIO = spectrum.OVERLOAD_RATIO
# 「利得を合わせる」の余裕（dB）。振り切れない一番高い利得からこれだけ下げる（2026-10-12 ユーザー決定: 3dB・全部のモード）
FIT_MARGIN_DB = 3
# 「利得を合わせる」で1つの位置を試す標本の数
FIT_SAMPLES = 16384
# 値が受け付けられなかった（機器は繋がっている）errno。ほかの失敗は繋がりが切れたとみなす
_EINVAL, _ERANGE = 22, 34
_DEFAULT_GAIN = object()
_RANGE_RE = re.compile(r"\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]")
# 測れる測定。S11 は機器から見れば TG と同じ掃引（多トーン・方形の窓・TG 高調波の平均除去・見積もり: 設計Q §7.8。
# GigaSt でも本体は S11 を信号源「tg」として扱う）。反射の比は本体が基準（オープン・ショート）から作る
_MODES = (Mode.SA, Mode.SG, Mode.TG, Mode.S11)
_TG_LIKE = (Mode.TG, Mode.S11)

# 機器・通路の名前（AD9364 の IIO の名前）
PHY = "ad9361-phy"
RX_DEV = "cf-ad9361-lpc"
TX_DEV = "cf-ad9361-dds-core-lpc"
# DDS の通路: altvoltage0 = TX1_I_F1、1 = TX1_I_F2、2 = TX1_Q_F1、3 = TX1_Q_F2
_DDS_I1, _DDS_I2, _DDS_Q1, _DDS_Q2 = "altvoltage0", "altvoltage1", "altvoltage2", "altvoltage3"


def default_profile() -> dict:
    return load_profile(PROFILE_PATH)


def parse_range(text: str) -> Optional[tuple[float, float, float]]:
    """"[min step max]" の形（frequency_available・hardwaregain_available）を読む。"""
    m = _RANGE_RE.search(text or "")
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _cancelled(cancel: Optional[threading.Event]) -> bool:
    return cancel is not None and cancel.is_set()


def samples_per_position(p: FftParams, profile: dict) -> int:
    """1つの位置で読む標本の数（K 回の FFT の分＋捨てる分）。"""
    return p.averages * p.n + int(profile.get("timing", {}).get("discard_samples", 0))


def tg_harmonic_spec(profile: dict) -> dict:
    """TG 高調波の平均除去の決まり（設計Q §7.7）: 選べる回数・初期値・効かせる位置の上限・合わせ直しで動かす幅。"""
    d = (profile.get("source") or {}).get("tg_harmonic_avg") or {}
    return {"choices": [int(c) for c in d.get("choices", (1, 4, 8))], "default": int(d.get("default", 1)),
            "below_hz": int(d.get("below_hz", 1_000_000_000)),
            "relock_offset_hz": int(d.get("relock_offset_hz", 10_000_000))}


def tg_reads_at(profile: dict, center_hz: int, count: int) -> int:
    """TG の位置 center_hz で読む回数。平均除去は受信の LO が below_hz より下の位置だけ（それ以上は1回）。"""
    if count <= 1 or int(center_hz) >= tg_harmonic_spec(profile)["below_hz"]:
        return 1
    return int(count)


def estimate_s(profile: dict, axis: FreqAxis, rbw_hz: int, mode=None, tg_count: int = 1) -> float:
    """1回の掃引の見積もり秒: 位置の数 ×（LO を変えてから使える値まで ＋ 読み取り）。

    読み取りは標本の時間と USB の RNDIS の速さの遅い方（実測 約 6MS/s: 2026-10-10）。TG で平均除去を使うと、
    1000MHz より下の位置は回数分の（送信と受信の LO の合わせ直し ＋ 読み取り）になる（設計Q §7.7）。
    """
    t = profile["timing"]
    settle = float(t["retune_settle_s"])
    relock = float(t.get("tg_relock_s", settle))
    link = float(t.get("link_samples_per_s") or profile["fft"]["sample_rate_hz"])
    p, pl = make_plan(profile, axis, rbw_hz)
    rate = min(float(p.fs), link)
    read_s = samples_per_position(p, profile) / rate
    tg = mode is not None and Mode(mode) in _TG_LIKE
    total = 0.0
    for c in pl.centers:
        k = tg_reads_at(profile, c, tg_count) if tg else 1
        total += settle + read_s if k == 1 else settle + k * (relock + read_s)
    return total


def timing_fn(profile: dict, tg_count: Optional[Callable[[], int]] = None) -> Callable[..., float]:
    """掃引条件モデル（sweep_kind "fft"）の見積もり（estimate_s）。tg_count はいまの TG 高調波の平均除去の回数を返す口
    （無ければ 1 回: 平均除去なし）。"""

    def f(axis: FreqAxis, rbw_hz: int, mode=None) -> float:
        return estimate_s(profile, axis, rbw_hz, mode, int(tg_count()) if tg_count is not None else 1)

    return f


def gain_limits(profile: dict, centers) -> tuple[int, int]:
    """位置の全部で使える受信の利得の範囲 (下限, 上限)（LO の帯ごとの範囲の重なり）。"""
    bands = (profile.get("gain") or {}).get("bands") or []
    lo, hi = -1000, 1000
    for c in centers:
        for b in bands:
            if c < int(b["lo_below_hz"]):
                lo = max(lo, int(b["min_db"]))
                hi = min(hi, int(b["max_db"]))
                break
    if lo > hi or lo == -1000:
        g = profile.get("gain") or {}
        return int(g.get("min_db", -1)), int(g.get("max_db", 73))
    return lo, hi


# ---------------------------------------------------------------------- 見つける（中身は discover.py）

_cache = _SerialCache()


def discover(lib, uris=(DEFAULT_URI,), cache: Optional[_SerialCache] = None) -> list[PlutoPortInfo]:
    """USB で見える PlutoSDR と、設定の URI で繋がる PlutoSDR を、シリアル番号で1行にまとめて並べる（discover.py）。"""
    return _discover(lib, uris, cache or _cache)


# ---------------------------------------------------------------------- 機器の口

class PlutoSdrDevice(Device):
    """PlutoSDR。measure・on_idle は機器スレッドだけが呼ぶ。利得・送信の減衰の変更はほかのスレッドから呼んでよく、
    測っている間は次の measure の始めに効かせる（1回の測定の中で利得を混ぜない）。"""

    def __init__(self, profile: Optional[dict] = None, lib=None,
                 dll_folder: Optional[Union[str, Path]] = None) -> None:
        self._profile = copy.deepcopy(profile if profile is not None else default_profile())
        self._lib = lib
        self._dll_folder = dll_folder
        self._ctx = None
        self._identity: Optional[DeviceIdentity] = None
        self._lock = threading.Lock()
        self._pending: dict = {}
        self._tuned_hz: Optional[int] = None
        self._tx_tuned_hz: Optional[int] = None
        self._gain_setting: Optional[float] = float(self._profile["gain"]["default_db"])  # None は自動
        self._gain_applied: Optional[int] = None
        self._auto_applied = False
        self._tx_atten = float(self._profile["source"]["tx_atten_db"]["default"])
        self._tg_count = tg_harmonic_spec(self._profile)["default"]  # TG 高調波の平均除去の回数（1 はなし）
        self._tx_state: Optional[tuple] = None  # このアプリが出している送信（None は出していない）
        self._tx_buf = None
        self._tx_forbidden = False
        # 外部 IQ の送信（設計S §1）: 押すスレッド・送信の属性の書き込みの錠（掃引の錠とは別）・コンテキストを押すスレッドへ
        # 渡すときの錠（待つ処理の間は持たない）・速さを変える前の fft・接続したときの送信のアナログの帯域
        self._ext = None
        self._tx_lock = threading.Lock()
        self._ctx_lock = threading.Lock()
        self._ext_base_fft: Optional[dict] = None
        self._tx_rf_bw: Optional[int] = None
        self._idle_wanted = False  # 口を握られていた間に on_idle が来た（終わった処理が送信を止める）
        self.fixed_center_hz: Optional[int] = None
        # rtl_tcp の出口へ IQ を見せる口: (complex64 の IQ（振り切れ ±1.0）, LO の Hz, 標本化の Hz)。機器スレッドから呼ぶ
        self.iq_sink: Optional[Callable[[np.ndarray, int, int], None]] = None
        self.iq_sink_error: Optional[str] = None
        self.connected_at: Optional[float] = None
        self.fw_version: Optional[str] = None
        self._xo = xo.XoState()  # 基準の補正（設計Q §7.10）

    # ---- 繋ぐ ----
    def _get_lib(self):
        if self._lib is None:
            self._lib = IioLib.load(self._dll_folder)
        return self._lib

    def open(self, uri: str, gain_db: object = _DEFAULT_GAIN, tx_atten_db: Optional[float] = None,
             xo_ppm_by_serial: Optional[dict] = None) -> DeviceIdentity:
        """URI（"ip:192.168.2.1"・"usb:1.2.5" など）で開く。gain_db は手動の利得（dB）か None（自動）。
        xo_ppm_by_serial はシリアル番号 → 基準の補正（ppm。設計Q §7.10）。無い機器は 0（書かない）。"""
        self.close()
        if self._ext_base_fft is not None:
            # 外部 IQ の送信で速さを変えたまま閉じた: 元の速さを書く（設計S §1 の P2）
            self._profile["fft"], self._ext_base_fft = self._ext_base_fft, None
        lib = self._get_lib()
        try:
            ctx = lib.open(uri, 3000)
        except IioError as e:
            raise DeviceDisconnected(f"{uri} に繋がらない: {e}") from e
        try:
            self._bind(ctx)
            serial = ctx.attr("hw_serial") or ""
            self.fw_version = ctx.attr("fw_version")
            # つないだら送信を止める（2026-10-11 ユーザー決定「電源入れたときは、送信止めて」）: 電源投入時は送信の LO が
            # 2450MHz で動作・減衰 −10dB のままで、受信に線が出る。出力をいちばん小さくし、LO を止め、波形を消す
            self._silence_tx()
            self._destroy_tx_buffer()
            self._dds_off()
            # 基準の補正は送信を止めた後、範囲を読んで LO の書ける下限を確かめる前に書く（LO もサンプリング周波数も
            # 基準から作るので、下限はその基準で確かめる: 設計Q §7.10）
            self._xo_open((xo_ppm_by_serial or {}).get(serial))
            self._read_ranges()
            try:
                self._tx_rf_bw = int(round(float(self._txc.attr("rf_bandwidth").split()[0])))
            except (IioError, ValueError, IndexError):
                self._tx_rf_bw = None  # 読めなければ外部 IQ の送信の間も送信の帯域には触らない
            fs = int(self._profile["fft"]["sample_rate_hz"])
            self._rx.set_attr("sampling_frequency", fs)
            self._rx.set_attr("rf_bandwidth", int(self._profile["fft"].get("rf_bandwidth_hz", fs)))
            f = self._profile["freq"]
            first = min(max(int(f.get("default_start_hz", f["min_hz"])), int(f["min_hz"])), int(f["max_hz"]))
            self._rxlo.set_attr("frequency", first)
            self._tuned_hz = first
            if gain_db is not _DEFAULT_GAIN:
                self._gain_setting = None if gain_db is None else float(gain_db)
            if tx_atten_db is not None:
                self._tx_atten = float(tx_atten_db)
            self._apply_gain([first])
        except IioError as e:
            self._ctx = None
            ctx.close()
            raise DeviceError(f"PlutoSDR の設定に失敗した: {e}") from e
        except BaseException:
            self._ctx = None
            ctx.close()
            raise
        self._identity = DeviceIdentity(model=self._profile["model"], serial=serial, port=uri)
        self.connected_at = time.time()
        return self._identity

    def _bind(self, ctx) -> None:
        self._ctx = ctx
        phy = ctx.device(PHY)
        self._phy = phy
        self._rx = phy.channel("voltage0", False)
        self._txc = phy.channel("voltage0", True)
        self._rxlo = phy.channel("altvoltage0", True)
        self._txlo = phy.channel("altvoltage1", True)
        self._adc = ctx.device(RX_DEV)
        for n in ("voltage0", "voltage1"):
            self._adc.channel(n, False).enable()
        self._dac = ctx.device(TX_DEV)
        self._dds = {n: self._dac.channel(n, True) for n in (_DDS_I1, _DDS_I2, _DDS_Q1, _DDS_Q2)}
        self._dac_iq = [self._dac.channel(n, True) for n in ("voltage0", "voltage1")]
        self._tuned_hz = None
        self._tx_tuned_hz = None
        self._gain_applied = None
        self._auto_applied = False
        self._tx_state = None
        self._tx_buf = None

    def _read_ranges(self) -> None:
        """機器が申告する範囲でプロファイルを上書きする（設計Q §2.1・§7.3）。"""
        prof = self._profile
        r = parse_range(self._rxlo.attr("frequency_available"))
        if r:
            prof["freq"]["min_hz"], prof["freq"]["max_hz"] = int(r[0]), int(r[2])
            prof["freq"]["range_basis"] = "device_reported"
        src = prof["source"]
        t = parse_range(self._txlo.attr("frequency_available"))
        if t:
            src["tx_lo_hz"] = [int(t[0]), int(t[2])]
        a = parse_range(self._txc.attr("hardwaregain_available"))
        if a:
            src["tx_atten_db"]["min"], src["tx_atten_db"]["step"], src["tx_atten_db"]["max"] = a[0], a[1], a[2]
        # 申告の下限そのものを断る機器がある（2026-10-13 実機: 送信 LO の申告 46875001Hz を書くと EINVAL、46875002Hz は
        # 通る。受信 LO の申告 70000000Hz は通る）。範囲の端を選んだ利用者の設定が測るときに断られないよう、送信を止めて
        # から両方の LO に下限を書いて確かめ、通る値を範囲の下限にする
        self._silence_tx()
        f = prof["freq"]
        f["min_hz"] = self._writable_floor(self._rxlo, int(f["min_hz"]), int(f["max_hz"]))
        src["tx_lo_hz"][0] = self._writable_floor(self._txlo, int(src["tx_lo_hz"][0]), int(src["tx_lo_hz"][1]))
        self._tx_tuned_hz = None
        off = int(src["dds_offset_hz"])
        lo_min, lo_max = src["tx_lo_hz"]
        src["sg"] = {"min_hz": int(lo_min) + off, "max_hz": int(lo_max), "unit_hz": int(src["sg"]["unit_hz"])}
        src["tg_range_hz"] = [max(int(prof["freq"]["min_hz"]), int(lo_min)), min(int(prof["freq"]["max_hz"]), int(lo_max))]
        # 受信の利得の帯ごとの範囲は LO をその帯に置いて読む（LO の帯で範囲が変わる）
        for b in prof["gain"]["bands"]:
            probe = int(b["probe_hz"])
            if not prof["freq"]["min_hz"] <= probe <= prof["freq"]["max_hz"]:
                continue
            self._rxlo.set_attr("frequency", probe)
            g = parse_range(self._rx.attr("hardwaregain_available"))
            if g:
                b["min_db"], b["max_db"] = int(round(g[0])), int(round(g[2]))
        self._tuned_hz = None

    def _writable_floor(self, ch, lo: int, hi: int) -> int:
        """LO の通路 ch に書ける最小の周波数（lo から上）。lo が通ればそのまま。hi も断られたら決められないので lo の
        まま（書けない値は測るときに断られ、掃引を止めて知らせる: measure）。"""
        def ok(hz: int) -> bool:
            try:
                ch.set_attr("frequency", int(hz))
                return True
            except IioError as e:
                if self._is_value_error(e):
                    return False
                raise
        if ok(lo) or not ok(hi):
            return lo
        bad, good = lo, hi
        while good - bad > 1:
            mid = (bad + good) // 2
            if ok(mid):
                good = mid
            else:
                bad = mid
        return good

    # ---- 基準の補正（設計Q §7.10。中身は xo.py）----
    def _xo_nominal(self) -> int:
        return int((self._profile.get("xo") or {}).get("nominal_hz", xo.NOMINAL_HZ))

    def _xo_open(self, ppm) -> None:
        """接続したとき: 申告の範囲といまの値を読み（範囲はプロファイルへ）、補正が 0 でなければ書く。範囲の外・機器が
        断った値は書かずに続ける（refused_ppm に残し、本体が知らせる）。"""
        st = self._xo
        st.read(self._phy, parse_range)
        if st.range_hz is not None:
            prof = self._profile.setdefault("xo", {})
            prof["min_hz"], prof["max_hz"] = st.range_hz
        if not ppm:
            return
        try:
            st.write(self._phy, float(ppm), self._xo_nominal())
        except XoOutOfRange:
            st.refused_ppm = xo.round_ppm(ppm)
        except IioError as e:
            if not self._is_value_error(e):
                raise
            st.refused_ppm = xo.round_ppm(ppm)

    def set_xo_ppm(self, ppm: float, wait_s: float = 2.0) -> bool:
        """基準の補正を ppm にする（本体は掃引も送信もしていないときだけ呼ぶ）。書いたら True（属性を持たない機器は
        False）。書いた後はサンプリング周波数を書き直し、受信・送信の LO は次の測定で書き直させる（どちらも基準から
        作るので、書き直すまでは前の基準のままの機器がある）。範囲の外は XoOutOfRange、外部 IQ の送信の間は InvalidSweep。"""
        if not self._lock.acquire(timeout=wait_s):
            raise DeviceError("PlutoSDR が測っている途中")
        try:
            if self._ctx is None:
                raise DeviceDisconnected("開いていない")
            if self._ext is not None:
                raise InvalidSweep("外部 IQ の送信の間は基準の補正を変えない")
            try:
                self._tx_off()
                wrote = self._xo.write(self._phy, ppm, self._xo_nominal())
                if wrote:
                    self._rx.set_attr("sampling_frequency", int(self._profile["fft"]["sample_rate_hz"]))
                    self._tuned_hz = None
                    self._tx_tuned_hz = None
                    self._xo.refused_ppm = None
                return wrote
            except IioError as e:
                if self._is_value_error(e):
                    raise DeviceError(f"PlutoSDR が基準の補正を断った: {e}") from e
                self._drop()
                raise DeviceDisconnected(str(e)) from e
        finally:
            self._lock.release()

    @property
    def xo_state(self) -> dict:
        """基準の補正のいまの様子（本体の状態に出す）。hz は機器から読み戻した値。"""
        st = self._xo
        return {"supported": st.supported, "hz": st.hz, "range_hz": None if st.range_hz is None else list(st.range_hz),
                "ppm": st.ppm, "refused_ppm": st.refused_ppm}

    # ---- 利得・送信の減衰（測っている間は次の measure の始めに効かせる）----
    def set_gain(self, gain_db: Optional[float]) -> None:
        """手動の利得（dB）か None（機器の自動利得 slow_attack）。"""
        self._pending["gain"] = None if gain_db is None else float(gain_db)
        self._apply_pending_if_idle()

    def set_tx_atten(self, atten_db: float) -> None:
        self._pending["tx_atten"] = float(atten_db)
        self._apply_pending_if_idle()

    def set_tg_harmonic_avg(self, count: int) -> None:
        """TG 高調波の平均除去の回数（1 はなし。設計Q §7.7）。測っている間は次の measure の始めに効かせる。"""
        self._pending["tg_count"] = max(1, int(count))
        self._apply_pending_if_idle()

    @property
    def tg_harmonic_avg(self) -> int:
        return int(self._tg_count)

    def on_reference_standard(self, standard: Optional[str]) -> None:
        """S11 の基準を取る間の標準器（設計E §6.5）。実機は何もしない（利用者が繋ぎ替える）。libiio の偽物
        （fake.py）だけが set_standard を持ち、その標準器を繋いだことにする（試験で手順を通すため）。"""
        hook = getattr(self._lib, "set_standard", None)
        if hook is not None:
            hook(standard)

    def _apply_pending_if_idle(self) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self._apply_pending()
            except DeviceError:
                pass  # 次の measure で入れ直す（その失敗は measure が知らせる）
            finally:
                self._lock.release()

    def _apply_pending(self) -> None:
        pend, self._pending = self._pending, {}
        if "gain" in pend:
            self._gain_setting = pend["gain"]
        if "tx_atten" in pend:
            self._tx_atten = pend["tx_atten"]
        if "tg_count" in pend:
            self._tg_count = pend["tg_count"]
        if self._ctx is None:
            return
        if "gain" in pend:
            self._apply_gain([self._tuned_hz] if self._tuned_hz else [])
        if "tx_atten" in pend and self._app_tx():
            self._txc.set_attr("hardwaregain", self._atten_text())

    def _app_tx(self) -> bool:
        """このアプリ（SG・TG・合わせる・補正）が送信を出しているか（外部 IQ の送信は含めない）。"""
        ts = self._tx_state
        return ts is not None and ts[0] != "ext"

    def _atten_text(self) -> str:
        return self._atten_value_text(self._tx_atten)

    def _atten_value_text(self, value: float) -> str:
        a = self._profile["source"]["tx_atten_db"]
        v = min(max(float(value), float(a["min"])), float(a["max"]))
        step = float(a.get("step") or 0.25)
        v = round(round(v / step) * step, 2)
        return f"{v:.2f}"

    def _apply_gain(self, centers) -> None:
        """設定の利得を、位置の全部で使える範囲に収めて入れる（自動なら自動にする）。"""
        if self._gain_setting is None:
            if not self._auto_applied:
                self._rx.set_attr("gain_control_mode", str(self._profile["gain"].get("auto_mode", "slow_attack")))
                self._auto_applied = True
                self._gain_applied = None
            return
        lo, hi = gain_limits(self._profile, centers) if centers else (-1000, 1000)
        g = int(round(min(max(self._gain_setting, lo), hi)))
        if self._auto_applied or self._gain_applied is None:
            self._rx.set_attr("gain_control_mode", "manual")
            self._auto_applied = False
        if g != self._gain_applied:
            self._rx.set_attr("hardwaregain", g)
            self._gain_applied = g

    def _gain_for_positions(self, centers) -> None:
        """位置の全部で使える利得を入れる。機器は「いまの LO の帯」の範囲の外の利得を受け付けない（実機 2026-10-10）
        ので、入れる利得がいまの LO の帯で使えなければ、先に LO を最初の位置へ動かしてから入れる。"""
        if not centers:
            return
        if self._gain_setting is not None and self._tuned_hz is not None:
            lo, hi = gain_limits(self._profile, centers)
            g = min(max(self._gain_setting, lo), hi)
            cur_lo, cur_hi = gain_limits(self._profile, [self._tuned_hz])
            if not cur_lo <= g <= cur_hi:
                self._rxlo.set_attr("frequency", int(centers[0]))
                self._tuned_hz = int(centers[0])
        elif self._tuned_hz is None:
            self._rxlo.set_attr("frequency", int(centers[0]))
            self._tuned_hz = int(centers[0])
        self._apply_gain(centers)

    @property
    def gain_db(self) -> Optional[float]:
        """いま入れている手動の利得（dB）。自動なら None。"""
        if self._gain_setting is None:
            return None
        return None if self._gain_applied is None else float(self._gain_applied)

    @property
    def gain_setting_db(self) -> Optional[float]:
        return self._gain_setting

    @property
    def tx_atten_db(self) -> float:
        return float(self._atten_text())

    @property
    def tx_on(self) -> Optional[str]:
        """出している送信（"sg"・"tg"・外部 IQ の "ext"）か None。"""
        ts = self._tx_state
        return None if ts is None else ts[0]

    def gain_limits_for(self, sweep: AdoptedSweep) -> tuple[int, int]:
        """その掃引条件の全部の位置で使える受信の利得の範囲。"""
        try:
            _, pl = make_plan(self._profile, sweep.axis, sweep.rbw_hz, self.fixed_center_hz)
            return gain_limits(self._profile, pl.centers)
        except (InvalidSweep, ValueError):
            return gain_limits(self._profile, [sweep.center_hz])

    # ---- 機器の口 ----
    def profile(self) -> dict:
        return self._profile

    def identity(self) -> DeviceIdentity:
        if self._identity is None:
            raise DeviceDisconnected("開いていない")
        return self._identity

    @property
    def is_open(self) -> bool:
        return self._ctx is not None

    def _drop(self) -> None:
        """コンテキストを使えなくする。外部 IQ の押すスレッドが生きていれば止める印を付けてコンテキストを渡し、待たない
        （設計S §1 の P1: 機器スレッドが条件の錠を持ったまま来る道がある）。"""
        ctx, self._ctx = self._ctx, None
        self._tuned_hz = None
        self._tx_buf = None
        if self._app_tx():
            self._tx_state = None
        ext = self._ext
        if ext is not None:
            ext.request_stop("device_lost")
        self._release_ctx(ctx)

    def _release_ctx(self, ctx) -> None:
        if ctx is None:
            return
        with self._ctx_lock:
            ext = self._ext
            if ext is not None and ext.alive:
                ext.close_ctx = ctx  # 押すスレッドが止める順の後に閉じる
                return
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _is_value_error(e: IioError) -> bool:
        return abs(int(e.code or 0)) in (_EINVAL, _ERANGE)

    def measure(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None) -> RawSweep:
        mode = Mode(sweep.mode)
        if mode not in _MODES:
            raise InvalidSweep(f"PlutoSDR はスペアナ・SG・TG・S11 だけ（{sweep.mode}）")
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"sent": 0})
        with self._lock:
            if self._ctx is None:
                raise DeviceDisconnected("開いていない")
            try:
                self._apply_pending()
                return self._measure_locked(sweep, mode, cancel)
            except IioError as e:
                if self._is_value_error(e):
                    # 機器が設定の値を断った（繋がりは生きている）: 送信を止め、切らずに掃引を止めて知らせる
                    try:
                        self._tx_off()
                    except IioError:
                        pass
                    raise InvalidSweep(f"PlutoSDR が設定を断った: {e}") from e
                self._drop()
                raise DeviceDisconnected(str(e)) from e

    def _read_position(self, c: int, p: FftParams, n_samples: int) -> np.ndarray:
        """LO を c にして（変わったときだけ）新しいバッファで n_samples を読む。int16 の I・Q の交互。"""
        if c != self._tuned_hz:
            self._rxlo.set_attr("frequency", int(c))
            self._tuned_hz = c
        buf = self._adc.create_buffer(n_samples)
        try:
            return buf.refill()
        finally:
            buf.destroy()

    def _measure_locked(self, sweep: AdoptedSweep, mode: Mode, cancel) -> RawSweep:
        axis = sweep.axis
        fixed = self.fixed_center_hz
        p, pl = make_plan(self._profile, axis, sweep.rbw_hz, fixed)
        self._gain_for_positions(pl.centers)
        if mode == Mode.SG:
            self._tx_tone(int(sweep.sg_hz))
        elif mode in _TG_LIKE:
            self._tx_multitone(p, int(pl.centers[0]))
        else:
            self._tx_off()
        # TG は方形の窓で読む: 多トーンは全部の格子にあり、Hann などでは隣の格子のトーンが漏れ込んで位相しだいで
        # 打ち消し合う（基準との比で消えない: 被測定物の位相で変わるため）。多トーンは N 標本で繰り返すので方形なら漏れない
        win = spectrum.RECT if mode in _TG_LIKE else p.window
        passband = spectrum.Passband.from_profile(self._profile)
        asm =spectrum.Assembler(axis.start_hz, p.grid, axis.points, p.half, p.delta, passband)
        full = float(self._profile["fft"].get("full_scale", 2048))
        discard = int(self._profile.get("timing", {}).get("discard_samples", 0))
        want = p.averages * p.n
        started_at = time.time()
        t0 = time.monotonic()
        max_ratio = 0.0
        retunes = 0
        repeated = 0
        sink = self.iq_sink
        tg_count = self._tg_count if mode in _TG_LIKE else 1
        self.report_progress(0, len(pl.centers))
        for i, c in enumerate(pl.centers):
            if i > 0 and _cancelled(cancel):
                raise SweepDiscarded("cancelled", {"positions_done": i})
            if c != self._tuned_hz:
                retunes += 1
            if mode in _TG_LIKE and c != self._tx_tuned_hz:
                # TG: 送信の LO を受信の LO に合わせる（多トーンは受信の使える幅に乗る）
                self._txlo.set_attr("frequency", int(c))
                self._tx_tuned_hz = c
            # TG 高調波の平均除去（設計Q §7.7）: 1000MHz より下の位置は、送信と受信の LO を合わせ直してから読む、を
            # 回数分くり返して電力を平均する（送信の高調波が戻る位相が合わせ直しごとに無作為に変わるので、揺れは
            # 回数の平方根で割った大きさに減る）。1000MHz 以上の位置は1回だけ読む
            reads = tg_reads_at(self._profile, c, tg_count)
            if reads > 1:
                repeated += 1
            power = None
            for k in range(reads):
                if k > 0 and _cancelled(cancel):
                    raise SweepDiscarded("cancelled", {"positions_done": i})
                if reads > 1:
                    self._tg_relock(int(c))
                a = self._read_position(c, p, want + discard)
                if a.size < 2 * (want + discard):
                    raise SweepDiscarded("short_read", {"position": i, "got": int(a.size // 2), "want": want + discard})
                a = a[2 * discard: 2 * (discard + want)]
                r = overload_ratio_i16(a, int(full))
                max_ratio = max(max_ratio, r)
                iq = iq_from_i16(a, full)
                if sink is not None:
                    try:
                        sink(iq, c, p.fs)
                    except Exception as e:  # noqa: BLE001 — 取り出し側の失敗で測定を止めない
                        self.iq_sink_error = repr(e)
                pw = spectrum.averaged_power(iq, p.n, win, p.averages)
                power = pw if power is None else power + pw
            asm.add(c, power / reads if reads > 1 else power)
            self.report_progress(i + 1, len(pl.centers))
        if _cancelled(cancel):
            raise SweepDiscarded("cancelled", {"positions_done": len(pl.centers)})
        duration = time.monotonic() - t0
        extra = {
            "gain_db": self.gain_db,
            "gain_mode": "auto" if self._gain_setting is None else "manual",
            "gain_setting_db": self._gain_setting,
            # 基準の補正（設計Q §7.10）: このアプリが書いた ppm（書いていなければ 0）と、機器から読み戻した基準の Hz
            # （属性を持たない機器は None）。周波数の意味が変わるので保存したデータに残す
            "ppm": self._xo.ppm,
            "xo_correction_hz": self._xo.hz,
            "window": win,
            "fft_n": p.n,
            "averages": p.averages,
            "sample_rate_hz": p.fs,
            "dc_delta_hz": p.delta,
            "passband_corrected": passband is not None,
            "half_usable_hz": p.half,
            "realtime": pl.realtime,
            "positions": len(pl.centers),
            "retunes": retunes,
            "centers_hz": list(pl.centers),
            "fixed_center": fixed is not None,
            "overload": max_ratio > OVERLOAD_RATIO,
            "overload_ratio_max": max_ratio,
            # 外部 IQ の送信を出しながら測ったかも残す（保存したデータで分かる: 設計S §1 §6.1・M-08）
            **self._tx_extra(),
            "flags": {"no_value": asm.no_value},
        }
        if mode in _TG_LIKE:
            # TG 高調波の平均除去の回数（1 はなし）と、くり返して読んだ位置の数（設計Q §7.7。回数は記録にも残す）
            extra["tg_harmonic_avg"] = int(tg_count)
            extra["tg_harmonic_positions"] = repeated
        return RawSweep(adopted=sweep, raw=asm.values, encoding=ENCODING, started_at=started_at,
                        duration_s=duration, transient_head_points=0, extra=extra)

    # ---- 送信（SG・TG）----
    def _dds_off(self) -> None:
        for ch in self._dds.values():
            ch.set_attr("scale", "0")

    def _silence_tx(self) -> None:
        """出力をいちばん小さくして送信の LO を止める（波形はそのまま）。"""
        self._txc.set_attr("hardwaregain", self._atten_min_text())
        self._txlo.set_attr("powerdown", 1)

    def _destroy_tx_buffer(self) -> None:
        b, self._tx_buf = self._tx_buf, None
        if b is not None:
            b.destroy()

    def _atten_min_text(self) -> str:
        return f"{float(self._profile['source']['tx_atten_db']['min']):.2f}"

    def _tx_off(self) -> None:
        """このアプリが出した送信を止める（出していなければ何もしない）。減衰を最大にしてから LO を止め、波形を消す。"""
        if not self._app_tx():
            return  # 出していない・外部 IQ の送信（押すスレッドが持つ: 設計S §1 §6.1）
        self._tx_state = None
        self._tx_tuned_hz = None
        self._txc.set_attr("hardwaregain", self._atten_min_text())
        self._txlo.set_attr("powerdown", 1)
        self._destroy_tx_buffer()
        self._dds_off()

    def _tx_safe_start(self, lo_hz: int) -> None:
        """送信を始める（種類を変える）ときの決まった手順の前半: 出力をいちばん小さくして LO を止め、前の波形を消し、
        LO を出したい周波数に合わせる。電源を入れた直後の状態（送信の LO 2450MHz で動作・減衰 −10dB）や前の種類の
        波形のまま、利用者の周波数・減衰より先に出力が出ないようにする。後半は波形 → LO を動かす → 利用者の減衰。"""
        self._txc.set_attr("hardwaregain", self._atten_min_text())
        self._txlo.set_attr("powerdown", 1)
        self._destroy_tx_buffer()
        self._dds_off()
        self._txlo.set_attr("frequency", int(lo_hz))
        self._tx_tuned_hz = int(lo_hz)

    def _tx_retune(self, lo_hz: int) -> None:
        """出している間に送信の LO を動かす（SG の周波数を変えた）: 動かす間は出力をいちばん小さくする。"""
        self._txc.set_attr("hardwaregain", self._atten_min_text())
        self._txlo.set_attr("frequency", int(lo_hz))
        self._tx_tuned_hz = int(lo_hz)
        self._txc.set_attr("hardwaregain", self._atten_text())

    def _tg_relock(self, c: int) -> None:
        """TG 高調波の平均除去の1回分の合わせ直し（設計Q §7.7）: 送信と受信の LO を位置から離して戻す。

        送信の LO は出している間に動かすので、SG の周波数を変えるときと同じ順（_tx_retune）: 動かす間は出力をいちばん
        小さくし、戻してから利用者の減衰へ（出力を上げない・利用者の減衰は変えない）。"""
        away = int(c) + tg_harmonic_spec(self._profile)["relock_offset_hz"]
        self._txc.set_attr("hardwaregain", self._atten_min_text())
        self._txlo.set_attr("frequency", away)
        self._txlo.set_attr("frequency", int(c))
        self._tx_tuned_hz = int(c)
        self._txc.set_attr("hardwaregain", self._atten_text())
        self._rxlo.set_attr("frequency", away)
        self._rxlo.set_attr("frequency", int(c))
        self._tuned_hz = int(c)

    def _check_tx_allowed(self) -> None:
        if self._tx_forbidden:
            raise DeviceDisconnected("閉じている途中（送信は出さない）")
        if self._ext is not None:
            # 外部 IQ の送信の間は SG・TG を出さない（本体が断る: 設計S §1 §6.2。ここは二重の防御）
            raise InvalidSweep("外部 IQ の送信の間は SG・TG を出さない")

    def _tx_tone(self, sg_hz: int) -> None:
        """SG: 送信の LO を SG の周波数 − DDS のずらし に合わせ、DDS で1本のトーンを出す。"""
        self._check_tx_allowed()
        src = self._profile["source"]
        off = int(src["dds_offset_hz"])
        lo = int(sg_hz) - off
        want = ("sg", int(sg_hz), self._atten_text())
        if self._tx_state == want:
            return
        if self._tx_state is None or self._tx_state[0] != "sg":
            self._tx_safe_start(lo)
            scale = f"{float(src['dds_scale']):.6f}"
            ph = int(src.get("dds_phase_i_mdeg", 90000))
            for name, phase in ((_DDS_I1, ph), (_DDS_Q1, 0)):
                ch = self._dds[name]
                ch.set_attr("frequency", off)
                ch.set_attr("phase", phase)
                ch.set_attr("scale", scale)
            self._dds[_DDS_I1].set_attr("raw", 1)
            self._txlo.set_attr("powerdown", 0)
            self._txc.set_attr("hardwaregain", self._atten_text())
        elif self._tx_tuned_hz != lo:
            self._tx_retune(lo)
        else:
            self._txc.set_attr("hardwaregain", self._atten_text())
        self._tx_state = want

    def _tx_multitone(self, p: FftParams, first_lo_hz: int) -> None:
        """TG: 受信の FFT の格子に乗る多トーンを循環バッファで出す（LO は位置ごとに受信へ合わせる）。
        first_lo_hz は最初の位置の LO（始めるときは、ここへ合わせてから出す）。"""
        self._check_tx_allowed()
        src = self._profile["source"]
        want = ("tg", p.n, p.grid, p.half, p.delta, self._atten_text())
        if self._tx_state == want:
            return
        if self._tx_state is None or self._tx_state[:5] != want[:5]:
            self._tx_safe_start(first_lo_hz)
            for ch in self._dac_iq:
                ch.enable()
            x = tones.multitone(p.n, p.grid, p.half, p.delta, float(src.get("tg_multitone_peak", 0.5)))
            buf = self._dac.create_buffer(p.n, cyclic=True)
            buf.push(tones.to_int16_iq(x, int(src.get("tx_full_scale", 32767))))
            self._tx_buf = buf
            self._txlo.set_attr("powerdown", 0)
        self._txc.set_attr("hardwaregain", self._atten_text())
        self._tx_state = want

    def emergency_tx_off(self, wait_s: float = 0.5) -> None:
        """閉じる途中（アプリを終える）で、機器スレッドがまだ終わらないときにほかのスレッドから呼ぶ: 送信を止め、以後は
        出させない。機器スレッドの測定が口を握ったままでも、待つのは wait_s まで（その後は握らずに書く。終わる直前の
        1回だけなので、測定の書き込みと重なっても止める方を優先する）。"""
        self._tx_forbidden = True
        ext = self._ext
        if ext is not None:
            # 外部 IQ の送信: 押すスレッドに止めさせる。wait_s で終わらなければ減衰を最小と LO を止めるだけを書く
            # （コンテキストは閉じない: 押すスレッドが終わってから閉じる。設計S §1 §5・P1）
            ext.request_stop("app_exit")
            if not ext.join(wait_s):
                for ch, name, value in ((ext._txc, "hardwaregain", self._atten_min_text()), (ext._txlo, "powerdown", 1)):
                    try:
                        ch.set_attr(name, value)
                    except Exception:  # noqa: BLE001
                        pass
        got = self._lock.acquire(timeout=wait_s)
        try:
            if self._ctx is None or not self._app_tx():
                return
            try:
                self._tx_off()
            except IioError:
                pass
        finally:
            if got:
                self._lock.release()

    def on_idle(self) -> None:
        """掃引を渡されていない間（機器スレッド）: このアプリが出した送信を止める（設計L: 出しているのは掃引の間）。

        口がほかの長い処理（利得を合わせる・利得の補正を測る）に握られているときは待たない: 機器スレッドは条件の
        錠を持ったままここを呼ぶので、待つと制御スレッドが条件を渡せず止まる。その処理が終わるときに送信を止める
        （_idle_wanted を見る。利得の補正を測る処理は終わりに必ず止める）。"""
        if not self._lock.acquire(blocking=False):
            self._idle_wanted = True
            return
        try:
            self._idle_locked()
        finally:
            self._lock.release()

    def _idle_locked(self) -> None:
        self._idle_wanted = False
        if self._ctx is None:
            return
        try:
            self._apply_pending()
            self._tx_off()
        except IioError as e:
            if not self._is_value_error(e):
                self._drop()

    # ---- 利得を合わせる（設計Q §6.5 と同じ: 振り切れない、いちばん大きい利得）----
    def fit_gain(self, sweep: AdoptedSweep, cancel: Optional[threading.Event] = None,
                 progress: Optional[Callable[[int, int], None]] = None) -> dict:
        mode = Mode(sweep.mode)
        if mode not in _MODES:
            raise InvalidSweep(f"PlutoSDR はスペアナ・SG・TG・S11 だけ（{sweep.mode}）")
        with self._lock:
            if self._ctx is None:
                raise DeviceDisconnected("開いていない")
            # SG・TG は測るときと同じ送信を出して合わせる（2026-10-12 ユーザー決定）。押す前に送信が出ていなかった
            # （掃引を止めていた）なら、終わったら止める。出ていた（連続で測っている）なら出したまま次の掃引へ渡す
            was_on = self._tx_state is not None
            sends = mode in (Mode.SG, *_TG_LIKE)
            try:
                self._apply_pending()
                return self._fit_gain_locked(sweep, mode, cancel, progress)
            except IioError as e:
                if self._is_value_error(e):
                    raise DeviceError(str(e)) from e
                self._drop()
                raise DeviceDisconnected(str(e)) from e
            finally:
                if self._idle_wanted:
                    self._idle_locked()  # 合わせている間に掃引が止まった: 出していた送信を止める
                elif sends and not was_on and self._ctx is not None:
                    self._fit_tx_off()

    def _fit_tx_off(self) -> None:
        """合わせるために出した送信を止める（途中で失敗して状態を残していなくても、書いたかもしれない送信を止める）。"""
        try:
            if self._tx_state is None:
                self._tx_state = ("fit",)
            self._tx_off()
        except IioError as e:
            if not self._is_value_error(e):
                self._drop()

    def _fit_gain_locked(self, sweep: AdoptedSweep, mode: Mode, cancel, progress) -> dict:
        """段は 1dB 刻みで多い（約 75 段）ので、上から1段ずつではなく二分探しで探す。振り切れは利得に対して単調
        （利得を上げて振り切れが消えることは無い）とみなすので、選ぶ段は上から1段ずつ試すのと同じになる。

        SG・TG は自分の送信を受けて振り切れを見る: 測るときと同じ決まった順で送信を始め（SG は SG の周波数の1本、
        TG は多トーン）、TG は読む位置ごとに送信の LO を受信の LO へ合わせる。スペアナは送信に触らない。"""
        p, pl = make_plan(self._profile, sweep.axis, sweep.rbw_hz, self.fixed_center_hz)
        centers = list(pl.centers)
        if mode == Mode.SG:
            self._tx_tone(int(sweep.sg_hz))
        elif mode in _TG_LIKE:
            self._tx_multitone(p, int(centers[0]))
        lo, hi = gain_limits(self._profile, centers)
        full = int(self._profile["fft"].get("full_scale", 2048))
        before = (self._gain_setting, self._gain_applied, self._auto_applied)
        total = max(1, int(np.ceil(np.log2(hi - lo + 2))) + 1)
        tried = 0
        hot = 0
        ratios: dict[int, float] = {}

        def over(g: int) -> bool:
            nonlocal hot, tried
            if _cancelled(cancel):
                raise SweepDiscarded("cancelled", {"steps_done": tried})
            if self._tuned_hz not in centers:
                # 利得はいまの LO の帯で受け付けられる（g は全部の位置の帯で使える値）
                self._rxlo.set_attr("frequency", int(centers[hot]))
                self._tuned_hz = int(centers[hot])
            if self._auto_applied:
                self._rx.set_attr("gain_control_mode", "manual")
                self._auto_applied = False
            self._rx.set_attr("hardwaregain", int(g))
            self._gain_applied = int(g)
            tried += 1
            worst = 0.0
            hit = False
            for j in range(len(centers)):
                i = (hot + j) % len(centers)
                if mode in _TG_LIKE and centers[i] != self._tx_tuned_hz:
                    self._txlo.set_attr("frequency", int(centers[i]))  # 測るときと同じく受信の LO に合わせる
                    self._tx_tuned_hz = centers[i]
                a = self._read_position(centers[i], p, FIT_SAMPLES)
                r = overload_ratio_i16(a, full)
                worst = max(worst, r)
                if r > OVERLOAD_RATIO:
                    hot = i
                    hit = True
                    break
            ratios[int(g)] = worst
            if progress is not None:
                progress(min(tried, total), total)
            return hit

        try:
            if not over(hi):
                chosen, overload = hi, False
            elif over(lo):
                chosen, overload = lo, True
            else:
                a, b = lo, hi  # a は振り切れない、b は振り切れる
                while b - a > 1:
                    m = (a + b) // 2
                    if over(m):
                        b = m
                    else:
                        a = m
                # 境目ちょうどの利得は、合わせた後の測定で揺れて振り切れることがある（実機 2026-10-12: 34dB で 5 回中 2 回）。
                # 振り切れの境目が見つかったときだけ、そこから FIT_MARGIN_DB 下げる（全部のモード: ユーザー決定）
                chosen, overload = max(lo, a - FIT_MARGIN_DB), False
        except BaseException:
            # やめた・失敗したときは試す前の利得へ戻す（黙って変えたままにしない）
            # 手動なら利得と手動の指定を、自動なら自動の指定を書き直させる
            self._gain_setting, self._gain_applied, self._auto_applied = before[0], None, False
            try:
                self._apply_gain(centers)
            except Exception:  # noqa: BLE001
                pass
            raise
        self._gain_setting = float(chosen)
        if self._gain_applied != chosen:
            self._rx.set_attr("hardwaregain", int(chosen))
            self._gain_applied = int(chosen)
        if progress is not None:
            progress(total, total)
        return {"gain_db": float(chosen), "overload": overload, "overload_ratio": ratios.get(int(chosen), 0.0),
                "steps_tried": tried, "positions": len(centers)}

    # ---- 受信の利得の補正を測る（設計Q §7.6）----
    def gain_calibration(self, freqs_hz, ext_atten_db: float, cancel: Optional[threading.Event] = None,
                         progress: Optional[Callable[[int, int], None]] = None,
                         on_freq: Optional[Callable[[object], None]] = None) -> list:
        """TX → 外付けの減衰器 → RX のつなぎ方で、周波数ごとに利得を帯の範囲いっぱいに 1dB 刻みで変えて自分の送信の
        トーンを読む（手順は gain_cal.py）。周波数を1つ測るたびに on_freq(gain_cal.FreqResult)。戻り値は全部の結果。

        終わり・やめた・失敗のどれでも、送信を止め（減衰を最小 → LO を止める → 波形を消す）、利用者の送信の減衰と
        受信の利得（手動の値か自動）へ戻す。測っている間に入れた利得・送信の減衰は次の測定の始めに入る。"""
        from . import gain_cal

        freqs = [int(f) for f in freqs_hz]
        with self._lock:
            if self._ctx is None:
                raise DeviceDisconnected("開いていない")
            self._check_tx_allowed()
            saved_atten = self._tx_atten
            bands = [gain_limits(self._profile, [gain_cal.rx_lo_for(f, int(self._profile["freq"]["max_hz"]))])
                     for f in freqs]
            total = sum(hi - lo + 1 for lo, hi in bands)
            done = 0

            def tick() -> None:
                nonlocal done
                done += 1
                if progress is not None:
                    progress(min(done, total), total)

            run = gain_cal._Run(self, cancel, tick)
            out = []
            first = gain_cal.first_atten_for(ext_atten_db)
            try:
                if progress is not None:
                    progress(0, total)
                for f in freqs:
                    res = run.measure(f, first)
                    out.append(res)
                    if on_freq is not None:
                        on_freq(res)
                return out
            except IioError as e:
                if self._is_value_error(e):
                    raise DeviceError(str(e)) from e
                self._drop()
                raise DeviceDisconnected(str(e)) from e
            finally:
                self._idle_wanted = False
                self._tx_atten = saved_atten
                if self._ctx is not None:
                    try:
                        if self._tx_state is None:
                            self._tx_state = ("sg",)  # 途中で失敗しても、書いたかもしれない送信を必ず止める
                        self._tx_off()
                    except IioError:
                        pass
                    try:
                        # 測る前の利得へ戻す（手動ならその値、自動なら自動。いまの LO の帯に収める）
                        self._gain_applied, self._auto_applied = None, False
                        self._apply_gain([self._tuned_hz] if self._tuned_hz else [])
                    except IioError:
                        pass

    def close(self, ext_wait_s: float = 3.0) -> None:
        """閉じる（アプリを閉じる・切断）。錠を持たずに待ってよい道なので、外部 IQ の押すスレッドの終わりを ext_wait_s まで
        待つ。過ぎても閉じない（押すスレッドがコンテキストを閉じる: 設計S §1 の P1）。"""
        ext = self._ext
        if ext is not None:
            ext.request_stop("device_lost")
        with self._lock:
            if self._ctx is not None:
                try:
                    self._tx_off()
                except Exception:  # noqa: BLE001 — 閉じるのは続ける
                    pass
            self._drop()
        if ext is not None:
            ext.join(ext_wait_s)

    # ---- 外部 IQ の送信（設計S §1。中身は ext_tx.py）----
    @property
    def ext_stream(self):
        return self._ext

    def _tx_extra(self) -> dict:
        ts = self._tx_state  # 押すスレッドが消すことがあるので1回だけ読む
        return {"tx": None if ts is None else ts[0], "tx_atten_db": None if ts is None else self._tx_atten_now(),
                "tx_lo_hz": ts[1] if ts is not None and ts[0] == "ext" else None}

    def _tx_atten_now(self) -> float:
        """いま出している送信の減衰（外部 IQ の送信はその値: 設計S §1 の O1）。"""
        ext = self._ext
        return float(self._atten_value_text(ext.atten_db if ext is not None else self._tx_atten))

    def ext_tx_prepare(self, fs: int) -> int:
        """標本化の速さを fs にして読み戻す（掃引の錠を待つので制御スレッドから呼ばない）。"""
        from . import ext_tx

        return ext_tx.prepare_rate(self, fs)

    def ext_tx_restore(self) -> None:
        from . import ext_tx

        ext_tx.restore_rate(self)

    def ext_tx_start(self, lo_hz: int, atten_db: float, send, on_event):
        """押すスレッドを始める（ext_tx_prepare の後）。送信の持ち主を外部にする。"""
        from . import ext_tx

        if self._ctx is None:
            raise DeviceDisconnected("開いていない")
        self._check_tx_allowed()
        spec = ext_tx.ExtTxSpec.from_profile(self._profile)
        stream = ext_tx.ExtTxStream(self, lo_hz, atten_db, spec, send, on_event,
                                    int(self._profile["source"].get("tx_full_scale", 32767)))
        with self._ctx_lock:
            self._ext = stream
            self._tx_state = ("ext", int(lo_hz))
        stream.start()
        return stream

    def ext_tx_stop(self, reason: str, detail=None) -> None:
        ext = self._ext
        if ext is not None:
            ext.request_stop(reason, detail)

    def _ext_finished(self, stream) -> None:
        """押すスレッドが止める順を通った（押すスレッドから呼ぶ）。持ち主を外し、渡されたコンテキストを閉じる。"""
        with self._ctx_lock:
            if self._ext is stream:
                self._ext = None
                if self._tx_state is not None and self._tx_state[0] == "ext":
                    self._tx_state = None
            ctx, stream.close_ctx = stream.close_ctx, None
        if ctx is not None:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass


def iq_from_i16(a: np.ndarray, full_scale: float) -> np.ndarray:
    """int16 の I・Q の交互を複素数へ（振り切れが 1.0）。"""
    x = np.asarray(a, dtype=np.int16)
    if x.size % 2:
        x = x[:-1]
    f = x.astype(np.float32) / np.float32(full_scale)
    return f.view(np.complex64).astype(np.complex64)


def overload_ratio_i16(a: np.ndarray, full_scale: int) -> float:
    """I か Q が端（12 ビットの −2048・+2047）に当たった標本の割合。"""
    x = np.asarray(a, dtype=np.int16)
    n = x.size // 2
    if n == 0:
        return 0.0
    i = x[0: 2 * n: 2]
    q = x[1: 2 * n: 2]
    top, bottom = full_scale - 1, -full_scale
    hit = (i >= top) | (i <= bottom) | (q >= top) | (q <= bottom)
    return float(np.count_nonzero(hit)) / n
