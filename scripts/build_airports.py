"""Rebuild gto/data/airports.csv from the OurAirports public-domain dataset.

    curl -o /tmp/ourairports/airports.csv https://davidmegginson.github.io/ourairports-data/airports.csv
    python3 -I scripts/build_airports.py /tmp/ourairports/airports.csv gto/data/airports.csv

Keeps every US airport with scheduled passenger service and an IATA code.
Rows already in the output file keep their hand-set name, coordinates and size
(the synthetic fare model depends on size); new rows get size from the
OurAirports type (large → hub, otherwise regional). City, state and search
keywords are filled in for every row.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

FIELDS = ["code", "name", "lat", "lng", "size", "city", "state", "keywords", "major"]

# `size` only drives the synthetic fare model. Treat every non-large airport as
# regional (pricier, usually a connection) so fake prices don't send groups to
# airports with little real service; hand-curated rows keep their own size.
SIZE_BY_TYPE = {"large_airport": "hub", "medium_airport": "regional", "small_airport": "regional"}

# OurAirports marks these "scheduled service" because of air-taxi / charter
# operators, but no airline sells tickets there. Add more as they turn up.
NO_AIRLINE_SERVICE = {"TEB", "TIW", "BFI", "PDK", "BJC", "CCR", "SQL", "OLM"}

# Airports whose IATA code changed in the source data: old code -> new code.
# The old code is dropped (it stays searchable through the new row's keywords).
RENAMED = {"PBI": "DJT"}


def main(src: str, dest: str) -> None:
    dest_path = Path(dest)
    existing: dict[str, dict] = {}
    if dest_path.exists():
        with open(dest_path, newline="") as fh:
            for row in csv.DictReader(fh):
                existing[row["code"].strip().upper()] = row

    out: dict[str, dict] = {}
    with open(src, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            code = r["iata_code"].strip().upper()
            if (
                r["iso_country"] != "US"
                or r["scheduled_service"] != "yes"
                or not code
                or r["type"] not in ("large_airport", "medium_airport", "small_airport")
                or code in NO_AIRLINE_SERVICE
            ):
                continue
            row = {
                "code": code,
                "name": r["name"].strip(),
                "lat": f"{float(r['latitude_deg']):.4f}",
                "lng": f"{float(r['longitude_deg']):.4f}",
                "size": SIZE_BY_TYPE[r["type"]],
                "city": r["municipality"].strip(),
                "state": r["iso_region"].removeprefix("US-"),
                "keywords": r["keywords"].strip(),
                # "major" = a large airport in OurAirports; used for "nearby airport" suggestions
                "major": "1" if r["type"] == "large_airport" else "",
            }
            if code in existing:  # keep hand-curated values
                for k in ("name", "lat", "lng", "size"):
                    row[k] = existing[code][k]
            out[code] = row

    missing = sorted(set(existing) - set(out))
    for code in missing:  # never drop a curated airport (unless it's on the exclusion list)
        if code not in NO_AIRLINE_SERVICE and code not in RENAMED:
            out[code] = {k: existing[code].get(k, "") for k in FIELDS}

    with open(dest_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for code in sorted(out):
            w.writerow(out[code])
    print(f"wrote {len(out)} airports to {dest_path} ({len(existing)} curated kept; not in source: {missing or 'none'})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
