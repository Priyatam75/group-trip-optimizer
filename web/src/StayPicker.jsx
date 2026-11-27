import { useEffect, useMemo, useState } from "react";
import { MapContainer, Marker, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "./api.js";

const fmtDay = (iso) =>
  new Date(iso + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
const usd = (x) => "$" + Math.round(x).toLocaleString();

// Price-label pins (divIcons also avoid Leaflet's default marker images, which bundlers break).
function priceIcon(hotel, selected) {
  const label = hotel.price_per_night != null ? usd(hotel.price_per_night) : "—";
  const cls = `price-pin${selected ? " selected" : ""}${hotel.price_per_night == null ? " none" : ""}`;
  return L.divIcon({ className: "", html: `<div class="${cls}">${label}</div>`, iconSize: null });
}
const centerIcon = L.divIcon({ className: "", html: '<div class="center-pin"></div>', iconSize: [14, 14] });

// Re-fit only when the set of pins changes, not on every click.
function FitBounds({ points }) {
  const map = useMap();
  const key = points.map((p) => p.join(",")).join(";");
  useEffect(() => {
    if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 14 });
  }, [map, key]);
  return null;
}

// Pick a hotel for one place. Display only: no booking happens.
export default function StayPicker({ place, center, checkin, checkout, adults, onPick, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(place.stay?.id || null);
  const [pricedOnly, setPricedOnly] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError("");
    api
      .lodging({ lat: center[0], lng: center[1], checkin, checkout, adults })
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [center[0], center[1], checkin, checkout, adults]);

  const hotels = useMemo(
    () => (data?.hotels || []).filter((h) => !pricedOnly || h.price_per_night != null),
    [data, pricedOnly]
  );
  const points = useMemo(() => [center, ...hotels.map((h) => [h.lat, h.lng])], [hotels, center]);
  const chosen = hotels.find((h) => h.id === selected);
  const priced = (data?.hotels || []).filter((h) => h.price_per_night != null).length;

  function pick(h) {
    onPick({
      id: h.id,
      name: h.name,
      lat: h.lat,
      lng: h.lng,
      price_per_night: h.price_per_night,
      checkin: data.checkin,
      checkout: data.checkout,
      checked_at: data.checked_at,
      sandbox: data.sandbox,
      provider: data.provider,
    });
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal stay-picker">
        <div className="modal-head">
          <div>
            <h3>Where to stay in {place.name}</h3>
            <div className="muted small">
              Cheapest room for {adults} people, {fmtDay(checkin)} → {fmtDay(checkout)}
              {data && ` · ${priced} of ${data.hotels.length} hotels have a price for these dates`}
              {data?.sandbox && <span className="tag warn-tag">test prices (sandbox)</span>}
            </div>
          </div>
          <button className="link" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="muted">Finding hotels and prices…</div>}

        {data && (
          <div className="stay-body">
            <div className="stay-map">
              <MapContainer center={center} zoom={12} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                  url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                <FitBounds points={points} />
                <Marker position={center} icon={centerIcon} title={place.label || place.name} />
                {hotels.map((h) => (
                  <Marker
                    key={h.id}
                    position={[h.lat, h.lng]}
                    icon={priceIcon(h, h.id === selected)}
                    title={h.name}
                    zIndexOffset={h.id === selected ? 1000 : 0}
                    eventHandlers={{ click: () => setSelected(h.id) }}
                  />
                ))}
              </MapContainer>
            </div>

            <div className="stay-list">
              <label className="check small">
                <input type="checkbox" checked={pricedOnly} onChange={(e) => setPricedOnly(e.target.checked)} />
                Only hotels with a price for these dates
              </label>
              {hotels.length === 0 && (
                <div className="muted">No hotels with prices here for these dates. Untick the box above to see all.</div>
              )}
              <ul>
                {hotels.map((h) => (
                  <li
                    key={h.id}
                    className={h.id === selected ? "active" : ""}
                    onClick={() => setSelected(h.id)}
                  >
                    {h.photo && <img src={h.photo} alt="" loading="lazy" />}
                    <div className="grow">
                      <div className="stay-name">{h.name}</div>
                      <div className="muted small">
                        {h.stars ? "★".repeat(Math.round(h.stars)) + " " : ""}
                        {h.rating ? `${h.rating}/10` : ""}
                        {h.reviews ? ` (${h.reviews.toLocaleString()} reviews)` : ""}
                      </div>
                      <div className="muted small">{h.address}</div>
                      {h.room && (
                        <div className="small">
                          {h.room}
                          {h.board ? ` · ${h.board}` : ""}
                          {h.refundable ? " · free cancellation" : ""}
                        </div>
                      )}
                    </div>
                    <div className="stay-price">
                      {h.price_per_night != null ? (
                        <>
                          <div className="big">{usd(h.price_per_night)}</div>
                          <div className="muted small">
                            /night{h.taxes_included === false ? " + taxes" : ""}
                          </div>
                          <div className="muted small">{usd(h.price_total)} total</div>
                        </>
                      ) : (
                        <div className="muted small">no price for these dates</div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        <div className="modal-foot">
          <span className="muted small">
            Prices from {data?.provider || "LiteAPI"}
            {data?.checked_at && `, checked ${new Date(data.checked_at).toLocaleTimeString()}`}. Nothing is booked.
          </span>
          <div className="row">
            <button onClick={onClose}>Cancel</button>
            <button
              className="primary"
              disabled={!chosen || chosen.price_per_night == null}
              onClick={() => chosen && pick(chosen)}
            >
              {chosen ? `Stay at ${chosen.name}` : "Pick a hotel"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
