// Step 1: pick a movie. GET /movies, with the API's own `q` search param
// wired to a search box — not a client-side filter over a full list.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { Loading, ErrorState, EmptyState, errorProps } from "../components/StateBlocks.jsx";

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
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {movies.map((m) => (
            <button
              key={m.id}
              onClick={() => onSelectMovie(m)}
              className="group overflow-hidden rounded-lg border border-transparent bg-panel text-left transition-colors hover:border-accent"
            >
              <div className="flex h-24 items-center justify-center bg-gradient-to-br from-accent/60 to-panel text-3xl opacity-70 group-hover:opacity-90">
                🎬
              </div>
              <div className="p-3">
                <div className="font-semibold leading-tight">{m.title}</div>
                <div className="mt-1 text-xs text-muted">
                  {m.rating} · {m.duration_minutes} min
                </div>
                {m.synopsis && (
                  <div className="mt-1 line-clamp-2 text-xs text-muted">{m.synopsis}</div>
                )}
                <div className="mt-2 text-xs text-accent">
                  {m.show_count} showtime{m.show_count === 1 ? "" : "s"}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
