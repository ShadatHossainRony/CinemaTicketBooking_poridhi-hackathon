// Three states every data-fetching view renders exactly one of, per
// 06-frontend-plan.md §5. ErrorState surfaces the request_id from the
// standard envelope — that's the concrete "greppable in our logs" story.
import React from "react";

export function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-accent" />
      {label}
    </div>
  );
}

export function ErrorState({ message, requestId, onRetry }) {
  return (
    <div className="rounded-lg border border-accent/40 bg-accent/10 p-4 text-sm">
      <div className="mb-1 font-medium text-accent">Something went wrong</div>
      <div className="mb-2 text-text/90">{message}</div>
      {requestId && (
        <div className="mb-2 font-mono text-[11px] text-muted">request_id: {requestId}</div>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded border border-muted/40 bg-panel px-3 py-1 text-xs hover:border-muted"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message, action = null }) {
  return (
    <div className="py-10 text-center text-sm text-muted">
      <div className="mb-2">{message}</div>
      {action}
    </div>
  );
}

// Convenience: every api/client.js call throws Error with .body set to the
// parsed standard envelope ({error:{code,message,details,request_id}}).
// Views pass the caught error straight through; this pulls out the parts
// ErrorState needs so no view has to repeat the optional-chaining dance.
export function errorProps(err) {
  return {
    message: err?.body?.error?.message || err?.message || "Unknown error.",
    requestId: err?.body?.error?.request_id || null,
  };
}
