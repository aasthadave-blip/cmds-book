import { getActiveServices, formatPrice } from "@/lib/store";

export const dynamic = "force-dynamic";

export default function ServicesPage() {
  const services = getActiveServices();

  return (
    <div className="py-16 px-4">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <p className="text-4xl mb-4">✨</p>
          <h1 className="text-4xl md:text-5xl font-bold text-gold-400 mb-4 font-[Georgia,serif]">
            Services &amp; Pricing
          </h1>
          <p className="text-mystic-300 max-w-2xl mx-auto leading-relaxed">
            Each session is conducted virtually via video call or phone, providing a comfortable
            and private experience from anywhere in the world.
          </p>
        </div>

        <div className="space-y-8">
          {services.map((service) => (
            <div key={service.id} className="bg-mystic-800 rounded-2xl border border-mystic-700/50 overflow-hidden">
              <div className="p-8 md:flex md:items-start md:gap-8">
                <div className="md:w-20 text-center md:text-left flex-shrink-0 mb-4 md:mb-0">
                  <span className="text-5xl">{service.icon}</span>
                </div>
                <div className="flex-1">
                  <div className="flex flex-wrap items-start justify-between gap-4 mb-3">
                    <div>
                      <h2 className="text-2xl font-bold text-white font-[Georgia,serif]">{service.name}</h2>
                      {service.popular && (
                        <span className="inline-block bg-gold-500 text-mystic-900 text-xs font-bold px-3 py-0.5 rounded-full mt-1">MOST POPULAR</span>
                      )}
                    </div>
                    <div className="text-right">
                      <p className="text-2xl font-bold text-gold-400">{formatPrice(service.price)}</p>
                      <p className="text-mystic-400 text-sm">{service.duration} minutes</p>
                    </div>
                  </div>
                  <p className="text-mystic-200 leading-relaxed mb-6">{service.fullDescription}</p>
                  <div className="flex flex-wrap gap-3 mb-6">
                    <span className="bg-mystic-700/50 text-mystic-200 text-xs px-3 py-1 rounded-full">Virtual Session</span>
                    <span className="bg-mystic-700/50 text-mystic-200 text-xs px-3 py-1 rounded-full">{service.duration} Min Duration</span>
                    <span className="bg-mystic-700/50 text-mystic-200 text-xs px-3 py-1 rounded-full">Recording Available</span>
                    <span className="bg-mystic-700/50 text-mystic-200 text-xs px-3 py-1 rounded-full">Email Follow-Up</span>
                  </div>
                  <a href={`/booking?service=${service.id}`} className="inline-block bg-gold-500 hover:bg-gold-400 text-mystic-900 px-6 py-2.5 rounded-full font-semibold transition-colors">
                    Book This Service
                  </a>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-20">
          <h2 className="text-3xl font-bold text-gold-400 text-center mb-10 font-[Georgia,serif]">Frequently Asked Questions</h2>
          <div className="max-w-3xl mx-auto space-y-6">
            {[
              { q: "How do virtual sessions work?", a: "All sessions are conducted via secure video call (Zoom) or phone. After booking and payment, you'll receive a confirmation email with your session link and preparation instructions." },
              { q: "What should I prepare for my reading?", a: "Come with an open mind and heart. You may prepare specific questions you'd like to explore. For astrology readings, please have your birth date, exact time, and location ready." },
              { q: "What is your cancellation policy?", a: "You may reschedule or cancel up to 24 hours before your appointment for a full refund. Cancellations within 24 hours are non-refundable but can be rescheduled once." },
              { q: "Are readings confidential?", a: "Absolutely. Everything discussed in our sessions is completely confidential. Your privacy and trust are of the utmost importance to me." },
              { q: "What time zone are you in?", a: "I operate on Eastern Time (ET). All booking times displayed on the calendar are in ET. I serve clients across all US time zones and internationally." },
            ].map((faq, i) => (
              <div key={i} className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
                <h3 className="text-white font-semibold mb-2">{faq.q}</h3>
                <p className="text-mystic-300 text-sm leading-relaxed">{faq.a}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
