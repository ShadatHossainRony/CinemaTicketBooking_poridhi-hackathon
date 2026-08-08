// Step 1: pick a movie. GET /movies, with the API's own `q` search param
// wired to a search box — not a client-side filter over a full list.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { Loading, ErrorState, EmptyState, errorProps } from "../components/StateBlocks.jsx";

// No poster images ship with the catalogue (REQ-10 seeds text fields only),
// so each card gets a deterministic gradient "poster" instead of a broken
// <img>. Hashing the title keeps the same movie the same colour across
// reloads without needing a stored field for it.
const POSTER_GRADIENTS = [
  "from-rose-600 via-red-700 to-neutral-900",
  "from-amber-500 via-orange-700 to-neutral-900",
  "from-fuchsia-600 via-purple-700 to-neutral-900",
  "from-cyan-500 via-blue-700 to-neutral-900",
  "from-emerald-500 via-teal-700 to-neutral-900",
  "from-rose-500 via-pink-700 to-neutral-900",
];

function posterGradient(title) {
  let hash = 0;
  for (let i = 0; i < title.length; i++) hash = (hash * 31 + title.charCodeAt(i)) >>> 0;
  return POSTER_GRADIENTS[hash % POSTER_GRADIENTS.length];
}

export default function Browse({ onSelectMovie }) {
  const [movies, setMovies] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState("");

  async function load(query) {
    setError(null);
    try {
      const data = await api.movies(query ? { q: query } : {});
      setMovies(data.items);
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <div className="mb-8 overflow-hidden rounded-2xl border border-panel bg-gradient-to-br from-panel via-panel to-accent/10 px-6 py-8 text-center sm:py-12">
        <div className="text-[11px] font-semibold uppercase tracking-[0.3em] text-accent">
          Now booking
        </div>
        <h1 className="mt-2 text-2xl font-bold tracking-tight sm:text-3xl">
          Big screen. Your seat. Locked in seconds.
        </h1>
        <p className="mx-auto mt-2 max-w-md text-sm text-muted">
          Pick a movie, choose your seat, and pay — your seat is reserved the moment you head to
          checkout, not before.
        </p>
      </div>

      <div className="mb-4">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(q)}
          placeholder="Search movies…"
          className="w-full rounded border border-muted/30 bg-panel px-3 py-2 text-sm sm:w-72"
        />
      </div>

      {error && <ErrorState {...errorProps(error)} onRetry={() => load(q)} />}
      {!error && movies === null && <Loading label="Loading movies…" />}
      {!error && movies !== null && movies.length === 0 && (
        <EmptyState message="No movies match that search." />
      )}

      {!error && movies && movies.length > 0 && (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {movies.map((m) => {
            const soldOut = m.show_count === 0;
            return (
              <button
                key={m.id}
                disabled={soldOut}
                onClick={() => onSelectMovie(m)}
                className={`group overflow-hidden rounded-xl border bg-panel text-left shadow-lg shadow-black/20 transition-all ${
                  soldOut
                    ? "cursor-not-allowed border-transparent opacity-50"
                    : "border-transparent hover:-translate-y-1 hover:border-accent hover:shadow-accent/20"
                }`}
              >
                <div
                  className={`relative flex h-40 items-end bg-gradient-to-br p-3 ${posterGradient(
                    m.title
                  )}`}
                >
                  <span className="absolute right-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-white backdrop-blur">
                    {m.rating}
                  </span>
                  <span className="text-4xl opacity-90 drop-shadow">🎬</span>
                </div>
                <div className="p-3.5">
                  <div className="font-semibold leading-tight">{m.title}</div>
                  <div className="mt-1 text-xs text-muted">{m.duration_minutes} min</div>
                  {m.synopsis && (
                    <div className="mt-1.5 line-clamp-2 text-xs text-muted">{m.synopsis}</div>
                  )}
                  <div className="mt-3 flex items-center justify-between">
                    <span className="text-xs text-accent">
                      {soldOut
                        ? "No showtimes"
                        : `${m.show_count} showtime${m.show_count === 1 ? "" : "s"}`}
                    </span>
                    {!soldOut && (
                      <span className="text-xs font-medium text-muted transition-colors group-hover:text-text">
                        Select seats →
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
