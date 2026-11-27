// Checks that run while the form is being filled in.
// errors     = the optimizer would refuse the trip (Save is disabled)
// warnings   = it would run, but probably find no plan or a poor one
// incomplete = something isn't filled in yet (shown once the user tries to save)
import { driveHours } from "./airports.js";
import { addDays } from "./tripForm.js";

const DEFAULTS = { max_drive_h_per_day: 5, people_per_room: 2, seats_per_car: 5, car_daily_rate: 70 };

function coordsOf(p) {
  const m = String(p.coords || "").match(/^\(?\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*\)?$/);
  return m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
}

function permutations(arr) {
  if (arr.length <= 1) return [arr];
  return arr.flatMap((x, i) => permutations([...arr.slice(0, i), ...arr.slice(i + 1)]).map((p) => [x, ...p]));
}

const usd = (x) => "$" + Math.round(x).toLocaleString();
const fmt = (iso) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export function checkForm(form, airports, maxPlaces = 6) {
  const errors = [];
  const warnings = [];
  const incomplete = [];
  const param = (k) => {
    const v = Number(form.params[k]);
    return form.params[k] === "" || form.params[k] === undefined || Number.isNaN(v) ? DEFAULTS[k] : v;
  };
  const days = Number(form.length_days);
  const nights = days - 1;

  // Trip length and dates
  if (!(days >= 2)) errors.push("A trip needs at least 2 days.");
  if (!form.earliest_start || !form.latest_start) errors.push("Pick the earliest and latest start dates.");
  else if (form.latest_start < form.earliest_start) errors.push("The latest start date is before the earliest one.");
  else {
    const today = new Date().toISOString().slice(0, 10);
    if (form.earliest_start < today) warnings.push("The start dates are in the past. Fares only exist for future dates.");
    const span = (new Date(form.latest_start) - new Date(form.earliest_start)) / 86400000;
    if (span > 10) warnings.push(`That's ${span + 1} possible start dates. Searching will be slow; a window of 5–7 days is plenty.`);
  }

  // Places
  if (form.places.length > maxPlaces) errors.push(`At most ${maxPlaces} places for now.`);
  const unresolved = form.places.filter((p) => !coordsOf(p));
  if (unresolved.length) {
    incomplete.push(
      `Search for and pick each place: ${unresolved.map((p) => p.name.trim() || `place ${form.places.indexOf(p) + 1}`).join(", ")}.`
    );
  }
  const names = form.places.map((p) => p.name.trim().toLowerCase()).filter(Boolean);
  if (new Set(names).size !== names.length) errors.push("Two places have the same name. Rename one.");
  const minNights = form.places.reduce((s, p) => s + (Number(p.min_nights) || 0), 0);
  if (days >= 2 && minNights > nights) {
    errors.push(
      `The places need at least ${minNights} nights, but a ${days}-day trip has ${nights}. ` +
        `Make the trip ${minNights + 1} days or more, lower some minimum nights, or remove a place.`
    );
  }

  // Driving between places (the optimizer drives one leg per day)
  const limit = param("max_drive_h_per_day") + 1;
  const pts = form.places.map((p) => ({ name: p.name.trim() || "?", c: coordsOf(p) })).filter((p) => p.c);
  if (pts.length >= 2 && pts.length === form.places.length && pts.length <= 7) {
    const orders = form.ordered ? [pts.map((_, i) => i)] : permutations(pts.map((_, i) => i));
    let best = null;
    for (const order of orders) {
      const legs = order.slice(1).map((j, k) => ({ h: driveHours(pts[order[k]].c, pts[j].c), from: pts[order[k]].name, to: pts[j].name }));
      const worst = legs.reduce((a, b) => (b.h > a.h ? b : a));
      if (!best || worst.h < best.worst.h) best = { worst, legs };
    }
    if (best.worst.h > limit) {
      const long = best.legs.filter((l) => l.h > limit).map((l) => `${l.from} → ${l.to} (~${l.h.toFixed(1)}h)`);
      warnings.push(
        `${form.ordered ? "In this order" : "Even in the best order"} some days' drives are too long: ${long.join(", ")}. ` +
          `The limit is ${limit}h a day, so no plan will be found. Drop a place, add a stop in between, or raise "Max drive hours / day".`
      );
    }
  }

  // Travelers
  const byCode = new Set(airports.map((a) => a.code));
  const n = form.travelers.length;
  form.travelers.forEach((t, i) => {
    const who = t.name.trim() || `Traveler ${i + 1}`;
    const codes = t.origins.split(/[\s,]+/).map((o) => o.trim().toUpperCase()).filter(Boolean);
    if (!codes.length) incomplete.push(`${who} needs at least one home airport.`);
    const unknown = codes.filter((c) => byCode.size && !byCode.has(c));
    if (unknown.length) errors.push(`${who}: unknown airport ${unknown.join(", ")}. Search by city or name instead.`);

    if (form.earliest_start && form.latest_start && days >= 2) {
      let fits = false;
      for (let s = form.earliest_start; s <= form.latest_start; s = addDays(s, 1)) {
        const end = addDays(s, days - 1);
        if ((!t.earliest_depart || s >= t.earliest_depart) && (!t.latest_return || end <= t.latest_return)) {
          fits = true;
          break;
        }
      }
      if (!fits) {
        const limits = [
          t.earliest_depart && `can't leave before ${fmt(t.earliest_depart)}`,
          t.latest_return && `must be back by ${fmt(t.latest_return)}`,
        ].filter(Boolean);
        warnings.push(
          `${who} ${limits.join(" and ")}, which doesn't fit a ${days}-day trip starting ${fmt(form.earliest_start)}–${fmt(form.latest_start)}. ` +
            "No plan can work. Fix or clear those dates."
        );
      }
    }
  });

  // Budgets vs the part of the trip everyone pays no matter what
  if (form.enforce_budgets && n > 0 && days >= 2 && minNights <= nights) {
    const rooms = Math.ceil(n / param("people_per_room"));
    const prices = form.places.map((p) => Number(p.lodging_per_night) || 0);
    const cheapest = prices.length ? Math.min(...prices) : 0;
    const lodging =
      rooms * (form.places.reduce((s, p) => s + (Number(p.min_nights) || 0) * (Number(p.lodging_per_night) || 0), 0) +
        (nights - minNights) * cheapest);
    const car = Math.ceil(n / param("seats_per_car")) * param("car_daily_rate") * days;
    const floor = (lodging + car) / n;
    form.travelers.forEach((t, i) => {
      const b = Number(t.budget);
      if (t.budget !== "" && t.budget !== null && b < floor) {
        warnings.push(
          `${t.name.trim() || `Traveler ${i + 1}`}'s ${usd(b)} budget is less than the ~${usd(floor)} each person pays ` +
            "for lodging and the car alone, before flights. Every plan will be ruled out."
        );
      }
    });
  }

  return { errors, warnings, incomplete };
}
