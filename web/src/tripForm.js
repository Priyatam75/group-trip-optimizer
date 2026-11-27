// Conversion between the optimizer's trip JSON and the form's editable state.

const DAY = 86400000;

export function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  return new Date(d.getTime() + n * DAY).toISOString().slice(0, 10);
}

export const PARAM_FIELDS = [
  ["lambda_per_hour", "Value of 1 travel hour ($)", 25],
  ["max_drive_h_per_day", "Max drive hours / day", 5],
  ["max_gateway_drive_h", "Max drive from airport (h)", 6],
  ["max_gateways", "Airports to consider", 6],
  ["car_daily_rate", "Car rental $/day", 70],
  ["seats_per_car", "Seats per car", 5],
  ["drop_fee_default", "One-way drop fee ($)", 250],
  ["fuel_price_per_gal", "Fuel $/gal", 3.8],
  ["mpg", "Car MPG", 25],
  ["people_per_room", "People per room", 2],
];

// lodging_source says where lodging_per_night came from, so automatic updates never
// overwrite a number the user typed: "default" ($180 guess) | "estimate" (typical
// price near the place) | "hotel" (picked on the map) | "manual" (typed).
export function newPlace() {
  return { name: "", coords: "", label: "", stay: null, min_nights: 1, lodging_per_night: 180,
    lodging_source: "default", estimate: null };
}

export function emptyForm() {
  const start = addDays(new Date().toISOString().slice(0, 10), 60);
  return {
    name: "",
    notes: "",
    ordered: false,
    length_days: 5,
    earliest_start: addDays(start, -2),
    latest_start: addDays(start, 2),
    places: [newPlace()],
    travelers: [{ name: "", origins: "", budget: "", earliest_depart: "", latest_return: "" }],
    params: {},
    enforce_budgets: true,
    split_gateways: true,
    round_trip_fares: true,
    extra: {},
  };
}

export function inputToForm(input) {
  const win = input.date_window || {};
  let earliest = win.earliest_start;
  let latest = win.latest_start;
  if (win.start) {
    const flex = win.flex_days ?? 2;
    earliest = addDays(win.start, -flex);
    latest = addDays(win.start, flex);
  }
  const { enforce_budgets = true, split_gateways = true, round_trip_fares = true, ...params } = input.params || {};
  return {
    name: input.name || "",
    notes: input.notes || "",
    ordered: !!input.ordered,
    length_days: input.length_days || 5,
    earliest_start: earliest || "",
    latest_start: latest || "",
    places: (input.places || []).map((p) => ({
      name: p.name,
      // With a hotel picked, lat/lng are the hotel's; `area` keeps the town for searching again.
      coords: p.area ? `${p.area.lat}, ${p.area.lng}` : `${p.lat}, ${p.lng}`,
      label: p.area?.label || p.name,
      stay: p.stay || null,
      min_nights: p.min_nights ?? 1,
      lodging_per_night: p.lodging_per_night ?? 180,
      lodging_source: p.lodging_source || (p.stay ? "hotel" : "manual"),
      estimate: p.lodging_estimate || null,
    })),
    travelers: (input.travelers || []).map((t) => ({
      name: t.name,
      origins: (t.origins || []).join(", "),
      budget: t.budget ?? "",
      earliest_depart: t.earliest_depart || "",
      latest_return: t.latest_return || "",
    })),
    params,
    enforce_budgets,
    split_gateways,
    round_trip_fares,
    // Fields the form doesn't edit directly; kept so they survive a save.
    extra: {
      drive_overrides: input.drive_overrides || {},
      drop_fees: input.drop_fees || {},
      actual_transport_usd: input.actual_transport_usd ?? null,
    },
  };
}

function parseCoords(s, name) {
  const m = String(s).trim().match(/^\(?\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*\)?$/);
  if (!m) throw new Error(`Place "${name || "?"}": coordinates should look like "43.48, -110.76"`);
  return [parseFloat(m[1]), parseFloat(m[2])];
}

const num = (v) => (v === "" || v === null || v === undefined ? undefined : Number(v));

export function formToInput(f) {
  const places = f.places.map((p, i) => {
    const name = p.name.trim() || `Place ${i + 1}`;
    const [lat, lng] = parseCoords(p.coords, name);
    const out = { name, lat, lng, min_nights: Number(p.min_nights) || 1, lodging_per_night: Number(p.lodging_per_night) || 0,
      lodging_source: p.lodging_source || "manual" };
    if (p.estimate) out.lodging_estimate = p.estimate;
    if (p.stay) {
      // Drive times are measured from the hotel; the town is kept so the map can search again.
      out.area = { lat, lng, label: p.label || name };
      out.lat = p.stay.lat;
      out.lng = p.stay.lng;
      out.stay = p.stay;
    }
    return out;
  });
  const travelers = f.travelers.map((t, i) => {
    const out = {
      name: t.name.trim() || `Traveler ${i + 1}`,
      origins: t.origins.split(/[\s,]+/).map((o) => o.trim().toUpperCase()).filter(Boolean),
    };
    if (num(t.budget) !== undefined) out.budget = num(t.budget);
    if (t.earliest_depart) out.earliest_depart = t.earliest_depart;
    if (t.latest_return) out.latest_return = t.latest_return;
    return out;
  });
  const params = {};
  for (const [k, v] of Object.entries(f.params)) {
    if (num(v) !== undefined) params[k] = num(v);
  }
  params.enforce_budgets = f.enforce_budgets;
  params.split_gateways = f.split_gateways;
  params.round_trip_fares = f.round_trip_fares;
  return {
    name: f.name.trim() || "Untitled trip",
    notes: f.notes,
    places,
    ordered: f.ordered,
    length_days: Number(f.length_days),
    date_window: { earliest_start: f.earliest_start, latest_start: f.latest_start },
    travelers,
    params,
    ...f.extra,
  };
}
