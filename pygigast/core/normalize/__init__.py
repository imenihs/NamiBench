"""ノーマライズ（TG の通過特性の基準・S11 の基準。設計E）。"""
from .apply import NormalizeResult, apply_reference
from .reference import (
    KIND_FINE,
    KIND_WIDE,
    VALUE_DB_LIMIT,
    WIDE_STEP_HZ,
    Reference,
    ReferenceBuildError,
    ReferenceSegment,
    average_power,
    check_value_db,
    new_reference,
    segment_from_frames,
    wide_range_hz,
)
from .s11 import ratio_db, return_loss_db, s11_segment_from_frames, swr
from .store import (
    ReferenceFileError,
    ReferenceInfo,
    delete_reference,
    list_references,
    load_reference,
    save_reference,
)

__all__ = [
    "KIND_FINE",
    "KIND_WIDE",
    "VALUE_DB_LIMIT",
    "WIDE_STEP_HZ",
    "NormalizeResult",
    "Reference",
    "ReferenceBuildError",
    "ReferenceFileError",
    "ReferenceInfo",
    "ReferenceSegment",
    "apply_reference",
    "average_power",
    "check_value_db",
    "delete_reference",
    "list_references",
    "load_reference",
    "new_reference",
    "ratio_db",
    "return_loss_db",
    "s11_segment_from_frames",
    "save_reference",
    "segment_from_frames",
    "swr",
    "wide_range_hz",
]
