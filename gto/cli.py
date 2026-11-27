"""gto — phase 0 command line.

  gto plan TRIP.json [--fares SPEC] [--json] [--save-fares OUT.json]
  gto replay DIR_OR_FILES... [--fares SPEC] [--csv OUT.csv]
  gto gateways TRIP.json [--fares SPEC]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .airports import load_airports
from .fares import MemoFares, build_fare_source, is_synthetic
from .models import load_trip
from .optimizer import search
from .replay import format_replay, replay, trip_files, write_csv
from .report import format_result, result_to_dict


def _fares(args, airports):
    src = build_fare_source(args.fares, airports, token=os.environ.get("TRAVELPAYOUTS_TOKEN"))
    return MemoFares(src)


def cmd_plan(args) -> int:
    airports = load_airports(args.airports)
    fares = _fares(args, airports)
    res = search(load_trip(args.trip), airports, fares)
    synthetic = is_synthetic(fares)
    if args.json:
        print(json.dumps(result_to_dict(res, synthetic), indent=2))
    else:
        print(format_result(res, synthetic))
    if args.save_fares:
        n = fares.dump_fixture(args.save_fares)
        print(f"\nSaved {n} fares to {args.save_fares}", file=sys.stderr)
    return 0 if res.plans else 2


def cmd_replay(args) -> int:
    airports = load_airports(args.airports)
    fares = _fares(args, airports)
    default = Path(__file__).resolve().parent.parent / "trips" / "examples"
    files = trip_files(args.paths or [str(default)])
    if not files:
        print("no trip files found", file=sys.stderr)
        return 1
    rows = replay(files, airports, fares)
    print(format_replay(rows, is_synthetic(fares)))
    if args.csv:
        write_csv(rows, args.csv)
        print(f"\nWrote {args.csv}", file=sys.stderr)
    if args.save_fares:
        n = fares.dump_fixture(args.save_fares)
        print(f"Saved {n} fares to {args.save_fares}", file=sys.stderr)
    return 0


def cmd_gateways(args) -> int:
    from .optimizer import Evaluator, candidate_gateways, drive_model_for, nearest_airport

    airports = load_airports(args.airports)
    trip = load_trip(args.trip)
    drive = drive_model_for(trip, airports)
    ev = Evaluator(trip, _fares(args, airports), drive)
    base = nearest_airport(trip, airports, drive)
    print(f"Nearest airport (baseline): {base}")
    print(f"{'Code':<6} {'Drive h':>8}  {'Nearest place':<28} {'Score':>10}")
    for g in candidate_gateways(trip, airports, ev, base):
        score = "no fares" if g.score is None else f"{g.score:,.0f}"
        print(f"{g.code:<6} {g.drive_h:>8.1f}  {g.nearest_place:<28} {score:>10}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gto", description="Group trip cost optimizer — phase 0 prototype")
    ap.add_argument("--airports", help="airports CSV (default: bundled)")
    ap.add_argument(
        "--fares",
        default="synthetic",
        help="fare source: synthetic | fixture:PATH | travelpayouts, comma-chained (default: synthetic)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="optimize one trip")
    p.add_argument("trip")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save-fares", metavar="OUT.json", help="write every fare looked up as a fixture")
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("replay", help="replay many trips and check Gate 1")
    r.add_argument("paths", nargs="*", help="trip files or folders (default: the bundled sample trips)")
    r.add_argument("--csv", metavar="OUT.csv")
    r.add_argument("--save-fares", metavar="OUT.json")
    r.set_defaults(fn=cmd_replay)

    g = sub.add_parser("gateways", help="list candidate gateway airports for a trip")
    g.add_argument("trip")
    g.set_defaults(fn=cmd_gateways)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
