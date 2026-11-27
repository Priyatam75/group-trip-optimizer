"""Fare sources (HLD section 8, phase 0 = cached fares only).

Every source answers one question: the cheapest one-way fare from `origin` to
`dest` departing on `day`, or None.

- travelpayouts: free cached fares (Aviasales Data API, needs a token).
- fixture:PATH:  fares recorded in a JSON file (for reproducible replays).
- synthetic:     a deterministic distance-based model. NOT real prices; it only
                 exists so the pipeline can run and be tested offline.

Sources chain with commas, first hit wins: "fixture:f.json,travelpayouts".
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from .airports import Airport
from .geo import haversine_km


@dataclass(frozen=True)
class Fare:
    origin: str
    dest: str
    day: date
    price: float
    duration_h: float
    source: str
    stops: int | None = None
    carrier: str | None = None
    duration_estimated: bool = False


@dataclass(frozen=True)
class RoundTrip:
    """A round-trip ticket origin→dest on out_day, dest→origin on ret_day (HLD 8.4)."""

    origin: str
    dest: str
    out_day: date
    ret_day: date
    price: float
    out_duration_h: float
    ret_duration_h: float
    source: str
    duration_estimated: bool = False


class FareSource(Protocol):
    name: str

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None: ...

    # Optional: sources without round-trip data simply don't define it.
    # def round_trip(self, origin, dest, out_day, ret_day) -> RoundTrip | None


def round_trip_of(source, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
    fn = getattr(source, "round_trip", None)
    return fn(origin, dest, out_day, ret_day) if fn else None


def estimate_duration_h(km: float, stops: int) -> float:
    return km / 780.0 + 0.6 + 1.6 * stops


def _airport_km(airports: dict[str, Airport], a: str, b: str) -> float | None:
    if a not in airports or b not in airports:
        return None
    x, y = airports[a], airports[b]
    return haversine_km(x.lat, x.lng, y.lat, y.lng)


# --------------------------------------------------------------------------- #
# Synthetic


def _unit(key: str) -> float:
    """Deterministic pseudo-random number in [0, 1) from a string."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0x100000000


class SyntheticFares:
    """Distance-based toy model: regional airports cost more and need a connection."""

    name = "synthetic"
    SIZE_FACTOR = {"hub": 1.0, "medium": 1.3, "regional": 1.9}

    def __init__(self, airports: dict[str, Airport]):
        self.airports = airports

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None:
        if origin == dest:
            return None
        km = _airport_km(self.airports, origin, dest)
        if km is None or km < 250:
            return None
        a, b = self.airports[origin], self.airports[dest]
        factor = max(self.SIZE_FACTOR[a.size], self.SIZE_FACTOR[b.size])
        noise = 0.85 + 0.35 * _unit(f"{origin}-{dest}-{day.isoformat()}")
        peak = 1.12 if day.weekday() in (4, 6) else 1.0  # Fri / Sun
        price = round((49 + 0.085 * km) * factor * noise * peak)

        regional = [ap for ap in (a, b) if ap.size == "regional"]
        if not regional:
            stops = 0
        elif len(regional) == 1 and km < 1500 and "hub" in (a.size, b.size):
            stops = 0
        else:
            stops = 1
        return Fare(origin, dest, day, float(price), estimate_duration_h(km, stops), self.name, stops)

    def round_trip(self, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
        out = self.cheapest(origin, dest, out_day)
        ret = self.cheapest(dest, origin, ret_day)
        if out is None or ret is None:
            return None
        # Round trips are usually about two one-ways, sometimes a little cheaper or dearer.
        factor = 0.85 + 0.30 * _unit(f"rt-{origin}-{dest}-{out_day.isoformat()}-{ret_day.isoformat()}")
        price = float(round((out.price + ret.price) * factor))
        return RoundTrip(origin, dest, out_day, ret_day, price, out.duration_h, ret.duration_h, self.name)


# --------------------------------------------------------------------------- #
# Fixture


class FixtureFares:
    """Fares from a JSON file: {"fares": [{origin, dest, date, price, duration_h?, stops?}]}

    A row with "return_date" is a round-trip ticket (optional "duration_h" and
    "return_duration_h").
    """

    def __init__(self, path: str | Path, airports: dict[str, Airport] | None = None):
        self.path = Path(path)
        self.name = f"fixture:{self.path.name}"
        data = json.loads(self.path.read_text())
        rows = data["fares"] if isinstance(data, dict) else data
        self.index: dict[tuple[str, str, date], Fare] = {}
        self.rt_index: dict[tuple[str, str, date, date], RoundTrip] = {}
        for r in rows:
            if r.get("return_date"):
                self._add_round_trip(r, airports)
                continue
            o, d = r["origin"].upper(), r["dest"].upper()
            day = date.fromisoformat(r["date"])
            stops = r.get("stops")
            dur = r.get("duration_h")
            estimated = False
            if dur is None:
                km = _airport_km(airports or {}, o, d)
                if km is None:
                    raise ValueError(f"{path}: fare {o}->{d} has no duration_h and unknown airports")
                dur, estimated = estimate_duration_h(km, stops or 0), True
            fare = Fare(o, d, day, float(r["price"]), float(dur), self.name, stops, r.get("carrier"), estimated)
            key = (o, d, day)
            if key not in self.index or fare.price < self.index[key].price:
                self.index[key] = fare

    def _add_round_trip(self, r: dict, airports: dict[str, Airport] | None) -> None:
        o, d = r["origin"].upper(), r["dest"].upper()
        out_day, ret_day = date.fromisoformat(r["date"]), date.fromisoformat(r["return_date"])
        dur_out, dur_ret = r.get("duration_h"), r.get("return_duration_h")
        estimated = False
        if dur_out is None or dur_ret is None:
            km = _airport_km(airports or {}, o, d)
            if km is None:
                raise ValueError(f"{self.path}: round trip {o}<->{d} has no durations and unknown airports")
            est = estimate_duration_h(km, r.get("stops") or 0)
            dur_out, dur_ret, estimated = dur_out or est, dur_ret or est, True
        rt = RoundTrip(o, d, out_day, ret_day, float(r["price"]), float(dur_out), float(dur_ret), self.name, estimated)
        key = (o, d, out_day, ret_day)
        if key not in self.rt_index or rt.price < self.rt_index[key].price:
            self.rt_index[key] = rt

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None:
        return self.index.get((origin, dest, day))

    def round_trip(self, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
        return self.rt_index.get((origin, dest, out_day, ret_day))


# --------------------------------------------------------------------------- #
# Travelpayouts (Aviasales Data API, cached prices)


class TravelpayoutsFares:
    """Free cached fares. One request per (origin, dest, month), cached on disk.

    Prices come from recent user searches (up to ~7 days old) and are not
    bookable — exactly the "narrowing" tier in HLD section 8.
    """

    name = "travelpayouts"
    URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

    def __init__(
        self,
        token: str,
        cache_dir: str | Path,
        airports: dict[str, Airport] | None = None,
        ttl_h: float = 24.0,
        opener=urllib.request.urlopen,
    ):
        self.token = token
        self.cache_dir = Path(cache_dir)
        self.airports = airports or {}
        self.ttl_s = ttl_h * 3600
        self.opener = opener
        self.months: dict[tuple[str, str, str], dict[date, Fare]] = {}
        self.rt_months: dict[tuple[str, str, str, str], dict[tuple[date, date], RoundTrip]] = {}
        self.api_calls = 0
        self.errors = 0

    def _fetch(self, origin: str, dest: str, month: str, return_month: str | None = None) -> list[dict]:
        tag = f"{origin}-{dest}-{month}" + (f"-rt-{return_month}" if return_month else "")
        cache_file = self.cache_dir / f"{tag}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if time.time() - cached["fetched_at"] < self.ttl_s:
                return cached["data"]

        params = {
            "origin": origin,
            "destination": dest,
            "departure_at": month,
            "one_way": "false" if return_month else "true",
            "direct": "false",
            "sorting": "price",
            "currency": "usd",
            "limit": 1000,
            "token": self.token,
        }
        if return_month:
            params["return_at"] = return_month
        query = urllib.parse.urlencode(params)
        self.api_calls += 1
        try:
            with self.opener(f"{self.URL}?{query}", timeout=20) as resp:
                payload = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.errors += 1
            print(f"warning: travelpayouts {tag}: {exc}", file=sys.stderr)
            return []
        if not payload.get("success", True):
            self.errors += 1
            print(f"warning: travelpayouts {tag}: {payload.get('error')}", file=sys.stderr)
            return []

        data = payload.get("data") or []
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
        return data

    def _month(self, origin: str, dest: str, month: str) -> dict[date, Fare]:
        key = (origin, dest, month)
        if key in self.months:
            return self.months[key]
        by_day: dict[date, Fare] = {}
        km = _airport_km(self.airports, origin, dest)
        for row in self._fetch(origin, dest, month):
            try:
                day = date.fromisoformat(row["departure_at"][:10])
                price = float(row["price"])
            except (KeyError, ValueError, TypeError):
                continue
            stops = row.get("transfers")
            minutes = row.get("duration_to") or row.get("duration")
            if minutes:
                dur, estimated = float(minutes) / 60.0, False
            elif km is not None:
                dur, estimated = estimate_duration_h(km, stops or 0), True
            else:
                dur, estimated = 4.0, True
            fare = Fare(origin, dest, day, price, dur, self.name, stops, row.get("airline"), estimated)
            if day not in by_day or price < by_day[day].price:
                by_day[day] = fare
        self.months[key] = by_day
        return by_day

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None:
        if origin == dest:
            return None
        return self._month(origin, dest, day.strftime("%Y-%m")).get(day)

    def _rt_months(self, origin: str, dest: str, month: str, ret_month: str) -> dict[tuple[date, date], RoundTrip]:
        key = (origin, dest, month, ret_month)
        if key in self.rt_months:
            return self.rt_months[key]
        by_pair: dict[tuple[date, date], RoundTrip] = {}
        km = _airport_km(self.airports, origin, dest)
        est = estimate_duration_h(km, 0) if km is not None else 4.0
        for row in self._fetch(origin, dest, month, ret_month):
            try:
                out_day = date.fromisoformat(row["departure_at"][:10])
                ret_day = date.fromisoformat(row["return_at"][:10])
                price = float(row["price"])
            except (KeyError, ValueError, TypeError):
                continue
            to_min, back_min = row.get("duration_to"), row.get("duration_back")
            estimated = not (to_min and back_min)
            rt = RoundTrip(
                origin, dest, out_day, ret_day, price,
                float(to_min) / 60.0 if to_min else est,
                float(back_min) / 60.0 if back_min else est,
                self.name, estimated,
            )
            pair = (out_day, ret_day)
            if pair not in by_pair or price < by_pair[pair].price:
                by_pair[pair] = rt
        self.rt_months[key] = by_pair
        return by_pair

    def round_trip(self, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
        if origin == dest:
            return None
        months = self._rt_months(origin, dest, out_day.strftime("%Y-%m"), ret_day.strftime("%Y-%m"))
        return months.get((out_day, ret_day))


# --------------------------------------------------------------------------- #
# Composition


class ChainFares:
    def __init__(self, sources: list[FareSource]):
        self.sources = sources
        self.name = "+".join(s.name for s in sources)

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None:
        for s in self.sources:
            fare = s.cheapest(origin, dest, day)
            if fare is not None:
                return fare
        return None

    def round_trip(self, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
        for s in self.sources:
            rt = round_trip_of(s, origin, dest, out_day, ret_day)
            if rt is not None:
                return rt
        return None


class MemoFares:
    """Memoizes lookups and counts them; can dump everything it saw as a fixture."""

    def __init__(self, inner: FareSource):
        self.inner = inner
        self.name = inner.name
        self.cache: dict[tuple[str, str, date], Fare | None] = {}
        self.rt_cache: dict[tuple[str, str, date, date], RoundTrip | None] = {}

    @property
    def lookups(self) -> int:
        return len(self.cache) + len(self.rt_cache)

    def cheapest(self, origin: str, dest: str, day: date) -> Fare | None:
        key = (origin, dest, day)
        if key not in self.cache:
            self.cache[key] = self.inner.cheapest(origin, dest, day)
        return self.cache[key]

    def round_trip(self, origin: str, dest: str, out_day: date, ret_day: date) -> RoundTrip | None:
        key = (origin, dest, out_day, ret_day)
        if key not in self.rt_cache:
            self.rt_cache[key] = round_trip_of(self.inner, origin, dest, out_day, ret_day)
        return self.rt_cache[key]

    def dump_fixture(self, path: str | Path) -> int:
        rows = []
        for fare in self.cache.values():
            if fare is None:
                continue
            r = asdict(fare)
            r["date"] = r.pop("day").isoformat()
            if r.pop("duration_estimated"):
                r.pop("duration_h")
            rows.append(r)
        for rt in self.rt_cache.values():
            if rt is None:
                continue
            r = {
                "origin": rt.origin,
                "dest": rt.dest,
                "date": rt.out_day.isoformat(),
                "return_date": rt.ret_day.isoformat(),
                "price": rt.price,
                "source": rt.source,
            }
            if not rt.duration_estimated:
                r["duration_h"], r["return_duration_h"] = rt.out_duration_h, rt.ret_duration_h
            rows.append(r)
        rows.sort(key=lambda r: (r["origin"], r["dest"], r["date"], r.get("return_date", "")))
        Path(path).write_text(json.dumps({"fares": rows}, indent=1))
        return len(rows)


def is_synthetic(source: FareSource) -> bool:
    if isinstance(source, MemoFares):
        source = source.inner
    if isinstance(source, ChainFares):
        return any(is_synthetic(s) for s in source.sources)
    return isinstance(source, SyntheticFares)


def build_fare_source(
    spec: str,
    airports: dict[str, Airport],
    *,
    token: str | None = None,
    cache_dir: str | Path = Path.home() / ".cache" / "gto" / "travelpayouts",
) -> FareSource:
    sources: list[FareSource] = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if part == "synthetic":
            sources.append(SyntheticFares(airports))
        elif part.startswith("fixture:"):
            sources.append(FixtureFares(part.removeprefix("fixture:"), airports))
        elif part == "travelpayouts":
            if not token:
                raise ValueError("travelpayouts needs a token: set TRAVELPAYOUTS_TOKEN")
            sources.append(TravelpayoutsFares(token, cache_dir, airports))
        else:
            raise ValueError(f"unknown fare source {part!r} (synthetic | fixture:PATH | travelpayouts)")
    if not sources:
        raise ValueError("empty fare source spec")
    return sources[0] if len(sources) == 1 else ChainFares(sources)
