"""Web API around the gto optimizer, plus the built React app.

Env vars:
  DATABASE_URL / MYSQL_URL  mysql://user:pass@host:3306/db (falls back to local SQLite)
  APP_PASSWORD              if set, the whole site asks for this password (HTTP basic auth)
  GTO_FARES                 fare source spec, default "synthetic" (see README)
  TRAVELPAYOUTS_TOKEN       needed if GTO_FARES includes travelpayouts
  LITEAPI_KEY               hotel search + prices on the map (sandbox key = test prices)
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gto.airports import load_airports
from gto.fares import MemoFares, build_fare_source, is_synthetic
from gto.models import trip_from_dict
from gto.optimizer import search
from gto.report import result_to_dict

from . import liteapi
from .db import RunRow, SessionLocal, TripRow, init_db

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"
EXAMPLES_DIR = ROOT / "trips" / "examples"
MAX_PLACES = 6  # permutations explode beyond this

AIRPORTS = load_airports()
FARE_SPEC = os.environ.get("GTO_FARES", "synthetic")
FARES = build_fare_source(
    FARE_SPEC,
    AIRPORTS,
    token=os.environ.get("TRAVELPAYOUTS_TOKEN"),
    cache_dir=Path(os.environ.get("GTO_CACHE_DIR", "/tmp/gto-cache")),
)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

app = FastAPI(title="Group Trip Optimizer")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.middleware("http")
async def password_gate(request: Request, call_next):
    if not APP_PASSWORD or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            pw = ""
        if secrets.compare_digest(pw.encode(), APP_PASSWORD.encode()):
            return await call_next(request)
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="gto"'})


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TripIn(BaseModel):
    input: dict[str, Any]
    created_by: str = Field(default="", max_length=100)


def _run(trip_input: dict) -> dict:
    try:
        trip = trip_from_dict(trip_input)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, f"Invalid trip: {exc}")
    if len(trip.places) > MAX_PLACES:
        raise HTTPException(422, f"At most {MAX_PLACES} places for now")
    memo = MemoFares(FARES)
    res = search(trip, AIRPORTS, memo)
    out = result_to_dict(res, is_synthetic(memo))
    out["gateway_details"] = [
        {
            "code": g.code,
            "name": AIRPORTS[g.code].name,
            "drive_h": round(g.drive_h, 2),
            "nearest_place": g.nearest_place,
            "score": None if g.score is None else round(g.score, 2),
            "kept": g.code in res.kept_gateways,
        }
        for g in res.gateways
    ]
    out["baseline_airport"] = res.baseline_airport
    out["feasible_count"] = res.feasible_count
    return out


def _summary(t: TripRow) -> dict:
    last = t.runs[0] if t.runs else None
    return {
        "id": t.id,
        "name": t.name,
        "created_by": t.created_by,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "last_savings_pct": last.savings_pct if last else None,
        "last_best_total": last.best_total if last else None,
    }


def _run_dict(r: RunRow) -> dict:
    return {"id": r.id, "created_at": r.created_at.isoformat(), "fare_source": r.fare_source, "result": r.result_json}


def _save_run(db: Session, t: TripRow) -> RunRow:
    result = _run(t.input_json)
    plans = result["plans"]
    run = RunRow(
        trip_id=t.id,
        fare_source=result["fare_source"],
        savings_pct=result["transport_savings_pct"],
        best_total=plans[0]["totals"]["money"] if plans else None,
        result_json=result,
    )
    db.add(run)
    db.commit()
    db.refresh(t)
    return run


# --------------------------------------------------------------------------- #
# API


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config")
def config():
    memo_synthetic = is_synthetic(FARES)
    return {
        "fare_source": FARE_SPEC,
        "synthetic": memo_synthetic,
        "max_places": MAX_PLACES,
        "hotel_prices": bool(liteapi.key()),
        "hotel_prices_sandbox": liteapi.is_sandbox(),
    }


@app.get("/api/airports")
def airports():
    return [
        {
            "code": a.code, "name": a.name, "lat": a.lat, "lng": a.lng, "size": a.size,
            "city": a.city, "state": a.state, "keywords": a.keywords, "major": a.major,
        }
        for a in sorted(AIRPORTS.values(), key=lambda a: a.code)
    ]


# Place search. Phase 0 uses OpenStreetMap's free Photon geocoder; the HLD
# swaps in a commercial maps API later. Only the typed text is sent.
GEOCODE_URL = "https://photon.komoot.io/api/"
GEOCODE_UA = "group-trip-optimizer/0.1 (prototype)"
_geocode_cache: dict[str, list[dict]] = {}
# Streets, paths and buildings aren't trip destinations.
SKIP_OSM_KEYS = {"highway", "railway", "building", "shop", "office", "craft", "power", "man_made"}


def _place_label(props: dict) -> str:
    parts = [props.get("name") or ""]
    for key in ("city", "county", "state"):
        v = props.get(key)
        if v and v not in parts:
            parts.append(v)
    return ", ".join(p for p in parts if p)


@app.get("/api/lodging")
def lodging(lat: float, lng: float, checkin: date, checkout: date, adults: int = 2, radius_km: float = 15):
    """Hotels around a place with the cheapest nightly price for the dates (display only)."""
    nights = (checkout - checkin).days
    if nights < 1 or nights > 30:
        raise HTTPException(422, "checkout must be 1–30 nights after checkin")
    try:
        hotels = liteapi.hotels_near(lat, lng, radius_m=int(min(max(radius_km, 1), 50) * 1000))
        prices = liteapi.rates([h["id"] for h in hotels], checkin.isoformat(), checkout.isoformat(),
                               adults=max(1, min(adults, 4)))
    except liteapi.LiteAPIError as exc:
        raise HTTPException(503, str(exc))
    out = []
    for h in hotels:
        p = prices.get(h["id"])
        out.append({
            **h,
            "price_total": round(p["total"], 2) if p else None,
            "price_per_night": round(p["total"] / nights, 2) if p else None,
            "room": p["room"] if p else None,
            "board": p["board"] if p else None,
            "refundable": p["refundable"] if p else None,
            "taxes_included": p["taxes_included"] if p else None,
        })
    out.sort(key=lambda h: (h["price_per_night"] is None, h["price_per_night"] or 0))
    nightly = [h["price_per_night"] for h in out if h["price_per_night"] is not None]
    return {
        "hotels": out,
        # Typical price for the area, used as the lodging estimate before a hotel is picked.
        "typical_per_night": round(statistics.median(nightly), 2) if nightly else None,
        "priced_count": len(nightly),
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "nights": nights,
        "sandbox": liteapi.is_sandbox(),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "LiteAPI",
    }


@app.get("/api/geocode")
def geocode(q: str):
    q = q.strip()
    if len(q) < 3:
        return []
    key = q.lower()
    if key in _geocode_cache:
        return _geocode_cache[key]
    params = urllib.parse.urlencode({"q": q, "limit": 20, "lang": "en", "bbox": "-180,17,-64,72"})
    req = urllib.request.Request(f"{GEOCODE_URL}?{params}", headers={"User-Agent": GEOCODE_UA})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(502, f"Place search is unavailable right now ({exc}). Try again in a moment.")
    out, seen = [], set()
    for f in data.get("features", []):
        props = f.get("properties", {})
        if props.get("countrycode", "").upper() != "US" or not props.get("name"):
            continue
        if props.get("osm_key") in SKIP_OSM_KEYS:
            continue
        lng, lat = f["geometry"]["coordinates"][:2]
        label = _place_label(props)
        if label in seen:
            continue
        seen.add(label)
        out.append({
            "name": props["name"],
            "label": label,
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "kind": (props.get("osm_value") or "").replace("_", " "),
        })
        if len(out) == 6:
            break
    if len(_geocode_cache) > 1000:
        _geocode_cache.clear()
    _geocode_cache[key] = out
    return out


@app.get("/api/examples")
def examples():
    out = []
    for f in sorted(EXAMPLES_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        out.append({"id": f.stem, "name": data["name"], "input": data})
    return out


@app.post("/api/preview")
def preview(body: TripIn):
    """Run without saving."""
    return _run(body.input)


@app.get("/api/trips")
def list_trips(db: Session = Depends(get_db)):
    rows = db.scalars(select(TripRow).order_by(TripRow.updated_at.desc())).all()
    return [_summary(t) for t in rows]


@app.post("/api/trips")
def create_trip(body: TripIn, db: Session = Depends(get_db)):
    _run(body.input)  # validate before saving
    t = TripRow(name=str(body.input.get("name", "Untitled"))[:200], created_by=body.created_by, input_json=body.input)
    db.add(t)
    db.commit()
    run = _save_run(db, t)
    return {**_summary(t), "input": t.input_json, "latest_run": _run_dict(run)}


def _get(db: Session, trip_id: int) -> TripRow:
    t = db.get(TripRow, trip_id)
    if t is None:
        raise HTTPException(404, "Trip not found")
    return t


@app.get("/api/trips/{trip_id}")
def get_trip(trip_id: int, db: Session = Depends(get_db)):
    t = _get(db, trip_id)
    return {
        **_summary(t),
        "input": t.input_json,
        "latest_run": _run_dict(t.runs[0]) if t.runs else None,
        "runs": [{"id": r.id, "created_at": r.created_at.isoformat(), "savings_pct": r.savings_pct} for r in t.runs],
    }


@app.put("/api/trips/{trip_id}")
def update_trip(trip_id: int, body: TripIn, db: Session = Depends(get_db)):
    t = _get(db, trip_id)
    _run(body.input)
    t.input_json = body.input
    t.name = str(body.input.get("name", "Untitled"))[:200]
    if body.created_by:
        t.created_by = body.created_by
    db.commit()
    run = _save_run(db, t)
    return {**_summary(t), "input": t.input_json, "latest_run": _run_dict(run)}


@app.post("/api/trips/{trip_id}/run")
def rerun_trip(trip_id: int, db: Session = Depends(get_db)):
    t = _get(db, trip_id)
    return _run_dict(_save_run(db, t))


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(RunRow, run_id)
    if r is None:
        raise HTTPException(404, "Run not found")
    return _run_dict(r)


@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, trip_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# React app (built into web/dist)

if (WEB_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    index = WEB_DIST / "index.html"
    if not index.exists():
        return Response("Frontend not built. Run: cd web && npm install && npm run build", media_type="text/plain")
    return FileResponse(index)
