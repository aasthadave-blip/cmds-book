import { NextRequest, NextResponse } from "next/server";
import {
  getSchedule,
  getBookings,
  addBooking,
  updateBooking,
  getServiceById,
  type BookingData,
} from "@/lib/store";

interface TimeSlot {
  time: string;
  label: string;
  available: boolean;
}

function getTimeSlots(date: string, serviceDurationMinutes: number): TimeSlot[] {
  const schedule = getSchedule();
  const slots: TimeSlot[] = [];
  const now = new Date();
  const { startHour, endHour, slotInterval, blockedDates } = schedule;

  if (blockedDates.includes(date)) return [];

  const bookedTimes = getBookings()
    .filter((b) => b.date === date && b.status !== "cancelled")
    .map((b) => b.time);

  for (let hour = startHour; hour < endHour; hour++) {
    for (let minute = 0; minute < 60; minute += slotInterval) {
      const endMinutes = hour * 60 + minute + serviceDurationMinutes;
      if (endMinutes > endHour * 60) continue;

      const timeStr = `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;
      const slotDate = new Date(`${date}T${timeStr}:00`);
      const isPast = slotDate < now;
      const isBooked = bookedTimes.includes(timeStr);

      const h = hour % 12 || 12;
      const ampm = hour < 12 ? "AM" : "PM";
      const label = `${h}:${minute.toString().padStart(2, "0")} ${ampm}`;

      slots.push({ time: timeStr, label, available: !isPast && !isBooked });
    }
  }

  return slots;
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const date = searchParams.get("date");
  const duration = parseInt(searchParams.get("duration") || "30", 10);

  if (!date) {
    return NextResponse.json({ error: "Date is required" }, { status: 400 });
  }

  const slots = getTimeSlots(date, duration);
  return NextResponse.json({ slots });
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { serviceId, date, time, clientName, clientEmail, clientPhone, notes } = body;

  if (!serviceId || !date || !time || !clientName || !clientEmail || !clientPhone) {
    return NextResponse.json({ error: "Missing required fields" }, { status: 400 });
  }

  const service = getServiceById(serviceId);

  const booking: BookingData = {
    id: `BK-${Date.now()}-${Math.random().toString(36).slice(2, 7).toUpperCase()}`,
    serviceId,
    serviceName: service?.name || serviceId,
    date,
    time,
    clientName,
    clientEmail,
    clientPhone,
    notes,
    status: "pending",
    createdAt: new Date().toISOString(),
    amount: service?.price || 0,
  };

  addBooking(booking);
  return NextResponse.json({ booking });
}

export async function PATCH(req: NextRequest) {
  const body = await req.json();
  const { bookingId, action, paymentIntentId } = body;

  if (!bookingId) {
    return NextResponse.json({ error: "Booking ID required" }, { status: 400 });
  }

  if (action === "confirm") {
    const booking = updateBooking(bookingId, {
      status: "confirmed",
      ...(paymentIntentId ? { paymentIntentId } : {}),
    });
    if (!booking) {
      return NextResponse.json({ error: "Booking not found" }, { status: 404 });
    }
    return NextResponse.json({ booking });
  }

  return NextResponse.json({ error: "Invalid action" }, { status: 400 });
}
