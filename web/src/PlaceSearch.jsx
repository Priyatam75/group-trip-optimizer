import { useEffect, useState } from "react";
import { api } from "./api.js";
import Suggest from "./Suggest.jsx";

// Type a place name, pick a match; coordinates are filled in behind the scenes.
export default function PlaceSearch({ place, onChange }) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [manual, setManual] = useState(false);
  const resolved = !!place.coords;
  const query = place.name;

  useEffect(() => {
    if (resolved || query.trim().length < 3) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const r = await api.geocode(query);
        if (!cancelled) {
          setResults(r);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, resolved]);

  const [lat, lng] = resolved ? place.coords.split(",").map((s) => s.trim()) : [];

  return (
    <div className="place-search">
      <Suggest
        value={place.name}
        placeholder="e.g. West Yellowstone, Moab, Jackson WY"
        // Typing again means a new search: forget the old match.
        onChange={(name) => onChange({ name, coords: "", label: "", stay: null })}
        items={results}
        loading={loading}
        empty="No US places found. Try a nearby town."
        onPick={(r) => onChange({ name: r.name, coords: `${r.lat}, ${r.lng}`, label: r.label, stay: null })}
        renderItem={(r) => (
          <>
            <div>{r.label}</div>
            {r.kind && <div className="muted small">{r.kind}</div>}
          </>
        )}
      />
      {error && <div className="bad small">{error}</div>}
      {resolved ? (
        <div className="muted small">
          📍 {place.label || place.name}{" "}
          <a
            href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=10/${lat}/${lng}`}
            target="_blank"
            rel="noreferrer"
          >
            check on map
          </a>{" "}
          ·{" "}
          <button type="button" className="link small" onClick={() => setManual((m) => !m)}>
            {manual ? "hide coordinates" : "coordinates"}
          </button>
        </div>
      ) : (
        query.trim().length >= 3 &&
        !loading && (
          <div className="warn-text small">
            Pick a match from the list ·{" "}
            <button type="button" className="link small" onClick={() => setManual(true)}>
              enter coordinates instead
            </button>
          </div>
        )
      )}
      {manual && (
        <input
          className="small-input"
          value={place.coords}
          placeholder="lat, lng, e.g. 44.6621, -111.1041"
          onChange={(e) => onChange({ coords: e.target.value, label: place.label || place.name })}
        />
      )}
    </div>
  );
}
