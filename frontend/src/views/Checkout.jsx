// Step 4: hold -> booking -> OTP -> pay. The screen shown is derived
// directly from `booking.status` (the real state machine in
// 04-api-contract.md §5), not a locally-tracked "phase" flag that could
// drift from what the server actually thinks happened.
import React, { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { ErrorState, errorProps } from "../components/StateBlocks.jsx";
import Countdown from "../components/Countdown.jsx";

export default function Checkout({ hold, onExpired, onDone, onBack }) {
  const [holdStatus, setHoldStatus] = useState(hold);
  const [booking, setBooking] = useState(null);
  const [code, setCode] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [stillProcessing, setStillProcessing] = useState(false);

  // The single place that hands off to the parent once the booking
  // reaches a terminal state — an effect, not a render-time call, so it
  // never fires while React is mid-render. Covers every path that can
  // produce a terminal status, including one the poll loop in doPay()
  // times out on but a later manual refresh (or the next poll tick)
  // eventually catches.
  useEffect(() => {
    if (booking && ["CONFIRMED", "FAILED", "EXPIRED", "REFUNDED"].includes(booking.status)) {
      onDone(booking);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booking?.status]);

  // Before a booking exists, the hold itself can expire out from under
  // the user. GET /holds/{id} is how we find out — rather than only
  // discovering it when POST /bookings comes back 410 HOLD_EXPIRED.
  useEffect(() => {
    if (booking) return undefined;
    const interval = setInterval(async () => {
      try {
        const h = await api.holdStatus(hold.hold_id);
        setHoldStatus(h);
        if (h.status !== "ACTIVE") {
          clearInterval(interval);
          onExpired();
        }
      } catch {
        // transient — try again next tick
      }
    }, 3000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booking, hold.hold_id]);

  async function doBook() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.booking(hold.hold_id);
      setBooking(res);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function doSendOtp() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.sendOtp(booking.booking_ref);
      setOtpSent(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function doVerify() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.verifyOtp(booking.booking_ref, code);
      const fresh = await api.bookingStatus(booking.booking_ref);
      setBooking(fresh);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function doPay() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setStillProcessing(false);
    try {
      await api.pay(booking.booking_ref);
    } catch (e) {
      setError(e);
      setBusy(false);
      return;
    }
    // /pay returns in ~200ms and does not wait on the gateway (REQ-12) —
    // the real outcome arrives via callback 2-15s later, always, and
    // fails ~10% of the time by specification. Poll for it. Reaching a
    // terminal status here just calls setBooking; the effect above is
    // what actually hands off to the parent.
    const started = Date.now();
    let reachedTerminal = false;
    while (Date.now() - started < 30_000) {
      try {
        const s = await api.bookingStatus(booking.booking_ref);
        setBooking(s);
        if (["CONFIRMED", "FAILED", "EXPIRED", "REFUNDED"].includes(s.status)) {
          reachedTerminal = true;
          break;
        }
      } catch {
        // transient — keep polling
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setBusy(false);
    if (!reachedTerminal) setStillProcessing(true);
  }

  if (error) {
    return <ErrorState {...errorProps(error)} onRetry={() => setError(null)} />;
  }

  // ---- Phase 1: hold not yet converted to a booking ----------------------
  if (!booking) {
    return (
      <div>
        <button onClick={onBack} className="mb-3 text-xs text-muted hover:text-text">
          ← Seat map
        </button>
        <div className="rounded-lg bg-panel p-4">
          <h2 className="mb-1 font-semibold">🔒 Your seats are locked</h2>
          <div className="mb-3 text-xs text-muted">
            Locked just for you, the way a train seat locks when you start checkout — finish paying
            before the timer runs out or they go back on sale.
          </div>
          <div className="mb-1 text-sm">
            Seats: <strong>{holdStatus.seats.map((s) => s.seat).join(", ")}</strong>
          </div>
          <div className="mb-1 text-sm">
            Total: <strong>{holdStatus.total_amount} {holdStatus.currency}</strong>
          </div>
          <div className="mb-4 text-sm">
            Expires in <Countdown expiresAt={holdStatus.expires_at} onExpire={onExpired} />
          </div>
          <button
            onClick={doBook}
            disabled={busy}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Creating…" : "Continue to booking"}
          </button>
        </div>
      </div>
    );
  }

  // ---- Phase 2: OTP -------------------------------------------------------
  if (booking.status === "PENDING_OTP") {
    return (
      <div className="rounded-lg bg-panel p-4">
        <h2 className="mb-1 font-semibold">Verify your phone</h2>
        <div className="mb-4 text-xs text-muted">
          Booking {booking.booking_ref} · {booking.phone_masked}
        </div>
        {!otpSent ? (
          <button
            onClick={doSendOtp}
            disabled={busy}
            className="rounded border border-muted/40 bg-panel px-3 py-2 text-sm disabled:opacity-50"
          >
            {busy ? "Sending…" : "Send code"}
          </button>
        ) : (
          <div className="flex items-end gap-2">
            <input
              placeholder="Code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="w-28 rounded border border-muted/30 bg-bg px-2 py-1.5"
            />
            <button
              onClick={doVerify}
              disabled={busy}
              className="rounded bg-accent px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Verifying…" : "Verify"}
            </button>
            <button onClick={doSendOtp} disabled={busy} className="text-xs text-muted hover:text-text">
              Resend
            </button>
          </div>
        )}
      </div>
    );
  }

  // ---- Phase 3: pay ---------------------------------------------------
  if (booking.status === "OTP_VERIFIED" || booking.status === "PAYMENT_PENDING") {
    return (
      <div className="rounded-lg bg-panel p-4">
        <h2 className="mb-1 font-semibold">Pay</h2>
        <div className="mb-4 text-sm text-muted">
          {booking.total_amount} {booking.currency} for {booking.seats.join(", ")}
        </div>
        <button
          onClick={doPay}
          disabled={busy}
          className="rounded bg-accent px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Processing…" : "Start payment"}
        </button>
        {stillProcessing && (
          <div className="mt-3 text-xs text-muted">
            Still processing — the gateway can take up to 15 seconds. Booking reference:{" "}
            <span className="font-mono">{booking.booking_ref}</span>. It's safe to wait or check
            back later; it will not be charged twice.
          </div>
        )}
      </div>
    );
  }

  // A terminal status renders nothing here — the effect above hands off
  // to the parent (which switches to the Ticket view) the moment
  // `booking.status` becomes CONFIRMED/FAILED/EXPIRED/REFUNDED, so this
  // component unmounts before this branch would ever be seen.
  return null;
}
