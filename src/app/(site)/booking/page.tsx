"use client";

import { useState, useEffect } from "react";
import {
  format,
  addMonths,
  startOfMonth,
  endOfMonth,
  startOfWeek,
  endOfWeek,
  addDays,
  isSameMonth,
  isSameDay,
  isBefore,
  startOfDay,
  isAfter,
} from "date-fns";

interface Service {
  id: string;
  name: string;
  shortDescription: string;
  fullDescription: string;
  duration: number;
  price: number;
  icon: string;
  popular?: boolean;
}

interface TimeSlot {
  time: string;
  label: string;
  available: boolean;
}

function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export default function BookingPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [step, setStep] = useState(1);
  const [selectedService, setSelectedService] = useState<Service | null>(null);
  const [currentMonth, setCurrentMonth] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [selectedTime, setSelectedTime] = useState<TimeSlot | null>(null);
  const [timeSlots, setTimeSlots] = useState<TimeSlot[]>([]);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    phone: "",
    notes: "",
  });

  // Fetch services and check URL params
  useEffect(() => {
    fetch("/api/services")
      .then((r) => r.json())
      .then((data) => {
        setServices(data.services || []);
        const params = new URLSearchParams(window.location.search);
        const serviceId = params.get("service");
        if (serviceId) {
          const found = (data.services || []).find((s: Service) => s.id === serviceId);
          if (found) {
            setSelectedService(found);
            setStep(2);
          }
        }
      });
  }, []);

  // Fetch time slots when date changes
  useEffect(() => {
    if (!selectedDate || !selectedService) return;

    setLoadingSlots(true);
    const dateStr = format(selectedDate, "yyyy-MM-dd");

    fetch(`/api/bookings?date=${dateStr}&duration=${selectedService.duration}`)
      .then((r) => r.json())
      .then((data) => {
        setTimeSlots(data.slots || []);
        setLoadingSlots(false);
      })
      .catch(() => setLoadingSlots(false));
  }, [selectedDate, selectedService]);

  // Calendar rendering
  function renderCalendar() {
    const monthStart = startOfMonth(currentMonth);
    const monthEnd = endOfMonth(currentMonth);
    const calendarStart = startOfWeek(monthStart);
    const calendarEnd = endOfWeek(monthEnd);
    const today = startOfDay(new Date());
    const maxDate = addMonths(today, 2);

    const days: React.ReactElement[] = [];
    let day = calendarStart;

    while (day <= calendarEnd) {
      const d = day;
      const isCurrentMonth = isSameMonth(d, currentMonth);
      const isPast = isBefore(d, today);
      const isFuture = isAfter(d, maxDate);
      const isSunday = d.getDay() === 0;
      const isSelected = selectedDate && isSameDay(d, selectedDate);
      const isDisabled = !isCurrentMonth || isPast || isFuture || isSunday;

      days.push(
        <button
          key={d.toISOString()}
          onClick={() => {
            if (!isDisabled) {
              setSelectedDate(d);
              setSelectedTime(null);
            }
          }}
          disabled={isDisabled}
          className={`w-10 h-10 rounded-full text-sm transition-all ${
            isSelected
              ? "bg-gold-500 text-mystic-900 font-bold"
              : isDisabled
              ? "text-mystic-700 cursor-not-allowed"
              : "text-mystic-200 hover:bg-mystic-700 cursor-pointer"
          }`}
        >
          {format(d, "d")}
        </button>
      );

      day = addDays(day, 1);
    }

    return days;
  }

  function handleSubmit() {
    if (!selectedService || !selectedDate || !selectedTime) return;

    const params = new URLSearchParams({
      service: selectedService.id,
      date: format(selectedDate, "yyyy-MM-dd"),
      time: selectedTime.time,
      name: formData.name,
      email: formData.email,
      phone: formData.phone,
      notes: formData.notes,
    });

    window.location.href = `/booking/confirm?${params.toString()}`;
  }

  return (
    <div className="py-16 px-4">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold text-gold-400 mb-3 font-[Georgia,serif]">
            Book Your Session
          </h1>
          <p className="text-mystic-300">
            Select a service, choose your date &amp; time, and confirm your booking.
          </p>
        </div>

        {/* Progress Steps */}
        <div className="flex items-center justify-center gap-2 mb-12">
          {["Service", "Date & Time", "Your Info"].map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                  step > i + 1
                    ? "bg-gold-500 text-mystic-900"
                    : step === i + 1
                    ? "bg-gold-500 text-mystic-900"
                    : "bg-mystic-700 text-mystic-400"
                }`}
              >
                {step > i + 1 ? "✓" : i + 1}
              </div>
              <span
                className={`text-sm hidden sm:inline ${
                  step === i + 1 ? "text-gold-400 font-semibold" : "text-mystic-400"
                }`}
              >
                {label}
              </span>
              {i < 2 && <div className="w-8 h-0.5 bg-mystic-700 mx-1" />}
            </div>
          ))}
        </div>

        {/* Step 1: Select Service */}
        {step === 1 && (
          <div>
            <h2 className="text-xl font-bold text-white mb-6">Choose a Service</h2>
            <div className="grid sm:grid-cols-2 gap-4">
              {services.map((service) => (
                <button
                  key={service.id}
                  onClick={() => {
                    setSelectedService(service);
                    setStep(2);
                  }}
                  className={`text-left bg-mystic-800 rounded-xl p-5 border transition-all hover:-translate-y-0.5 ${
                    selectedService?.id === service.id
                      ? "border-gold-500"
                      : "border-mystic-700/50 hover:border-mystic-600"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <span className="text-3xl">{service.icon}</span>
                    <div className="flex-1">
                      <h3 className="text-white font-semibold">{service.name}</h3>
                      <p className="text-mystic-400 text-sm mt-1">
                        {service.shortDescription}
                      </p>
                      <div className="flex items-center gap-3 mt-3">
                        <span className="text-gold-400 font-bold">
                          {formatPrice(service.price)}
                        </span>
                        <span className="text-mystic-500 text-sm">
                          {service.duration} min
                        </span>
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Select Date & Time */}
        {step === 2 && selectedService && (
          <div>
            <button
              onClick={() => setStep(1)}
              className="text-mystic-400 hover:text-gold-400 text-sm mb-6 flex items-center gap-1"
            >
              &larr; Change Service
            </button>

            {/* Selected service summary */}
            <div className="bg-mystic-800 rounded-xl p-4 border border-mystic-700/50 mb-8 flex items-center gap-4">
              <span className="text-3xl">{selectedService.icon}</span>
              <div>
                <h3 className="text-white font-semibold">{selectedService.name}</h3>
                <p className="text-mystic-400 text-sm">
                  {selectedService.duration} min &bull;{" "}
                  {formatPrice(selectedService.price)}
                </p>
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-8">
              {/* Calendar */}
              <div>
                <h3 className="text-white font-semibold mb-4">Select a Date</h3>
                <div className="bg-mystic-800 rounded-xl p-5 border border-mystic-700/50">
                  <div className="flex items-center justify-between mb-4">
                    <button
                      onClick={() =>
                        setCurrentMonth(addMonths(currentMonth, -1))
                      }
                      className="text-mystic-300 hover:text-gold-400 p-1"
                    >
                      ◀
                    </button>
                    <h4 className="text-white font-semibold">
                      {format(currentMonth, "MMMM yyyy")}
                    </h4>
                    <button
                      onClick={() =>
                        setCurrentMonth(addMonths(currentMonth, 1))
                      }
                      className="text-mystic-300 hover:text-gold-400 p-1"
                    >
                      ▶
                    </button>
                  </div>

                  <div className="grid grid-cols-7 gap-1 text-center mb-2">
                    {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((d) => (
                      <div key={d} className="text-mystic-500 text-xs font-medium py-1">
                        {d}
                      </div>
                    ))}
                  </div>

                  <div className="grid grid-cols-7 gap-1 justify-items-center">
                    {renderCalendar()}
                  </div>

                  <p className="text-mystic-500 text-xs mt-3">
                    All times are in Eastern Time (ET). Sundays are unavailable.
                  </p>
                </div>
              </div>

              {/* Time Slots */}
              <div>
                <h3 className="text-white font-semibold mb-4">
                  {selectedDate
                    ? `Available Times - ${format(selectedDate, "MMM d, yyyy")}`
                    : "Select a date first"}
                </h3>

                {!selectedDate && (
                  <div className="bg-mystic-800 rounded-xl p-10 border border-mystic-700/50 text-center">
                    <p className="text-mystic-400">
                      Please select a date from the calendar
                    </p>
                  </div>
                )}

                {selectedDate && loadingSlots && (
                  <div className="bg-mystic-800 rounded-xl p-10 border border-mystic-700/50 text-center">
                    <p className="text-mystic-400 animate-pulse">Loading available times...</p>
                  </div>
                )}

                {selectedDate && !loadingSlots && (
                  <div className="bg-mystic-800 rounded-xl p-5 border border-mystic-700/50">
                    <div className="grid grid-cols-3 gap-2 max-h-80 overflow-y-auto">
                      {timeSlots.map((slot) => (
                        <button
                          key={slot.time}
                          onClick={() => slot.available && setSelectedTime(slot)}
                          disabled={!slot.available}
                          className={`py-2 px-3 rounded-lg text-sm transition-all ${
                            selectedTime?.time === slot.time
                              ? "bg-gold-500 text-mystic-900 font-bold"
                              : slot.available
                              ? "bg-mystic-700/50 text-mystic-200 hover:bg-mystic-700"
                              : "bg-mystic-900/50 text-mystic-600 cursor-not-allowed line-through"
                          }`}
                        >
                          {slot.label}
                        </button>
                      ))}
                    </div>

                    {timeSlots.length === 0 && (
                      <p className="text-mystic-400 text-center py-4">
                        No available slots for this date.
                      </p>
                    )}
                  </div>
                )}

                {selectedTime && (
                  <button
                    onClick={() => setStep(3)}
                    className="w-full mt-4 bg-gold-500 hover:bg-gold-400 text-mystic-900 py-3 rounded-full font-semibold transition-colors"
                  >
                    Continue to Details
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Client Info */}
        {step === 3 && selectedService && selectedDate && selectedTime && (
          <div>
            <button
              onClick={() => setStep(2)}
              className="text-mystic-400 hover:text-gold-400 text-sm mb-6 flex items-center gap-1"
            >
              &larr; Change Date/Time
            </button>

            {/* Booking Summary */}
            <div className="bg-mystic-800 rounded-xl p-5 border border-mystic-700/50 mb-8">
              <h3 className="text-white font-semibold mb-3">Booking Summary</h3>
              <div className="grid sm:grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-mystic-400">Service:</span>{" "}
                  <span className="text-white">{selectedService.name}</span>
                </div>
                <div>
                  <span className="text-mystic-400">Price:</span>{" "}
                  <span className="text-gold-400 font-semibold">
                    {formatPrice(selectedService.price)}
                  </span>
                </div>
                <div>
                  <span className="text-mystic-400">Date:</span>{" "}
                  <span className="text-white">
                    {format(selectedDate, "EEEE, MMMM d, yyyy")}
                  </span>
                </div>
                <div>
                  <span className="text-mystic-400">Time:</span>{" "}
                  <span className="text-white">{selectedTime.label} ET</span>
                </div>
              </div>
            </div>

            {/* Contact Form */}
            <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
              <h3 className="text-white font-semibold mb-4">Your Information</h3>
              <div className="space-y-4">
                <div>
                  <label className="block text-mystic-300 text-sm mb-1">
                    Full Name *
                  </label>
                  <input
                    type="text"
                    required
                    value={formData.name}
                    onChange={(e) =>
                      setFormData({ ...formData, name: e.target.value })
                    }
                    className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-4 py-2.5 text-white placeholder-mystic-500 focus:outline-none focus:border-gold-500"
                    placeholder="Your full name"
                  />
                </div>
                <div>
                  <label className="block text-mystic-300 text-sm mb-1">
                    Email Address *
                  </label>
                  <input
                    type="email"
                    required
                    value={formData.email}
                    onChange={(e) =>
                      setFormData({ ...formData, email: e.target.value })
                    }
                    className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-4 py-2.5 text-white placeholder-mystic-500 focus:outline-none focus:border-gold-500"
                    placeholder="your@email.com"
                  />
                </div>
                <div>
                  <label className="block text-mystic-300 text-sm mb-1">
                    Phone Number *
                  </label>
                  <input
                    type="tel"
                    required
                    value={formData.phone}
                    onChange={(e) =>
                      setFormData({ ...formData, phone: e.target.value })
                    }
                    className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-4 py-2.5 text-white placeholder-mystic-500 focus:outline-none focus:border-gold-500"
                    placeholder="(555) 123-4567"
                  />
                </div>
                <div>
                  <label className="block text-mystic-300 text-sm mb-1">
                    Questions or Notes (Optional)
                  </label>
                  <textarea
                    value={formData.notes}
                    onChange={(e) =>
                      setFormData({ ...formData, notes: e.target.value })
                    }
                    rows={3}
                    className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-4 py-2.5 text-white placeholder-mystic-500 focus:outline-none focus:border-gold-500 resize-none"
                    placeholder="Any specific questions or topics you'd like to explore..."
                  />
                </div>

                <button
                  onClick={handleSubmit}
                  disabled={!formData.name || !formData.email || !formData.phone}
                  className="w-full bg-gold-500 hover:bg-gold-400 disabled:bg-mystic-700 disabled:text-mystic-500 text-mystic-900 py-3 rounded-full font-semibold transition-colors mt-2"
                >
                  Proceed to Payment
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
