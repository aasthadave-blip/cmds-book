import { NextRequest, NextResponse } from "next/server";
import Stripe from "stripe";
import { getServiceById } from "@/lib/services";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY || "");

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { serviceId, bookingId, clientEmail, clientName } = body;

    const service = getServiceById(serviceId);
    if (!service) {
      return NextResponse.json({ error: "Service not found" }, { status: 404 });
    }

    const paymentIntent = await stripe.paymentIntents.create({
      amount: service.price,
      currency: "usd",
      metadata: {
        bookingId,
        serviceId,
        serviceName: service.name,
      },
      receipt_email: clientEmail,
      description: `${service.name} - ${service.duration} min session with Mystic Luna`,
    });

    return NextResponse.json({
      clientSecret: paymentIntent.client_secret,
      paymentIntentId: paymentIntent.id,
    });
  } catch (error: any) {
    console.error("Stripe error:", error);
    return NextResponse.json(
      { error: error.message || "Payment creation failed" },
      { status: 500 }
    );
  }
}
