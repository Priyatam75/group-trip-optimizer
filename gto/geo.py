"""Distance and drive-time estimates.

Phase 0 has no routing engine (OSRM comes later), so drive time is
great-circle distance x a road factor / an average speed. Trips can override
any pair with a measured value via `drive_overrides`.
"""

from __future__ import annotations

import math

EARTH_KM = 6371.0
ROAD_FACTOR = 1.25  # road km per straight-line km
AVG_KMH = 75.0  # mixed highway / park-road average


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


class DriveModel:
    """Drive hours and road km between named points (airport codes or place names)."""

    def __init__(
        self,
        points: dict[str, tuple[float, float]],
        overrides: dict[tuple[str, str], float] | None = None,
    ):
        self.points = points
        self.overrides = overrides or {}
        self._km: dict[tuple[str, str], float] = {}  # the search asks for the same pairs many times

    def _override(self, a: str, b: str) -> float | None:
        if (a, b) in self.overrides:
            return self.overrides[(a, b)]
        return self.overrides.get((b, a))

    def km(self, a: str, b: str) -> float:
        cached = self._km.get((a, b))
        if cached is not None:
            return cached
        if a == b:
            value = 0.0
        elif (hours := self._override(a, b)) is not None:
            value = hours * AVG_KMH
        else:
            (lat1, lng1), (lat2, lng2) = self.points[a], self.points[b]
            value = haversine_km(lat1, lng1, lat2, lng2) * ROAD_FACTOR
        self._km[(a, b)] = value
        return value

    def hours(self, a: str, b: str) -> float:
        if a == b:
            return 0.0
        hours = self._override(a, b)
        if hours is not None:
            return hours
        return self.km(a, b) / AVG_KMH
