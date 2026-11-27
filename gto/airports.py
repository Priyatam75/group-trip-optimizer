"""Airport reference data (code, coordinates, size class)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "data" / "airports.csv"
SIZES = ("hub", "medium", "regional")


@dataclass(frozen=True)
class Airport:
    code: str
    name: str
    lat: float
    lng: float
    size: str  # hub | medium | regional
    city: str = ""
    state: str = ""
    keywords: str = ""  # other names people use, e.g. "SeaTac Airport"
    major: bool = False  # a large airport; used for "nearby airport" suggestions


def load_airports(path: str | Path | None = None) -> dict[str, Airport]:
    """Airport list. Data from OurAirports (public domain); see scripts/build_airports.py."""
    airports: dict[str, Airport] = {}
    with open(path or DEFAULT_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            size = row["size"].strip()
            if size not in SIZES:
                raise ValueError(f"airport {row['code']}: size must be one of {SIZES}")
            code = row["code"].strip().upper()
            airports[code] = Airport(
                code=code,
                name=row["name"].strip(),
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                size=size,
                city=(row.get("city") or "").strip(),
                state=(row.get("state") or "").strip(),
                keywords=(row.get("keywords") or "").strip(),
                major=(row.get("major") or "").strip() == "1",
            )
    return airports
