"""Plain-language explanations: why no plan was found, and what's wrong with the baseline.

Everything here reads numbers the search already produced; it never changes a result.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import permutations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .optimizer import Evaluator, Plan
    from .models import Trip


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def best_place_order(trip: "Trip", drive) -> tuple[tuple[int, ...], float, tuple[str, str] | None]:
    """The place order whose longest drive between places is shortest (airports excluded)."""
    k = len(trip.places)
    if k == 1:
        return (0,), 0.0, None
    orders = [tuple(range(k))] if trip.ordered else permutations(range(k))
    best: tuple[tuple[int, ...], float, tuple[str, str] | None] | None = None
    for order in orders:
        names = [trip.places[i].name for i in order]
        legs = [(drive.hours(a, b), (a, b)) for a, b in zip(names, names[1:])]
        h, pair = max(legs)
        if best is None or h < best[1]:
            best = (order, h, pair)
    return best


def why_no_plan(trip: "Trip", ev: "Evaluator", stats: dict, arrivals: list[str], departures: list[str]) -> list[str]:
    p = trip.params
    limit = p.max_drive_h_per_day + 1
    reasons: list[str] = []

    if not arrivals or not departures:
        return [
            f"No airport with flights for everyone is within {p.max_gateway_drive_h:g}h of any place. "
            "Raise 'Max drive from airport', or check everyone's home airports."
        ]

    # 1. Traveler date limits that no start date can satisfy.
    starts = trip.start_dates()
    span = timedelta(days=trip.length_days - 1)
    for t in trip.travelers:
        fits = [
            s for s in starts
            if (not t.earliest_depart or s >= t.earliest_depart) and (not t.latest_return or s + span <= t.latest_return)
        ]
        if not fits:
            parts = []
            if t.earliest_depart:
                parts.append(f"can't leave before {t.earliest_depart:%b %d %Y}")
            if t.latest_return:
                parts.append(f"must be back by {t.latest_return:%b %d %Y}")
            reasons.append(
                f"{t.name} {' and '.join(parts)}, but a {trip.length_days}-day trip starting between "
                f"{trip.earliest_start:%b %d} and {trip.latest_start:%b %d %Y} doesn't fit. "
                "Fix or clear those dates."
            )

    # 2. Drives too long to do in a day.
    order, longest, pair = best_place_order(trip, ev.drive)
    if longest > limit and pair:
        how = "In the listed order" if trip.ordered else "Even in the best order"
        reasons.append(
            f"{how}, one day's drive is {longest:.1f}h ({pair[0]} → {pair[1]}); the limit is {limit:g}h a day. "
            "Drop or swap a place, add a stop in between, or raise 'Max drive hours / day'."
        )
    else:
        names = [trip.places[i].name for i in order]
        first, last = names[0], names[-1]
        # With the best place order, can any airport reach the ends within a day?
        ends = {first, last} if trip.ordered else {pl.name for pl in trip.places}
        in_h = min(ev.drive.hours(g, pl) for g in arrivals for pl in ends)
        out_h = min(ev.drive.hours(pl, g) for g in departures for pl in ends)
        if max(in_h, out_h) > limit:
            reasons.append(
                f"The nearest airport with flights is {max(in_h, out_h):.1f}h from where the trip starts or ends; "
                f"the limit is {limit:g}h a day. Raise 'Max drive hours / day' or 'Max drive from airport'."
            )

    # 3. Budgets below the cheapest valid plan for that person.
    if p.enforce_budgets:
        for ti, t in enumerate(trip.travelers):
            m = ev.min_share.get(ti)
            if t.budget is not None and m is not None and m > t.budget + 0.005:
                reasons.append(
                    f"{t.name}'s budget is {_usd(t.budget)}, but the cheapest plan found costs {t.name} "
                    f"about {_usd(m)} (flights + their share of car and lodging). Raise or clear the budget, "
                    "or untick 'Rule out plans that break someone's budget' to see plans flagged."
                )

    # 4. Travelers with no flights to any airport considered.
    for t in trip.travelers:
        has_in = any(ev.flight(t, g, s, True) for g in arrivals for s in starts)
        has_out = any(ev.flight(t, g, s + span, False) for g in departures for s in starts)
        if not (has_in and has_out):
            reasons.append(
                f"No flights found for {t.name} from {', '.join(t.origins)} to any of the airports considered "
                f"({', '.join(sorted(set(arrivals) | set(departures)))}). Add another home airport."
            )

    if not reasons:
        rejected = {k.removeprefix("rejected: "): v for k, v in stats.items() if k.startswith("rejected")}
        detail = ", ".join(f"{k} {v:,}" for k, v in sorted(rejected.items(), key=lambda kv: -kv[1]))
        reasons.append(f"Every combination broke at least one rule ({detail}). Try loosening budgets or drive limits.")
    return reasons


def baseline_warnings(trip: "Trip", plan: "Plan | None") -> list[str]:
    """Rules the naive plan breaks — it's shown for comparison even when it isn't allowed."""
    if plan is None:
        return []
    p = trip.params
    out: list[str] = []
    limit = p.max_drive_h_per_day + 1
    worst = max(plan.drive_hours_by_day)
    if worst > limit:
        out.append(f"Has a {worst:.1f}h driving day (limit {limit:g}h).")
    over = [m.name for m in plan.members if m.over_budget]
    if over:
        out.append(f"Over budget for {', '.join(over)}.")
    sk = plan.skeleton
    late = [t.name for t in trip.travelers if t.earliest_depart and sk.start < t.earliest_depart]
    early = [t.name for t in trip.travelers if t.latest_return and sk.end > t.latest_return]
    if late or early:
        out.append(f"Breaks travel dates for {', '.join(sorted(set(late + early)))}.")
    return out
