"""Replay a set of trips and check Gate 1 (HLD section 16: median savings ≥ 15%)."""

from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

from .airports import Airport
from .fares import FareSource
from .models import load_trip
from .optimizer import SearchResult, search
from .report import airports_label

GATE1_THRESHOLD = 0.15
GATE1_MIN_TRIPS = 10


@dataclass
class ReplayRow:
    file: str
    result: SearchResult | None
    error: str | None = None

    @property
    def savings_pct(self) -> float | None:
        return None if self.result is None else self.result.transport_savings_pct


def trip_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        files.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    return files


def replay(files: list[Path], airports: dict[str, Airport], fares: FareSource) -> list[ReplayRow]:
    rows = []
    for f in files:
        try:
            rows.append(ReplayRow(f.name, search(load_trip(f), airports, fares)))
        except (ValueError, KeyError) as exc:
            rows.append(ReplayRow(f.name, None, f"{type(exc).__name__}: {exc}"))
    return rows


def format_replay(rows: list[ReplayRow], synthetic: bool) -> str:
    header = f"{'Trip':<34} {'Baseline':>10} {'Best':>10} {'Saved':>9} {'Saved %':>8}  {'Airports':<16} {'vs actual':>10}"
    lines = [header, "-" * len(header)]
    for r in rows:
        if r.result is None:
            lines.append(f"{r.file:<34} ERROR {r.error}")
            continue
        res = r.result
        name = res.trip.name[:34]
        if res.baseline is None or res.best is None:
            why = "no baseline fares" if res.baseline is None else "no feasible plan"
            lines.append(f"{name:<34} {why}")
            continue
        sk = res.best.skeleton
        actual = ""
        if res.trip.actual_transport_usd:
            a = res.trip.actual_transport_usd
            actual = f"{(a - res.best.transport) / a * 100:+.1f}%"
        lines.append(
            f"{name:<34} {res.baseline.transport:>10,.0f} {res.best.transport:>10,.0f} "
            f"{res.transport_savings:>9,.0f} {res.transport_savings_pct * 100:>7.1f}%  "
            f"{airports_label(sk):<16} {actual:>10}"
        )

    vals = [r.savings_pct for r in rows if r.savings_pct is not None]
    lines.append("")
    if not vals:
        lines.append("No trips produced a savings number.")
        return "\n".join(lines)

    median = statistics.median(vals)
    passed = median >= GATE1_THRESHOLD
    lines.append(f"Trips scored: {len(vals)}   Median transport savings: {median * 100:.1f}%")
    verdict = "PASS" if passed else "FAIL"
    lines.append(f"Gate 1 (median savings ≥ {GATE1_THRESHOLD:.0%}): {verdict}")
    caveats = []
    if len(vals) < GATE1_MIN_TRIPS:
        caveats.append(f"only {len(vals)} trips scored; the plan calls for {GATE1_MIN_TRIPS} real trips")
    if synthetic:
        caveats.append("fares are SYNTHETIC, so this verdict means nothing yet")
    for c in caveats:
        lines.append(f"  ⚠ {c}")
    return "\n".join(lines)


def write_csv(rows: list[ReplayRow], path: str | Path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["file", "trip", "baseline_transport", "best_transport", "savings_usd", "savings_pct",
             "gateway_in", "gateway_out", "pickup", "dropoff", "mode", "start", "transit_hours_delta", "actual_transport", "error"]
        )
        for r in rows:
            res = r.result
            if res is None or res.best is None or res.baseline is None:
                w.writerow([r.file, res.trip.name if res else "", "", "", "", "", "", "", "", "", "", "", "", "",
                            r.error or "no plan or baseline"])
                continue
            sk = res.best.skeleton
            w.writerow([
                r.file, res.trip.name, round(res.baseline.transport, 2), round(res.best.transport, 2),
                round(res.transport_savings, 2), round(res.transport_savings_pct * 100, 2),
                sk.gw_in, sk.gw_out, sk.pickup or "", sk.dropoff or "", sk.mode, sk.start.isoformat(),
                round(res.best.transit_hours - res.baseline.transit_hours, 2),
                res.trip.actual_transport_usd or "", "",
            ])
