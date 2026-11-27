async function req(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d ? JSON.stringify(d) : `HTTP ${res.status}`);
  }
  return data;
}

export const api = {
  config: () => req("GET", "/api/config"),
  airports: () => req("GET", "/api/airports"),
  geocode: (q) => req("GET", `/api/geocode?q=${encodeURIComponent(q)}`),
  lodging: (params) => req("GET", `/api/lodging?${new URLSearchParams(params)}`),
  examples: () => req("GET", "/api/examples"),
  trips: () => req("GET", "/api/trips"),
  trip: (id) => req("GET", `/api/trips/${id}`),
  run: (id) => req("GET", `/api/runs/${id}`),
  create: (input, created_by) => req("POST", "/api/trips", { input, created_by }),
  update: (id, input, created_by) => req("PUT", `/api/trips/${id}`, { input, created_by }),
  rerun: (id) => req("POST", `/api/trips/${id}/run`),
  remove: (id) => req("DELETE", `/api/trips/${id}`),
};
