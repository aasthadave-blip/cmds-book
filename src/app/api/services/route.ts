import { NextResponse } from "next/server";
import { getActiveServices } from "@/lib/store";

export async function GET() {
  return NextResponse.json({ services: getActiveServices() });
}
