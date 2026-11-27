# Group Trip Cost Optimizer — phase 0 prototype

Phase 0 from the HLD (section 16): **optimizer as a CLI, cached fares only,
replay 10 real trips, measure savings.** Gate 1 passes if the median savings vs
the naive plan ("everyone round-trips to the nearest airport") is ≥ 15% of
group transport cost.

Pure Python 3.10+, no dependencies.

Quick start, no setup or sign-up needed (uses synthetic fares and 10 sample trips):

```sh
python3 -m gto replay                                             # all sample trips + Gate 1 check
python3 -m gto plan trips/examples/tetons_yellowstone.json        # 3 ranked plans + baseline
python3 -m gto plan TRIP.json --json                              # machine-readable
python3 -m gto gateways TRIP.json                                 # candidate airports + scores
python3 -m gto replay trips/real --csv results.csv                # Gate 1 check over many trips
python3 -m unittest discover -s tests -t .                        # tests
```

## Web app (prototype for testers)

`server/` is a FastAPI app that wraps the optimizer and stores trips and every
run in MySQL. `web/` is a React front end (Vite) that the API serves after it's
built. One Docker image contains both.

Run locally (uses SQLite unless `DATABASE_URL` is set):

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-server.txt
(cd web && npm install && npm run build)
.venv/bin/uvicorn server.app:app --reload          # http://localhost:8000
# front-end dev with hot reload: (cd web && npm run dev) → http://localhost:5173
```

The form searches places by name (OpenStreetMap's free Photon geocoder, called
from the server via `/api/geocode`; only the typed text is sent) and airports by
city, name, code or nickname (local list, no outside service). It checks the
trip as you type (nights, traveler dates, budgets vs lodging + car, drives too
long for one day), and when no plan works the results explain why
(`why_no_plan`). The naive plan is labeled "for comparison only" with the rules
it breaks (`baseline_warnings`).

Airports: `gto/data/airports.csv` has every US airport with scheduled service
from [OurAirports](https://ourairports.com/data/) (public domain), rebuilt with
`scripts/build_airports.py`. The original 65 hand-curated rows keep their sizes
(size only affects synthetic fares). A few air-taxi-only fields are excluded by
hand, and "nearby airport" suggestions use major airports only.

| Env var | Meaning |
|---|---|
| `DATABASE_URL` or `MYSQL_URL` | `mysql://user:pass@host:3306/db`. Tables are created on startup. |
| `APP_PASSWORD` | If set, the browser asks for this password (any username). |
| `GTO_FARES` | Fare source spec, default `synthetic`. |
| `TRAVELPAYOUTS_TOKEN` | Needed when `GTO_FARES` includes `travelpayouts`. |
| `LITEAPI_KEY` | Hotel search + nightly prices for the "Choose where to stay" map (LiteAPI / Nuitée). Display only, nothing is booked. A `sand_…` key returns test prices. |

### Deploy to Railway (app + MySQL in one project)

1. Install the CLI and log in: `brew install railway && railway login`
2. From this folder: `railway init` (creates a project), then
   `railway add --database mysql`
3. `railway add --service web`, then in the dashboard set the `web`
   service's variables: `MYSQL_URL=${{MySQL.MYSQL_URL}}` and `APP_PASSWORD=...`
4. `railway up --service web` (builds the Dockerfile), then in the
   service's Settings → Networking click **Generate Domain**.
5. Share the URL and password with testers.

## Fare sources (`--fares`, goes before the subcommand)

| Spec | What it is |
|---|---|
| `synthetic` (default) | Deterministic distance-based model. **Not real prices.** For testing the pipeline only. |
| `travelpayouts` | Free cached fares (Aviasales Data API). Needs `TRAVELPAYOUTS_TOKEN`. One call per (origin, dest, month), cached 24 h in `~/.cache/gto/`. |
| `fixture:PATH` | Fares recorded in JSON (e.g. hand-copied from Google Flights). |

Chain with commas, first hit wins: `--fares fixture:manual.json,travelpayouts`.
Add `--save-fares out.json` to `plan` or `replay` to freeze every fare looked
up into a fixture, so a replay can be rerun later with identical prices.

```sh
export TRAVELPAYOUTS_TOKEN=...
python3 -m gto --fares travelpayouts replay trips/real --save-fares fares/2026-11-14.json
python3 -m gto --fares fixture:fares/2026-11-14.json replay trips/real   # reproducible rerun
```

## Running the Gate 1 test

1. Copy `trips/TEMPLATE.json` once per real past group trip into `trips/real/`.
2. Fill in places (lat/lng), length, each person's home airport(s), and if known
   what the group actually spent on flights + car (`actual_transport_usd`).
3. **Use a future date window.** Cached fares only exist for upcoming dates, so a
   past trip is replayed as "the same group, same places, same season, next
   year". The savings comparison (optimizer vs naive) is like-for-like on the
   same fares; the `vs actual` column is only indicative.
4. `python3 -m gto --fares travelpayouts replay trips/real --csv gate1.csv --save-fares fares/gate1.json`

## What's modelled (HLD section 7)

- Gateways: every airport in `gto/data/airports.csv` within `max_gateway_drive_h` of a
  place, scored by cached fares in both directions, top `max_gateways` kept (+ baseline airport).
- Skeletons: arrival gateway × departure gateway × optional second arrival /
  departure gateway × ground mode × place order × nights split × start date.
- Split gateways (HLD 7.6): part of the group can fly into (or out of) a second
  airport. Ground mode `detour` = one car fleet that picks them up on day 1 /
  drops them on the last day; `second_car` = two car groups, each with its own
  rental, drop fee and fuel, meeting at the first place. Turn off with
  `"split_gateways": false`.
- Tickets: each traveler gets the cheapest of one-way + one-way (any home airport
  each way) or a round-trip ticket (HLD 8.4), by price + λ × flight hours. The
  naive baseline gets round trips too, so savings aren't inflated. Turn off with
  `"round_trip_fares": false`.
- Cost(k) = flights + car (rate × days × cars) + one-way drop fee + fuel + lodging
  + λ × transit person-hours + penalty for days over the drive limit.
- Over-budget plans: rejected by default; `"enforce_budgets": false` keeps them,
  flagged (HLD 7.4).
- Hard constraints: traveler date limits, budgets (default fairness rule
  "own flights, shared ground"), no day over D + 1 hours of driving.
- Final three: lowest total, least travel time, fairest split, the last two within
  15% of the lowest total, each a distinct gateway pair.

## Known phase 0 gaps (intentional)

- **Drive times are estimates** (straight-line × 1.25 at 75 km/h). Override known
  pairs per trip with `drive_overrides`; OSRM replaces this later.
- **Drop fees** come from the trip's `drop_fees` table, else `drop_fee_default`
  (open question in HLD section 15).
- No arrival-time bins or arrival-spread check: cached fares lack reliable times.
- Split sub-groups always meet at the first place and part at the last place.
  In `second_car` mode a traveler's fleet is picked by their own fare + time and
  the car count follows from that, so the split isn't fully co-optimized with
  the number of cars. No "no car" mode (needs a transfer-cost source).
- No live pricing, no other fairness rules yet, no tolls / park fees.
- Travelpayouts round-trip queries (`one_way=false` + `return_at`) are tested
  against a mocked response only, like the one-way ones.
- `airports.csv` covers US airports with scheduled service; size (hub / medium / regional)
  is a rough class used only by the synthetic fare model.
- Travelpayouts integration is written against the v3 `prices_for_dates`
  endpoint and tested against a mocked response; it has not been run against the
  live API yet.
