"use client";

import { useState, useEffect } from "react";
import { Elements, PaymentElement, useStripe, useElements } from "@stripe/react-stripe-js";
import { stripePromise } from "@/lib/stripe";
import { format, parse } from "date-fns";

interface Service {
  id: string;
  name: string;
  duration: number;
  price: number;
  icon: string;
}

function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function PaymentForm({
  bookingId,
  onSuccess,
}: {
  bookingId: string;
  onSuccess: (paymentIntentId: string) => void;
}) {
  const stripe = useStripe();
  const elements = useElements();
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!stripe || !elements) return;

    setProcessing(true);
    setError(null);

    const result = await stripe.confirmPayment({
      elements,
      redirect: "if_required",
    });

    if (result.error) {
      setError(result.error.message || "Payment failed");
      setProcessing(false);
    } else if (result.paymentIntent?.status === "succeeded") {
      onSuccess(result.paymentIntent.id);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <PaymentElement
        options={{
          layout: "tabs",
        }}
      />
      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-3 mt-4">
          <p className="text-red-300 text-sm">{error}</p>
        </div>
      )}
      <button
        type="submit"
        disabled={!stripe || processing}
        className="w-full mt-6 bg-gold-500 hover:bg-gold-400 disabled:bg-mystic-700 disabled:text-mystic-500 text-mystic-900 py-3 rounded-full font-semibold transition-colors"
      >
        {processing ? "Processing Payment..." : "Complete Payment"}
      </button>
    </form>
  );
}

export default function ConfirmPage() {
  const [service, setService] = useState<Service | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [bookingId, setBookingId] = useState<string | null>(null);
  const [bookingDetails, setBookingDetails] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const serviceId = params.get("service");
    const date = params.get("date");
    const time = params.get("time");
    const name = params.get("name");
    const email = params.get("email");
    const phone = params.get("phone");
    const notes = params.get("notes");

    if (!serviceId || !date || !time || !name || !email || !phone) {
      setError("Missing booking details. Please go back and try again.");
      setLoading(false);
      return;
    }

    setBookingDetails({ date, time, name, email, phone, notes });

    async function initialize() {
      // Fetch service details
      const svcRes = await fetch("/api/services");
      const svcData = await svcRes.json();
      const found = (svcData.services || []).find((s: Service) => s.id === serviceId);
      if (!found) {
        setError("Service not found.");
        setLoading(false);
        return;
      }
      setService(found);
      try {
        // 1. Create the booking
        const bookingRes = await fetch("/api/bookings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            serviceId,
            date,
            time,
            clientName: name,
            clientEmail: email,
            clientPhone: phone,
            notes,
          }),
        });
        const bookingData = await bookingRes.json();
        if (!bookingRes.ok) throw new Error(bookingData.error);

        setBookingId(bookingData.booking.id);

        // 2. Create payment intent
        const paymentRes = await fetch("/api/create-payment-intent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            serviceId,
            bookingId: bookingData.booking.id,
            clientEmail: email,
            clientName: name,
          }),
        });
        const paymentData = await paymentRes.json();
        if (!paymentRes.ok) throw new Error(paymentData.error);

        setClientSecret(paymentData.clientSecret);
      } catch (err: any) {
        setError(err.message || "Failed to initialize payment");
      } finally {
        setLoading(false);
      }
    }

    initialize();
  }, []);

  async function handlePaymentSuccess(paymentIntentId: string) {
    // Confirm the booking
    await fetch("/api/bookings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bookingId,
        action: "confirm",
        paymentIntentId,
      }),
    });

    // Redirect to success
    const params = new URLSearchParams({
      bookingId: bookingId || "",
      service: service?.name || "",
      date: bookingDetails?.date || "",
      time: bookingDetails?.time || "",
      name: bookingDetails?.name || "",
      email: bookingDetails?.email || "",
    });

    window.location.href = `/booking/success?${params.toString()}`;
  }

  if (loading) {
    return (
      <div className="py-20 text-center">
        <div className="animate-pulse">
          <p className="text-4xl mb-4">🔮</p>
          <p className="text-mystic-300">Preparing your payment...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="py-20 px-4 max-w-lg mx-auto text-center">
        <p className="text-4xl mb-4">⚠️</p>
        <p className="text-red-300 mb-4">{error}</p>
        <a
          href="/booking"
          className="text-gold-400 hover:text-gold-300 underline"
        >
          Go back to booking
        </a>
      </div>
    );
  }

  // Format the time for display
  let timeLabel = bookingDetails?.time || "";
  if (timeLabel) {
    const [h, m] = timeLabel.split(":").map(Number);
    const hour = h % 12 || 12;
    const ampm = h < 12 ? "AM" : "PM";
    timeLabel = `${hour}:${m.toString().padStart(2, "0")} ${ampm}`;
  }

  // Format the date
  let dateLabel = "";
  if (bookingDetails?.date) {
    const d = parse(bookingDetails.date, "yyyy-MM-dd", new Date());
    dateLabel = format(d, "EEEE, MMMM d, yyyy");
  }

  return (
    <div className="py-16 px-4">
      <div className="max-w-2xl mx-auto">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gold-400 mb-2 font-[Georgia,serif]">
            Complete Your Booking
          </h1>
          <p className="text-mystic-300 text-sm">
            Review your details and complete payment to reserve your spot.
          </p>
        </div>

        {/* Booking Summary */}
        <div className="bg-mystic-800 rounded-xl p-5 border border-mystic-700/50 mb-8">
          <h2 className="text-white font-semibold mb-4 flex items-center gap-2">
            <span className="text-xl">{service?.icon}</span> Booking Summary
          </h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-mystic-400">Service</span>
              <span className="text-white">{service?.name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Duration</span>
              <span className="text-white">{service?.duration} minutes</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Date</span>
              <span className="text-white">{dateLabel}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Time</span>
              <span className="text-white">{timeLabel} ET</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Client</span>
              <span className="text-white">{bookingDetails?.name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-mystic-400">Email</span>
              <span className="text-white">{bookingDetails?.email}</span>
            </div>
            <div className="border-t border-mystic-700 my-3" />
            <div className="flex justify-between text-lg">
              <span className="text-white font-semibold">Total</span>
              <span className="text-gold-400 font-bold">
                {service ? formatPrice(service.price) : ""}
              </span>
            </div>
          </div>
        </div>

        {/* Payment Form */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Payment Details</h2>
          {clientSecret && (
            <Elements
              stripe={stripePromise}
              options={{
                clientSecret,
                appearance: {
                  theme: "night",
                  variables: {
                    colorPrimary: "#d4a843",
                    colorBackground: "#1a0a2e",
                    colorText: "#e5d7f7",
                    colorDanger: "#ff6b6b",
                    borderRadius: "8px",
                  },
                },
              }}
            >
              <PaymentForm
                bookingId={bookingId || ""}
                onSuccess={handlePaymentSuccess}
              />
            </Elements>
          )}
          <p className="text-mystic-500 text-xs mt-4 text-center">
            🔒 Your payment is processed securely via Stripe. We never store your card details.
          </p>
        </div>
      </div>
    </div>
  );
}
