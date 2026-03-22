export interface Service {
  id: string;
  name: string;
  shortDescription: string;
  fullDescription: string;
  duration: number; // minutes
  price: number; // USD cents
  icon: string;
  popular?: boolean;
}

export const services: Service[] = [
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
  },
];

export function getServiceById(id: string): Service | undefined {
  return services.find((s) => s.id === id);
}

export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
