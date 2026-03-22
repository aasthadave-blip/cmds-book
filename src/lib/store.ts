import fs from "fs";
import path from "path";

const DATA_DIR = path.join(process.cwd(), "data");

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
}

function readJSON<T>(filename: string, defaultValue: T): T {
  ensureDataDir();
  const filePath = path.join(DATA_DIR, filename);
  if (!fs.existsSync(filePath)) {
    writeJSON(filename, defaultValue);
    return defaultValue;
  }
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return defaultValue;
  }
}

function writeJSON<T>(filename: string, data: T): void {
  ensureDataDir();
  const filePath = path.join(DATA_DIR, filename);
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2), "utf-8");
}

// ---- Services ----
export interface ServiceData {
  id: string;
  name: string;
  shortDescription: string;
  fullDescription: string;
  duration: number;
  price: number;
  icon: string;
  popular?: boolean;
  active: boolean;
}

const DEFAULT_SERVICES: ServiceData[] = [
  {
    id: "tarot-reading",
    name: "Tarot Card Reading",
    shortDescription: "Gain clarity and insight through the ancient wisdom of tarot cards.",
    fullDescription:
      "Experience a comprehensive tarot card reading that explores your past, present, and future. Using a traditional Celtic Cross spread, I will uncover hidden influences, reveal opportunities, and provide guidance on your life path. Each reading is deeply personal and tailored to your specific questions and concerns. Whether you're seeking answers about love, career, finances, or personal growth, the cards will illuminate the way forward.",
    duration: 30,
    price: 7500,
    icon: "🃏",
    popular: true,
    active: true,
  },
  {
    id: "psychic-medium",
    name: "Psychic Medium Session",
    shortDescription: "Connect with loved ones who have passed to the other side.",
    fullDescription:
      "In this deeply moving session, I serve as a bridge between you and your departed loved ones. Through my mediumship abilities, I receive messages, memories, and validations from the spirit world. This session can bring healing, closure, and reassurance that your loved ones are at peace. I create a safe, compassionate space for this sacred connection. Please come with an open heart and mind.",
    duration: 60,
    price: 15000,
    icon: "🔮",
    popular: true,
    active: true,
  },
  {
    id: "love-reading",
    name: "Love & Relationship Reading",
    shortDescription: "Explore the energies surrounding your romantic life and partnerships.",
    fullDescription:
      "This specialized reading focuses entirely on matters of the heart. Whether you're single and looking for love, in a new relationship, or navigating challenges in a long-term partnership, I will tune into the energies surrounding your love life. Discover compatibility insights, identify blockages to love, and receive guidance on how to attract or strengthen your romantic connections. Includes a personalized love affirmation.",
    duration: 45,
    price: 9500,
    icon: "💜",
    active: true,
  },
  {
    id: "energy-healing",
    name: "Energy Healing & Chakra Balancing",
    shortDescription: "Restore balance and harmony to your body's energy centers.",
    fullDescription:
      "This powerful healing session combines intuitive energy work with chakra balancing to restore harmony to your mind, body, and spirit. I will scan your energy field, identify blockages or imbalances, and channel healing energy to clear and realign your chakras. Many clients report feeling lighter, more centered, and deeply relaxed after this session. Ideal for those experiencing stress, emotional turbulence, or a general sense of being 'stuck'.",
    duration: 60,
    price: 12000,
    icon: "✨",
    active: true,
  },
  {
    id: "astrology-reading",
    name: "Birth Chart & Astrology Reading",
    shortDescription: "Unlock the secrets written in the stars at the moment of your birth.",
    fullDescription:
      "Discover your cosmic blueprint with a detailed birth chart analysis. Using your exact date, time, and place of birth, I will map out the positions of the planets and interpret how they influence your personality, strengths, challenges, and life purpose. This reading covers your Sun, Moon, and Rising signs, as well as key planetary aspects and current transits affecting your life. Birth details (date, time, location) are required.",
    duration: 60,
    price: 12500,
    icon: "⭐",
    active: true,
  },
  {
    id: "spiritual-guidance",
    name: "Spiritual Life Coaching Session",
    shortDescription: "Receive personalized spiritual guidance for your life journey.",
    fullDescription:
      "This comprehensive session combines psychic insight with practical spiritual coaching. Together, we'll explore your soul's purpose, identify patterns holding you back, and create an actionable plan for spiritual growth and life fulfillment. I draw upon my intuitive gifts along with years of spiritual study to offer guidance that is both mystical and grounded. Ideal for those at a crossroads or seeking deeper meaning in life. Includes a follow-up email summary.",
    duration: 90,
    price: 20000,
    icon: "🌙",
    active: true,
  },
];

export function getServices(): ServiceData[] {
  return readJSON("services.json", DEFAULT_SERVICES);
}

export function getActiveServices(): ServiceData[] {
  return getServices().filter((s) => s.active);
}

export function getServiceById(id: string): ServiceData | undefined {
  return getServices().find((s) => s.id === id);
}

export function saveServices(services: ServiceData[]): void {
  writeJSON("services.json", services);
}

export function addService(service: ServiceData): void {
  const services = getServices();
  services.push(service);
  saveServices(services);
}

export function updateService(id: string, updates: Partial<ServiceData>): ServiceData | null {
  const services = getServices();
  const index = services.findIndex((s) => s.id === id);
  if (index === -1) return null;
  services[index] = { ...services[index], ...updates };
  saveServices(services);
  return services[index];
}

export function deleteService(id: string): boolean {
  const services = getServices();
  const filtered = services.filter((s) => s.id !== id);
  if (filtered.length === services.length) return false;
  saveServices(filtered);
  return true;
}

// ---- Schedule Settings ----
export interface ScheduleSettings {
  startHour: number;
  endHour: number;
  slotInterval: number;
  daysOff: number[]; // 0=Sun, 1=Mon, etc.
  blockedDates: string[]; // "YYYY-MM-DD"
  timezone: string;
}

const DEFAULT_SCHEDULE: ScheduleSettings = {
  startHour: 10,
  endHour: 18,
  slotInterval: 30,
  daysOff: [0],
  blockedDates: [],
  timezone: "America/New_York",
};

export function getSchedule(): ScheduleSettings {
  return readJSON("schedule.json", DEFAULT_SCHEDULE);
}

export function saveSchedule(schedule: ScheduleSettings): void {
  writeJSON("schedule.json", schedule);
}

// ---- Bookings ----
export interface BookingData {
  id: string;
  serviceId: string;
  serviceName: string;
  date: string;
  time: string;
  clientName: string;
  clientEmail: string;
  clientPhone: string;
  notes?: string;
  paymentIntentId?: string;
  status: "pending" | "confirmed" | "cancelled";
  createdAt: string;
  amount: number;
}

export function getBookings(): BookingData[] {
  return readJSON("bookings.json", []);
}

export function saveBookings(bookings: BookingData[]): void {
  writeJSON("bookings.json", bookings);
}

export function addBooking(booking: BookingData): void {
  const bookings = getBookings();
  bookings.push(booking);
  saveBookings(bookings);
}

export function getBookingById(id: string): BookingData | null {
  return getBookings().find((b) => b.id === id) || null;
}

export function updateBooking(id: string, updates: Partial<BookingData>): BookingData | null {
  const bookings = getBookings();
  const index = bookings.findIndex((b) => b.id === id);
  if (index === -1) return null;
  bookings[index] = { ...bookings[index], ...updates };
  saveBookings(bookings);
  return bookings[index];
}

// ---- Site Content ----
export interface SiteContent {
  psychicName: string;
  tagline: string;
  aboutText: string[];
  heroDescription: string;
  ctaTitle: string;
  ctaDescription: string;
  testimonials: {
    name: string;
    location: string;
    text: string;
    stars: number;
  }[];
}

const DEFAULT_CONTENT: SiteContent = {
  psychicName: "Mystic Luna",
  tagline: "Gifted Psychic & Spiritual Advisor",
  aboutText: [
    "Welcome, dear soul. I am Luna, a third-generation psychic medium born and raised in the heart of the United States. From a young age, I discovered my ability to sense energies and receive messages from the spiritual realm.",
    "With over 15 years of professional experience, I have guided thousands of clients through life's most challenging moments — from matters of the heart to career crossroads, from grief and loss to spiritual awakening.",
    "I specialize in tarot reading, mediumship, astrology, and energy healing. Every session is conducted with compassion, honesty, and a deep respect for your journey. My mission is to empower you with the insight you need to live your best life.",
  ],
  heroDescription:
    "Unlock the mysteries of your past, present, and future. With over 15 years of experience, I channel divine guidance to help you find clarity, healing, and direction on your life's journey.",
  ctaTitle: "Ready to Discover Your Path?",
  ctaDescription:
    "Take the first step toward clarity and empowerment. Book your personal reading today and let the universe reveal what's in store for you.",
  testimonials: [
    {
      name: "Sarah M.",
      location: "California",
      text: "Luna's reading was incredibly accurate and gave me the clarity I desperately needed. She connected with my late mother and delivered messages that only she would know. Truly gifted.",
      stars: 5,
    },
    {
      name: "James T.",
      location: "New York",
      text: "I was skeptical at first, but Luna's tarot reading completely changed my perspective. Her insights about my career path were spot-on, and I followed her guidance to a much better place in life.",
      stars: 5,
    },
    {
      name: "Maria L.",
      location: "Texas",
      text: "The energy healing session with Luna was transformative. I felt a weight lift off my shoulders that I'd been carrying for years. She's compassionate, genuine, and incredibly talented.",
      stars: 5,
    },
  ],
};

export function getSiteContent(): SiteContent {
  return readJSON("content.json", DEFAULT_CONTENT);
}

export function saveSiteContent(content: SiteContent): void {
  writeJSON("content.json", content);
}

// ---- Helpers ----
export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
