"use client";

import { useState, useEffect } from "react";
import { useAdmin } from "../layout";

interface Schedule {
  startHour: number;
  endHour: number;
  slotInterval: number;
  daysOff: number[];
  blockedDates: string[];
  timezone: string;
}

const DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export default function AdminSchedulePage() {
  const { headers } = useAdmin();
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [newBlockedDate, setNewBlockedDate] = useState("");

  useEffect(() => {
    fetch("/api/admin?section=schedule", { headers: headers() })
      .then((r) => r.json())
      .then((d) => setSchedule(d.schedule));
  }, []);

  async function handleSave() {
    if (!schedule) return;
    setSaving(true);
    const res = await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action: "update-schedule", schedule }),
    });
    if (res.ok) {
      setMessage("Schedule updated!");
      setTimeout(() => setMessage(""), 3000);
    }
    setSaving(false);
  }

  function toggleDayOff(day: number) {
    if (!schedule) return;
    const daysOff = schedule.daysOff.includes(day)
      ? schedule.daysOff.filter((d) => d !== day)
      : [...schedule.daysOff, day];
    setSchedule({ ...schedule, daysOff });
  }

  function addBlockedDate() {
    if (!schedule || !newBlockedDate) return;
    if (!schedule.blockedDates.includes(newBlockedDate)) {
      setSchedule({ ...schedule, blockedDates: [...schedule.blockedDates, newBlockedDate].sort() });
    }
    setNewBlockedDate("");
  }

  function removeBlockedDate(date: string) {
    if (!schedule) return;
    setSchedule({ ...schedule, blockedDates: schedule.blockedDates.filter((d) => d !== date) });
  }

  function formatHour(h: number) {
    const hour = h % 12 || 12;
    const ampm = h < 12 ? "AM" : "PM";
    return `${hour}:00 ${ampm}`;
  }

  if (!schedule) {
    return <p className="text-mystic-400 animate-pulse">Loading schedule...</p>;
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-white mb-6">Schedule Settings</h1>

      {message && (
        <div className="bg-green-900/30 border border-green-700 text-green-300 rounded-lg px-4 py-2 mb-4 text-sm">
          {message}
        </div>
      )}

      <div className="space-y-6">
        {/* Working Hours */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Working Hours</h2>
          <div className="grid md:grid-cols-3 gap-4">
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Start Hour</label>
              <select
                value={schedule.startHour}
                onChange={(e) => setSchedule({ ...schedule, startHour: parseInt(e.target.value) })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500"
              >
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>{formatHour(i)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">End Hour</label>
              <select
                value={schedule.endHour}
                onChange={(e) => setSchedule({ ...schedule, endHour: parseInt(e.target.value) })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500"
              >
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>{formatHour(i)}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Slot Interval (minutes)</label>
              <select
                value={schedule.slotInterval}
                onChange={(e) => setSchedule({ ...schedule, slotInterval: parseInt(e.target.value) })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500"
              >
                <option value={15}>15 minutes</option>
                <option value={30}>30 minutes</option>
                <option value={45}>45 minutes</option>
                <option value={60}>60 minutes</option>
              </select>
            </div>
          </div>
          <p className="text-mystic-500 text-xs mt-3">
            Currently: {formatHour(schedule.startHour)} - {formatHour(schedule.endHour)}, every {schedule.slotInterval} min
          </p>
        </div>

        {/* Days Off */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Days Off (Weekly)</h2>
          <div className="flex flex-wrap gap-2">
            {DAY_NAMES.map((name, i) => (
              <button
                key={i}
                onClick={() => toggleDayOff(i)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  schedule.daysOff.includes(i)
                    ? "bg-red-900/50 text-red-300 border border-red-700"
                    : "bg-mystic-700/50 text-mystic-200 border border-mystic-600 hover:bg-mystic-700"
                }`}
              >
                {name} {schedule.daysOff.includes(i) ? "(OFF)" : ""}
              </button>
            ))}
          </div>
        </div>

        {/* Blocked Dates */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Blocked Dates (Holidays/Vacations)</h2>
          <div className="flex gap-2 mb-4">
            <input
              type="date"
              value={newBlockedDate}
              onChange={(e) => setNewBlockedDate(e.target.value)}
              className="bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500"
            />
            <button
              onClick={addBlockedDate}
              disabled={!newBlockedDate}
              className="bg-gold-500 hover:bg-gold-400 disabled:bg-mystic-700 text-mystic-900 px-4 py-2 rounded-lg font-semibold text-sm transition-colors"
            >
              Block Date
            </button>
          </div>
          {schedule.blockedDates.length === 0 ? (
            <p className="text-mystic-500 text-sm">No blocked dates. Add holidays or vacation days above.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {schedule.blockedDates.map((date) => (
                <span key={date} className="bg-red-900/30 text-red-300 text-sm px-3 py-1 rounded-full flex items-center gap-2">
                  {date}
                  <button onClick={() => removeBlockedDate(date)} className="hover:text-red-100">&times;</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Timezone */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Timezone</h2>
          <select
            value={schedule.timezone}
            onChange={(e) => setSchedule({ ...schedule, timezone: e.target.value })}
            className="bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500"
          >
            <option value="America/New_York">Eastern Time (ET)</option>
            <option value="America/Chicago">Central Time (CT)</option>
            <option value="America/Denver">Mountain Time (MT)</option>
            <option value="America/Los_Angeles">Pacific Time (PT)</option>
          </select>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-8 py-3 rounded-lg font-semibold transition-colors"
        >
          {saving ? "Saving..." : "Save Schedule Settings"}
        </button>
      </div>
    </div>
  );
}
