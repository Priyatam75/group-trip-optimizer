"""Hotel search + prices from LiteAPI (Nuitée). Display only: nothing here books.

The key comes from the LITEAPI_KEY env var and never leaves the server.
Sandbox keys return test inventory/prices, so treat prices as estimates.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.liteapi.travel/v3.0"
CACHE_TTL_S = 30 * 60  # prices move; HLD keeps live quotes ~30 min
_cache: dict[str, tuple[float, object]] = {}


class LiteAPIError(Exception):
    pass


def key() -> str:
    return os.environ.get("LITEAPI_KEY", "").strip()


def is_sandbox() -> bool:
    return key().startswith(("sand_", "sandbox_"))


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None, timeout: float = 30):
    k = key()
    if not k:
        raise LiteAPIError("Hotel prices aren't set up: start the server with LITEAPI_KEY set.")
    url = f"{BASE}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-Key": k,
        "accept": "application/json",
        **({"content-type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise LiteAPIError(f"LiteAPI {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LiteAPIError(f"LiteAPI unreachable: {exc}") from exc


def _cached(cache_key: str, fn):
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = fn()
    if len(_cache) > 500:
        _cache.clear()
    _cache[cache_key] = (time.time(), value)
    return value


def hotels_near(lat: float, lng: float, radius_m: int = 15000, limit: int = 40) -> list[dict]:
    def fetch():
        d = _call("GET", "/data/hotels", params={
            "countryCode": "US", "latitude": round(lat, 4), "longitude": round(lng, 4),
            "radius": radius_m, "limit": limit,
        })
        out = []
        for h in d.get("data") or []:
            if h.get("deletedAt") or h.get("latitude") is None:
                continue
            out.append({
                "id": h["id"],
                "name": h.get("name") or "",
                "lat": h["latitude"],
                "lng": h["longitude"],
                "stars": h.get("stars"),
                "rating": h.get("rating"),
                "reviews": h.get("reviewCount"),
                "address": ", ".join(x for x in (h.get("address"), h.get("city")) if x),
                "photo": h.get("thumbnail") or h.get("main_photo") or "",
            })
        return out

    return _cached(f"near:{lat:.4f}:{lng:.4f}:{radius_m}:{limit}", fetch)


def _amount(x) -> float | None:
    try:
        return float(x["amount"])
    except (TypeError, KeyError, ValueError):
        return None


def rates(hotel_ids: list[str], checkin: str, checkout: str, adults: int = 2) -> dict[str, dict]:
    """Cheapest room per hotel for the dates: {hotel_id: {total, room, board, refundable}}."""
    if not hotel_ids:
        return {}

    def fetch():
        d = _call("POST", "/hotels/rates", body={
            "hotelIds": hotel_ids, "checkin": checkin, "checkout": checkout,
            "occupancies": [{"adults": adults}], "currency": "USD", "guestNationality": "US",
            "maxRatesPerHotel": 1,
        }, timeout=45)
        out: dict[str, dict] = {}
        for h in d.get("data") or []:
            best = None
            for rt in h.get("roomTypes") or []:
                # The price a guest would see; fall back to the offer price.
                total = _amount(rt.get("suggestedSellingPrice")) or _amount(rt.get("offerRetailRate"))
                if total is None:
                    continue
                rate = (rt.get("rates") or [{}])[0]
                refundable = any(
                    (p.get("refundableTag") or "").upper() == "RFN"
                    for p in [rate.get("cancellationPolicies") or {}]
                )
                taxes = (rate.get("retailRate") or {}).get("taxesAndFees") or []
                cand = {"total": total, "room": rate.get("name") or "", "board": rate.get("boardName") or "",
                        "refundable": refundable, "taxes_included": all(t.get("included") for t in taxes)}
                if best is None or total < best["total"]:
                    best = cand
            if best:
                out[h["hotelId"]] = best
        return out

    return _cached(f"rates:{','.join(sorted(hotel_ids))}:{checkin}:{checkout}:{adults}", fetch)
