import { useMemo, useState } from "react";
import Suggest from "./Suggest.jsx";
import { airportLabel, nearbyAirports, searchAirports } from "./airports.js";

const split = (s) =>
  s
    .split(/[\s,]+/)
    .map((o) => o.trim().toUpperCase())
    .filter(Boolean);

// Home airports as removable tags. `value` is the comma-separated code string the form stores.
export default function AirportPicker({ value, onChange, airports }) {
  const [query, setQuery] = useState("");
  const codes = split(value);
  const byCode = useMemo(() => Object.fromEntries(airports.map((a) => [a.code, a])), [airports]);
  const results = useMemo(
    () => searchAirports(airports, query).filter((a) => !codes.includes(a.code)),
    [airports, query, value]
  );
  const nearby = useMemo(() => {
    const seen = new Set(codes);
    const out = [];
    for (const c of codes) {
      for (const a of nearbyAirports(airports, c)) {
        if (!seen.has(a.code)) {
          seen.add(a.code);
          out.push(a);
        }
      }
    }
    return out.slice(0, 3);
  }, [airports, value]);

  const set = (list) => onChange(list.join(", "));
  const add = (code) => {
    set([...codes, code]);
    setQuery("");
  };

  return (
    <div className="airport-picker">
      <div className="chips">
        {codes.map((c) => (
          <span key={c} className={`chip ${byCode[c] || !airports.length ? "" : "bad"}`} title={byCode[c]?.name}>
            {byCode[c] ? airportLabel(byCode[c]) : `${c} · unknown`}
            <button type="button" onClick={() => set(codes.filter((x) => x !== c))} aria-label={`Remove ${c}`}>
              ×
            </button>
          </span>
        ))}
      </div>
      <Suggest
        value={query}
        onChange={setQuery}
        placeholder={codes.length ? "Add another airport…" : "City, airport or code, e.g. Seattle, SeaTac, SEA"}
        items={results}
        empty="No US airport with scheduled flights matches that."
        onPick={(a) => add(a.code)}
        renderItem={(a) => (
          <>
            <div>
              <b>{a.code}</b> · {a.city}
              {a.state ? `, ${a.state}` : ""}
            </div>
            <div className="muted small">{a.name}</div>
          </>
        )}
      />
      {nearby.length > 0 && (
        <div className="muted small">
          Also nearby:{" "}
          {nearby.map((a, i) => (
            <span key={a.code}>
              {i > 0 && " · "}
              <button type="button" className="link small" onClick={() => add(a.code)} title={a.name}>
                + {a.code} ({a.city}, {Math.round(a.km)} km)
              </button>
            </span>
          ))}
          <span> — more airports can mean cheaper plans</span>
        </div>
      )}
    </div>
  );
}
