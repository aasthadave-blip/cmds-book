import { NextRequest, NextResponse } from "next/server";
import {
  getServices,
  saveServices,
  addService,
  updateService,
  deleteService,
  getSchedule,
  saveSchedule,
  getBookings,
  updateBooking,
  getSiteContent,
  saveSiteContent,
  type ServiceData,
  type ScheduleSettings,
  type SiteContent,
} from "@/lib/store";

function checkAuth(req: NextRequest): boolean {
  const auth = req.headers.get("x-admin-password");
  return auth === process.env.ADMIN_PASSWORD;
}

export async function GET(req: NextRequest) {
  if (!checkAuth(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(req.url);
  const section = searchParams.get("section");

  switch (section) {
    case "services":
      return NextResponse.json({ services: getServices() });
    case "schedule":
      return NextResponse.json({ schedule: getSchedule() });
    case "bookings":
      return NextResponse.json({ bookings: getBookings() });
    case "content":
      return NextResponse.json({ content: getSiteContent() });
    case "dashboard": {
      const bookings = getBookings();
      const services = getServices();
      const confirmed = bookings.filter((b) => b.status === "confirmed");
      const totalRevenue = confirmed.reduce((sum, b) => sum + b.amount, 0);
      const upcoming = confirmed.filter((b) => new Date(`${b.date}T${b.time}`) > new Date());
      return NextResponse.json({
        totalBookings: bookings.length,
        confirmedBookings: confirmed.length,
        totalRevenue,
        upcomingBookings: upcoming.length,
        activeServices: services.filter((s) => s.active).length,
        recentBookings: bookings.slice(-10).reverse(),
      });
    }
    default:
      return NextResponse.json({ error: "Invalid section" }, { status: 400 });
  }
}

export async function POST(req: NextRequest) {
  if (!checkAuth(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await req.json();
  const { action } = body;

  switch (action) {
    case "login":
      return NextResponse.json({ success: true });

    case "add-service": {
      const { service } = body as { service: ServiceData };
      service.id = service.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/(^-|-$)/g, "");
      addService(service);
      return NextResponse.json({ success: true, service });
    }

    case "update-service": {
      const { id, updates } = body;
      const updated = updateService(id, updates);
      if (!updated) return NextResponse.json({ error: "Not found" }, { status: 404 });
      return NextResponse.json({ success: true, service: updated });
    }

    case "delete-service": {
      const { id } = body;
      const deleted = deleteService(id);
      if (!deleted) return NextResponse.json({ error: "Not found" }, { status: 404 });
      return NextResponse.json({ success: true });
    }

    case "update-schedule": {
      const { schedule } = body as { schedule: ScheduleSettings };
      saveSchedule(schedule);
      return NextResponse.json({ success: true });
    }

    case "update-booking": {
      const { bookingId, updates } = body;
      const updated = updateBooking(bookingId, updates);
      if (!updated) return NextResponse.json({ error: "Not found" }, { status: 404 });
      return NextResponse.json({ success: true, booking: updated });
    }

    case "update-content": {
      const { content } = body as { content: SiteContent };
      saveSiteContent(content);
      return NextResponse.json({ success: true });
    }

    default:
      return NextResponse.json({ error: "Invalid action" }, { status: 400 });
  }
}
