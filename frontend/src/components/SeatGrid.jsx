// The seat map: rows x seats, colour by status. Status comes straight
// from GET /shows/{id}/seats — nothing here decides whether a seat is
// available; the API already applied the lazy-expiry predicate
// (03-data-model.md §4.2) before this component ever sees the data.
import React, { useMemo } from "react";

function Legend({ swatch, label }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`seat ${swatch}`} style={{ width: 14, height: 14, cursor: "default" }} />
      {label}
    </span>
  );
}

export default function SeatGrid({ seats, selected, onToggle, maxSeats }) {
  const seatsByRow = useMemo(() => {
    const grouped = {};
    for (const s of seats) (grouped[s.row] ||= []).push(s);
    return grouped;
  }, [seats]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted">
        <Legend swatch="available" label="Available" />
        <Legend swatch="selected" label="Your selection" />
        <Legend swatch="held" label="Held by someone else" />
        <Legend swatch="booked" label="Booked" />
      </div>

      <div className="mb-4 rounded-lg bg-bg/50 p-3 text-center text-[11px] tracking-widest text-muted">
        SCREEN THIS WAY
      </div>

      <div className="space-y-1.5 overflow-x-auto pb-1">
        {Object.entries(seatsByRow).map(([row, rowSeats]) => (
          <div key={row} className="flex items-center gap-1.5">
            <span className="w-5 text-right text-xs text-muted">{row}</span>
            {rowSeats.map((s) => {
              const isSelected = selected.includes(s.seat);
              const atLimit = !isSelected && selected.length >= maxSeats;
              const disabled = (s.status !== "AVAILABLE" && !isSelected) || atLimit;
              const cls =
                s.status === "AVAILABLE"
                  ? isSelected
                    ? "selected"
                    : "available"
                  : s.status === "HELD"
                  ? "held"
                  : "booked";
              const title = `${s.seat} · ${s.seat_class}${
                s.status !== "AVAILABLE" ? ` · ${s.status.toLowerCase()}` : ` · ${s.price}`
              }`;
              return (
                <button
                  key={s.seat}
                  type="button"
                  disabled={disabled}
                  title={title}
                  onClick={() => onToggle(s)}
                  className={`seat ${cls} ${s.seat_class === "PREMIUM" ? "seat-premium" : ""} ${
                    atLimit ? "opacity-40" : ""
                  }`}
                >
                  {s.number}
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
