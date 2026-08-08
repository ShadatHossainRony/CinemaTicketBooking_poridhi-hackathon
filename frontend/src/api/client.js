// Single fetch wrapper. Base URL is relative ("") so the same code runs
// behind the same origin in production (Nginx) and behind the Vite proxy
// in dev.
//
// The API is mounted at the ROOT — no /api, no /v1 prefix (CLAUDE.md,
// REQ-18, REQ-20). Nginx uses a per-prefix allow-list to forward
// /health, /shows, /holds, /bookings, etc. to the upstream; the frontend
// calls those paths directly.

const BASE = "";

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const r = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await r.json() : await r.text();
  if (!r.ok) {
    const err = new Error(`HTTP ${r.status}`);
    err.body = data;
    err.status = r.status;
    throw err;
  }
  return data;
}

export const api = {
  movies: () => request("/movies"),
  theatres: () => request("/theatres"),
  shows: (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return request("/shows" + (q ? `?${q}` : ""));
  },
  seatMap: (showId) => request(`/shows/${showId}/seats`),
  hold: (body) => request("/holds", { method: "POST", body }),
  holdStatus: (holdId) => request(`/holds/${holdId}`),
  booking: (holdId) => request("/bookings", { method: "POST", body: { hold_id: holdId } }),
  bookingStatus: (bookingRef) => request(`/bookings/${bookingRef}`),
  sendOtp: (bookingRef) =>
    request(`/bookings/${bookingRef}/otp`, { method: "POST" }),
  verifyOtp: (bookingRef, code) =>
    request(`/bookings/${bookingRef}/otp/verify`, {
      method: "POST",
      body: { code },
    }),
  pay: (bookingRef, headers = {}) =>
    request(`/bookings/${bookingRef}/pay`, { method: "POST", headers }),
  health: () => request("/health"),
  ready: () => request("/ready"),
};