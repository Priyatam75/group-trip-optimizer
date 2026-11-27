"""Text and JSON output for search results."""

from __future__ import annotations

from datetime import date

from .optimizer import Plan, SearchResult

LABELS = {
    "lowest_total": "Lowest total",
    "least_time": "Least travel time",
    "fairest": "Fairest split",
    "baseline": "Baseline (naive)",
}


def usd(x: float) -> str:
    return f"${x:,.0f}"


def signed_usd(x: float) -> str:
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.0f}"


def _d(day: date) -> str:
    return day.strftime("%a %b %d")


MODES = {"single": "", "detour": "split, one car picks up", "second_car": "split, two car groups"}


def airports_label(sk) -> str:
    """Short label like 'SLC+JAC→BZN' for the airports a plan uses."""
    a = sk.gw_in + (f"+{sk.pickup}" if sk.pickup else "")
    d = sk.gw_out + (f"+{sk.dropoff}" if sk.dropoff else "")
    return f"{a}→{d}"


def _track_line(kind: str, tr) -> str:
    cars = f", {tr.cars} car{'s' if tr.cars != 1 else ''}" if tr.cars else ", no car (joins the others)"
    return f"   {kind} {tr.airport}: {', '.join(tr.members)} — drive {' → '.join(tr.path)} {tr.hours:.1f}h{cars}"


def _plan_block(plan: Plan, baseline: Plan | None, idx: str) -> list[str]:
    sk = plan.skeleton
    kind = "open-jaw" if sk.open_jaw else "round trip"
    if sk.split:
        kind = MODES[sk.mode]
    route = " → ".join(
        [sk.gw_in] + [f"{name} ({n}n)" for name, n in zip(plan.route, sk.nights)] + [sk.gw_out]
    )
    lines = [
        f"{idx} {LABELS.get(plan.label, plan.label)}: {airports_label(sk)} ({kind}), "
        f"{_d(sk.start)} → {_d(sk.end)}",
        f"   Route:     {route}",
    ]
    if sk.split:
        lines += [_track_line("Arrive", tr) for tr in plan.arrivals]
        lines += [_track_line("Leave ", tr) for tr in plan.departures]
    lines += [
        f"   Drive/day: " + ", ".join(f"{h:.1f}h" for h in plan.drive_hours_by_day),
        f"   Group:     {usd(plan.money_total)} total · {usd(plan.per_person)}/person avg · "
        f"{plan.transit_hours:.0f} transit person-hours",
        f"   Breakdown: flights {usd(plan.flights)} · car {usd(plan.car_rental)} ({plan.n_cars}) · "
        f"drop fee {usd(plan.drop_fee)} · fuel {usd(plan.fuel)} · lodging {usd(plan.lodging)}"
        + (f" · drive penalty {usd(plan.penalty)}" if plan.penalty else ""),
    ]
    if baseline is not None and plan is not baseline:
        dt = plan.transport - baseline.transport
        pct = dt / baseline.transport * 100 if baseline.transport else 0.0
        dh = plan.transit_hours - baseline.transit_hours
        lines.append(
            f"   vs baseline: transport {signed_usd(dt)} ({pct:+.1f}%) · "
            f"total {signed_usd(plan.money_total - baseline.money_total)} · {dh:+.0f} person-hours"
        )
    lines.append(f"   {'Member':<12} {'Outbound':<22} {'Return':<22} {'Share':>8} {'Hours':>6}")
    for m in plan.members:
        if m.round_trip:
            out = f"{m.out.origin}⇄{m.out.dest} RT {usd(m.flights)}"
            ret = "(same ticket)"
        else:
            out = f"{m.out.origin}→{m.out.dest} {usd(m.out.price)}"
            ret = f"{m.ret.origin}→{m.ret.dest} {usd(m.ret.price)}"
        flag = "  OVER BUDGET" if m.over_budget else ""
        lines.append(f"   {m.name:<12} {out:<22} {ret:<22} {usd(m.share):>8} {m.hours:>6.1f}{flag}")
    lines.append(f"   Split gap (max − min share): {usd(plan.share_gap)}")
    return lines


def format_result(res: SearchResult, synthetic: bool) -> str:
    trip = res.trip
    lines = [
        f"Trip: {trip.name} · {len(trip.travelers)} travelers · {trip.length_days} days · "
        f"start window {trip.earliest_start} … {trip.latest_start}",
        f"Fares: {res.fare_source}"
        + ("   ⚠ SYNTHETIC — NOT REAL PRICES, for pipeline testing only" if synthetic else ""),
        f"Search: {res.stats.get('evaluated', 0):,} skeletons, {res.feasible_count:,} feasible, "
        f"{res.fare_lookups:,} fare lookups, {res.elapsed_s:.2f}s",
    ]
    rejected = {k: v for k, v in res.stats.items() if k.startswith("rejected")}
    if rejected:
        lines.append("        " + " · ".join(f"{k.removeprefix('rejected: ')} {v:,}" for k, v in rejected.items()))

    gw = []
    for g in res.gateways:
        if g.code in res.kept_gateways:
            gw.append(f"{g.code} ({g.drive_h:.1f}h to {g.nearest_place})")
    dropped = [g.code for g in res.gateways if g.code not in res.kept_gateways]
    lines.append("Gateways: " + ", ".join(gw))
    if dropped:
        lines.append("  not kept: " + ", ".join(dropped))
    lines.append("")

    if res.baseline is None:
        lines.append(f"Baseline: no fares found for everyone round-tripping {res.baseline_airport}")
    else:
        lines += _plan_block(res.baseline, None, "  ")
        for w in res.baseline_warnings:
            lines.append(f"   ⚠ For comparison only: {w}")
    lines.append("")

    if not res.plans:
        lines.append("No feasible plan:")
        lines += [f"  - {r}" for r in res.why_no_plan]
        return "\n".join(lines)

    for i, plan in enumerate(res.plans, 1):
        lines += _plan_block(plan, res.baseline, f"#{i}")
        lines.append("")

    pct = res.transport_savings_pct
    if pct is not None:
        lines.append(
            f"Savings (lowest-total plan vs baseline): {usd(res.transport_savings)} "
            f"= {pct * 100:.1f}% of group transport cost"
        )
    if trip.actual_transport_usd and res.best:
        diff = trip.actual_transport_usd - res.best.transport
        lines.append(
            f"vs what the group actually paid ({usd(trip.actual_transport_usd)}): "
            f"{usd(diff)} = {diff / trip.actual_transport_usd * 100:.1f}%"
        )
    return "\n".join(lines)


def plan_to_dict(plan: Plan) -> dict:
    sk = plan.skeleton
    def track(tr):
        return {"airport": tr.airport, "members": tr.members, "path": tr.path,
                "drive_h": round(tr.hours, 2), "cars": tr.cars}

    return {
        "label": plan.label,
        "gateway_in": sk.gw_in,
        "gateway_out": sk.gw_out,
        "pickup": sk.pickup,
        "dropoff": sk.dropoff,
        "mode": sk.mode,
        "airports_label": airports_label(sk),
        "n_cars": plan.n_cars,
        "arrivals": [track(t) for t in plan.arrivals],
        "departures": [track(t) for t in plan.departures],
        "start_date": sk.start.isoformat(),
        "end_date": sk.end.isoformat(),
        "route": [{"place": n, "nights": k} for n, k in zip(plan.route, sk.nights)],
        "drive_hours_by_day": [round(h, 2) for h in plan.drive_hours_by_day],
        "totals": {
            "money": round(plan.money_total, 2),
            "transport": round(plan.transport, 2),
            "flights": round(plan.flights, 2),
            "car_rental": round(plan.car_rental, 2),
            "drop_fee": round(plan.drop_fee, 2),
            "fuel": round(plan.fuel, 2),
            "lodging": round(plan.lodging, 2),
            "penalty": round(plan.penalty, 2),
            "objective": round(plan.objective, 2),
        },
        "transit_hours": round(plan.transit_hours, 2),
        "share_gap": round(plan.share_gap, 2),
        "per_member": [
            {
                "name": m.name,
                "out": {"from": m.out.origin, "to": m.out.dest, "price": m.out.price, "source": m.out.source},
                "ret": {"from": m.ret.origin, "to": m.ret.dest, "price": m.ret.price, "source": m.ret.source},
                "round_trip": m.round_trip,
                "flights": round(m.flights, 2),
                "share": round(m.share, 2),
                "hours": round(m.hours, 2),
                "over_budget": m.over_budget,
            }
            for m in plan.members
        ],
    }


def result_to_dict(res: SearchResult, synthetic: bool) -> dict:
    return {
        "trip": res.trip.name,
        "fare_source": res.fare_source,
        "synthetic_fares": synthetic,
        "stats": {**res.stats, "fare_lookups": res.fare_lookups, "elapsed_s": round(res.elapsed_s, 3)},
        "gateways": res.kept_gateways,
        "baseline": plan_to_dict(res.baseline) if res.baseline else None,
        "plans": [plan_to_dict(p) for p in res.plans],
        "transport_savings_usd": None if res.transport_savings is None else round(res.transport_savings, 2),
        "transport_savings_pct": None
        if res.transport_savings_pct is None
        else round(res.transport_savings_pct * 100, 2),
        "actual_transport_usd": res.trip.actual_transport_usd,
        "why_no_plan": res.why_no_plan,
        "baseline_warnings": res.baseline_warnings,
    }
