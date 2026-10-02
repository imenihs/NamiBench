"""実機の GigaSt を使う手動の確認（自動の試験には含めない。ファイル名が test_ で始まらない）。

使い方（プロジェクトのルートで）:
    venv\\Scripts\\python.exe -m tests.manual_smoke_gigast [記録を置くフォルダ]

FTDI 0403:6001 のポートを探し、確認の掃引（設計B §7.3）で GigaSt かを確かめてから、
次の掃引を回して、受け取ったバイト数・所要時間と見積もり・捨てた回数を出す。
- SA 0〜1000MHz（点数は自動）
- TG 100〜3000MHz
- SA イメージ除去 正確（帯の中の4回）
- 12000 点の掃引
送るのは設計の範囲内の32バイトの要求だけ（範囲外の値は送らない）。
掃引条件（AdoptedSweep）は掃引条件モデルを使わず、設計C §5.3・§5.6 の決まりで手で作る。
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Optional

from pygigast.core.devices.gigast import protocol
from pygigast.core.devices.gigast.driver import GigaStDevice
from pygigast.core.devices.gigast.traffic_log import TrafficLog
from pygigast.core.devices.gigast.transport import discover
from pygigast.core.sweep import image_reject as ir
from pygigast.core.types import AdoptedSweep, DeviceDisconnected, FreqAxis, Mode, SweepDiscarded, SweepSegment

PROFILE = protocol.default_profile()


def make_sweep(start_hz: int, stop_hz: int, mode: Mode, image_reject: str = "off",
               sg_hz: Optional[int] = None, rbw_hz: int = 180_000) -> AdoptedSweep:
    """設計C §5.3（自動の Step・点数 = ceil(Span/Step)+1）と §5.6（前置き）、設計F §7.2（帯で分けた区間）で手で作る。
    前置きはどの区間も同じ点数（範囲の下限の近くから始めない）。"""
    span = stop_hz - start_hz
    auto = PROFILE["freq"]["auto_step"]
    if span > auto["span_threshold_hz"]:
        step = auto["wide_step_hz"]
    else:
        step = (span // auto["divisions"]) // 100 * 100
    points = math.ceil(span / step) + 1
    pre = protocol.preroll_points_for(PROFILE, step, rbw_hz)
    first, per = protocol.timing(PROFILE, step, rbw_hz)
    axis = FreqAxis(start_hz, step, points)
    choice = ir.OFF if mode == Mode.NF else image_reject
    segs = tuple(SweepSegment(point0=p0, points=n, b3=ir.b3_order(k), preroll_points=pre, in_band=inside)
                 for p0, n, k, inside in ir.passes_by_region(choice, axis, ir.RejectProfile(PROFILE)))
    est = sum(s.passes * (first + (s.points + pre) * per) for s in segs) * (2 if mode == Mode.NF else 1)
    return AdoptedSweep(
        start_hz=start_hz, stop_hz=stop_hz, axis=axis, rbw_hz=rbw_hz,
        rbw_preference_hz=rbw_hz, mode=mode, sg_hz=sg_hz, image_reject=image_reject,
        preroll_points=pre, transient_head_points=0, estimated_sweep_s=est, segments=segs,
    )


def run(dev: GigaStDevice, name: str, sweep: AdoptedSweep, repeat: int) -> dict:
    ds = protocol.device_sweep(sweep, PROFILE, 1)
    print(f"\n[{name}] Start {sweep.start_hz / 1e6:g}MHz Stop {sweep.stop_hz / 1e6:g}MHz "
          f"Step {sweep.axis.step_hz}Hz 点数 {sweep.axis.points} 前置き {sweep.preroll_points} "
          f"機器の点数 {ds.points} Sample {ds.sample} イメージ除去 {sweep.image_reject} "
          f"期待バイト数 {ds.expected_bytes} 見積もり {sweep.estimated_sweep_s:.3f}s")
    durs, discards = [], []
    for i in range(repeat):
        try:
            r = dev.measure(sweep)
            durs.append(r.duration_s)
            print(f"  {i + 1}: {r.duration_s:.3f}s  点数 {len(r.raw)}  AD 最小 {int(r.raw.min())} "
                  f"中央 {int(statistics.median(r.raw.tolist()))} 最大 {int(r.raw.max())} "
                  f"(最大の周波数 {sweep.axis.freqs()[int(r.raw.argmax())] / 1e6:g}MHz)")
        except SweepDiscarded as e:
            discards.append(e.reason)
            print(f"  {i + 1}: 捨てた reason={e.reason} detail={e.detail}")
    return {"name": name, "estimate_s": sweep.estimated_sweep_s, "durations_s": durs, "discards": discards}


def summarize_log(jsonl: Path) -> None:
    rows = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
    print(f"\n送受信の記録 {len(rows)} 件: バイト数ちょうど {sum(r['received'] == r['expected'] for r in rows)} 件、"
          f"打ち切り {sum(r['limit'] is not None for r in rows)} 件、例外 {sum(r['error'] is not None for r in rows)} 件")
    for r in rows:
        ds = protocol.unpack(bytes.fromhex(r["request"]))
        est = protocol.limits(PROFILE, ds.step_hz, ds.rbw_hz, ds.points)
        f_est = protocol.timing(PROFILE, ds.step_hz, ds.rbw_hz)[0]
        print(f"  {r['note'] or '':6s} MODE 0x{ds.mode_bits:02X} 点数 {ds.points:5d} 受信 {r['received']:5d}/{r['expected']:5d} "
              f"初バイト {r['first_byte_s']}s (見積 {f_est:.3f}) 全体 {r['duration_s']}s (見積 {est.estimated_s:.3f}"
              f" 上限 {est.total_s:.2f}) limit={r['limit']}")


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    ports = discover()
    print(f"FTDI 0403:6001 のポート: {len(ports)} 件")
    for p in ports:
        print(f"  {p.port}  シリアル番号 {p.serial}  {p.description}")
    if len(ports) != 1:
        print("GigaSt の候補がちょうど1つでないので止める（知らない機器には送らない）")
        return 2
    log = TrafficLog(out_dir / "traffic") if out_dir else None
    port = ports[0]
    t0 = time.monotonic()
    ok = GigaStDevice.probe(port.port, traffic_log=log)
    print(f"確認の掃引（Plot 500・1004 バイト）: {'GigaSt と判断' if ok else '応答が違う'} ({time.monotonic() - t0:.3f}s)")
    if not ok:
        return 1
    dev = GigaStDevice(traffic_log=log)
    dev.open(serial=port.serial)
    results = []
    try:
        results.append(run(dev, "SA 0-1000MHz 自動", make_sweep(0, 1_000_000_000, Mode.SA), 3))
        results.append(run(dev, "TG 100-3000MHz", make_sweep(100_000_000, 3_000_000_000, Mode.TG), 3))
        results.append(run(dev, "SA 900-1000MHz イメージ除去 正確",
                           make_sweep(900_000_000, 1_000_000_000, Mode.SA, image_reject="accurate"), 3))
        results.append(run(dev, "SA 0-11999MHz 12000点", make_sweep(0, 11_999_000_000, Mode.SA), 2))
    except DeviceDisconnected as e:
        print(f"切断: {e}")
    finally:
        dev.close()
    print("\n=== まとめ ===")
    for r in results:
        d = r["durations_s"]
        rng = f"{min(d):.3f}〜{max(d):.3f}s" if d else "-"
        print(f"{r['name']}: 見積 {r['estimate_s']:.3f}s / 実測 {rng} / 成功 {len(d)} / 捨てた {len(r['discards'])} {r['discards']}")
    if log:
        log.close()
        summarize_log(log.jsonl_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
