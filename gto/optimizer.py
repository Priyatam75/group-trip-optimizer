"""Skeleton search, cost function and plan selection (HLD sections 7.2–7.7).

A skeleton fixes what the group shares: the car pickup airport (gw_in), the car
drop-off airport (gw_out), optionally a second arrival / departure airport for a
sub-group (split gateways, HLD 7.6), the ground mode, place order, nights per
place and dates. Given a skeleton, each traveler independently picks their
cheapest tickets (one-ways or a round trip, from any home airport) and, for a
split skeleton, which airport to use.

Ground modes:
- single:     one fleet, everyone flies into gw_in and out of gw_out.
- detour:     one fleet, picked up at gw_in; it detours via `pickup` to collect
              the second sub-group on day 1 and via `dropoff` on the last day.
- second_car: two fleets. Sub-group A drives gw_in → route → gw_out, sub-group B
              drives (pickup or gw_in) → route → (dropoff or gw_out). Each fleet
              pays its own rental, drop fee and fuel.

Phase 0 simplifications versus the HLD:
- Cached fares only, so no live re-pricing step and no arrival-time bins
  (cached fares don't carry reliable times). Arrival spread is not checked.
- Split sub-groups meet at the first place and part at the last place.
- In second_car mode, travelers pick a fleet by their own fare + time; the
  number of cars per fleet follows from that choice rather than being
  co-optimized with it.
- Fairness rule is the default "own flights, shared ground" (7.8).
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cached_property
from itertools import permutations
from typing import Iterator

from .airports import Airport
from .diagnose import baseline_warnings, why_no_plan
from .fares import Fare, FareSource, MemoFares, round_trip_of
from .geo import DriveModel
from .models import Trip, Traveler

KM_PER_MILE = 1.609344
SINGLE, DETOUR, SECOND_CAR = "single", "detour", "second_car"


@dataclass(frozen=True)
class Skeleton:
    gw_in: str  # where the (first) car is picked up
    gw_out: str  # where the (first) car is dropped off
    order: tuple[int, ...]  # indexes into trip.places
    nights: tuple[int, ...]  # nights per place, aligned with `order`
    start: date
    end: date
    pickup: str | None = None  # second arrival airport for a sub-group
    dropoff: str | None = None  # second departure airport for a sub-group
    mode: str = SINGLE
    overnight: bool = False  # long drives between places get nights on the road

    @property
    def gateway_pair(self) -> tuple[str, str]:
        return (self.gw_in, self.gw_out)

    @property
    def gateways_key(self) -> tuple[str, str | None, str, str | None]:
        """What the group sees as 'which airports' — used to keep the final three distinct."""
        return (self.gw_in, self.pickup, self.gw_out, self.dropoff)

    @property
    def open_jaw(self) -> bool:
        return self.gw_in != self.gw_out

    @property
    def split(self) -> bool:
        return self.pickup is not None or self.dropoff is not None

    @property
    def b_in(self) -> str:
        return self.pickup or self.gw_in

    @property
    def b_out(self) -> str:
        return self.dropoff or self.gw_out


@dataclass
class MemberCost:
    name: str
    out: Fare
    ret: Fare
    ground_share: float
    hours: float
    budget: float | None
    round_trip: bool = False  # out + ret are one round-trip ticket
    drive_h: float = 0.0  # the driving part of `hours`

    @property
    def flights(self) -> float:
        return self.out.price + self.ret.price

    @property
    def share(self) -> float:
        return self.flights + self.ground_share

    @property
    def over_budget(self) -> bool:
        return self.budget is not None and self.share > self.budget + 0.005


@dataclass
class Track:
    """One sub-group's arrival or departure: which airport, who, and the drive."""

    airport: str
    members: list[str]
    path: list[str]  # airport → … → first place, or last place → … → airport
    hours: float
    cars: int  # cars picked up / dropped here (0 = rides in another track's car)


@dataclass
class Leg:
    """A drive between two places. Long ones take several days with nights on the road."""

    start: str
    end: str
    hours: float
    days: int = 1
    stops: list[str] = field(default_factory=list)  # where each night on the road is, e.g. "Idaho Falls, ID"


@dataclass
class Plan:
    skeleton: Skeleton
    route: list[str]
    members: list[MemberCost]
    n_cars: int
    car_rental: float
    drop_fee: float
    fuel: float
    lodging: float
    drive_hours_by_day: list[float]
    penalty: float
    lambda_per_hour: float
    label: str = ""
    arrivals: list[Track] = field(default_factory=list)
    departures: list[Track] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)  # drives between places, in order
    road_lodging: float = 0.0  # part of `lodging` spent on nights on the road
    solo_drives: list[str] = field(default_factory=list)  # e.g. "Rahul drives 9.5h alone (Bozeman → SLC)"
    drive_hour_weight: float = 1.0

    @cached_property
    def flights(self) -> float:
        return sum(m.flights for m in self.members)

    @property
    def ground(self) -> float:
        return self.car_rental + self.drop_fee + self.fuel

    @property
    def transport(self) -> float:
        """Flights + car + drop fee + fuel — the HLD's 'group transport cost'."""
        return self.flights + self.ground

    @property
    def money_total(self) -> float:
        return self.transport + self.lodging

    @cached_property
    def transit_hours(self) -> float:
        return sum(m.hours for m in self.members)

    @cached_property
    def weighted_hours(self) -> float:
        """Travel hours with driving counted `drive_hour_weight` times (used for ranking only)."""
        return sum(m.hours + (self.drive_hour_weight - 1) * m.drive_h for m in self.members)

    @cached_property
    def objective(self) -> float:
        """Cost(k) from HLD 7.3, with driving hours weighted above flying hours."""
        return self.money_total + self.lambda_per_hour * self.weighted_hours + self.penalty

    @property
    def share_gap(self) -> float:
        shares = [m.share for m in self.members]
        return max(shares) - min(shares)

    @property
    def per_person(self) -> float:
        return self.money_total / len(self.members)


@dataclass
class GatewayCandidate:
    code: str
    nearest_place: str
    drive_h: float
    score: float | None  # None = no direction has fares for every traveler
    arrival_ok: bool = True
    departure_ok: bool = True


@dataclass(frozen=True)
class _Option:
    """One way a traveler can do the trip under a skeleton."""

    arr: str
    dep: str
    drive_h: float  # this traveler's arrival + departure driving
    fleet: int  # 0 = fleet A, 1 = fleet B (second_car only)


@dataclass(frozen=True)
class _Tickets:
    out: Fare
    ret: Fare
    round_trip: bool
    score: float  # price + λ × flight hours


def night_allocations(total: int, mins: list[int]) -> Iterator[tuple[int, ...]]:
    """All ways to split `total` nights across places, each getting at least its minimum."""
    spare = total - sum(mins)
    if spare < 0:
        return
    k = len(mins)

    def rec(i: int, left: int) -> Iterator[tuple[int, ...]]:
        if i == k - 1:
            yield (left,)
            return
        for extra in range(left + 1):
            for rest in rec(i + 1, left - extra):
                yield (extra, *rest)

    for extras in rec(0, spare):
        yield tuple(m + e for m, e in zip(mins, extras))


class Evaluator:
    def __init__(self, trip: Trip, fares: FareSource, drive: DriveModel,
                 airports: dict[str, Airport] | None = None):
        self.trip = trip
        self.fares = fares
        self.drive = drive
        self.airports = airports or {}
        self.stats: Counter[str] = Counter()
        self._tickets: dict[tuple, _Tickets | None] = {}
        self._cores: dict[tuple, object] = {}
        self._legs: dict[tuple, tuple[list[Leg], int]] = {}
        # Cheapest share each traveler reached in any otherwise-valid plan (explains budget failures).
        self.min_share: dict[int, float] = {}

    def flight(self, t: Traveler, gateway: str, day: date, outbound: bool) -> Fare | None:
        """Cheapest one-way for a traveler from any home airport (used to score gateways)."""
        best = None
        for o in t.origins:
            f = self.fares.cheapest(o, gateway, day) if outbound else self.fares.cheapest(gateway, o, day)
            if f is not None and (best is None or f.price < best.price):
                best = f
        return best

    def tickets(self, ti: int, arr: str, dep: str, out_day: date, ret_day: date) -> _Tickets | None:
        """Best tickets for traveler `ti`: into `arr`, home from `dep`, by price + λ × flight hours."""
        key = (ti, arr, dep, out_day, ret_day)
        if key in self._tickets:
            return self._tickets[key]
        t = self.trip.travelers[ti]
        lam = self.trip.params.lambda_per_hour

        def best_one_way(outbound: bool) -> Fare | None:
            best, best_s = None, math.inf
            for o in t.origins:
                f = self.fares.cheapest(o, arr, out_day) if outbound else self.fares.cheapest(dep, o, ret_day)
                if f is not None and f.price + lam * f.duration_h < best_s:
                    best, best_s = f, f.price + lam * f.duration_h
            return best

        cands: list[_Tickets] = []
        out, ret = best_one_way(True), best_one_way(False)
        if out is not None and ret is not None:
            cands.append(_Tickets(out, ret, False, out.price + ret.price + lam * (out.duration_h + ret.duration_h)))
        if self.trip.params.round_trip_fares and arr == dep:
            for o in t.origins:
                rt = round_trip_of(self.fares, o, arr, out_day, ret_day)
                if rt is None:
                    continue
                half = rt.price / 2
                f_out = Fare(o, arr, out_day, half, rt.out_duration_h, rt.source, duration_estimated=rt.duration_estimated)
                f_ret = Fare(arr, o, ret_day, rt.price - half, rt.ret_duration_h, rt.source,
                             duration_estimated=rt.duration_estimated)
                cands.append(_Tickets(f_out, f_ret, True, rt.price + lam * (rt.out_duration_h + rt.ret_duration_h)))
        best = min(cands, key=lambda c: c.score) if cands else None
        self._tickets[key] = best
        return best

    def legs(self, order: tuple[int, ...], overnight: bool) -> tuple[list[Leg], int]:
        """Drives between places for an order, and how many nights they spend on the road.

        A leg longer than the daily hard limit (D + 1 h) is split evenly over
        ceil(h / D) days when overnight stops are on; otherwise it stays one day
        (and the drive-limit check rejects it).
        """
        key = (order, overnight)
        if key in self._legs:
            return self._legs[key]
        p = self.trip.params
        names = [self.trip.places[i].name for i in order]
        legs, road_nights = [], 0
        for a, b in zip(names, names[1:]):
            h = self.drive.hours(a, b)
            days = math.ceil(h / p.max_drive_h_per_day) if overnight and h > p.max_drive_h_per_day + 1 else 1
            legs.append(Leg(a, b, h, days, [self._near(a, b, j / days) for j in range(1, days)]))
            road_nights += days - 1
        self._legs[key] = (legs, road_nights)
        return legs, road_nights

    def _near(self, a: str, b: str, frac: float) -> str:
        """A town name for a point `frac` of the way from a to b (nearest airport's city)."""
        (lat1, lng1), (lat2, lng2) = self.drive.points[a], self.drive.points[b]
        lat, lng = lat1 + (lat2 - lat1) * frac, lng1 + (lng2 - lng1) * frac
        if not self.airports:
            return f"{lat:.2f}, {lng:.2f}"
        ap = min(self.airports.values(), key=lambda x: (x.lat - lat) ** 2 + ((x.lng - lng) * math.cos(math.radians(lat))) ** 2)
        return f"{ap.city or ap.name}, {ap.state}".strip(", ")

    def _path_h(self, path: list[str]) -> float:
        return sum(self.drive.hours(a, b) for a, b in zip(path, path[1:]))

    def _path_km(self, path: list[str]) -> float:
        return sum(self.drive.km(a, b) for a, b in zip(path, path[1:]))

    def evaluate(self, sk: Skeleton, strict: bool = True) -> Plan | None:
        """Price one skeleton. strict=False skips hard constraints (used for the naive baseline)."""
        trip, p = self.trip, self.trip.params
        n = len(trip.travelers)
        lam = p.lambda_per_hour
        self.stats["evaluated"] += 1

        for t in trip.travelers:
            if strict and t.earliest_depart and sk.start < t.earliest_depart:
                self.stats["rejected: traveler dates"] += 1
                return None
            if strict and t.latest_return and sk.end > t.latest_return:
                self.stats["rejected: traveler dates"] += 1
                return None

        core = self._core(sk, strict)
        if isinstance(core, str):
            self.stats[core] += 1
            return None
        names, first, last, paths_in, paths_out, drive, penalty, priced, ground, legs, fleet_hours, seg_in, seg_out = core
        route = [trip.places[i] for i in sk.order]

        rooms = math.ceil(n / p.people_per_room)
        road_nights = sum(lg.days - 1 for lg in legs)
        road_lodging = rooms * road_nights * p.road_stop_lodging
        lodging = rooms * sum(pl.lodging_per_night * nn for pl, nn in zip(route, sk.nights)) + road_lodging

        choice = [opts[0] for opts in priced]
        cars, rental, drop, fuel = ground(choice)
        if strict and p.enforce_budgets:
            # Someone over budget on their best option may fit on another (e.g. the other airport).
            for _ in range(2):  # second pass lets fleet sizes settle in second_car mode
                share = (rental + drop + fuel + lodging) / n
                for ti, t in enumerate(trip.travelers):
                    if t.budget is None or choice[ti][2].out.price + choice[ti][2].ret.price + share <= t.budget:
                        continue
                    fits = [o for o in priced[ti] if o[2].out.price + o[2].ret.price + share <= t.budget]
                    if fits:
                        choice[ti] = fits[0]
                cars, rental, drop, fuel = ground(choice)

        # A split skeleton must actually split; otherwise a simpler skeleton covers it.
        if sk.mode == SECOND_CAR:
            if not all(any(c[1].fleet == f for c in choice) for f in (0, 1)):
                self.stats["rejected: split unused"] += 1
                return None
        elif sk.split:
            used_in = {c[1].arr for c in choice}
            used_out = {c[1].dep for c in choice}
            if (sk.pickup and used_in != {sk.gw_in, sk.pickup}) or (sk.dropoff and used_out != {sk.gw_out, sk.dropoff}):
                self.stats["rejected: split unused"] += 1
                return None

        ground_share = (rental + drop + fuel + lodging) / n
        if strict:
            for ti, (_, _, tk) in enumerate(choice):
                share = tk.out.price + tk.ret.price + ground_share
                if share < self.min_share.get(ti, float("inf")):
                    self.min_share[ti] = share
        members = []
        for t, (_, opt, tk) in zip(trip.travelers, choice):
            m = MemberCost(
                name=t.name,
                out=tk.out,
                ret=tk.ret,
                ground_share=ground_share,
                hours=tk.out.duration_h + tk.ret.duration_h + opt.drive_h,
                budget=t.budget,
                round_trip=tk.round_trip,
                drive_h=opt.drive_h,
            )
            if strict and p.enforce_budgets and m.over_budget:
                self.stats["rejected: over budget"] += 1
                return None
            members.append(m)

        solo_h, solo = self._solo_driving(sk, choice, cars, fleet_hours, seg_in, seg_out)
        arrivals, departures = self._tracks(sk, choice, members, paths_in, paths_out, cars, first, last)
        self.stats["feasible"] += 1
        return Plan(
            sk, names, members, sum(cars), rental, drop, fuel, lodging, drive,
            penalty + solo_h * p.solo_drive_penalty_per_h, lam,
            arrivals=arrivals, departures=departures, legs=legs, road_lodging=road_lodging,
            solo_drives=solo, drive_hour_weight=p.drive_hour_weight,
        )

    def _solo_driving(self, sk, choice, cars, fleet_hours, seg_in, seg_out) -> tuple[float, list[str]]:
        """Hours someone drives a car alone, and who. People are spread evenly across a fleet's cars."""
        trip = self.trip
        n = len(trip.travelers)
        if n == 1:  # a one-person trip has no one to share the driving with
            return 0.0, []

        def solo_cars(people: int, k: int) -> int:
            return min(k, people) if people <= k else max(0, 2 * k - people)

        names = [t.name for t in trip.travelers]
        hours, notes = 0.0, []
        if sk.mode == SECOND_CAR:
            for f in (0, 1):
                riders = [names[i] for i, c in enumerate(choice) if c[1].fleet == f]
                k = solo_cars(len(riders), cars[f])
                if k:
                    hours += k * fleet_hours[f]
                    who = riders[0] if len(riders) == 1 else "one driver"
                    notes.append(f"{who} drives alone the whole trip ({fleet_hours[f]:.1f}h)")
            return hours, notes

        k = solo_cars(n, cars[0])
        if k:
            hours += k * fleet_hours[0]
            notes.append(f"{k} car{'s' if k > 1 else ''} with a single driver for the whole trip ({fleet_hours[0]:.1f}h)")
        if seg_in:
            riders = [names[i] for i, c in enumerate(choice) if c[1].arr == sk.gw_in]
            if solo_cars(len(riders), cars[0]):
                hours += solo_cars(len(riders), cars[0]) * seg_in
                notes.append(f"{riders[0] if len(riders) == 1 else 'one driver'} drives {seg_in:.1f}h alone "
                             f"({sk.gw_in} → {sk.pickup}) to pick the others up")
        if seg_out:
            riders = [names[i] for i, c in enumerate(choice) if c[1].dep == sk.gw_out]
            if solo_cars(len(riders), cars[0]):
                hours += solo_cars(len(riders), cars[0]) * seg_out
                notes.append(f"{riders[0] if len(riders) == 1 else 'one driver'} drives {seg_out:.1f}h alone "
                             f"({sk.dropoff} → {sk.gw_out}) after dropping the others off")
        return hours, notes

    def _core(self, sk: Skeleton, strict: bool):
        """Everything about a skeleton that doesn't depend on the nights split (cached).

        Returns a rejection stat name, or the pieces evaluate() needs.
        """
        key = (sk.gw_in, sk.pickup, sk.gw_out, sk.dropoff, sk.mode, sk.order, sk.start, sk.end, sk.overnight, strict)
        if key not in self._cores:
            self._cores[key] = self._compute_core(sk, strict)
        return self._cores[key]

    def _compute_core(self, sk: Skeleton, strict: bool):
        trip, p = self.trip, self.trip.params
        n = len(trip.travelers)
        lam = p.lambda_per_hour
        route = [trip.places[i] for i in sk.order]
        names = [pl.name for pl in route]
        first, last = names[0], names[-1]
        legs, _ = self.legs(sk.order, sk.overnight)
        middle = [lg.hours / lg.days for lg in legs for _ in range(lg.days)]  # hours per driving day
        middle_h = sum(lg.hours for lg in legs)  # everyone rides along between places
        middle_km = self._path_km(names)
        w = p.drive_hour_weight

        # Arrival / departure paths per fleet, and each traveler's options.
        if sk.mode == SECOND_CAR:
            paths_in = [[sk.gw_in, first], [sk.b_in, first]]
            paths_out = [[last, sk.gw_out], [last, sk.b_out]]
            day1 = max(self._path_h(x) for x in paths_in)
            day_n = max(self._path_h(x) for x in paths_out)
            fleet_hours = [self._path_h(paths_in[f]) + middle_h + self._path_h(paths_out[f]) for f in (0, 1)]
            options = [
                _Option(sk.gw_in, sk.gw_out, fleet_hours[0], 0),
                _Option(sk.b_in, sk.b_out, fleet_hours[1], 1),
            ]
        else:
            path_in = [sk.gw_in] + ([sk.pickup] if sk.pickup else []) + [first]
            path_out = [last] + ([sk.dropoff] if sk.dropoff else []) + [sk.gw_out]
            paths_in, paths_out = [path_in], [path_out]
            day1, day_n = self._path_h(path_in), self._path_h(path_out)
            arr_opts = [(sk.gw_in, day1)] + ([(sk.pickup, self.drive.hours(sk.pickup, first))] if sk.pickup else [])
            dep_opts = [(sk.gw_out, day_n)] + ([(sk.dropoff, self.drive.hours(last, sk.dropoff))] if sk.dropoff else [])
            options = [_Option(a, d, ha + hd + middle_h, 0) for a, ha in arr_opts for d, hd in dep_opts]
            fleet_hours = [day1 + middle_h + day_n]

        drive = [day1] + middle + [day_n]
        if strict and max(drive) > p.max_drive_h_per_day + 1:
            return "rejected: drive limit"
        penalty = sum(max(0.0, h - p.max_drive_h_per_day) for h in drive) * p.drive_penalty_per_h * n

        # Price every option for every traveler; cheapest (money + λ × time) first.
        priced: list[list[tuple[float, _Option, _Tickets]]] = []
        for ti in range(n):
            opts = []
            for opt in options:
                tk = self.tickets(ti, opt.arr, opt.dep, sk.start, sk.end)
                if tk is not None:
                    opts.append((tk.score + lam * w * opt.drive_h, opt, tk))
            if not opts:
                return "rejected: no fare"
            opts.sort(key=lambda x: x[0])
            priced.append(opts)

        # Fleet distances and drop fees don't depend on who rides where; work them out once.
        fleets = (0, 1) if sk.mode == SECOND_CAR else (0,)
        kms = [self._path_km(paths_in[f]) + middle_km + self._path_km(paths_out[f]) for f in fleets]
        if sk.mode == SECOND_CAR:
            fees = [trip.drop_fee(sk.gw_in, sk.gw_out), trip.drop_fee(sk.b_in, sk.b_out)]
        else:
            fees = [trip.drop_fee(sk.gw_in, sk.gw_out)]
        single_cars = [math.ceil(n / p.seats_per_car)]

        def ground(choice: list[tuple[float, _Option, _Tickets]]) -> tuple[list[int], float, float, float]:
            if sk.mode == SECOND_CAR:
                counts = [sum(1 for c in choice if c[1].fleet == f) for f in (0, 1)]
                cars = [math.ceil(k / p.seats_per_car) for k in counts]
            else:
                cars = single_cars
            rental = sum(cars) * p.car_daily_rate * trip.length_days
            drop = sum(c * f for c, f in zip(cars, fees))
            fuel = sum(c * km for c, km in zip(cars, kms)) / KM_PER_MILE / p.mpg * p.fuel_price_per_gal
            return cars, rental, drop, fuel

        # Stretches a car may drive with fewer people on board (detour pickups / drop-offs).
        seg_in = self.drive.hours(sk.gw_in, sk.pickup) if sk.mode == DETOUR and sk.pickup else 0.0
        seg_out = self.drive.hours(sk.dropoff, sk.gw_out) if sk.mode == DETOUR and sk.dropoff else 0.0
        return names, first, last, paths_in, paths_out, drive, penalty, priced, ground, legs, fleet_hours, seg_in, seg_out

    def _tracks(self, sk, choice, members, paths_in, paths_out, cars, first, last):
        def build(airport_of, paths, cars_for, reverse: bool) -> list[Track]:
            tracks: dict[str, Track] = {}
            for (_, opt, _), m in zip(choice, members):
                code = airport_of(opt)
                if code not in tracks:
                    path = paths(opt)
                    tracks[code] = Track(code, [], path, self._path_h(path), 0)
                tracks[code].members.append(m.name)
            for code, k in cars_for():
                if code in tracks:
                    tracks[code].cars += k
            ordered = sorted(tracks.values(), key=lambda tr: -tr.hours)
            return list(reversed(ordered)) if reverse else ordered

        if sk.mode == SECOND_CAR:
            arr = build(
                lambda o: o.arr,
                lambda o: paths_in[o.fleet],
                lambda: [(sk.gw_in, cars[0]), (sk.b_in, cars[1])],
                reverse=False,
            )
            dep = build(
                lambda o: o.dep,
                lambda o: paths_out[o.fleet],
                lambda: [(sk.gw_out, cars[0]), (sk.b_out, cars[1])],
                reverse=True,
            )
        else:
            full_in, full_out = paths_in[0], paths_out[0]
            arr = build(
                lambda o: o.arr,
                lambda o: full_in if o.arr == sk.gw_in else [o.arr, first],
                lambda: [(sk.gw_in, cars[0])],
                reverse=False,
            )
            dep = build(
                lambda o: o.dep,
                lambda o: full_out if o.dep == sk.gw_out else [last, o.dep],
                lambda: [(sk.gw_out, cars[0])],
                reverse=True,
            )
        return arr, dep


def drive_model_for(trip: Trip, airports: dict[str, Airport]) -> DriveModel:
    points = {a.code: (a.lat, a.lng) for a in airports.values()}
    points.update({pl.name: (pl.lat, pl.lng) for pl in trip.places})
    return DriveModel(points, trip.drive_overrides)


def nearest_airport(trip: Trip, airports: dict[str, Airport], drive: DriveModel) -> str:
    """The naive choice: the airport closest to the first listed place."""
    first = trip.places[0].name
    return min(airports, key=lambda code: drive.hours(code, first))


def candidate_gateways(
    trip: Trip, airports: dict[str, Airport], ev: Evaluator, baseline: str
) -> list[GatewayCandidate]:
    """HLD 7.5 step 1: airports within the drive limit, scored by cached fare level."""
    p = trip.params
    n = len(trip.travelers)
    dates = trip.start_dates()
    out: list[GatewayCandidate] = []
    for code in airports:
        place, hours = min(((pl.name, ev.drive.hours(code, pl.name)) for pl in trip.places), key=lambda x: x[1])
        if hours > p.max_gateway_drive_h and code != baseline:
            continue
        # Cheapest inbound / return fare per traveler across the window; a
        # gateway can serve as arrival, departure, or both (open-jaw).
        span = timedelta(days=trip.length_days - 1)
        sums: dict[bool, float | None] = {}
        for outbound in (True, False):
            total: float | None = 0.0
            for t in trip.travelers:
                prices = [
                    f.price for d in dates if (f := ev.flight(t, code, d if outbound else d + span, outbound))
                ]
                if not prices:
                    total = None
                    break
                total += min(prices)
            sums[outbound] = total
        s_in, s_out = sums[True], sums[False]
        if s_in is None and s_out is None:
            score = None
        else:
            fares_total = (s_in if s_in is not None else s_out) + (s_out if s_out is not None else s_in)
            score = fares_total + p.lambda_per_hour * n * hours
        out.append(GatewayCandidate(code, place, hours, score, s_in is not None, s_out is not None))
    out.sort(key=lambda g: (g.score is None, g.score or 0.0))
    return out


@dataclass
class SearchResult:
    trip: Trip
    fare_source: str
    plans: list[Plan]
    baseline: Plan | None
    baseline_airport: str
    gateways: list[GatewayCandidate]
    kept_gateways: list[str]
    stats: dict[str, int]
    fare_lookups: int
    elapsed_s: float
    feasible_count: int = 0
    all_plans: list[Plan] = field(default_factory=list, repr=False)
    why_no_plan: list[str] = field(default_factory=list)  # plain-language reasons when plans is empty
    baseline_warnings: list[str] = field(default_factory=list)  # rules the naive plan breaks

    @property
    def best(self) -> Plan | None:
        return self.plans[0] if self.plans else None

    @property
    def transport_savings(self) -> float | None:
        if self.best is None or self.baseline is None:
            return None
        return self.baseline.transport - self.best.transport

    @property
    def transport_savings_pct(self) -> float | None:
        s = self.transport_savings
        if s is None or self.baseline.transport <= 0:
            return None
        return s / self.baseline.transport


def choose_plans(plans: list[Plan], band: float) -> list[Plan]:
    """HLD 7.7: lowest total, least travel time, fairest split — each on distinct airports."""
    if not plans:
        return []
    ranked = sorted(plans, key=lambda p: p.objective)
    lowest = ranked[0]
    lowest.label = "lowest_total"
    chosen = [lowest]
    limit = lowest.money_total * (1 + band)
    in_band = [p for p in ranked if p.money_total <= limit]
    criteria = (
        ("least_time", lambda p: (p.transit_hours, p.objective)),
        ("fairest", lambda p: (p.share_gap, p.objective)),
    )
    for label, key in criteria:
        used = {c.skeleton.gateways_key for c in chosen}
        pick = next((p for p in sorted(in_band, key=key) if p.skeleton.gateways_key not in used), None)
        if pick is not None:
            pick.label = label
            chosen.append(pick)
    return chosen


def place_nights(trip: Trip, order: tuple[int, ...], total: int) -> list[tuple[int, ...]]:
    """Nights per place (aligned with `order`) for `total` nights at places.

    Travel and time don't depend on the split, only lodging does, so the best
    split is always the minimums plus every spare night at the cheapest place.
    `all_night_splits` lists every split instead (same best plans, much slower).
    """
    mins = [trip.places[i].min_nights for i in order]
    if total < sum(mins):
        return []
    if trip.params.all_night_splits:
        return list(night_allocations(total, mins))
    prices = [trip.places[i].lodging_per_night for i in order]
    cheapest = prices.index(min(prices))
    alloc = list(mins)
    alloc[cheapest] += total - sum(mins)
    return [tuple(alloc)]


def _baseline(trip: Trip, ev: Evaluator, airport: str) -> Plan | None:
    """Everyone round-trips the nearest airport; places in listed order; best dates."""
    order = tuple(range(len(trip.places)))
    # The naive group splits long drives too, if the trip has the nights for it.
    overnight = trip.params.overnight_stops
    road = ev.legs(order, overnight)[1]
    allocs = place_nights(trip, order, trip.nights - road)
    if not allocs:
        overnight, allocs = False, place_nights(trip, order, trip.nights)
    best = None
    for start in trip.start_dates():
        end = start + timedelta(days=trip.length_days - 1)
        for alloc in allocs:
            plan = ev.evaluate(Skeleton(airport, airport, order, alloc, start, end, overnight=overnight), strict=False)
            if plan and (best is None or plan.money_total < best.money_total):
                best = plan
    if best:
        best.label = "baseline"
    return best


def _gateway_configs(trip: Trip, arrivals: list[str], departures: list[str]):
    """(gw_in, pickup, gw_out, dropoff, mode) combinations to search."""
    split = trip.params.split_gateways and len(trip.travelers) >= 2
    ins = [(a, None) for a in arrivals]
    outs = [(d, None) for d in departures]
    if split:
        ins += [(a, b) for a in arrivals for b in arrivals if b != a]
        outs += [(d, x) for d in departures for x in departures if x != d]
    for gi, pk in ins:
        for go, dp in outs:
            if pk is None and dp is None:
                yield gi, None, go, None, SINGLE
                continue
            yield gi, pk, go, dp, DETOUR
            # Second car: fleets (gi→go) and (pk or gi → dp or go). Swapping the
            # fleets gives the same plan, so keep one ordering.
            if (gi, go) < (pk or gi, dp or go):
                yield gi, pk, go, dp, SECOND_CAR


def search(trip: Trip, airports: dict[str, Airport], fares: FareSource) -> SearchResult:
    t0 = time.perf_counter()
    memo = fares if isinstance(fares, MemoFares) else MemoFares(fares)
    drive = drive_model_for(trip, airports)
    ev = Evaluator(trip, memo, drive, airports)

    base_code = nearest_airport(trip, airports, drive)
    gateways = candidate_gateways(trip, airports, ev, base_code)
    usable = [g for g in gateways if g.score is not None]
    kept_g = usable[: trip.params.max_gateways]
    base_g = next((g for g in usable if g.code == base_code), None)
    if base_g is not None and base_g not in kept_g:
        kept_g.append(base_g)
    kept = [g.code for g in kept_g]
    arrivals = [g.code for g in kept_g if g.arrival_ok]
    departures = [g.code for g in kept_g if g.departure_ok]
    configs = list(_gateway_configs(trip, arrivals, departures))

    k = len(trip.places)
    orders = [tuple(range(k))] if trip.ordered else list(permutations(range(k)))
    overnight = trip.params.overnight_stops
    # Nights on the road depend only on the place order, so settle each order's
    # nights split once (orders whose long drives leave too few nights drop out).
    order_allocs = []
    for order in orders:
        allocs = place_nights(trip, order, trip.nights - ev.legs(order, overnight)[1])
        if allocs:
            order_allocs.append((order, allocs))
        else:
            ev.stats["rejected: not enough days for long drives"] += len(configs) * len(trip.start_dates())
    plans: list[Plan] = []
    for start in trip.start_dates():
        end = start + timedelta(days=trip.length_days - 1)
        for gw_in, pickup, gw_out, dropoff, mode in configs:
            for order, allocs in order_allocs:
                for alloc in allocs:
                    sk = Skeleton(gw_in, gw_out, order, alloc, start, end, pickup, dropoff, mode, overnight)
                    plan = ev.evaluate(sk)
                    if plan is not None:
                        plans.append(plan)

    search_stats = dict(ev.stats)
    baseline = _baseline(trip, ev, base_code)
    chosen = choose_plans(plans, trip.params.plan_band)
    reasons = [] if chosen else why_no_plan(trip, ev, search_stats, arrivals, departures)

    return SearchResult(
        trip=trip,
        fare_source=memo.name,
        plans=chosen,
        baseline=baseline,
        baseline_airport=base_code,
        gateways=gateways,
        kept_gateways=kept,
        stats=search_stats,
        fare_lookups=memo.lookups,
        elapsed_s=time.perf_counter() - t0,
        feasible_count=len(plans),
        all_plans=plans,
        why_no_plan=reasons,
        baseline_warnings=baseline_warnings(trip, baseline),
    )
