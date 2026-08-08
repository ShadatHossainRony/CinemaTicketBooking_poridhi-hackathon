import React, { useEffect, useMemo, useState } from "react";
import { api } from "./api/client.js";

const DEMO_PHONE = "+8801700000001";

export default function App() {
  const [movies, setMovies] = useState([]);
  const [theatres, setTheatres] = useState([]);
  const [shows, setShows] = useState([]);
  const [selectedShow, setSelectedShow] = useState(null);
  const [seatMap, setSeatMap] = useState(null);
  const [selected, setSelected] = useState([]);
  const [phone, setPhone] = useState(DEMO_PHONE);
  const [hold, setHold] = useState(null);
  const [booking, setBooking] = useState(null);
  const [code, setCode] = useState("");
  const [pollStatus, setPollStatus] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [m, t, s] = await Promise.all([api.movies(), api.theatres(), api.shows()]);
        setMovies(m.items);
        setTheatres(t.items);
        setShows(s.items);
      } catch (e) {
        setError(e.message);
      }
    })();
  }, []);

  async function loadSeatMap(showId) {
    const data = await api.seatMap(showId);
    setSeatMap(data);
    setSelectedShow(showId);
    setSelected([]);
    setHold(null);
    setBooking(null);
  }

  function toggleSeat(s) {
    if (s.status !== "AVAILABLE") return;
    setSelected((cur) =>
      cur.includes(s.seat) ? cur.filter((x) => x !== s.seat) : [...cur, s.seat]
    );
  }

  async function doHold() {
    setError(null);
    try {
      const res = await api.hold({
        show_id: selectedShow,
        seats: selected,
        phone,
      });
      setHold(res);
    } catch (e) {
      setError(e.body?.error?.message || e.message);
    }
  }

  async function doBook() {
    setError(null);
    try {
      const res = await api.booking(hold.hold_id);
      setBooking(res);
    } catch (e) {
      setError(e.body?.error?.message || e.message);
    }
  }

  async function doSendOtp() {
    setError(null);
    try {
      await api.sendOtp(booking.booking_ref);
    } catch (e) {
      setError(e.body?.error?.message || e.message);
    }
  }

  async function doVerify() {
    setError(null);
    try {
      await api.verifyOtp(booking.booking_ref, code);
    } catch (e) {
      setError(e.body?.error?.message || e.message);
    }
  }

  async function doPay() {
    setError(null);
    try {
      await api.pay(booking.booking_ref);
    } catch (e) {
      setError(e.body?.error?.message || e.message);
    }
    // Poll until the booking is no longer PAYMENT_PENDING.
    const started = Date.now();
    while (Date.now() - started < 30_000) {
      try {
        const s = await api.bookingStatus(booking.booking_ref);
        setPollStatus(s);
        if (["CONFIRMED", "FAILED", "EXPIRED", "REFUNDED"].includes(s.status)) break;
      } catch {}
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  const seatsByRow = useMemo(() => {
    if (!seatMap) return {};
    const grouped = {};
    for (const s of seatMap.seats) {
      (grouped[s.row] ||= []).push(s);
    }
    return grouped;
  }, [seatMap]);

  return (
    <div className="min-h-full">
      <header className="border-b border-panel/60 px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">🎬 CinemaSeat</h1>
        <span className="text-xs text-muted">Single-page demo · v0.1</span>
      </header>

      {error && (
        <div className="bg-accent/15 text-accent px-4 py-2 text-sm">{error}</div>
      )}

      <main className="max-w-5xl mx-auto p-6 grid gap-6">
        {/* Step 1: pick a show */}
        <section className="bg-panel rounded-lg p-4">
          <h2 className="font-semibold mb-3">1. Pick a show</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {shows.map((s) => (
              <button
                key={s.id}
                onClick={() => loadSeatMap(s.id)}
                className={`text-left rounded p-3 border ${
                  selectedShow === s.id
                    ? "border-accent"
                    : "border-panel/60 hover:border-muted"
                } bg-bg`}
              >
                <div className="font-medium">{s.movie.title}</div>
                <div className="text-xs text-muted">
                  {s.theatre.name} · {s.screen.name}
                </div>
                <div className="text-xs text-muted">
                  {new Date(s.starts_at).toUTCString()}
                </div>
                <div className="text-xs">
                  {s.seats_available}/{s.seats_total} available · {s.base_price} {s.currency}
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* Step 2: seat map */}
        {seatMap && (
          <section className="bg-panel rounded-lg p-4">
            <h2 className="font-semibold mb-3">
              2. Pick your seats · {seatMap.hold_ttl_seconds}s hold window
            </h2>
            <div className="text-xs text-muted mb-2">
              Summary: {seatMap.summary.available} available · {seatMap.summary.held} held ·{" "}
              {seatMap.summary.booked} booked
            </div>
            <div className="bg-bg/50 rounded p-3 mb-3 text-center text-muted text-xs">
              SCREEN
            </div>
            <div className="space-y-1">
              {Object.entries(seatsByRow).map(([row, seats]) => (
                <div key={row} className="flex items-center gap-1">
                  <span className="w-6 text-xs text-muted">{row}</span>
                  {seats.map((s) => {
                    const isSel = selected.includes(s.seat);
                    const cls =
                      s.status === "AVAILABLE"
                        ? isSel ? "selected" : "available"
                        : s.status === "HELD"
                        ? "held"
                        : "booked";
                    return (
                      <span
                        key={s.seat}
                        title={`${s.seat} · ${s.seat_class} · ${s.price}`}
                        className={`seat ${cls}`}
                        onClick={() => toggleSeat(s)}
                      >
                        {s.number}
                      </span>
                    );
                  })}
                </div>
              ))}
            </div>

            {selected.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-3 items-end">
                <label className="text-sm flex flex-col">
                  <span className="text-muted text-xs mb-1">Phone</span>
                  <input
                    className="bg-bg rounded px-2 py-1 border border-panel/60"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                  />
                </label>
                <button
                  onClick={doHold}
                  className="bg-accent text-white rounded px-4 py-2 text-sm font-medium"
                >
                  Hold {selected.join(", ")}
                </button>
              </div>
            )}
          </section>
        )}

        {/* Step 3: hold → booking */}
        {hold && (
          <section className="bg-panel rounded-lg p-4">
            <h2 className="font-semibold mb-3">
              3. Hold active · expires in {hold.expires_in_seconds}s
            </h2>
            <div className="text-sm text-muted mb-3">
              Hold {hold.hold_id} · {hold.total_amount} {hold.currency}
            </div>
            <button
              onClick={doBook}
              className="bg-accent text-white rounded px-4 py-2 text-sm font-medium"
            >
              Create booking
            </button>
          </section>
        )}

        {/* Step 4: OTP */}
        {booking && (
          <section className="bg-panel rounded-lg p-4">
            <h2 className="font-semibold mb-3">4. Verify phone</h2>
            <div className="text-sm text-muted mb-3">
              Booking {booking.booking_ref} · status {booking.status}
            </div>
            <button
              onClick={doSendOtp}
              className="bg-panel border border-muted text-text rounded px-3 py-2 text-sm mr-2"
            >
              Send OTP
            </button>
            <div className="mt-3 flex gap-2 items-end">
              <input
                placeholder="OTP code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="bg-bg rounded px-2 py-1 border border-panel/60"
              />
              <button
                onClick={doVerify}
                className="bg-accent text-white rounded px-4 py-2 text-sm"
              >
                Verify
              </button>
            </div>
          </section>
        )}

        {/* Step 5: pay */}
        {booking && (
          <section className="bg-panel rounded-lg p-4">
            <h2 className="font-semibold mb-3">5. Pay</h2>
            <button
              onClick={doPay}
              className="bg-accent text-white rounded px-4 py-2 text-sm"
            >
              Start payment
            </button>
            {pollStatus && (
              <div className="mt-3 text-sm">
                <div>Status: <strong>{pollStatus.status}</strong></div>
                {pollStatus.payment && (
                  <div className="text-muted text-xs">
                    payment {pollStatus.payment.payment_id} · {pollStatus.payment.status}
                  </div>
                )}
                {pollStatus.ticket_code && (
                  <div className="mt-2 font-mono">{pollStatus.ticket_code}</div>
                )}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}