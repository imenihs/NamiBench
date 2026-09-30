"""PNG に描く中身（設計I §3.2 の帯とグラフ）を画面の状態から作る。形は png_export の冒頭の説明のとおり。"""
from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import numpy as np

from . import marker_rows, texts
from .plot_layers import marked_points

if TYPE_CHECKING:
    from .main_window import MainWindow


def build(win: "MainWindow") -> dict:
    """画面に出ているもの（状態・描いているトレース・マーカー表・注意）から画像の中身を作る（I §3.2）。"""
    st = win.state
    sw = st.get("sweep") or {}
    ident = (st.get("connection") or {}).get("identity") or {}
    latest = None
    traces = []
    for _curve, label, p in win.traces.legend_entries():
        ax = p["axis"]
        if "freqs_hz" in ax:
            continue  # 不等間隔の軸は画像の描き方（Start・Step）に載らない
        item = {"start_hz": ax["start_hz"], "step_hz": ax["step_hz"], "points": ax["points"],
                "values_db": np.asarray(p["values"], dtype=np.float64), "visible": True,
                "flags": None}
        if p.get("source") == "file":
            item.update({"number": "", "action": "", "label": label,
                         "color": win.traces.files[str(p["trace"])]["color"], "count": None})
        else:
            item.update({**texts.legend_parts(p), "color": f"trace{p['trace']}"})
            item["flags"] = marked_points(p, ax["points"]).tolist()
            if p.get("time") and (latest is None or p["time"] > latest):
                latest = p["time"]
        traces.append(item)
    cal = st.get("calibration")
    norm = st.get("normalization") or {}
    nz = None
    if norm.get("used"):
        sel = norm.get("selected") or {}
        nz = {"name": sel.get("name", ""),
              "at": datetime.datetime.fromtimestamp(sel["taken_at"]).strftime("%Y-%m-%d %H:%M")
              if sel.get("taken_at") else "",
              "applied": bool(norm.get("applied")), "before_power_cycle": bool(norm.get("power_cycled")),
              "reason": texts.NORM_REASONS.get(norm.get("reason") or "", "")}
    when = datetime.datetime.fromtimestamp(latest).astimezone() if latest else datetime.datetime.now().astimezone()
    warnings = [line for line in win.cond_label.text().split("\n") if line]
    return {
        "measured_at": when.isoformat(timespec="seconds"),
        "device": {"model": ident.get("model") or (st.get("connection") or {}).get("profile_model"),
                   "serial": ident.get("serial")},
        "mode": win.MODE_NAMES.get(sw.get("mode"), sw.get("mode")),
        "conditions": {"start_hz": sw.get("start_hz"), "stop_hz": sw.get("stop_hz"), "rbw_hz": sw.get("rbw_hz"),
                       "points": sw.get("points"), "step_hz": sw.get("step_hz"),
                       "image_cancel": sw.get("image_cancel"), "ref_level": win._ref_dbm,
                       "db_per_div": win._db_div, "unit": win.display_unit()},
        "calibration": {"source": (cal.get("source") or {}).get("file_name") or "校正データ",
                        "at": cal.get("created_at")} if cal else None,
        "normalization": nz,
        "traces": traces,
        "markers": marker_rows.png_rows(win.marker_row_list),
        "warnings": warnings,
    }
