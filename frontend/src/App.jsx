// The view-state machine — one page, no router (06-frontend-plan.md §2).
// The API is mounted at the root, so a client-side route named /shows
// would collide with the API's own /shows and be shadowed by Nginx's
// allow-list. Every "screen" here is a value swap, never a URL change.
import React, { useState } from "react";
import StatusBadge from "./components/StatusBadge.jsx";
import Browse from "./views/Browse.jsx";
import Showtimes from "./views/Showtimes.jsx";
import SeatMap from "./views/SeatMap.jsx";
import Checkout from "./views/Checkout.jsx";
import Ticket from "./views/Ticket.jsx";

const DEMO_PHONE = "+8801700000001";
const STEPS = ["Movie", "Showtime", "Seats", "Checkout"];
const STEP_INDEX = { browse: 0, showtimes: 1, seatmap: 2, checkout: 3, ticket: 3 };

export default function App() {
  const [view, setView] = useState("browse");
  const [movie, setMovie] = useState(null);
  const [show, setShow] = useState(null);
  const [hold, setHold] = useState(null);
  const [finalBooking, setFinalBooking] = useState(null);
  const [phone, setPhone] = useState(DEMO_PHONE);
  const [notice, setNotice] = useState(null);

  function startOver() {
    setView("browse");
    setMovie(null);
    setShow(null);
    setHold(null);
    setFinalBooking(null);
    setNotice(null);
  }

  return (
    <div className="min-h-full">
      <header className="flex items-center justify-between border-b border-panel/60 px-6 py-4">
        <button onClick={startOver} className="text-xl font-bold tracking-tight">
          🎬 CinemaSeat
        </button>
        <StatusBadge />
      </header>

      {view !== "ticket" && (
        <div className="mx-auto max-w-3xl px-6 pt-4">
          <ol className="flex flex-wrap gap-2 text-xs text-muted">
            {STEPS.map((label, i) => (
              <li
                key={label}
                className={`flex items-center gap-2 ${i <= STEP_INDEX[view] ? "text-accent" : ""}`}
              >
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full border ${
                    i <= STEP_INDEX[view] ? "border-accent bg-accent/10" : "border-muted/40"
                  }`}
                >
                  {i + 1}
                </span>
                {label}
                {i < STEPS.length - 1 && <span className="text-muted/40">—</span>}
              </li>
            ))}
          </ol>
        </div>
      )}

      {notice && (
        <div className="mx-auto max-w-3xl px-6 pt-3">
          <div className="rounded border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-200">
            {notice}
          </div>
        </div>
      )}

      <main className="mx-auto max-w-3xl p-6">
        {view === "browse" && (
          <Browse
            onSelectMovie={(m) => {
              setMovie(m);
              setView("showtimes");
            }}
          />
        )}

        {view === "showtimes" && movie && (
          <Showtimes
            movie={movie}
            onSelectShow={(s) => {
              setShow(s);
              setView("seatmap");
            }}
            onBack={() => setView("browse")}
          />
        )}

        {view === "seatmap" && show && (
          <SeatMap
            show={show}
            phone={phone}
            onPhoneChange={setPhone}
            onHold={(h) => {
              setHold(h);
              setNotice(null);
              setView("checkout");
            }}
            onBack={() => setView("showtimes")}
          />
        )}

        {view === "checkout" && hold && (
          <Checkout
            hold={hold}
            onExpired={() => {
              setHold(null);
              setNotice("Your hold expired — pick your seats again.");
              setView("seatmap");
            }}
            onDone={(booking) => {
              setFinalBooking(booking);
              setView("ticket");
            }}
            onBack={() => setView("seatmap")}
          />
        )}

        {view === "ticket" && finalBooking && <Ticket booking={finalBooking} onStartOver={startOver} />}
      </main>
    </div>
  );
}
