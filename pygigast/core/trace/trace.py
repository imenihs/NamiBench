"""トレース（設計F）。

- トレースは4本（1〜4）。値はリニアで持ち、dB は表示のときの表し方（設計F §2）
- 動作（更新・最大保持・最小保持・平均・固定）と「表示するか」は別の設定（設計F §5.1）。
  非表示でも処理は動作に従って続く
- 平均・最大保持・最小保持は回数 N を持てる。単発はそのうち最大の N 回で止まる（設計F §5.2.1）
- 値の意味が変わる条件の変更で蓄積を捨て、動作は OFF にしない。表示だけの変更では何も捨てない。
  固定のトレースは捨てない（設計F §5.3）
- 知らせはコードで返す。画面の文言は画面側が決める（U-22）
- NF（設計M §1）の平均は、HOT と COLD をそれぞれパワー平均してから NF にする（NF の値を平均しない）。
  掃引の Frame.nf（core/level/nf.NfParts）が HOT・COLD と NF にする計算を持つ
純粋な計算だけ。スレッド・入出力を持たない。
"""
from __future__ import annotations

import copy
import enum
from dataclasses import dataclass
from typing import Optional, Union

import numpy as np

from ..types import FreqAxis, Frame, Mode, Quantity, ValueKind
from . import formats
from .average import (
    NOTICE_LOG_AVERAGE_READS_LOW,
    AverageType,
    allowed_average_types,
    default_average_type,
    from_domain,
    to_domain,
)

TRACE_COUNT = 4


class TraceAction(str, enum.Enum):
    """トレースの動作（設計F §5.2）。非表示は動作ではなく visible で持つ（設計F §5.1）。"""

    UPDATE = "update"
    MAX_HOLD = "max_hold"
    MIN_HOLD = "min_hold"
    AVERAGE = "average"
    FIXED = "fixed"


# 蓄積を持つ動作（条件の変更で捨てる対象。設計F §5.3）
ACCUMULATING_ACTIONS = (TraceAction.MAX_HOLD, TraceAction.MIN_HOLD, TraceAction.AVERAGE)


class AverageAfterCount(str, enum.Enum):
    """連続で平均が N 回に達した後（設計F §5.2.1）。"""

    EXPONENTIAL = "exponential"  # 重み 1/N で平均し続ける（既定）
    RESTART = "restart"  # N 回ごとに新しい平均を作る


class HoldAfterCount(str, enum.Enum):
    """最大・最小保持が回数 N に達した後（設計F §5.2.1）。"""

    CONTINUE = "continue"  # 保持し続ける（既定）
    STOP = "stop"  # それ以上は蓄積しない


class TraceLine(str, enum.Enum):
    """トレースの線（設計F §9）。描き方だけの設定で値の意味は変わらないので、変えても蓄積を捨てない。
    描き方の数（幅・切れ目・重なりの順）は core/report_layout.TRACE_LINE_STYLES（Qt・Web・PNG が同じ値を使う）。"""

    THIN = "thin"
    SOLID = "solid"  # 既定（今までの線）
    THICK = "thick"
    DASH = "dash"
    DOT = "dot"


class ConditionChange(str, enum.Enum):
    """値の意味が変わる条件の変更。蓄積を捨てる（設計F §5.3）。"""

    AXIS = "axis"
    RBW = "rbw"
    MODE = "mode"
    IMAGE_REJECT = "image_reject"  # イメージ除去の選択（設計F §7.3）
    CALIBRATION = "calibration"
    REFERENCE = "reference"  # ノーマライズの基準
    # SG モードで SG の周波数を変えた（設計F §8）。最大・最小保持を残すかは HoldKeep で渡す
    SG_FREQ = "sg_freq"
    # NF の ENR を変えた（設計M。NF の値の意味が変わる）
    ENR = "enr"


class HoldKeep(str, enum.Enum):
    """条件の変更で最大保持・最小保持を残すか（設計F §8）。平均・更新の蓄積はどれでも捨てる。"""

    DISCARD = "discard"  # 残さない（ふつうの条件の変更。SG の周波数を手で変え「消す」を選んでいるとき）
    # 回数で止まらない保持だけ残す（SG の周波数を手で変え「消す」を外しているとき）。「N 回で止める」保持は
    # 残すと N 回で止まったまま新しい周波数を取り込まないので捨てる（F §8 の「回数で止まる蓄積」）
    UNLIMITED = "unlimited"
    ALL = "all"  # 全部残す（SG のステップ掃引が周波数を変えたとき: 特性を積み上げて描く）


class DisplayChange(str, enum.Enum):
    """表示だけの変更。何も捨てない（設計F §5.3）。"""

    REF_LEVEL = "ref_level"
    DB_PER_DIV = "db_per_div"
    UNIT = "unit"
    COLOR = "color"
    VISIBLE = "visible"
    WINDOW_SIZE = "window_size"


# 知らせのコード（文言は画面側）
# 条件の変更で蓄積を捨てた（トレースの一覧に1回だけ「条件変更でリセット」: 設計F §3）
NOTICE_RESET_BY_CONDITION = "reset_by_condition"
# 条件の変更を告げられないまま軸・量・値の種類の違う掃引が来たので蓄積を捨てた（設計に無い判断:
# 違う軸の値は点ごとに比べられないため）
NOTICE_RESET_BY_FRAME_MISMATCH = "reset_by_frame_mismatch"
# 選んでいた平均の種類がこの掃引の値の種類では選べないので既定に戻した（設計に無い判断）
NOTICE_AVERAGE_TYPE_RESET = "average_type_reset"


@dataclass(frozen=True)
class TraceUpdate:
    """1回の掃引を受けたときに変わったこと。

    changed: 表示する値が変わったか。count: いまの回数（「平均 12/16」の 12）。
    count_target: 回数 N（回数を持たないときは None）。count_reached: いまの回数が N に達しているか。
    notices: 知らせのコード。
    """

    number: int
    changed: bool
    count: int
    count_target: Optional[int]
    count_reached: bool
    notices: tuple[str, ...] = ()


_UNSET = object()


def _flags_of(frame: Frame) -> dict[str, np.ndarray]:
    """掃引の点ごとの印を、点数の長さの bool の配列にそろえて写す。"""
    n = frame.axis.points
    return {k: np.broadcast_to(np.asarray(v, dtype=bool), (n,)).copy() for k, v in frame.flags.items()}


def _or_flags(acc: dict[str, np.ndarray], new: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """印の和。蓄積に1回でも印の付いた掃引が入った点は印を持つ（設計に無い判断: 最も慎重な扱い）。"""
    out = {k: v.copy() for k, v in acc.items()}
    for k, v in new.items():
        out[k] = out[k] | v if k in out else v.copy()
    return out


class Trace:
    """1本のトレース（設計F §5.1）。"""

    def __init__(
        self,
        number: int,
        action: TraceAction = TraceAction.UPDATE,
        visible: bool = True,
        count: Optional[int] = None,
        average_type: Optional[AverageType] = None,
        average_after: AverageAfterCount = AverageAfterCount.EXPONENTIAL,
        hold_after: HoldAfterCount = HoldAfterCount.CONTINUE,
        line: TraceLine = TraceLine.SOLID,
        fmt: str = formats.DB,
    ) -> None:
        if not isinstance(number, int) or not 1 <= number <= TRACE_COUNT:
            raise ValueError(f"トレースの番号は 1〜{TRACE_COUNT}")
        self.number = number
        self.action = TraceAction(action)
        self.visible = bool(visible)
        self.count = count
        # None は既定（量と値の種類で決まる: default_average_type）
        self.average_type = None if average_type is None else AverageType(average_type)
        self.average_after = AverageAfterCount(average_after)
        self.hold_after = HoldAfterCount(hold_after)
        self.line = TraceLine(line)
        # 表示の形式（設計R §3）。選んだ形式を覚えておき、値に当たらないとき（S11 の比でない）は dB で出す
        self.format = fmt
        self._validate()

        # 表示する値と、その値の条件
        self.axis: Optional[FreqAxis] = None
        self.values: Optional[np.ndarray] = None
        self.quantity: Optional[Quantity] = None
        self.value_kind: Optional[ValueKind] = None
        self.flags: dict[str, np.ndarray] = {}
        self.profile: dict = {}
        # 値を測ったモード（S11 の比かどうかで、表示の形式の |Γ|・SWR が当たるかが決まる: 設計R §3）
        self.mode: Optional[Mode] = None
        self.display_start_hz: Optional[int] = None
        self.display_stop_hz: Optional[int] = None
        self.last_update_time: Optional[float] = None
        # SG のステップ掃引の結果（設計L §1.3。固定のトレースにだけ入る）: 全部重ねるときの段ごとの値 [段, 点]（リニア）と、
        # 掃引の設定と済んだ段の数（凡例・保存に出す）。固定でなくなったら・値を捨てたら消す
        self.steps: Optional[np.ndarray] = None
        self.step_info: Optional[dict] = None

        # 蓄積（平均は平均の領域の値、最大・最小保持はリニアの値）と回数
        self._acc: Optional[np.ndarray] = None
        self._acc_flags: dict[str, np.ndarray] = {}
        self._acc_kind: Optional[AverageType] = None
        self._k = 0
        # N 回ごとにやり直す平均で、1回目の N 回が済んだか（済むまでは途中の平均を出す）
        self._restart_published = False

    # ---- 設定 ----

    def _validate(self) -> None:
        if self.count is not None and (not isinstance(self.count, int) or self.count < 1):
            raise ValueError("回数は 1 以上の int か None")
        if self.action == TraceAction.AVERAGE and self.count is None:
            raise ValueError("平均は回数を持つ（設計F §2: 平均（回数指定））")
        if not formats.is_format(self.format):
            raise ValueError(f"表示の形式が不明: {self.format}")

    @property
    def effective_format(self) -> str:
        """いま使う表示の形式（設計R §3）。選んだ形式が値に当たらなければ dB。"""
        return formats.effective(self.format, self.mode, self.quantity)

    @property
    def effective_count(self) -> Optional[int]:
        """いまの動作で意味を持つ回数 N。平均・最大保持・最小保持だけが回数を持つ。"""
        return self.count if self.action in ACCUMULATING_ACTIONS else None

    def configure(
        self,
        action=_UNSET,
        count=_UNSET,
        average_type=_UNSET,
        average_after=_UNSET,
        hold_after=_UNSET,
        visible=_UNSET,
        line=_UNSET,
        fmt=_UNSET,
    ) -> tuple[str, ...]:
        """設定を変える。渡した欄だけ変え、知らせのコードを返す。

        動作・回数・平均の種類が変わったら蓄積を捨てる（蓄積の意味が変わるため。設計に無い判断）。
        固定に変えたときは、いまの値を残す（比べるために残す: 設計F §5.2）。
        表示するか・N 回に達した後の選び方・線（設計F §9）・表示の形式（設計R §3。値は同じで表し方だけ）は何も捨てない。
        """
        old = (self.action, self.count, self.average_type, self.average_after,
               self.hold_after, self.visible, self.line, self.format)
        if fmt is not _UNSET and not formats.is_format(fmt):
            raise ValueError(f"表示の形式が不明: {fmt}")  # ほかの欄はまだ変えていない
        if line is not _UNSET:
            self.line = TraceLine(line)  # 読めない線は ValueError（ほかの欄はまだ変えていない）
        if fmt is not _UNSET:
            self.format = fmt
        if action is not _UNSET:
            self.action = TraceAction(action)
        if count is not _UNSET:
            self.count = count
        if average_type is not _UNSET:
            self.average_type = None if average_type is None else AverageType(average_type)
        if average_after is not _UNSET:
            self.average_after = AverageAfterCount(average_after)
        if hold_after is not _UNSET:
            self.hold_after = HoldAfterCount(hold_after)
        if visible is not _UNSET:
            self.visible = bool(visible)
        try:
            self._validate()
            if self.average_type is not None and self.value_kind is not None:
                if self.average_type not in allowed_average_types(self.value_kind):
                    raise ValueError("この値の種類では選べない平均の種類")
        except ValueError:
            (self.action, self.count, self.average_type, self.average_after,
             self.hold_after, self.visible, self.line, self.format) = old
            raise

        notices: list[str] = []
        if (self.action, self.count, self.average_type) != old[:3]:
            if self.action != TraceAction.FIXED:
                self._discard(clear_values=self.action in ACCUMULATING_ACTIONS)
                self.steps = self.step_info = None  # ステップ掃引の結果の印は固定の間だけ
        if self.average_after != old[3]:
            self._restart_published = False
        if (self.action == TraceAction.AVERAGE and self.effective_average_type() == AverageType.LOG
                and (old[0] != TraceAction.AVERAGE or old[2] != AverageType.LOG)):
            notices.append(NOTICE_LOG_AVERAGE_READS_LOW)
        return tuple(notices)

    def effective_average_type(self) -> Optional[AverageType]:
        """いま使う平均の種類。選んでいなければ量と値の種類の既定（値が来る前は選んだものか None）。

        NF は選択によらずパワー平均（HOT と COLD の電力を平均する: 設計M §1。選んだ種類は覚えておき、ほかの量で使う）。
        """
        if self.quantity == Quantity.NF:
            return AverageType.POWER
        if self.average_type is not None:
            return self.average_type
        if self.quantity is None or self.value_kind is None:
            return None
        return default_average_type(self.quantity, self.value_kind)

    @property
    def current_count(self) -> int:
        """いまの回数（「平均 12/16」の 12）。"""
        return self._k

    @property
    def count_reached(self) -> bool:
        n = self.effective_count
        return n is not None and self._k >= n

    # ---- 捨てる ----

    def _discard(self, clear_values: bool) -> None:
        self._acc = None
        self._acc_flags = {}
        self._acc_kind = None
        self._k = 0
        self._restart_published = False
        if clear_values:
            self.values = None
            self.flags = {}
            self.steps = self.step_info = None

    def discard(self) -> None:
        """蓄積を捨てる（単発の前など）。動作はそのまま（設計F §5.2.1）。"""
        if self.action == TraceAction.FIXED:
            return
        self._discard(clear_values=self.action in ACCUMULATING_ACTIONS)

    def on_condition_change(self, kind: Union[ConditionChange, DisplayChange, str],
                            holds: Union[HoldKeep, str] = HoldKeep.DISCARD) -> Optional[str]:
        """条件が変わったことを受ける（設計F §5.3）。捨てたら知らせのコードを返す。

        値の意味が変わる条件（ConditionChange）なら蓄積を捨てる。動作は OFF にしない。
        表示だけの変更（DisplayChange）では何も捨てない。固定のトレースは何も捨てない。
        holds が残すと言う最大・最小保持は捨てない（設計F §8: SG の周波数の変更）。
        知らない種類は ValueError（捨てるか捨てないかを決められないため）。
        """
        if isinstance(kind, DisplayChange):
            return None
        if not isinstance(kind, ConditionChange):
            try:
                kind = ConditionChange(kind)
            except ValueError:
                DisplayChange(kind)  # どちらでもなければ ValueError
                return None
        if self.action == TraceAction.FIXED or self._keeps_hold(HoldKeep(holds)):
            return None
        had = self.action in ACCUMULATING_ACTIONS and self._k > 0
        self._discard(clear_values=self.action in ACCUMULATING_ACTIONS)
        return NOTICE_RESET_BY_CONDITION if had else None

    def _keeps_hold(self, holds: HoldKeep) -> bool:
        """この最大・最小保持を holds の決まりで残すか（設計F §8）。"""
        if holds == HoldKeep.DISCARD or self.action not in (TraceAction.MAX_HOLD, TraceAction.MIN_HOLD):
            return False
        if holds == HoldKeep.ALL:
            return True
        return not (self.effective_count is not None and self.hold_after == HoldAfterCount.STOP)

    def axis_differs(self, current_axis: Optional[FreqAxis]) -> bool:
        """持っている値の軸がいまの軸と違うか（固定のトレースの凡例に出す: 設計F §5.3）。"""
        return self.axis is not None and current_axis is not None and self.axis != current_axis

    def scale_db(self, delta_db: float) -> None:
        """持っている比の値を delta_db だけ動かす（基準の値を直したとき: 設計E §6.7。測り直さない）。

        蓄積（平均の領域の値・保持の値）も同じだけ動かすので、次の掃引の値と平均・保持がそろう。比（振幅）の
        トレースにだけ使う: 平均の領域は振幅（電圧平均・ベクトル平均）は ×g、電力（パワー平均）は ×g²、
        dB（対数平均）は +delta_db（core/trace/average.py の領域）。
        """
        if not delta_db or self.values is None:
            return
        g = 10.0 ** (float(delta_db) / 20.0)
        self.values = self.values * g
        if self._acc is not None:
            if self._acc_kind == AverageType.POWER:
                self._acc = self._acc * (g * g)
            elif self._acc_kind == AverageType.LOG:
                self._acc = self._acc + float(delta_db)
            else:
                self._acc = self._acc * g  # 電圧平均・ベクトル平均・最大/最小保持（リニアの振幅）
        if self.steps is not None:
            self.steps = self.steps * g

    # ---- 掃引を受ける ----

    def apply(self, frame: Frame) -> TraceUpdate:
        """1回の掃引を動作に従って取り込む（設計F §5.2）。"""
        if frame.values.shape != (frame.axis.points,):
            raise ValueError("values の長さが軸の点数と違う")
        if self.action == TraceAction.FIXED:
            return self._result(changed=False)
        self.steps = self.step_info = None

        notices: list[str] = []
        if self.axis is not None and (
            self.axis != frame.axis or self.quantity != frame.quantity or self.value_kind != frame.value_kind
        ):
            if self.action in ACCUMULATING_ACTIONS and self._k > 0:
                notices.append(NOTICE_RESET_BY_FRAME_MISMATCH)
            self._discard(clear_values=True)
        if self.average_type is not None and self.average_type not in allowed_average_types(frame.value_kind):
            self.average_type = None
            self._discard(clear_values=True)
            notices.append(NOTICE_AVERAGE_TYPE_RESET)

        self.axis = frame.axis
        self.quantity = frame.quantity
        self.value_kind = frame.value_kind
        self.profile = copy.deepcopy(frame.profile)
        self.mode = frame.adopted.mode if frame.adopted is not None else None
        self.display_start_hz = frame.display_start_hz
        self.display_stop_hz = frame.display_stop_hz

        dtype = np.complex128 if frame.value_kind == ValueKind.COMPLEX else np.float64
        new = np.asarray(frame.values, dtype=dtype).copy()
        new_flags = _flags_of(frame)

        if self.action == TraceAction.UPDATE:
            changed = self._apply_update(new, new_flags)
        elif self.action == TraceAction.AVERAGE:
            changed = self._apply_average(new, new_flags, frame.nf if frame.quantity == Quantity.NF else None)
        else:
            changed = self._apply_hold(new, new_flags)
        if changed:
            self.last_update_time = frame.time
        return self._result(changed=changed, notices=tuple(notices))

    def set_result(self, frame: Optional[Frame], steps: Optional[np.ndarray] = None,
                   step_info: Optional[dict] = None) -> None:
        """固定のトレースに値を入れる（SG のステップ掃引の結果: 設計L §1.3）。frame が None なら空にする。

        frame の値・軸・量・印・プロファイル・表示の範囲・時刻をそのまま持つ。steps は全部重ねるときの段ごとの値
        [段, 点]（リニア）。固定でないトレースには入れない（ValueError）。
        """
        if self.action != TraceAction.FIXED:
            raise ValueError("ステップ掃引の結果は固定のトレースに入れる")
        self._discard(clear_values=True)
        if frame is None:
            return
        self.axis = frame.axis
        self.quantity = frame.quantity
        self.value_kind = frame.value_kind
        self.profile = copy.deepcopy(frame.profile)
        self.mode = frame.adopted.mode if frame.adopted is not None else None  # 測ったモード（表示の形式: 設計R §3）
        self.display_start_hz = frame.display_start_hz
        self.display_stop_hz = frame.display_stop_hz
        self.values = np.asarray(frame.values, dtype=np.float64).copy()
        self.flags = _flags_of(frame)
        self.last_update_time = frame.time
        self.steps = None if steps is None else np.asarray(steps, dtype=np.float64).copy()
        self.step_info = None if step_info is None else dict(step_info)

    def _result(self, changed: bool, notices: tuple[str, ...] = ()) -> TraceUpdate:
        return TraceUpdate(
            number=self.number,
            changed=changed,
            count=self._k,
            count_target=self.effective_count,
            count_reached=self.count_reached,
            notices=notices,
        )

    def _apply_update(self, new: np.ndarray, new_flags: dict) -> bool:
        self.values = new
        self.flags = new_flags
        self._k = 1
        return True

    def _apply_hold(self, new: np.ndarray, new_flags: dict) -> bool:
        n = self.effective_count
        if n is not None and self._k >= n and self.hold_after == HoldAfterCount.STOP:
            return False
        if self._acc is None:
            self._acc = new
            self._acc_flags = new_flags
        else:
            a_new = np.abs(new)
            a_acc = np.abs(self._acc)
            # 複素数は振幅で比べ、その点の値（複素数）を持つ（設計F §5.2）。nan の点は新しい値で埋める
            if self.action == TraceAction.MAX_HOLD:
                take = (a_new > a_acc) | np.isnan(a_acc)
            else:
                take = (a_new < a_acc) | np.isnan(a_acc)
            self._acc = np.where(take, new, self._acc)
            self._acc_flags = _or_flags(self._acc_flags, new_flags)
        # 回数を持つときは N で止める（「最大保持 16/16」）。持たないときは数え続ける
        self._k = self._k + 1 if n is None else min(self._k + 1, n)
        self.values = self._acc.copy()
        self.flags = {k: v.copy() for k, v in self._acc_flags.items()}
        return True

    def _apply_average(self, new: np.ndarray, new_flags: dict, nf=None) -> bool:
        """nf（NF の掃引の HOT・COLD: core/level/nf.NfParts）があれば、HOT と COLD をそれぞれパワー平均し、
        平均から NF を出す（設計M §1）。差が小さい点の印は平均した HOT・COLD で決め直す（1回ずつの印を足さない）。"""
        kind = self.effective_average_type()
        n = self.count
        assert kind is not None and n is not None and self.quantity is not None
        if self._acc_kind != kind:
            self._acc = None
            self._k = 0
        if nf is not None:
            d = np.stack([np.asarray(nf.hot_w, dtype=np.float64), np.asarray(nf.cold_w, dtype=np.float64)])
            if self._acc is not None and self._acc.shape != d.shape:
                self._acc = None
                self._k = 0
        else:
            d = to_domain(new, self.quantity, kind)

        if self._k >= n and self.average_after == AverageAfterCount.RESTART:
            # N 回分の平均を出し終えたので、次の N 回で新しい平均を作る。出す値は前の平均のまま
            self._acc = None
            self._acc_flags = {}
            self._k = 0
            self._restart_published = True

        if self._acc is None:
            self._acc = d
            self._acc_flags = new_flags
            self._acc_kind = kind
            self._k = 1
        elif self._k < n:
            # 累積平均
            self._acc = self._acc + (d - self._acc) / (self._k + 1)
            self._acc_flags = _or_flags(self._acc_flags, new_flags)
            self._k += 1
        else:
            # 指数平均（重み 1/N）。回数は N のまま
            self._acc = self._acc + (d - self._acc) / n
            self._acc_flags = _or_flags(self._acc_flags, new_flags)

        if self.average_after == AverageAfterCount.RESTART and self._restart_published and self._k < n:
            return False
        if nf is not None:
            self.values, nf_flags = nf.evaluate(self._acc[0], self._acc[1])
            self.flags = {**{k: v.copy() for k, v in self._acc_flags.items()}, **nf_flags}
            return True
        self.values = from_domain(self._acc, self.quantity, kind)
        self.flags = {k: v.copy() for k, v in self._acc_flags.items()}
        return True


class TraceSet:
    """4本のトレース（設計F §2）。

    既定: トレース1 は表示する「更新」、2〜4 は表示しない「更新」（設計F §5.2 で更新が既定。
    どれを表示するかは設計に無いので、1本だけを出す）。
    """

    def __init__(self) -> None:
        self.traces: tuple[Trace, ...] = tuple(
            Trace(i, TraceAction.UPDATE, visible=(i == 1)) for i in range(1, TRACE_COUNT + 1)
        )

    def __getitem__(self, number: int) -> Trace:
        """番号（1〜4）でトレースを引く。"""
        if not isinstance(number, int) or not 1 <= number <= TRACE_COUNT:
            raise KeyError(number)
        return self.traces[number - 1]

    def __iter__(self):
        return iter(self.traces)

    def apply(self, frame: Frame) -> list[TraceUpdate]:
        """4本すべてに掃引を渡す（非表示でも動作に従って処理する: 設計F §5.1）。"""
        return [t.apply(frame) for t in self.traces]

    def on_condition_change(self, kind: Union[ConditionChange, DisplayChange, str],
                            holds: Union[HoldKeep, str] = HoldKeep.DISCARD) -> dict[int, str]:
        """条件の変更を4本に渡し、捨てたトレースの番号と知らせのコードを返す（設計F §5.3・§8）。"""
        out: dict[int, str] = {}
        for t in self.traces:
            notice = t.on_condition_change(kind, holds)
            if notice is not None:
                out[t.number] = notice
        return out

    def fixed_axis_differs(self, current_axis: FreqAxis) -> list[int]:
        """いまの軸と違う軸の値を持つ固定のトレースの番号（凡例に出す: 設計F §5.3）。"""
        return [t.number for t in self.traces if t.action == TraceAction.FIXED and t.axis_differs(current_axis)]

