// Step 2: pick a showtime for the chosen movie. GET /shows?movie_id=,
// optionally narrowed by GET /theatres for a theatre filter.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { Loading, ErrorState, EmptyState, errorProps } from "../components/StateBlocks.jsx";

export default function Showtimes({ movie, onSelectShow, onBack }) {
  const [shows, setShows] = useState(null);
  const [theatres, setTheatres] = useState([]);
  const [theatreId, setTheatreId] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .theatres()
      .then((d) => setTheatres(d.items))
      .catch(() => {
        // A failed filter fetch shouldn't block the (more important)
        // showtime list — the dropdown just stays hidden.
      });
  }, []);

  async function load() {
    setError(null);
    setShows(null);
    try {
      const params = { movie_id: movie.id };
      if (theatreId) params.theatre_id = theatreId;
      const data = await api.shows(params);
      setShows(data.items);
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [movie.id, theatreId]);

  return (
    <div>
      <button onClick={onBack} className="mb-3 text-xs text-muted hover:text-text">
        ← All movies
      </button>
      <h2 className="text-lg font-semibold">{movie.title}</h2>
      <div className="mb-4 text-xs text-muted">
        {movie.rating} · {movie.duration_minutes} min
      </div>

      {theatres.length > 0 && (
        <div className="mb-4">
          <select
            value={theatreId}
            onChange={(e) => setTheatreId(e.target.value)}
            className="rounded border border-muted/30 bg-panel px-2 py-1.5 text-sm"
          >
            <option value="">All theatres</option>
            {theatres.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {t.city}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <ErrorState {...errorProps(error)} onRetry={load} />}
      {!error && shows === null && <Loading label="Loading showtimes…" />}
      {!error && shows !== null && shows.length === 0 && (
        <EmptyState message="No showtimes match that filter." />
      )}

      {!error && shows && shows.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {shows.map((s) => {
            const soldOut = s.seats_available === 0;
            return (
              <button
                key={s.id}
                disabled={soldOut}
                onClick={() => onSelectShow(s)}
                className={`rounded-lg border bg-panel p-3 text-left ${
                  soldOut
                    ? "cursor-not-allowed border-transparent opacity-40"
                    : "border-transparent hover:border-accent"
                }`}
              >
                <div className="font-medium">
                  {s.theatre.name} · {s.screen.name}
                </div>
                <div className="text-xs text-muted">
                  {new Date(s.starts_at).toLocaleString(undefined, {
                    weekday: "short",
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
                <div className="mt-2 flex items-center justify-between text-xs">
                  <span className={soldOut ? "text-accent" : "text-muted"}>
                    {soldOut ? "Sold out" : `${s.seats_available}/${s.seats_total} available`}
                  </span>
                  <span className="font-medium">
                    from {s.base_price} {s.currency}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
