// Ticks from `expiresAt` (an ISO timestamp from the server), recomputed
// against Date.now() every second — never a plain client-side decrement,
// which drifts the moment the tab is backgrounded. 06-frontend-plan.md §5.
import React, { useEffect, useState } from "react";

function secondsLeft(expiresAt) {
  const ms = new Date(expiresAt).getTime() - Date.now();
  return Math.max(0, Math.round(ms / 1000));
}

export default function Countdown({ expiresAt, onExpire }) {
  const [remaining, setRemaining] = useState(() => secondsLeft(expiresAt));

  useEffect(() => {
    setRemaining(secondsLeft(expiresAt));
    const id = setInterval(() => {
      const s = secondsLeft(expiresAt);
      setRemaining(s);
      if (s <= 0) {
        clearInterval(id);
        onExpire?.();
      }
    }, 1000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiresAt]);

  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");

  return (
    <span className={`font-mono font-semibold ${remaining <= 20 ? "text-accent" : "text-text"}`}>
      {mm}:{ss}
    </span>
  );
}
