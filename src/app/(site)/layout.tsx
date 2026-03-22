function Navbar() {
  return (
    <nav className="bg-mystic-900/95 backdrop-blur-sm border-b border-mystic-700/50 sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
        <a href="/" className="flex items-center gap-2">
          <span className="text-3xl">🔮</span>
          <span className="text-xl font-bold text-gold-400 font-[Georgia,serif]">
            Mystic Luna
          </span>
        </a>
        <div className="flex items-center gap-6">
          <a href="/" className="text-mystic-200 hover:text-gold-400 transition-colors text-sm">Home</a>
          <a href="/services" className="text-mystic-200 hover:text-gold-400 transition-colors text-sm">Services</a>
          <a href="/booking" className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-4 py-2 rounded-full text-sm font-semibold transition-colors">Book Now</a>
        </div>
      </div>
    </nav>
  );
}

function Footer() {
  return (
    <footer className="bg-mystic-900 border-t border-mystic-700/50 py-10">
      <div className="max-w-6xl mx-auto px-4 text-center">
        <p className="text-3xl mb-3">🔮</p>
        <p className="text-mystic-300 text-sm mb-2">
          Mystic Luna &mdash; Psychic Readings & Spiritual Guidance
        </p>
        <p className="text-mystic-500 text-xs mb-4">
          Based in the United States &bull; Available for virtual sessions worldwide
        </p>
        <p className="text-mystic-600 text-xs">
          For entertainment purposes. All readings are subject to personal interpretation.
        </p>
      </div>
    </footer>
  );
}

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </>
  );
}
