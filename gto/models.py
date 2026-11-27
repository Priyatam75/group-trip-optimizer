"""Trip input model and loader (HLD section 7.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lng: float
    min_nights: int = 1
    lodging_per_night: float = 180.0  # USD per room per night


@dataclass(frozen=True)
class Traveler:
    name: str
    origins: tuple[str, ...]
    budget: float | None = None
    earliest_depart: date | None = None
    latest_return: date | None = None


@dataclass
class Params:
    lambda_per_hour: float = 25.0  # value of a transit person-hour (λ)
    max_drive_h_per_day: float = 5.0  # D
    max_gateway_drive_h: float = 6.0
    max_gateways: int = 6
    car_daily_rate: float = 70.0
    seats_per_car: int = 5
    drop_fee_default: float = 250.0  # one-way drop fee when no table entry
    fuel_price_per_gal: float = 3.80
    mpg: float = 25.0
    people_per_room: int = 2
    drive_penalty_per_h: float = 50.0  # per person, per hour over D
    plan_band: float = 0.15  # "within 15% of the lowest total"
    enforce_budgets: bool = True  # False = keep over-budget plans, flagged (HLD 7.4)
    split_gateways: bool = True  # sub-groups may use a second airport (HLD 7.6)
    round_trip_fares: bool = True  # use a round-trip ticket when cheaper (HLD 8.4)
    # Travel-time weighting: an hour behind the wheel costs more than an hour on a plane.
    drive_hour_weight: float = 1.5  # driving hours count this many times λ
    solo_drive_penalty_per_h: float = 40.0  # per hour someone drives a car alone
    # Long drives between places: split over days with a night on the road.
    overnight_stops: bool = True
    road_stop_lodging: float = 150.0  # USD per room for a night on the road
    # The cheapest nights split is always "minimums + spare nights at the cheapest
    # place"; set True to also list every other split (slower, same best plans).
    all_night_splits: bool = False


@dataclass
class Trip:
    name: str
    places: list[Place]
    ordered: bool
    length_days: int
    earliest_start: date
    latest_start: date
    travelers: list[Traveler]
    params: Params = field(default_factory=Params)
    drive_overrides: dict[tuple[str, str], float] = field(default_factory=dict)
    drop_fees: dict[tuple[str, str], float] = field(default_factory=dict)
    actual_transport_usd: float | None = None  # what the group really paid, if known
    notes: str = ""

    @property
    def nights(self) -> int:
        return self.length_days - 1

    def start_dates(self) -> list[date]:
        days = (self.latest_start - self.earliest_start).days
        return [self.earliest_start + timedelta(days=i) for i in range(days + 1)]

    def drop_fee(self, pickup: str, dropoff: str) -> float:
        if pickup == dropoff:
            return 0.0
        return self.drop_fees.get((pickup, dropoff), self.params.drop_fee_default)


def _date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _pair_map(raw: dict[str, float] | None) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for key, val in (raw or {}).items():
        a, sep, b = key.partition("|")
        if not sep:
            raise ValueError(f"pair key {key!r} must look like 'A|B'")
        out[(a.strip(), b.strip())] = float(val)
    return out


def trip_from_dict(d: dict) -> Trip:
    places = [
        Place(
            name=p["name"],
            lat=float(p["lat"]),
            lng=float(p["lng"]),
            min_nights=int(p.get("min_nights", 1)),
            lodging_per_night=float(p.get("lodging_per_night", 180.0)),
        )
        for p in d["places"]
    ]
    travelers = [
        Traveler(
            name=t["name"],
            origins=tuple(o.upper() for o in t["origins"]),
            budget=t.get("budget"),
            earliest_depart=_date(t.get("earliest_depart")),
            latest_return=_date(t.get("latest_return")),
        )
        for t in d["travelers"]
    ]

    win = d["date_window"]
    if "start" in win:
        start = date.fromisoformat(win["start"])
        flex = int(win.get("flex_days", 2))
        earliest, latest = start - timedelta(days=flex), start + timedelta(days=flex)
    else:
        earliest = date.fromisoformat(win["earliest_start"])
        latest = date.fromisoformat(win["latest_start"])

    known = {f.name for f in fields(Params)}
    raw_params = d.get("params", {})
    unknown = set(raw_params) - known
    if unknown:
        raise ValueError(f"unknown params: {sorted(unknown)}")

    trip = Trip(
        name=d["name"],
        places=places,
        ordered=bool(d.get("ordered", False)),
        length_days=int(d["length_days"]),
        earliest_start=earliest,
        latest_start=latest,
        travelers=travelers,
        params=Params(**raw_params),
        drive_overrides=_pair_map(d.get("drive_overrides")),
        drop_fees=_pair_map(d.get("drop_fees")),
        actual_transport_usd=d.get("actual_transport_usd"),
        notes=d.get("notes", ""),
    )
    validate(trip)
    return trip


def validate(trip: Trip) -> None:
    if not trip.places:
        raise ValueError("trip needs at least one place")
    if not trip.travelers:
        raise ValueError("trip needs at least one traveler")
    if trip.length_days < 2:
        raise ValueError("length_days must be at least 2")
    if trip.latest_start < trip.earliest_start:
        raise ValueError("date window is empty")
    names = [p.name for p in trip.places]
    if len(set(names)) != len(names):
        raise ValueError("place names must be unique")
    if any(p.min_nights < 1 for p in trip.places):
        raise ValueError("min_nights must be at least 1")
    if sum(p.min_nights for p in trip.places) > trip.nights:
        raise ValueError(
            f"places need {sum(p.min_nights for p in trip.places)} nights "
            f"but a {trip.length_days}-day trip has {trip.nights}"
        )
    for t in trip.travelers:
        if not t.origins:
            raise ValueError(f"traveler {t.name} has no origin airports")


def load_trip(path: str | Path) -> Trip:
    return trip_from_dict(json.loads(Path(path).read_text()))
