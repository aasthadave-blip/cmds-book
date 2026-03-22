"use client";

import { useState, useEffect } from "react";
import { useAdmin } from "../layout";

interface Testimonial {
  name: string;
  location: string;
  text: string;
  stars: number;
}

interface SiteContent {
  psychicName: string;
  tagline: string;
  aboutText: string[];
  heroDescription: string;
  ctaTitle: string;
  ctaDescription: string;
  testimonials: Testimonial[];
}

export default function AdminContentPage() {
  const { headers } = useAdmin();
  const [content, setContent] = useState<SiteContent | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    fetch("/api/admin?section=content", { headers: headers() })
      .then((r) => r.json())
      .then((d) => setContent(d.content));
  }, []);

  async function handleSave() {
    if (!content) return;
    setSaving(true);
    const res = await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action: "update-content", content }),
    });
    if (res.ok) {
      setMessage("Content updated! Changes are live on the site.");
      setTimeout(() => setMessage(""), 4000);
    }
    setSaving(false);
  }

  function updateTestimonial(index: number, field: keyof Testimonial, value: string | number) {
    if (!content) return;
    const testimonials = [...content.testimonials];
    testimonials[index] = { ...testimonials[index], [field]: value };
    setContent({ ...content, testimonials });
  }

  function addTestimonial() {
    if (!content) return;
    setContent({
      ...content,
      testimonials: [...content.testimonials, { name: "", location: "", text: "", stars: 5 }],
    });
  }

  function removeTestimonial(index: number) {
    if (!content) return;
    setContent({
      ...content,
      testimonials: content.testimonials.filter((_, i) => i !== index),
    });
  }

  if (!content) {
    return <p className="text-mystic-400 animate-pulse">Loading content...</p>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Edit Site Content</h1>
        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-6 py-2 rounded-lg font-semibold text-sm transition-colors"
        >
          {saving ? "Saving..." : "Save All Changes"}
        </button>
      </div>

      {message && (
        <div className="bg-green-900/30 border border-green-700 text-green-300 rounded-lg px-4 py-2 mb-4 text-sm">
          {message}
        </div>
      )}

      <div className="space-y-6">
        {/* Basic Info */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Basic Information</h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Psychic Name</label>
              <input value={content.psychicName}
                onChange={(e) => setContent({ ...content, psychicName: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Tagline</label>
              <input value={content.tagline}
                onChange={(e) => setContent({ ...content, tagline: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
          </div>
        </div>

        {/* Hero Section */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Hero Section</h2>
          <div>
            <label className="block text-mystic-400 text-xs mb-1">Hero Description</label>
            <textarea value={content.heroDescription}
              onChange={(e) => setContent({ ...content, heroDescription: e.target.value })}
              rows={3} className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500 resize-none" />
          </div>
        </div>

        {/* About Section */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">About Section</h2>
          {content.aboutText.map((text, i) => (
            <div key={i} className="mb-3">
              <label className="block text-mystic-400 text-xs mb-1">Paragraph {i + 1}</label>
              <textarea value={text}
                onChange={(e) => {
                  const aboutText = [...content.aboutText];
                  aboutText[i] = e.target.value;
                  setContent({ ...content, aboutText });
                }}
                rows={3} className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500 resize-none" />
            </div>
          ))}
          <div className="flex gap-2">
            <button onClick={() => setContent({ ...content, aboutText: [...content.aboutText, ""] })}
              className="text-gold-400 hover:text-gold-300 text-sm">+ Add Paragraph</button>
            {content.aboutText.length > 1 && (
              <button onClick={() => setContent({ ...content, aboutText: content.aboutText.slice(0, -1) })}
                className="text-red-400 hover:text-red-300 text-sm">- Remove Last</button>
            )}
          </div>
        </div>

        {/* CTA Section */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">Call to Action Section</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-mystic-400 text-xs mb-1">CTA Title</label>
              <input value={content.ctaTitle}
                onChange={(e) => setContent({ ...content, ctaTitle: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">CTA Description</label>
              <textarea value={content.ctaDescription}
                onChange={(e) => setContent({ ...content, ctaDescription: e.target.value })}
                rows={2} className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500 resize-none" />
            </div>
          </div>
        </div>

        {/* Testimonials */}
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-semibold">Testimonials</h2>
            <button onClick={addTestimonial}
              className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-3 py-1 rounded-lg text-xs font-semibold transition-colors">
              + Add Testimonial
            </button>
          </div>
          <div className="space-y-4">
            {content.testimonials.map((t, i) => (
              <div key={i} className="bg-mystic-900 rounded-lg p-4 border border-mystic-700/30">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-mystic-400 text-xs">Testimonial #{i + 1}</span>
                  <button onClick={() => removeTestimonial(i)}
                    className="text-red-400 hover:text-red-300 text-xs">Remove</button>
                </div>
                <div className="grid md:grid-cols-3 gap-3 mb-3">
                  <input value={t.name} placeholder="Name"
                    onChange={(e) => updateTestimonial(i, "name", e.target.value)}
                    className="bg-mystic-800 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
                  <input value={t.location} placeholder="Location"
                    onChange={(e) => updateTestimonial(i, "location", e.target.value)}
                    className="bg-mystic-800 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
                  <select value={t.stars}
                    onChange={(e) => updateTestimonial(i, "stars", parseInt(e.target.value))}
                    className="bg-mystic-800 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500">
                    {[5, 4, 3, 2, 1].map((n) => (
                      <option key={n} value={n}>{n} Stars</option>
                    ))}
                  </select>
                </div>
                <textarea value={t.text} placeholder="Testimonial text..."
                  onChange={(e) => updateTestimonial(i, "text", e.target.value)}
                  rows={2} className="w-full bg-mystic-800 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500 resize-none" />
              </div>
            ))}
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-8 py-3 rounded-lg font-semibold transition-colors"
        >
          {saving ? "Saving..." : "Save All Changes"}
        </button>
      </div>
    </div>
  );
}
