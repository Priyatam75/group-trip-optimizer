import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import AirportPicker from "./AirportPicker.jsx";
import PlaceSearch from "./PlaceSearch.jsx";
import Results from "./Results.jsx";
import StayPicker from "./StayPicker.jsx";
import { checkForm } from "./checks.js";
import { PARAM_FIELDS, addDays, emptyForm, formToInput, inputToForm, newPlace } from "./tripForm.js";

export default function App() {
  const [config, setConfig] = useState(null);
  const [airports, setAirports] = useState([]);
  const [examples, setExamples] = useState([]);
  const [trips, setTrips] = useState([]);
  const [tripId, setTripId] = useState(null);
  const [runs, setRuns] = useState([]);
  const [form, setForm] = useState(emptyForm());
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [me, setMe] = useState(() => localStorage.getItem("gto-name") || "");
  const [attempted, setAttempted] = useState(false); // show "not filled in yet" only after a save attempt
  const [picking, setPicking] = useState(null); // index of the place whose hotel map is open

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(e.message));
    api.airports().then(setAirports);
    api.examples().then(setExamples);
    refreshTrips();
    const id = Number(new URLSearchParams(location.search).get("trip"));
    if (id) openTrip(id);
  }, []);

  useEffect(() => localStorage.setItem("gto-name", me), [me]);

  // Hotel prices are looked up for one representative stay per place: the middle of the
  // start window, places in listed order, minimum nights each. The optimizer then
  // treats that nightly price as the place's lodging cost.
  const stayDates = useMemo(() => {
    if (!form.earliest_start || !form.latest_start) return form.places.map(() => ({}));
    const span = Math.max(0, (new Date(form.latest_start) - new Date(form.earliest_start)) / 86400000);
    let day = addDays(form.earliest_start, Math.floor(span / 2));
    return form.places.map((p) => {
      const n = Math.max(1, Number(p.min_nights) || 1);
      const d = { checkin: day, checkout: addDays(day, n) };
      day = d.checkout;
      return d;
    });
  }, [form.earliest_start, form.latest_start, form.places]);
  const stayNames = useMemo(
    () => Object.fromEntries(form.places.filter((p) => p.stay).map((p) => [p.name, p.stay.name])),
    [form.places]
  );

  // Fill each place's lodging price with the typical (median) nightly price nearby, until
  // the user picks a hotel or types a price. Re-runs when the place or its dates change.
  const estimating = useRef(new Set());
  const failedAt = useRef(new Map()); // don't retry a failed lookup on every keystroke
  useEffect(() => {
    if (!config?.hotel_prices) return;
    const adults = Number(form.params.people_per_room) || 2;
    form.places.forEach((p, i) => {
      const d = stayDates[i];
      if (!p.coords || p.stay || !d?.checkin) return;
      if (p.lodging_source !== "default" && p.lodging_source !== "estimate") return;
      const e = p.estimate;
      if (e && e.coords === p.coords && e.checkin === d.checkin && e.checkout === d.checkout) return;
      const key = `${p.coords}|${d.checkin}|${d.checkout}`;
      if (estimating.current.has(key)) return;
      if (Date.now() - (failedAt.current.get(key) || 0) < 60000) return;
      estimating.current.add(key);
      const [lat, lng] = coordsOf(p.coords);
      api
        .lodging({ lat, lng, checkin: d.checkin, checkout: d.checkout, adults })
        .then((res) => {
          const est = {
            per_night: res.typical_per_night,
            count: res.priced_count,
            hotels: res.hotels.length,
            checkin: res.checkin,
            checkout: res.checkout,
            sandbox: res.sandbox,
            coords: p.coords,
          };
          setForm((f) => ({
            ...f,
            places: f.places.map((q) => {
              // Only update if nothing changed meanwhile (same place, still not picked or typed).
              if (q.coords !== p.coords || q.stay || !["default", "estimate"].includes(q.lodging_source)) return q;
              return res.typical_per_night
                ? { ...q, estimate: est, lodging_source: "estimate", lodging_per_night: Math.round(res.typical_per_night) }
                : { ...q, estimate: est, lodging_source: "default" };
            }),
          }));
        })
        .catch(() => failedAt.current.set(key, Date.now())) // price stays the guess; the hotel map shows the error
        .finally(() => estimating.current.delete(key));
    });
  }, [form.places, stayDates, config]);

  const checks = useMemo(() => checkForm(form, airports, config?.max_places ?? 6), [form, airports, config]);

  function refreshTrips() {
    api.trips().then(setTrips).catch((e) => setError(e.message));
  }

  function setUrl(id) {
    history.replaceState(null, "", id ? `?trip=${id}` : location.pathname);
  }

  async function openTrip(id) {
    setError("");
    try {
      const t = await api.trip(id);
      setTripId(t.id);
      setForm(inputToForm(t.input));
      setRun(t.latest_run);
      setRuns(t.runs);
      setUrl(t.id);
    } catch (e) {
      setError(e.message);
    }
  }

  function newTrip(input) {
    setAttempted(false);
    setTripId(null);
    setRun(null);
    setRuns([]);
    setForm(input ? inputToForm(input) : emptyForm());
    setUrl(null);
    setError("");
  }

  async function save(asNew = false) {
    setError("");
    if (checks.incomplete.length) {
      setAttempted(true);
      return;
    }
    let input;
    try {
      input = formToInput(form);
    } catch (e) {
      setError(e.message);
      return;
    }
    setBusy(true);
    try {
      const t = tripId && !asNew ? await api.update(tripId, input, me) : await api.create(input, me);
      setTripId(t.id);
      setRun(t.latest_run);
      setUrl(t.id);
      const full = await api.trip(t.id);
      setRuns(full.runs);
      refreshTrips();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!tripId || !confirm("Delete this trip and all its runs?")) return;
    await api.remove(tripId);
    newTrip();
    refreshTrips();
  }

  const upd = (patch) => setForm((f) => ({ ...f, ...patch }));
  const updList = (key, i, patch) =>
    setForm((f) => ({ ...f, [key]: f[key].map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  const addRow = (key, row) => setForm((f) => ({ ...f, [key]: [...f[key], row] }));
  const delRow = (key, i) => setForm((f) => ({ ...f, [key]: f[key].filter((_, j) => j !== i) }));

  const nights = Number(form.length_days) - 1;
  const minNights = form.places.reduce((s, p) => s + (Number(p.min_nights) || 0), 0);

  return (
    <div className="app">
      <aside>
        <h1>Group Trip Optimizer</h1>
        <div className="muted small">Prototype · fares: {config?.fare_source ?? "…"}</div>
        <label className="field">
          Your name
          <input value={me} onChange={(e) => setMe(e.target.value)} placeholder="so others know who saved what" />
        </label>
        <button className="primary wide" onClick={() => newTrip()}>
          + New trip
        </button>
        <select
          className="wide"
          value=""
          onChange={(e) => {
            const ex = examples.find((x) => x.id === e.target.value);
            if (ex) newTrip(ex.input);
          }}
        >
          <option value="">Start from an example…</option>
          {examples.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>

        <h4>Saved trips</h4>
        {trips.length === 0 && <div className="muted small">None yet.</div>}
        <ul className="trip-list">
          {trips.map((t) => (
            <li key={t.id} className={t.id === tripId ? "active" : ""} onClick={() => openTrip(t.id)}>
              <div>{t.name}</div>
              <div className="muted small">
                {t.created_by || "anon"} · {new Date(t.updated_at + "Z").toLocaleDateString()}
                {t.last_savings_pct !== null && ` · saves ${t.last_savings_pct.toFixed(0)}%`}
              </div>
            </li>
          ))}
        </ul>
      </aside>

      <main>
        <div className="card">
          <div className="row">
            <label className="field grow">
              Trip name
              <input value={form.name} onChange={(e) => upd({ name: e.target.value })} placeholder="e.g. Tetons 2027" />
            </label>
            <label className="field">
              Trip length (days)
              <input
                type="number"
                min="2"
                value={form.length_days}
                onChange={(e) => upd({ length_days: e.target.value })}
              />
            </label>
            <label className="field">
              Earliest start
              <input type="date" value={form.earliest_start} onChange={(e) => upd({ earliest_start: e.target.value })} />
            </label>
            <label className="field">
              Latest start
              <input type="date" value={form.latest_start} onChange={(e) => upd({ latest_start: e.target.value })} />
            </label>
          </div>

          <h4>
            Places <span className="muted small">({nights} nights to split; minimums add up to {minNights})</span>
          </h4>
          <div className="muted small">
            Search for each place. For parks, the town you'll stay in gives the most accurate drive times.
          </div>
          <table className="edit">
            <thead>
              <tr>
                <th>Place</th>
                <th>Min nights</th>
                <th>Lodging $/room/night</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.places.map((p, i) => (
                <tr key={i}>
                  <td className="wide-cell">
                    <PlaceSearch
                      place={p}
                      onChange={(patch) => {
                        // A different place: drop the old estimate unless the user typed the price.
                        const reset =
                          "coords" in patch && patch.coords !== p.coords && p.lodging_source !== "manual"
                            ? { estimate: null, lodging_source: "default", lodging_per_night: 180 }
                            : {};
                        updList("places", i, { ...patch, ...reset });
                      }}
                    />
                    {p.coords && (
                      <StayLine
                        place={p}
                        enabled={!!config?.hotel_prices}
                        onOpen={() => setPicking(i)}
                        onClear={() =>
                          updList("places", i, {
                            stay: null,
                            // Back to the area estimate (or the default guess) unless the price was typed.
                            ...(p.lodging_source === "manual"
                              ? {}
                              : p.estimate?.per_night
                              ? { lodging_source: "estimate", lodging_per_night: Math.round(p.estimate.per_night) }
                              : { lodging_source: "default", lodging_per_night: 180 }),
                          })
                        }
                      />
                    )}
                  </td>
                  <td>
                    <input
                      type="number"
                      min="1"
                      value={p.min_nights}
                      onChange={(e) => updList("places", i, { min_nights: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      value={p.lodging_per_night}
                      onChange={(e) => updList("places", i, { lodging_per_night: e.target.value, lodging_source: "manual" })}
                    />
                    <LodgingSource place={p} pricesOn={!!config?.hotel_prices} />
                  </td>
                  <td>
                    {form.places.length > 1 && (
                      <button className="link" onClick={() => delRow("places", i)}>
                        remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row">
            <button
              onClick={() => addRow("places", newPlace())}
              disabled={config && form.places.length >= config.max_places}
            >
              + Add place
            </button>
            <label className="check">
              <input type="checkbox" checked={form.ordered} onChange={(e) => upd({ ordered: e.target.checked })} />
              Visit in this exact order
            </label>
          </div>

          <h4>Travelers</h4>
          <table className="edit">
            <thead>
              <tr>
                <th>Name</th>
                <th>Home airports (any of)</th>
                <th>Budget $ (optional)</th>
                <th>Can't leave before</th>
                <th>Must be back by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.travelers.map((t, i) => {
                return (
                  <tr key={i}>
                    <td>
                      <input value={t.name} onChange={(e) => updList("travelers", i, { name: e.target.value })} />
                    </td>
                    <td className="wide-cell">
                      <AirportPicker
                        value={t.origins}
                        airports={airports}
                        onChange={(origins) => updList("travelers", i, { origins })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        value={t.budget}
                        onChange={(e) => updList("travelers", i, { budget: e.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="date"
                        value={t.earliest_depart}
                        onChange={(e) => updList("travelers", i, { earliest_depart: e.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        type="date"
                        value={t.latest_return}
                        onChange={(e) => updList("travelers", i, { latest_return: e.target.value })}
                      />
                    </td>
                    <td>
                      {form.travelers.length > 1 && (
                        <button className="link" onClick={() => delRow("travelers", i)}>
                          remove
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <button
            onClick={() =>
              addRow("travelers", { name: "", origins: "", budget: "", earliest_depart: "", latest_return: "" })
            }
          >
            + Add traveler
          </button>

          <details>
            <summary>Assumptions (car, fuel, rooms, drive limits)</summary>
            <div className="params">
              {PARAM_FIELDS.map(([key, label, def]) => (
                <label className="field" key={key}>
                  {label}
                  <input
                    type="number"
                    step="any"
                    value={form.params[key] ?? ""}
                    placeholder={String(def)}
                    onChange={(e) => upd({ params: { ...form.params, [key]: e.target.value } })}
                  />
                </label>
              ))}
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.enforce_budgets}
                  onChange={(e) => upd({ enforce_budgets: e.target.checked })}
                />
                Rule out plans that break someone's budget (untick to see them, flagged)
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.split_gateways}
                  onChange={(e) => upd({ split_gateways: e.target.checked })}
                />
                Let part of the group use a different airport
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.round_trip_fares}
                  onChange={(e) => upd({ round_trip_fares: e.target.checked })}
                />
                Use round-trip tickets when cheaper
              </label>
            </div>
            <label className="field">
              Notes
              <textarea rows={2} value={form.notes} onChange={(e) => upd({ notes: e.target.value })} />
            </label>
          </details>

          {(checks.errors.length > 0 || checks.warnings.length > 0 || (attempted && checks.incomplete.length > 0)) && (
            <div className="checks">
              {attempted &&
                checks.incomplete.map((m, i) => (
                  <div key={`i${i}`} className="check-error">
                    ✖ {m}
                  </div>
                ))}
              {checks.errors.map((m, i) => (
                <div key={`e${i}`} className="check-error">
                  ✖ {m}
                </div>
              ))}
              {checks.warnings.map((m, i) => (
                <div key={`w${i}`} className="check-warn">
                  ⚠ {m}
                </div>
              ))}
            </div>
          )}
          {error && <div className="error">{error}</div>}
          <div className="row actions">
            <button
              className="primary"
              onClick={() => save(false)}
              disabled={busy || checks.errors.length > 0}
              title={checks.errors.length ? "Fix the problems above first" : ""}
            >
              {busy ? "Optimizing…" : tripId ? "Save & re-optimize" : "Save & optimize"}
            </button>
            {tripId && (
              <>
                <button onClick={() => save(true)} disabled={busy || checks.errors.length > 0}>
                  Save as new trip
                </button>
                <button
                  onClick={() => navigator.clipboard.writeText(location.href)}
                  title="Copy a link to this trip"
                >
                  Copy link
                </button>
                <button className="link bad" onClick={remove}>
                  Delete
                </button>
              </>
            )}
          </div>
        </div>

        {runs.length > 1 && (
          <div className="row runs">
            <span className="muted small">Past runs:</span>
            <select
              value={run?.id ?? ""}
              onChange={async (e) => setRun(await api.run(Number(e.target.value)))}
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {new Date(r.created_at + "Z").toLocaleString()}
                  {r.savings_pct !== null ? ` · saves ${r.savings_pct.toFixed(1)}%` : ""}
                </option>
              ))}
            </select>
          </div>
        )}

        {run && <Results run={run} stays={stayNames} />}
        {picking !== null && form.places[picking] && (
          <StayPicker
            place={form.places[picking]}
            center={coordsOf(form.places[picking].coords)}
            checkin={stayDates[picking].checkin}
            checkout={stayDates[picking].checkout}
            adults={Number(form.params.people_per_room) || 2}
            onClose={() => setPicking(null)}
            onPick={(stay) => {
              updList("places", picking, {
                stay,
                lodging_per_night: Math.round(stay.price_per_night),
                lodging_source: "hotel",
              });
              setPicking(null);
            }}
          />
        )}
      </main>
    </div>
  );
}

function coordsOf(s) {
  const [lat, lng] = String(s).split(",").map((x) => parseFloat(x));
  return [lat, lng];
}

const fmtDay = (iso) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

// Under each place: the hotel picked (with its price) or a button to open the map.
function StayLine({ place, enabled, onOpen, onClear }) {
  const st = place.stay;
  if (!st) {
    return (
      <button
        type="button"
        className="link small stay-btn"
        onClick={onOpen}
        disabled={!enabled}
        title={enabled ? "" : "Hotel prices need the server started with LITEAPI_KEY"}
      >
        🏨 Choose where to stay{enabled ? "" : " (not set up)"}
      </button>
    );
  }
  return (
    <div className="small stay-chosen">
      🏨 <b>{st.name}</b> · ~${Math.round(st.price_per_night)}/night
      <span className="muted">
        {" "}
        ({fmtDay(st.checkin)}–{fmtDay(st.checkout)}
        {st.sandbox ? ", test price" : ""})
      </span>{" "}
      ·{" "}
      <button type="button" className="link small" onClick={onOpen} disabled={!enabled}>
        change
      </button>{" "}
      ·{" "}
      <button type="button" className="link small" onClick={onClear}>
        remove
      </button>
    </div>
  );
}

// Where the lodging price came from, so a guess never looks like a real quote.
function LodgingSource({ place, pricesOn }) {
  const st = place.stay;
  const e = place.estimate;
  const price = Math.round(Number(place.lodging_per_night));
  const test = (x) => (x?.sandbox ? ", test prices" : "");
  if (st) {
    const hotel = Math.round(st.price_per_night);
    if (place.lodging_source !== "manual" || price === hotel) {
      return <div className="good small">from {st.name}{st.sandbox ? " (test price)" : ""}</div>;
    }
    return <div className="warn-text small">edited · {st.name} was ${hotel}</div>;
  }
  if (place.lodging_source === "manual") {
    return (
      <div className="muted small">
        your price{e?.per_night ? ` · typical nearby ~$${Math.round(e.per_night)}` : ""}
      </div>
    );
  }
  if (place.lodging_source === "estimate" && e?.per_night) {
    return (
      <div className="muted small" title={`${fmtDay(e.checkin)}–${fmtDay(e.checkout)}`}>
        typical nearby: median of {e.count} hotel{e.count === 1 ? "" : "s"}{test(e)}
      </div>
    );
  }
  if (e && !e.per_night) return <div className="warn-text small">guess · no hotel prices nearby for these dates</div>;
  if (pricesOn && place.coords) return <div className="muted small">guess · looking up prices nearby…</div>;
  return <div className="muted small">guess · pick a hotel for a real price</div>;
}
