import { addDays, format, isBefore, startOfDay, setHours, setMinutes } from "date-fns";

export interface TimeSlot {
  time: string; // "HH:mm"
  label: string; // "10:00 AM"
  available: boolean;
}

export interface Booking {
  id: string;
  serviceId: string;
  date: string; // "YYYY-MM-DD"
  time: string; // "HH:mm"
  clientName: string;
  clientEmail: string;
  clientPhone: string;
  notes?: string;
  paymentIntentId?: string;
  status: "pending" | "confirmed" | "cancelled";
  createdAt: string;
}

// In-memory store (replace with database in production)
const bookings: Booking[] = [];

// Available hours: 10 AM - 6 PM EST, Monday-Saturday
const WORKING_HOURS = {
  start: 10,
  end: 18,
  slotInterval: 30, // minutes
  daysOff: [0], // Sunday
};

export function getAvailableDates(weeksAhead: number = 4): string[] {
  const dates: string[] = [];
  const today = startOfDay(new Date());

  for (let i = 1; i <= weeksAhead * 7; i++) {
    const date = addDays(today, i);
    const dayOfWeek = date.getDay();

    if (!WORKING_HOURS.daysOff.includes(dayOfWeek)) {
      dates.push(format(date, "yyyy-MM-dd"));
    }
  }

  return dates;
}

export function getTimeSlots(date: string, serviceDurationMinutes: number): TimeSlot[] {
  const slots: TimeSlot[] = [];
  const { start, end, slotInterval } = WORKING_HOURS;
  const now = new Date();

  const bookedTimes = bookings
    .filter((b) => b.date === date && b.status !== "cancelled")
    .map((b) => b.time);

  for (let hour = start; hour < end; hour++) {
    for (let minute = 0; minute < 60; minute += slotInterval) {
      // Check if the service fits before closing time
      const endMinutes = hour * 60 + minute + serviceDurationMinutes;
      if (endMinutes > end * 60) continue;

      const timeStr = `${hour.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;

      // Check if slot is in the past
      const slotDate = new Date(`${date}T${timeStr}:00`);
      const isPast = isBefore(slotDate, now);

      // Check if slot conflicts with existing bookings
      const isBooked = bookedTimes.includes(timeStr);

      const h = hour % 12 || 12;
      const ampm = hour < 12 ? "AM" : "PM";
      const label = `${h}:${minute.toString().padStart(2, "0")} ${ampm}`;

      slots.push({
        time: timeStr,
        label,
        available: !isPast && !isBooked,
      });
    }
  }

  return slots;
}

export function createBooking(data: Omit<Booking, "id" | "createdAt" | "status">): Booking {
  const booking: Booking = {
    ...data,
    id: `BK-${Date.now()}-${Math.random().toString(36).slice(2, 7).toUpperCase()}`,
    status: "pending",
    createdAt: new Date().toISOString(),
  };

  bookings.push(booking);
  return booking;
}

export function confirmBooking(id: string): Booking | null {
  const booking = bookings.find((b) => b.id === id);
  if (booking) {
    booking.status = "confirmed";
  }
  return booking || null;
}

export function getBookingById(id: string): Booking | null {
  return bookings.find((b) => b.id === id) || null;
}
