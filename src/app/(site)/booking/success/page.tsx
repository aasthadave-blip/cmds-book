"use client";

import { useEffect, useState } from "react";
import { format, parse } from "date-fns";

export default function SuccessPage() {
  const [details, setDetails] = useState<any>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const date = params.get("date");
    const time = params.get("time");

    let dateLabel = "";
    if (date) {
      const d = parse(date, "yyyy-MM-dd", new Date());
      dateLabel = format(d, "EEEE, MMMM d, yyyy");
    }

    let timeLabel = time || "";
    if (timeLabel) {
      const [h, m] = timeLabel.split(":").map(Number);
      const hour = h % 12 || 12;
      const ampm = h < 12 ? "AM" : "PM";
      timeLabel = `${hour}:${m.toString().padStart(2, "0")} ${ampm}`;
    }

    setDetails({
      bookingId: params.get("bookingId"),
      service: params.get("service"),
      date: dateLabel,
      time: timeLabel,
      name: params.get("name"),
      email: params.get("email"),
    });
  }, []);

  if (!details) {
    return (
      <div className="py-20 text-center">
        <div className="animate-pulse">
          <p className="text-mystic-300">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="py-20 px-4">
      <div className="max-w-lg mx-auto text-center">
        {/* Success Icon */}
        <div className="w-20 h-20 bg-green-500/20 rounded-full flex items-center justify-center mx-auto mb-6">
          <span className="text-4xl">✅</span>
        </div>

        <h1 className="text-3xl font-bold text-gold-400 mb-2 font-[Georgia,serif]">
          Booking Confirmed!
        </h1>
        <p className="text-mystic-300 mb-8">
          Thank you, {details.name}! Your session has been booked and payment received.
        </p>

        {/* Booking Details Card */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50 text-left mb-8">
          <h2 className="text-white font-semibold mb-4 text-center">
            Your Booking Details
          </h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-mystic-400">Booking ID</span>
              <span className="text-white font-mono text-xs">{details.bookingId}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Service</span>
              <span className="text-white">{details.service}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Date</span>
              <span className="text-white">{details.date}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Time</span>
              <span className="text-white">{details.time} ET</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Confirmation sent to</span>
              <span className="text-white">{details.email}</span>
            </div>
          </div>
        </div>

        {/* What's Next */}
        <div className="bg-mystic-800/50 rounded-xl p-6 border border-mystic-700/30 text-left mb-8">
          <h3 className="text-gold-400 font-semibold mb-3">What Happens Next?</h3>
          <ul className="space-y-2 text-sm text-mystic-300">
            <li className="flex items-start gap-2">
              <span className="text-gold-500 mt-0.5">1.</span>
              You&apos;ll receive a confirmation email with your session details and a Zoom link.
            </li>
            <li className="flex items-start gap-2">
              <span className="text-gold-500 mt-0.5">2.</span>
              15 minutes before your session, you&apos;ll get a reminder email.
            </li>
            <li className="flex items-start gap-2">
              <span className="text-gold-500 mt-0.5">3.</span>
              Join the video call at your scheduled time. Come with an open mind and any questions you&apos;d like to explore.
            </li>
            <li className="flex items-start gap-2">
              <span className="text-gold-500 mt-0.5">4.</span>
              After your session, you&apos;ll receive a follow-up email with a recording and summary.
            </li>
          </ul>
        </div>

        {/* Actions */}
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <a
            href="/"
            className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-6 py-2.5 rounded-full font-semibold transition-colors"
          >
            Back to Home
          </a>
          <a
            href="/booking"
            className="border border-mystic-600 text-mystic-200 hover:bg-mystic-800 px-6 py-2.5 rounded-full font-semibold transition-colors"
          >
            Book Another Session
          </a>
        </div>
      </div>
    </div>
  );
}
