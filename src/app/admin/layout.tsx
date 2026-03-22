"use client";

import { useState, useEffect, createContext, useContext } from "react";

const AuthContext = createContext<{
  password: string;
  setPassword: (p: string) => void;
  headers: () => Record<string, string>;
}>({
  password: "",
  setPassword: () => {},
  headers: () => ({}),
});

export function useAdmin() {
  return useContext(AuthContext);
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [password, setPassword] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [loginInput, setLoginInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const saved = sessionStorage.getItem("admin_password");
    if (saved) {
      setPassword(saved);
      setAuthenticated(true);
    }
    setLoading(false);
  }, []);

  const headers = () => ({
    "Content-Type": "application/json",
    "x-admin-password": password,
  });

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await fetch("/api/admin", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-admin-password": loginInput,
        },
        body: JSON.stringify({ action: "login" }),
      });
      if (res.ok) {
        setPassword(loginInput);
        sessionStorage.setItem("admin_password", loginInput);
        setAuthenticated(true);
      } else {
        setError("Invalid password");
      }
    } catch {
      setError("Connection error");
    }
  }

  function handleLogout() {
    setPassword("");
    setAuthenticated(false);
    sessionStorage.removeItem("admin_password");
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-mystic-900 flex items-center justify-center">
        <p className="text-mystic-400 animate-pulse">Loading...</p>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="min-h-screen bg-mystic-900 flex items-center justify-center px-4">
        <div className="bg-mystic-800 rounded-2xl p-8 border border-mystic-700/50 w-full max-w-sm">
          <div className="text-center mb-6">
            <p className="text-4xl mb-3">🔐</p>
            <h1 className="text-2xl font-bold text-gold-400 font-[Georgia,serif]">Admin Panel</h1>
            <p className="text-mystic-400 text-sm mt-1">Enter your admin password</p>
          </div>
          <form onSubmit={handleLogin}>
            <input
              type="password"
              value={loginInput}
              onChange={(e) => setLoginInput(e.target.value)}
              placeholder="Password"
              className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-4 py-3 text-white placeholder-mystic-500 focus:outline-none focus:border-gold-500 mb-3"
            />
            {error && <p className="text-red-400 text-sm mb-3">{error}</p>}
            <button
              type="submit"
              className="w-full bg-gold-500 hover:bg-gold-400 text-mystic-900 py-3 rounded-lg font-semibold transition-colors"
            >
              Sign In
            </button>
          </form>
        </div>
      </div>
    );
  }

  const navItems = [
    { href: "/admin", label: "Dashboard", icon: "📊" },
    { href: "/admin/services", label: "Services", icon: "✨" },
    { href: "/admin/schedule", label: "Schedule", icon: "📅" },
    { href: "/admin/bookings", label: "Bookings", icon: "📋" },
    { href: "/admin/content", label: "Content", icon: "📝" },
  ];

  return (
    <AuthContext.Provider value={{ password, setPassword, headers }}>
      <div className="min-h-screen bg-gray-950 flex">
        {/* Sidebar */}
        <aside className="w-64 bg-mystic-900 border-r border-mystic-700/50 flex flex-col flex-shrink-0">
          <div className="p-4 border-b border-mystic-700/50">
            <a href="/" className="flex items-center gap-2">
              <span className="text-xl">🔮</span>
              <span className="text-gold-400 font-bold font-[Georgia,serif]">Mystic Luna</span>
            </a>
            <p className="text-mystic-500 text-xs mt-1">Admin Panel</p>
          </div>
          <nav className="flex-1 p-3 space-y-1">
            {navItems.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-mystic-300 hover:bg-mystic-800 hover:text-white transition-colors text-sm"
              >
                <span>{item.icon}</span>
                {item.label}
              </a>
            ))}
          </nav>
          <div className="p-3 border-t border-mystic-700/50">
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-mystic-400 hover:bg-red-900/30 hover:text-red-300 transition-colors text-sm"
            >
              <span>🚪</span> Sign Out
            </button>
            <a
              href="/"
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-mystic-400 hover:bg-mystic-800 hover:text-white transition-colors text-sm mt-1"
            >
              <span>🌐</span> View Site
            </a>
          </div>
        </aside>

        {/* Main */}
        <main className="flex-1 overflow-auto">
          <div className="p-8">{children}</div>
        </main>
      </div>
    </AuthContext.Provider>
  );
}
