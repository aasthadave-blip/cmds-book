"use client";

import { useState, useEffect } from "react";
import { useAdmin } from "../layout";

interface Booking {
  id: string;
  serviceId: string;
  serviceName: string;
  date: string;
  time: string;
  clientName: string;
  clientEmail: string;
  clientPhone: string;
  notes?: string;
  status: "pending" | "confirmed" | "cancelled";
  amount: number;
  createdAt: string;
}

export default function AdminBookingsPage() {
  const { headers } = useAdmin();
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [message, setMessage] = useState("");

  function loadBookings() {
    fetch("/api/admin?section=bookings", { headers: headers() })
      .then((r) => r.json())
      .then((d) => setBookings((d.bookings || []).reverse()));
  }

  useEffect(() => { loadBookings(); }, []);

  async function updateStatus(id: string, status: string) {
    await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action: "update-booking", bookingId: id, updates: { status } }),
    });
    setMessage(`Booking ${status}!`);
    setTimeout(() => setMessage(""), 3000);
    loadBookings();
  }

  const filtered = filter === "all" ? bookings : bookings.filter((b) => b.status === filter);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Manage Bookings</h1>

      {message && (
        <div className="bg-green-900/30 border border-green-700 text-green-300 rounded-lg px-4 py-2 mb-4 text-sm">
          {message}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-2 mb-6">
        {["all", "pending", "confirmed", "cancelled"].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors capitalize ${
              filter === f ? "bg-gold-500 text-mystic-900" : "bg-mystic-800 text-mystic-300 hover:bg-mystic-700"
            }`}
          >
            {f} ({f === "all" ? bookings.length : bookings.filter((b) => b.status === f).length})
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="bg-mystic-800 rounded-xl p-10 border border-mystic-700/50 text-center">
          <p className="text-mystic-400">No bookings found.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((b) => (
            <div key={b.id} className="bg-mystic-800 rounded-xl p-5 border border-mystic-700/50">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-white font-semibold">{b.clientName}</h3>
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      b.status === "confirmed" ? "bg-green-900/50 text-green-300" :
                      b.status === "cancelled" ? "bg-red-900/50 text-red-300" :
                      "bg-yellow-900/50 text-yellow-300"
                    }`}>{b.status}</span>
                  </div>
                  <p className="text-mystic-300 text-sm">{b.serviceName}</p>
                  <div className="flex flex-wrap gap-4 mt-2 text-sm text-mystic-400">
                    <span>📅 {b.date} at {b.time}</span>
                    <span>📧 {b.clientEmail}</span>
                    <span>📱 {b.clientPhone}</span>
                    <span>💰 ${(b.amount / 100).toFixed(2)}</span>
                  </div>
                  {b.notes && <p className="text-mystic-500 text-sm mt-2 italic">Note: {b.notes}</p>}
                  <p className="text-mystic-600 text-xs mt-1">ID: {b.id} &bull; Created: {new Date(b.createdAt).toLocaleDateString()}</p>
                </div>
                <div className="flex gap-2">
                  {b.status === "pending" && (
                    <button onClick={() => updateStatus(b.id, "confirmed")}
                      className="bg-green-900/30 hover:bg-green-900/50 text-green-300 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors">
                      Confirm
                    </button>
                  )}
                  {b.status !== "cancelled" && (
                    <button onClick={() => updateStatus(b.id, "cancelled")}
                      className="bg-red-900/30 hover:bg-red-900/50 text-red-300 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors">
                      Cancel
                    </button>
                  )}
                  {b.status === "cancelled" && (
                    <button onClick={() => updateStatus(b.id, "confirmed")}
                      className="bg-green-900/30 hover:bg-green-900/50 text-green-300 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors">
                      Reactivate
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
