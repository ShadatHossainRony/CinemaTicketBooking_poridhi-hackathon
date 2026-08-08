// Two separate checks, not one — matching the actual distinction the API
// makes (04-api-contract.md §7). /health touches nothing and must always
// answer if the process is up; /ready additionally reports the database
// and the gateway, and can legitimately say "degraded" while the gateway
// is down (REQ-44) without that being a failure of the API itself.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";

const DOT = {
  ready: "bg-green-500",
  degraded: "bg-yellow-500",
  down: "bg-accent",
  checking: "bg-muted",
};

const LABEL = {
  ready: "All systems go",
  degraded: "Payments degraded",
  down: "API unreachable",
  checking: "Checking…",
};

export default function StatusBadge() {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    let cancelled = false;

    async function ping() {
      try {
        await api.health();
      } catch {
        if (!cancelled) setStatus("down");
        return;
      }
      try {
        const r = await api.ready();
        if (!cancelled) setStatus(r.status === "ready" ? "ready" : "degraded");
      } catch {
        if (!cancelled) setStatus("degraded");
      }
    }

    ping();
    const timer = setInterval(ping, 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex items-center gap-2 text-xs text-muted" title={LABEL[status]}>
      <span className={`inline-block h-2 w-2 rounded-full ${DOT[status]}`} />
      <span className="hidden sm:inline">{LABEL[status]}</span>
    </div>
  );
}
