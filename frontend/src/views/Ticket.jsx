// Step 5: the outcome. `booking` is the last GET /bookings/{ref} response
// Checkout received — every field here is server-truth, nothing computed.
import React from "react";

export default function Ticket({ booking, onStartOver }) {
  const confirmed = booking.status === "CONFIRMED";

  return (
    <div className="rounded-lg bg-panel p-6 text-center">
      {confirmed ? (
        <>
          <div className="mb-2 text-4xl">🎟️</div>
          <h2 className="mb-1 text-lg font-semibold">Booking confirmed</h2>
          {booking.ticket_code && (
            <div className="mb-4 mt-2 inline-block rounded bg-bg px-3 py-2 font-mono text-sm">
              {booking.ticket_code}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="mb-2 text-4xl">⚠️</div>
          <h2 className="mb-1 text-lg font-semibold">
            {booking.status === "FAILED" ? "Payment failed" : `Booking ${booking.status?.toLowerCase()}`}
          </h2>
          <div className="mb-4 text-sm text-muted">
            Your seats have been released.
            {booking.status === "FAILED" && " You can try again."}
          </div>
        </>
      )}

      <div className="mx-auto mb-4 max-w-sm space-y-1 text-left text-sm">
        <div>
          <span className="text-muted">Booking</span> {booking.booking_ref}
        </div>
        <div>
          <span className="text-muted">Movie</span> {booking.show?.movie}
        </div>
        <div>
          <span className="text-muted">Where</span> {booking.show?.theatre} · {booking.show?.screen}
        </div>
        {booking.show?.starts_at && (
          <div>
            <span className="text-muted">When</span> {new Date(booking.show.starts_at).toLocaleString()}
          </div>
        )}
        <div>
          <span className="text-muted">Seats</span> {booking.seats?.join(", ")}
        </div>
        <div>
          <span className="text-muted">Amount</span> {booking.total_amount} {booking.currency}
        </div>
        {booking.payment && (
          <div>
            <span className="text-muted">Payment</span> {booking.payment.status}
          </div>
        )}
      </div>

      <button onClick={onStartOver} className="rounded bg-accent px-4 py-2 text-sm text-white">
        {confirmed ? "Book another" : "Try again"}
      </button>
    </div>
  );
}
