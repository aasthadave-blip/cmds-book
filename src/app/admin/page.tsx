"use client";

import { useState, useEffect } from "react";
import { useAdmin } from "./layout";

interface DashboardData {
  totalBookings: number;
  confirmedBookings: number;
  totalRevenue: number;
  upcomingBookings: number;
  activeServices: number;
  recentBookings: any[];
}

export default function AdminDashboard() {
  const { headers } = useAdmin();
  const [data, setData] = useState<DashboardData | null>(null);

  useEffect(() => {
    fetch("/api/admin?section=dashboard", { headers: headers() })
      .then((r) => r.json())
      .then(setData);
  }, []);

  if (!data) {
    return <p className="text-mystic-400 animate-pulse">Loading dashboard...</p>;
  }

  const stats = [
    { label: "Total Bookings", value: data.totalBookings, icon: "📋" },
    { label: "Confirmed", value: data.confirmedBookings, icon: "✅" },
    { label: "Upcoming", value: data.upcomingBookings, icon: "📅" },
    { label: "Revenue", value: `$${(data.totalRevenue / 100).toFixed(2)}`, icon: "💰" },
    { label: "Active Services", value: data.activeServices, icon: "✨" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Dashboard</h1>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
        {stats.map((stat) => (
          <div key={stat.label} className="bg-mystic-800 rounded-xl p-4 border border-mystic-700/50">
            <p className="text-2xl mb-1">{stat.icon}</p>
            <p className="text-2xl font-bold text-white">{stat.value}</p>
            <p className="text-mystic-400 text-sm">{stat.label}</p>
          </div>
        ))}
      </div>

      <div className="bg-mystic-800 rounded-xl border border-mystic-700/50">
        <div className="p-4 border-b border-mystic-700/50">
          <h2 className="text-white font-semibold">Recent Bookings</h2>
        </div>
        {data.recentBookings.length === 0 ? (
          <p className="text-mystic-400 text-sm p-6 text-center">No bookings yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-mystic-400 text-left border-b border-mystic-700/30">
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">Client</th>
                  <th className="px-4 py-3">Service</th>
                  <th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Amount</th>
                </tr>
              </thead>
              <tbody>
                {data.recentBookings.map((b: any) => (
                  <tr key={b.id} className="border-b border-mystic-700/20 text-mystic-200">
                    <td className="px-4 py-3 font-mono text-xs">{b.id.slice(0, 12)}...</td>
                    <td className="px-4 py-3">{b.clientName}</td>
                    <td className="px-4 py-3">{b.serviceName}</td>
                    <td className="px-4 py-3">{b.date} {b.time}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        b.status === "confirmed" ? "bg-green-900/50 text-green-300" :
                        b.status === "cancelled" ? "bg-red-900/50 text-red-300" :
                        "bg-yellow-900/50 text-yellow-300"
                      }`}>{b.status}</span>
                    </td>
                    <td className="px-4 py-3">${(b.amount / 100).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
