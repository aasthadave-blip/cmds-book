import { getActiveServices, getSiteContent, formatPrice } from "@/lib/store";

export const dynamic = "force-dynamic";

export default function HomePage() {
  const services = getActiveServices();
  const content = getSiteContent();
  const featured = services.slice(0, 3);

  return (
    <>
      {/* Hero */}
      <section className="relative stars-bg bg-mystic-900 py-24 px-4 text-center overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-mystic-900/50 via-transparent to-mystic-900" />
        <div className="relative max-w-3xl mx-auto">
          <p className="text-6xl mb-6">🔮</p>
          <h1 className="text-5xl md:text-6xl font-bold text-gold-400 mb-4 font-[Georgia,serif]">
            {content.psychicName}
          </h1>
          <p className="text-mystic-200 text-xl mb-2 italic">{content.tagline}</p>
          <p className="text-mystic-300 text-lg max-w-xl mx-auto mb-8 leading-relaxed">
            {content.heroDescription}
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <a href="/booking" className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-8 py-3 rounded-full font-semibold text-lg transition-colors">
              Book a Reading
            </a>
            <a href="/services" className="border border-gold-500 text-gold-400 hover:bg-gold-500/10 px-8 py-3 rounded-full font-semibold text-lg transition-colors">
              View Services
            </a>
          </div>
        </div>
      </section>

      {/* About */}
      <section className="bg-mystic-800 py-20 px-4">
        <div className="max-w-4xl mx-auto grid md:grid-cols-2 gap-12 items-center">
          <div className="text-center">
            <div className="w-64 h-64 mx-auto rounded-full bg-gradient-to-br from-mystic-600 to-mystic-400 flex items-center justify-center text-8xl shadow-2xl shadow-mystic-500/30">
              🌙
            </div>
          </div>
          <div>
            <h2 className="text-3xl font-bold text-gold-400 mb-4 font-[Georgia,serif]">
              About {content.psychicName}
            </h2>
            {content.aboutText.map((p, i) => (
              <p key={i} className="text-mystic-200 leading-relaxed mb-4">{p}</p>
            ))}
          </div>
        </div>
      </section>

      {/* Services Preview */}
      <section className="py-20 px-4 bg-mystic-900">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-gold-400 text-center mb-3 font-[Georgia,serif]">
            My Services
          </h2>
          <p className="text-mystic-300 text-center mb-12 max-w-xl mx-auto">
            Choose from a variety of readings and spiritual services tailored to your needs.
          </p>
          <div className="grid md:grid-cols-3 gap-8">
            {featured.map((service) => (
              <div key={service.id} className="bg-mystic-800 rounded-2xl p-6 border border-mystic-700/50 hover:border-gold-500/50 transition-all hover:-translate-y-1 flex flex-col">
                {service.popular && (
                  <span className="bg-gold-500 text-mystic-900 text-xs font-bold px-3 py-1 rounded-full self-start mb-3">POPULAR</span>
                )}
                <p className="text-4xl mb-3">{service.icon}</p>
                <h3 className="text-xl font-bold text-white mb-2 font-[Georgia,serif]">{service.name}</h3>
                <p className="text-mystic-300 text-sm leading-relaxed flex-1 mb-4">{service.shortDescription}</p>
                <div className="flex items-center justify-between">
                  <span className="text-gold-400 font-bold text-lg">{formatPrice(service.price)}</span>
                  <span className="text-mystic-400 text-sm">{service.duration} min</span>
                </div>
              </div>
            ))}
          </div>
          <div className="text-center mt-10">
            <a href="/services" className="text-gold-400 hover:text-gold-300 underline underline-offset-4 transition-colors">
              View All Services &rarr;
            </a>
          </div>
        </div>
      </section>

      {/* Testimonials */}
      <section className="bg-mystic-800 py-20 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-gold-400 text-center mb-3 font-[Georgia,serif]">
            What My Clients Say
          </h2>
          <p className="text-mystic-300 text-center mb-12">
            Hear from those whose lives have been touched by spiritual guidance.
          </p>
          <div className="grid md:grid-cols-3 gap-8">
            {content.testimonials.map((t, i) => (
              <div key={i} className="bg-mystic-900 rounded-2xl p-6 border border-mystic-700/50">
                <div className="text-gold-400 mb-3">{"★".repeat(t.stars)}</div>
                <p className="text-mystic-200 text-sm leading-relaxed mb-4 italic">
                  &ldquo;{t.text}&rdquo;
                </p>
                <p className="text-white font-semibold text-sm">{t.name}</p>
                <p className="text-mystic-400 text-xs">{t.location}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-4 bg-mystic-900 stars-bg relative">
        <div className="absolute inset-0 bg-mystic-900/80" />
        <div className="relative max-w-2xl mx-auto text-center">
          <h2 className="text-3xl font-bold text-gold-400 mb-4 font-[Georgia,serif]">
            {content.ctaTitle}
          </h2>
          <p className="text-mystic-200 mb-8 leading-relaxed">{content.ctaDescription}</p>
          <a href="/booking" className="inline-block bg-gold-500 hover:bg-gold-400 text-mystic-900 px-10 py-4 rounded-full font-semibold text-lg transition-colors">
            Book Your Reading Now
          </a>
        </div>
      </section>
    </>
  );
}
