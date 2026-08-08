// The one screen that matters: pick a showtime (a compact chip row, not a
// separate page) and pick seats. GET /shows/{id}/seats is polled every 3s
// so a seat someone else takes greys out for every viewer without them
// having to click it first.
//
// Seat locking follows the BD Railway pattern: selecting seats here is
// free and reversible — nothing is reserved yet. The seat is only locked
// (POST /holds) the moment the customer hits "Proceed to Payment", exactly
// like a train seat locks when you leave the seat map and enter passenger
// details. That single click is the only place `onHold` fires.
import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api/client.js";
import { Loading, ErrorState, EmptyState, errorProps } from "../components/StateBlocks.jsx";
import SeatGrid from "../components/SeatGrid.jsx";

// Mirrors MAX_SEATS_PER_HOLD's server default (05-backend-plan.md §4). The
// server is authoritative — this only avoids a round trip for the common
// case; exceeding it still comes back as 422 TOO_MANY_SEATS if this ever
// drifts from the real config.
const MAX_SEATS = 6;

function formatChipTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function SeatMap({ movie, phone, onPhoneChange, onHold, onBack }) {
  const [shows, setShows] = useState(null);
  const [showsError, setShowsError] = useState(null);
  const [showId, setShowId] = useState(null);

  const [seatMap, setSeatMap] = useState(null);
  const [selected, setSelected] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // ---- Showtimes for this movie (the old "Showtimes" step, folded in) ----
  useEffect(() => {
    let cancelled = false;
    setShows(null);
    setShowsError(null);
    setShowId(null);
    api
      .shows({ movie_id: movie.id })
      .then((data) => {
        if (cancelled) return;
        setShows(data.items);
        const firstBookable = data.items.find((s) => s.seats_available > 0);
        setShowId((firstBookable || data.items[0])?.id ?? null);
      })
      .catch((e) => !cancelled && setShowsError(e));
    return () => {
      cancelled = true;
    };
  }, [movie.id]);

  const showsByTheatre = useMemo(() => {
    if (!shows) return [];
    const grouped = new Map();
    for (const s of shows) {
      const key = s.theatre.id;
      if (!grouped.has(key)) grouped.set(key, { theatre: s.theatre, shows: [] });
      grouped.get(key).shows.push(s);
    }
    return [...grouped.values()];
  }, [shows]);

  // ---- Seat map for the selected showtime ---------------------------------
  async function loadSeatMap() {
    if (!showId) return;
    try {
      const data = await api.seatMap(showId);
      setSeatMap(data);
      setError(null);
      // A poll refresh that shows a locally-selected seat as no longer
      // AVAILABLE (someone else took it) drops it from the selection too
      // — otherwise "Proceed to Payment" would submit a seat the map
      // already shows as unavailable, and the 409 would be a surprise
      // instead of the grey-out being the answer.
      setSelected((cur) => {
        const stillAvailable = new Set(
          data.seats.filter((s) => s.status === "AVAILABLE").map((s) => s.seat)
        );
        return cur.filter((s) => stillAvailable.has(s));
      });
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    setSeatMap(null);
    setSelected([]);
    if (!showId) return undefined;
    loadSeatMap();
    const interval = setInterval(loadSeatMap, 3000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showId]);

  function toggleSeat(s) {
    if (s.status !== "AVAILABLE") return;
    setSelected((cur) => {
      if (cur.includes(s.seat)) return cur.filter((x) => x !== s.seat);
      if (cur.length >= MAX_SEATS) return cur;
      return [...cur, s.seat];
    });
  }

  async function doProceedToPayment() {
    if (busy || selected.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.hold({ show_id: showId, seats: selected, phone });
      onHold(res);
    } catch (e) {
      setError(e);
      // A 409 here means someone won the race for one of these seats —
      // refresh immediately rather than waiting up to 3s for the next
      // poll tick, so the map reflects reality right away.
      loadSeatMap();
    } finally {
      setBusy(false);
    }
  }

  const total = seatMap
    ? seatMap.seats
        .filter((s) => selected.includes(s.seat))
        .reduce((sum, s) => sum + Number(s.price), 0)
    : 0;

  return (
    <div>
      <button onClick={onBack} className="mb-3 text-xs text-muted hover:text-text">
        ← All movies
      </button>

      <h2 className="text-lg font-semibold">{movie.title}</h2>
      <div className="mb-4 text-xs text-muted">
        {movie.rating} · {movie.duration_minutes} min
      </div>

      {showsError && <ErrorState {...errorProps(showsError)} />}
      {!showsError && shows === null && <Loading label="Loading showtimes…" />}
      {!showsError && shows !== null && shows.length === 0 && (
        <EmptyState message="No showtimes scheduled for this movie right now." />
      )}

      {showsByTheatre.length > 0 && (
        <div className="mb-5 space-y-3">
          {showsByTheatre.map(({ theatre, shows: theatreShows }) => (
            <div key={theatre.id}>
              <div className="mb-1.5 text-xs font-medium text-muted">{theatre.name}</div>
              <div className="flex flex-wrap gap-2">
                {theatreShows.map((s) => {
                  const soldOut = s.seats_available === 0;
                  const active = s.id === showId;
                  return (
                    <button
                      key={s.id}
                      disabled={soldOut}
                      onClick={() => setShowId(s.id)}
                      className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                        soldOut
                          ? "cursor-not-allowed border-transparent bg-panel/50 text-muted/50"
                          : active
                          ? "border-accent bg-accent/15 text-text"
                          : "border-muted/30 bg-panel text-muted hover:border-accent/60 hover:text-text"
                      }`}
                    >
                      {s.screen.name} · {formatChipTime(s.starts_at)}
                      {soldOut && " · Sold out"}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {error && <ErrorState {...errorProps(error)} onRetry={loadSeatMap} />}
      {showId && !seatMap && !error && <Loading label="Loading seat map…" />}

      {seatMap && (
        <>
          <div className="mb-3 text-xs text-muted">
            {seatMap.show.theatre.name} · {seatMap.show.screen.name} ·{" "}
            {new Date(seatMap.show.starts_at).toLocaleString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}{" "}
            · {seatMap.summary.available} available · {seatMap.summary.held} held ·{" "}
            {seatMap.summary.booked} booked
          </div>
          <SeatGrid seats={seatMap.seats} selected={selected} onToggle={toggleSeat} maxSeats={MAX_SEATS} />
        </>
      )}

      {selected.length > 0 && (
        <div className="mt-5 flex flex-wrap items-end gap-4 rounded-lg bg-panel p-4">
          <div className="text-sm">
            <div className="mb-0.5 text-xs text-muted">Seats</div>
            <div className="font-medium">{selected.join(", ")}</div>
          </div>
          <div className="text-sm">
            <div className="mb-0.5 text-xs text-muted">Total</div>
            <div className="font-medium">
              {total.toFixed(2)} {seatMap?.show.currency}
            </div>
          </div>
          <label className="flex flex-col text-sm">
            <span className="mb-0.5 text-xs text-muted">Phone</span>
            <input
              value={phone}
              onChange={(e) => onPhoneChange(e.target.value)}
              className="rounded border border-muted/30 bg-bg px-2 py-1.5"
            />
          </label>
          <button
            onClick={doProceedToPayment}
            disabled={busy}
            className="ml-auto rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Locking seats…" : "Proceed to Payment"}
          </button>
          <div className="w-full text-[11px] text-muted">
            Your seats lock for {seatMap?.hold_ttl_seconds}s once you proceed — just like reserving a
            train seat, they're yours only while you finish paying.
          </div>
        </div>
      )}
    </div>
  );
}
