// Step 3: live seat map + the atomic hold. GET /shows/{id}/seats (polled
// every 3s so a seat someone else takes greys out for every viewer without
// them having to click it first) and POST /holds.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { Loading, ErrorState, errorProps } from "../components/StateBlocks.jsx";
import SeatGrid from "../components/SeatGrid.jsx";

// Mirrors MAX_SEATS_PER_HOLD's server default (05-backend-plan.md §4). The
// server is authoritative — this only avoids a round trip for the common
// case; exceeding it still comes back as 422 TOO_MANY_SEATS if this ever
// drifts from the real config.
const MAX_SEATS = 6;

export default function SeatMap({ show, phone, onPhoneChange, onHold, onBack }) {
  const [seatMap, setSeatMap] = useState(null);
  const [selected, setSelected] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const data = await api.seatMap(show.id);
      setSeatMap(data);
      setError(null);
      // A poll refresh that shows a locally-selected seat as no longer
      // AVAILABLE (someone else took it) drops it from the selection too
      // — otherwise "Hold" would submit a seat the map already shows as
      // unavailable, and the 409 would be a surprise instead of the
      // grey-out being the answer.
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
    load();
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [show.id]);

  function toggleSeat(s) {
    if (s.status !== "AVAILABLE") return;
    setSelected((cur) => {
      if (cur.includes(s.seat)) return cur.filter((x) => x !== s.seat);
      if (cur.length >= MAX_SEATS) return cur;
      return [...cur, s.seat];
    });
  }

  async function doHold() {
    if (busy || selected.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.hold({ show_id: show.id, seats: selected, phone });
      onHold(res);
    } catch (e) {
      setError(e);
      // A 409 here means someone won the race for one of these seats —
      // refresh immediately rather than waiting up to 3s for the next
      // poll tick, so the map reflects reality right away.
      load();
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
        ← Showtimes
      </button>

      {seatMap && (
        <div className="mb-4">
          <h2 className="text-lg font-semibold">{seatMap.show.movie.title}</h2>
          <div className="text-xs text-muted">
            {seatMap.show.theatre.name} · {seatMap.show.screen.name} ·{" "}
            {new Date(seatMap.show.starts_at).toLocaleString(undefined, {
              weekday: "short",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </div>
          <div className="mt-1 text-xs text-muted">
            {seatMap.summary.available} available · {seatMap.summary.held} held ·{" "}
            {seatMap.summary.booked} booked · hold window {seatMap.hold_ttl_seconds}s
          </div>
        </div>
      )}

      {error && <ErrorState {...errorProps(error)} onRetry={load} />}
      {!seatMap && !error && <Loading label="Loading seat map…" />}

      {seatMap && (
        <SeatGrid seats={seatMap.seats} selected={selected} onToggle={toggleSeat} maxSeats={MAX_SEATS} />
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
            onClick={doHold}
            disabled={busy}
            className="ml-auto rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Holding…" : `Hold ${selected.length} seat${selected.length > 1 ? "s" : ""}`}
          </button>
        </div>
      )}
    </div>
  );
}
