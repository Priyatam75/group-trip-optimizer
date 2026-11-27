import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from gto.airports import Airport, load_airports
from gto.fares import FixtureFares, MemoFares, SyntheticFares, TravelpayoutsFares
from gto.models import trip_from_dict
from gto.optimizer import night_allocations, search

ROOT = Path(__file__).resolve().parent.parent

# A tiny world: a cheap far hub (HUB), an expensive near regional (REG), two home cities.
AIRPORTS = {
    "HUB": Airport("HUB", "Hub", 40.0, -100.0, "hub"),
    "REG": Airport("REG", "Regional", 41.0, -100.0, "regional"),
    "AAA": Airport("AAA", "Home A", 40.0, -90.0, "hub"),
    "BBB": Airport("BBB", "Home B", 40.0, -110.0, "hub"),
}


def tiny_trip(**over):
    d = {
        "name": "tiny",
        "places": [{"name": "Park", "lat": 41.05, "lng": -100.0, "min_nights": 2, "lodging_per_night": 100}],
        "length_days": 3,
        "date_window": {"earliest_start": "2027-06-01", "latest_start": "2027-06-01"},
        "travelers": [{"name": "A", "origins": ["AAA"]}, {"name": "B", "origins": ["BBB"]}],
        "params": {"drop_fee_default": 100, "max_gateway_drive_h": 3},
    }
    d.update(over)
    return trip_from_dict(d)


def fixture(rows):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"fares": rows}, tmp)
    tmp.close()
    return FixtureFares(tmp.name)


def fare(o, d, day, price, dur=2.0):
    return {"origin": o, "dest": d, "date": day, "price": price, "duration_h": dur}


class NightAllocationTests(unittest.TestCase):
    def test_respects_minimums_and_total(self):
        allocs = list(night_allocations(5, [2, 1]))
        self.assertEqual(allocs, [(2, 3), (3, 2), (4, 1)])

    def test_impossible(self):
        self.assertEqual(list(night_allocations(2, [2, 1])), [])


class SearchTests(unittest.TestCase):
    def test_finds_cheaper_far_hub_and_reports_savings(self):
        fares = fixture([
            fare("AAA", "REG", "2027-06-01", 400), fare("REG", "AAA", "2027-06-03", 400),
            fare("BBB", "REG", "2027-06-01", 400), fare("REG", "BBB", "2027-06-03", 400),
            fare("AAA", "HUB", "2027-06-01", 150), fare("HUB", "AAA", "2027-06-03", 150),
            fare("BBB", "HUB", "2027-06-01", 150), fare("HUB", "BBB", "2027-06-03", 150),
        ])
        res = search(tiny_trip(), AIRPORTS, fares)
        self.assertEqual(res.baseline_airport, "REG")
        self.assertEqual(res.best.skeleton.gateway_pair, ("HUB", "HUB"))
        self.assertGreater(res.transport_savings_pct, 0.3)
        # Every plan uses a distinct set of airports.
        keys = [p.skeleton.gateways_key for p in res.plans]
        self.assertEqual(len(keys), len(set(keys)))

    def test_open_jaw_pays_drop_fee(self):
        fares = fixture([
            fare("AAA", "HUB", "2027-06-01", 100), fare("BBB", "HUB", "2027-06-01", 100),
            fare("REG", "AAA", "2027-06-03", 100), fare("REG", "BBB", "2027-06-03", 100),
        ])
        res = search(tiny_trip(), AIRPORTS, fares)
        best = res.best
        self.assertEqual(best.skeleton.gateway_pair, ("HUB", "REG"))
        self.assertEqual(best.drop_fee, 100)

    def test_budget_is_hard_constraint(self):
        fares = fixture([
            fare("AAA", "REG", "2027-06-01", 400), fare("REG", "AAA", "2027-06-03", 400),
            fare("BBB", "REG", "2027-06-01", 400), fare("REG", "BBB", "2027-06-03", 400),
        ])
        trip = tiny_trip(travelers=[{"name": "A", "origins": ["AAA"], "budget": 500},
                                    {"name": "B", "origins": ["BBB"]}])
        res = search(trip, AIRPORTS, fares)
        self.assertEqual(res.plans, [])
        self.assertIsNotNone(res.baseline)  # the naive baseline ignores budgets
        self.assertGreater(res.stats["rejected: over budget"], 0)

    def test_traveler_dates_enforced(self):
        fares = SyntheticFares(AIRPORTS)
        trip = tiny_trip(
            date_window={"earliest_start": "2027-06-01", "latest_start": "2027-06-03"},
            travelers=[{"name": "A", "origins": ["AAA"], "earliest_depart": "2027-06-02"},
                       {"name": "B", "origins": ["BBB"]}],
        )
        res = search(trip, AIRPORTS, fares)
        self.assertTrue(res.plans)
        self.assertTrue(all(p.skeleton.start >= date(2027, 6, 2) for p in res.all_plans))

    def test_cost_identity(self):
        res = search(tiny_trip(), AIRPORTS, SyntheticFares(AIRPORTS))
        for p in res.all_plans:
            self.assertAlmostEqual(sum(m.share for m in p.members), p.money_total, places=6)
            self.assertAlmostEqual(
                p.objective, p.money_total + p.lambda_per_hour * p.transit_hours + p.penalty, places=6
            )

    def test_over_budget_plans_kept_and_flagged_when_allowed(self):
        fares = fixture([
            fare("AAA", "REG", "2027-06-01", 400), fare("REG", "AAA", "2027-06-03", 400),
            fare("BBB", "REG", "2027-06-01", 400), fare("REG", "BBB", "2027-06-03", 400),
        ])
        trip = tiny_trip(
            travelers=[{"name": "A", "origins": ["AAA"], "budget": 500}, {"name": "B", "origins": ["BBB"]}],
            params={"drop_fee_default": 100, "max_gateway_drive_h": 3, "enforce_budgets": False},
        )
        res = search(trip, AIRPORTS, fares)
        self.assertTrue(res.plans)
        self.assertTrue(res.best.members[0].over_budget)


def split_fares(extra=()):
    """A is cheap only into HUB, B is cheap only into REG."""
    return [
        fare("AAA", "HUB", "2027-06-01", 100), fare("HUB", "AAA", "2027-06-03", 100),
        fare("AAA", "REG", "2027-06-01", 500), fare("REG", "AAA", "2027-06-03", 500),
        fare("BBB", "REG", "2027-06-01", 100), fare("REG", "BBB", "2027-06-03", 100),
        fare("BBB", "HUB", "2027-06-01", 500), fare("HUB", "BBB", "2027-06-03", 500),
        *extra,
    ]


class SplitGatewayTests(unittest.TestCase):
    def test_each_subgroup_uses_its_cheap_airport(self):
        res = search(tiny_trip(), AIRPORTS, fixture(split_fares()))
        best = res.best
        self.assertTrue(best.skeleton.split)
        by_name = {m.name: m for m in best.members}
        self.assertEqual(by_name["A"].out.dest, "HUB")
        self.assertEqual(by_name["B"].out.dest, "REG")
        self.assertEqual(best.flights, 400)
        # Two people fit in one car, so picking B up on the way beats renting a second car.
        self.assertEqual(best.skeleton.mode, "detour")
        self.assertEqual(best.n_cars, 1)
        self.assertEqual({tr.airport for tr in best.arrivals}, {"HUB", "REG"})
        # The car's day-1 drive goes via the pickup airport.
        hub_track = next(tr for tr in best.arrivals if tr.airport == "HUB")
        self.assertEqual(hub_track.path, ["HUB", "REG", "Park"])
        self.assertEqual(hub_track.cars, 1)

    def test_second_car_when_seats_force_it(self):
        trip = tiny_trip(params={"drop_fee_default": 100, "max_gateway_drive_h": 3, "seats_per_car": 1})
        res = search(trip, AIRPORTS, fixture(split_fares()))
        best = res.best
        # One seat per car: a single fleet needs 2 cars anyway, so each person drives from their own airport.
        self.assertEqual(best.skeleton.mode, "second_car")
        self.assertEqual(best.n_cars, 2)
        self.assertEqual(best.flights, 400)
        self.assertEqual(best.drop_fee, 0)

    def test_split_can_be_turned_off(self):
        trip = tiny_trip(params={"drop_fee_default": 100, "max_gateway_drive_h": 3, "split_gateways": False})
        res = search(trip, AIRPORTS, fixture(split_fares()))
        self.assertTrue(all(not p.skeleton.split for p in res.all_plans))
        # Without a split, someone always flies a $500 leg each way: 2 × 100 + 2 × 500.
        self.assertEqual(res.best.flights, 1200)

    def test_split_beats_every_single_airport_plan(self):
        res = search(tiny_trip(), AIRPORTS, fixture(split_fares()))
        single = [p for p in res.all_plans if not p.skeleton.split]
        self.assertLess(res.best.objective, min(p.objective for p in single))


class WhyNoPlanTests(unittest.TestCase):
    def test_traveler_dates_explained(self):
        trip = tiny_trip(travelers=[
            {"name": "A", "origins": ["AAA"], "latest_return": "2027-05-01"},
            {"name": "B", "origins": ["BBB"]},
        ])
        res = search(trip, AIRPORTS, SyntheticFares(AIRPORTS))
        self.assertEqual(res.plans, [])
        self.assertTrue(any("A must be back by May 01 2027" in r for r in res.why_no_plan), res.why_no_plan)
        self.assertTrue(any("Breaks travel dates for A" in w for w in res.baseline_warnings))

    def test_budget_explained_with_cheapest_share(self):
        fares = fixture([
            fare("AAA", "REG", "2027-06-01", 400), fare("REG", "AAA", "2027-06-03", 400),
            fare("BBB", "REG", "2027-06-01", 400), fare("REG", "BBB", "2027-06-03", 400),
        ])
        trip = tiny_trip(travelers=[{"name": "A", "origins": ["AAA"], "budget": 500},
                                    {"name": "B", "origins": ["BBB"]}])
        res = search(trip, AIRPORTS, fares)
        reason = next(r for r in res.why_no_plan if r.startswith("A's budget"))
        # Cheapest plan for A: $800 flights + half of car (3 days × $70), fuel and 2 nights' lodging.
        self.assertIn("$500", reason)
        self.assertRegex(reason, r"about \$1,0\d\d")

    def test_long_drive_explained(self):
        places = [
            {"name": "Park", "lat": 41.05, "lng": -100.0, "min_nights": 1},
            {"name": "Far", "lat": 46.0, "lng": -100.0, "min_nights": 1},  # ~550 km north
        ]
        trip = tiny_trip(places=places, params={"max_gateway_drive_h": 20})
        res = search(trip, AIRPORTS, SyntheticFares(AIRPORTS))
        self.assertEqual(res.plans, [])
        self.assertTrue(any("one day's drive is" in r and "Park" in r and "Far" in r for r in res.why_no_plan),
                        res.why_no_plan)

    def test_no_reasons_when_plans_exist(self):
        res = search(tiny_trip(), AIRPORTS, SyntheticFares(AIRPORTS))
        self.assertTrue(res.plans)
        self.assertEqual(res.why_no_plan, [])


class RoundTripTests(unittest.TestCase):
    def rt(self, o, d, out_day, ret_day, price):
        return {"origin": o, "dest": d, "date": out_day, "return_date": ret_day, "price": price,
                "duration_h": 2.0, "return_duration_h": 2.0}

    def fares(self):
        return fixture([
            fare("AAA", "REG", "2027-06-01", 200), fare("REG", "AAA", "2027-06-03", 200),
            fare("BBB", "REG", "2027-06-01", 200), fare("REG", "BBB", "2027-06-03", 200),
            self.rt("AAA", "REG", "2027-06-01", "2027-06-03", 300),
        ])

    def test_cheaper_round_trip_is_used(self):
        res = search(tiny_trip(), AIRPORTS, self.fares())
        a = next(m for m in res.best.members if m.name == "A")
        b = next(m for m in res.best.members if m.name == "B")
        self.assertTrue(a.round_trip)
        self.assertEqual(a.flights, 300)
        self.assertFalse(b.round_trip)
        self.assertEqual(b.flights, 400)
        # The naive baseline buys the round trip too, so savings aren't inflated.
        self.assertEqual(next(m for m in res.baseline.members if m.name == "A").flights, 300)

    def test_round_trips_can_be_turned_off(self):
        trip = tiny_trip(params={"drop_fee_default": 100, "max_gateway_drive_h": 3, "round_trip_fares": False})
        res = search(trip, AIRPORTS, self.fares())
        self.assertTrue(all(not m.round_trip for m in res.best.members))

    def test_synthetic_round_trip_is_deterministic(self):
        s = SyntheticFares(AIRPORTS)
        a = s.round_trip("AAA", "HUB", date(2027, 6, 1), date(2027, 6, 3))
        self.assertEqual(a, s.round_trip("AAA", "HUB", date(2027, 6, 1), date(2027, 6, 3)))
        self.assertIsNone(s.round_trip("HUB", "REG", date(2027, 6, 1), date(2027, 6, 3)))


class FareSourceTests(unittest.TestCase):
    def test_synthetic_is_deterministic(self):
        s = SyntheticFares(AIRPORTS)
        a = s.cheapest("AAA", "HUB", date(2027, 6, 1))
        b = s.cheapest("AAA", "HUB", date(2027, 6, 1))
        self.assertEqual(a, b)
        self.assertIsNone(s.cheapest("HUB", "REG", date(2027, 6, 1)))  # too close to fly

    def test_memo_dump_round_trips(self):
        memo = MemoFares(SyntheticFares(AIRPORTS))
        memo.cheapest("AAA", "HUB", date(2027, 6, 1))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.json"
            self.assertEqual(memo.dump_fixture(path), 1)
            again = FixtureFares(path, AIRPORTS).cheapest("AAA", "HUB", date(2027, 6, 1))
        self.assertEqual(again.price, memo.cheapest("AAA", "HUB", date(2027, 6, 1)).price)

    def test_memo_dump_includes_round_trips(self):
        memo = MemoFares(SyntheticFares(AIRPORTS))
        rt = memo.round_trip("AAA", "HUB", date(2027, 6, 1), date(2027, 6, 3))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.json"
            memo.dump_fixture(path)
            again = FixtureFares(path, AIRPORTS).round_trip("AAA", "HUB", date(2027, 6, 1), date(2027, 6, 3))
        self.assertEqual(again.price, rt.price)

    def test_travelpayouts_round_trip_query(self):
        calls = []
        payload = {
            "success": True,
            "data": [
                {"departure_at": "2027-06-01T08:00:00-05:00", "return_at": "2027-06-06T18:00:00-06:00",
                 "price": 320, "duration_to": 200, "duration_back": 210},
                {"departure_at": "2027-06-01T09:00:00-05:00", "return_at": "2027-06-06T10:00:00-06:00",
                 "price": 290, "duration_to": 260, "duration_back": 250},
            ],
        }

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url, timeout):
            calls.append(url)
            return Resp(json.dumps(payload).encode())

        with tempfile.TemporaryDirectory() as d:
            tp = TravelpayoutsFares("tok", d, AIRPORTS, opener=opener)
            rt = tp.round_trip("AAA", "HUB", date(2027, 6, 1), date(2027, 6, 6))
            self.assertEqual(rt.price, 290.0)
            self.assertAlmostEqual(rt.out_duration_h, 260 / 60)
            self.assertIsNone(tp.round_trip("AAA", "HUB", date(2027, 6, 2), date(2027, 6, 6)))
            self.assertEqual(len(calls), 1)
            self.assertIn("one_way=false", calls[0])
            self.assertIn("return_at=2027-06", calls[0])

    def test_travelpayouts_parses_and_caches(self):
        calls = []
        payload = {
            "success": True,
            "data": [
                {"departure_at": "2027-06-01T08:00:00-05:00", "price": 210, "duration_to": 185, "transfers": 1},
                {"departure_at": "2027-06-01T15:00:00-05:00", "price": 180, "duration_to": 240, "transfers": 1},
                {"departure_at": "2027-06-02T09:00:00-05:00", "price": 99, "duration": 120, "transfers": 0},
            ],
        }

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url, timeout):
            calls.append(url)
            return Resp(json.dumps(payload).encode())

        with tempfile.TemporaryDirectory() as d:
            tp = TravelpayoutsFares("tok", d, AIRPORTS, opener=opener)
            f = tp.cheapest("AAA", "HUB", date(2027, 6, 1))
            self.assertEqual((f.price, f.duration_h), (180.0, 4.0))
            self.assertEqual(tp.cheapest("AAA", "HUB", date(2027, 6, 2)).price, 99.0)
            self.assertIsNone(tp.cheapest("AAA", "HUB", date(2027, 6, 3)))
            self.assertEqual(len(calls), 1)  # one call per (origin, dest, month)
            self.assertIn("departure_at=2027-06", calls[0])
            # A fresh instance reads the disk cache instead of calling again.
            tp2 = TravelpayoutsFares("tok", d, AIRPORTS, opener=opener)
            tp2.cheapest("AAA", "HUB", date(2027, 6, 1))
            self.assertEqual(len(calls), 1)


class ExampleTripTests(unittest.TestCase):
    def test_examples_run_end_to_end(self):
        airports = load_airports()
        from gto.models import load_trip

        for f in sorted((ROOT / "trips" / "examples").glob("*.json")):
            res = search(load_trip(f), airports, SyntheticFares(airports))
            self.assertTrue(res.plans, f.name)
            self.assertIsNotNone(res.baseline, f.name)
            self.assertLess(res.elapsed_s, 20, f.name)


if __name__ == "__main__":
    unittest.main()
