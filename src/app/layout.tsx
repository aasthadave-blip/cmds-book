import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mystic Luna | Psychic Readings & Spiritual Guidance",
  description:
    "Connect with Mystic Luna for tarot readings, psychic medium sessions, astrology, energy healing, and spiritual guidance. Book your session today.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-mystic-900 text-white min-h-screen flex flex-col">
        {children}
      </body>
    </html>
  );
}
