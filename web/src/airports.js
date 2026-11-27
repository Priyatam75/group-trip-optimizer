// Airport search and geography helpers, all local (the airport list ships with the app).

export const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[^a-z0-9]/g, "");

export function searchAirports(airports, query, limit = 8) {
  const tokens = query.trim().split(/[\s,]+/).map(norm).filter(Boolean);
  if (!tokens.length) return [];
  const scored = [];
  for (const a of airports) {
    const code = a.code.toLowerCase();
    const city = norm(a.city);
    const hay = norm(`${a.code} ${a.city} ${a.state} ${a.name} ${a.keywords}`);
    if (!tokens.every((t) => hay.includes(t))) continue;
    const t0 = tokens[0];
    let score = 10;
    if (code === t0) score = 100;
    else if (city.startsWith(t0)) score = 80;
    else if (norm(a.name).startsWith(t0)) score = 60;
    else if (norm(a.keywords).includes(t0)) score = 50;
    if (a.size === "hub") score += 5;
    scored.push([score, a]);
  }
  scored.sort((x, y) => y[0] - x[0] || x[1].code.localeCompare(y[1].code));
  return scored.slice(0, limit).map((s) => s[1]);
}

export function km(lat1, lng1, lat2, lng2) {
  const r = Math.PI / 180;
  const dp = (lat2 - lat1) * r;
  const dl = (lng2 - lng1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dl / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(a));
}

// Same estimate the optimizer uses (gto/geo.py): straight line × 1.25 road factor at 75 km/h.
export const driveHours = (a, b) => (km(a[0], a[1], b[0], b[1]) * 1.25) / 75;

export function nearbyAirports(airports, code, maxKm = 120, limit = 3) {
  const home = airports.find((a) => a.code === code);
  if (!home) return [];
  return airports
    // Only suggest major airports: the source data can't reliably tell a small airport with
    // airline flights from one with air-taxi service only. Everything is still searchable.
    .filter((a) => a.code !== code && a.major)
    .map((a) => ({ ...a, km: km(home.lat, home.lng, a.lat, a.lng) }))
    .filter((a) => a.km <= maxKm)
    .sort((x, y) => x.km - y.km)
    .slice(0, limit);
}

export const airportLabel = (a) => `${a.code} · ${a.city || a.name}${a.state ? ", " + a.state : ""}`;
