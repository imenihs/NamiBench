"""トレース（設計F）。"""
from .average import NOTICE_LOG_AVERAGE_READS_LOW, AverageType, allowed_average_types, default_average_type
from .ops import (
    DIFF_NO_OVERLAP,
    DIFF_QUANTITY_MISMATCH,
    Decimated,
    TraceDifference,
    decimate,
    difference,
    interp_db,
    to_db,
)
from .trace import (
    ACCUMULATING_ACTIONS,
    NOTICE_AVERAGE_TYPE_RESET,
    NOTICE_RESET_BY_CONDITION,
    NOTICE_RESET_BY_FRAME_MISMATCH,
    TRACE_COUNT,
    AverageAfterCount,
    ConditionChange,
    DisplayChange,
    HoldAfterCount,
    HoldKeep,
    Trace,
    TraceAction,
    TraceLine,
    TraceSet,
    TraceUpdate,
)
