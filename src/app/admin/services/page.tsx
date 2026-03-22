"use client";

import { useState, useEffect } from "react";
import { useAdmin } from "../layout";

interface Service {
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

const EMPTY_SERVICE: Omit<Service, "id"> = {
  name: "",
  shortDescription: "",
  fullDescription: "",
  duration: 30,
  price: 5000,
  icon: "🔮",
  popular: false,
  active: true,
};

export default function AdminServicesPage() {
  const { headers } = useAdmin();
  const [services, setServices] = useState<Service[]>([]);
  const [editing, setEditing] = useState<Service | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<any>(EMPTY_SERVICE);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  function loadServices() {
    fetch("/api/admin?section=services", { headers: headers() })
      .then((r) => r.json())
      .then((d) => setServices(d.services || []));
  }

  useEffect(() => { loadServices(); }, []);

  function showMessage(msg: string) {
    setMessage(msg);
    setTimeout(() => setMessage(""), 3000);
  }

  async function handleSave() {
    setSaving(true);
    const action = creating ? "add-service" : "update-service";
    const body = creating
      ? { action, service: { ...form, id: "" } }
      : { action, id: editing!.id, updates: form };

    const res = await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body),
    });

    if (res.ok) {
      showMessage(creating ? "Service added!" : "Service updated!");
      setEditing(null);
      setCreating(false);
      loadServices();
    }
    setSaving(false);
  }

  async function handleDelete(id: string) {
    if (!confirm("Are you sure you want to delete this service?")) return;
    await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action: "delete-service", id }),
    });
    showMessage("Service deleted!");
    loadServices();
  }

  async function handleToggleActive(id: string, active: boolean) {
    await fetch("/api/admin", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action: "update-service", id, updates: { active: !active } }),
    });
    loadServices();
  }

  function openEdit(service: Service) {
    setEditing(service);
    setCreating(false);
    setForm({ ...service });
  }

  function openCreate() {
    setCreating(true);
    setEditing(null);
    setForm({ ...EMPTY_SERVICE });
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Manage Services</h1>
        <button
          onClick={openCreate}
          className="bg-gold-500 hover:bg-gold-400 text-mystic-900 px-4 py-2 rounded-lg font-semibold text-sm transition-colors"
        >
          + Add Service
        </button>
      </div>

      {message && (
        <div className="bg-green-900/30 border border-green-700 text-green-300 rounded-lg px-4 py-2 mb-4 text-sm">
          {message}
        </div>
      )}

      {/* Service List */}
      {!editing && !creating && (
        <div className="space-y-3">
          {services.map((s) => (
            <div key={s.id} className={`bg-mystic-800 rounded-xl p-4 border flex items-center gap-4 ${s.active ? "border-mystic-700/50" : "border-red-900/50 opacity-60"}`}>
              <span className="text-3xl">{s.icon}</span>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-white font-semibold">{s.name}</h3>
                  {s.popular && <span className="text-gold-400 text-xs font-bold">POPULAR</span>}
                  {!s.active && <span className="text-red-400 text-xs font-bold">INACTIVE</span>}
                </div>
                <p className="text-mystic-400 text-sm">{s.duration} min &bull; ${(s.price / 100).toFixed(2)}</p>
              </div>
              <div className="flex gap-2">
                <button onClick={() => handleToggleActive(s.id, s.active)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${s.active ? "bg-red-900/30 text-red-300 hover:bg-red-900/50" : "bg-green-900/30 text-green-300 hover:bg-green-900/50"}`}>
                  {s.active ? "Deactivate" : "Activate"}
                </button>
                <button onClick={() => openEdit(s)} className="bg-mystic-700 hover:bg-mystic-600 text-white px-3 py-1.5 rounded-lg text-xs transition-colors">Edit</button>
                <button onClick={() => handleDelete(s.id)} className="bg-red-900/30 hover:bg-red-900/50 text-red-300 px-3 py-1.5 rounded-lg text-xs transition-colors">Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Edit/Create Form */}
      {(editing || creating) && (
        <div className="bg-mystic-800 rounded-xl p-6 border border-mystic-700/50">
          <h2 className="text-white font-semibold mb-4">
            {creating ? "Add New Service" : `Edit: ${editing!.name}`}
          </h2>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Service Name</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Icon (emoji)</label>
              <input value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Duration (minutes)</label>
              <input type="number" value={form.duration} onChange={(e) => setForm({ ...form, duration: parseInt(e.target.value) || 0 })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div>
              <label className="block text-mystic-400 text-xs mb-1">Price (in cents, e.g., 7500 = $75.00)</label>
              <input type="number" value={form.price} onChange={(e) => setForm({ ...form, price: parseInt(e.target.value) || 0 })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div className="md:col-span-2">
              <label className="block text-mystic-400 text-xs mb-1">Short Description</label>
              <input value={form.shortDescription} onChange={(e) => setForm({ ...form, shortDescription: e.target.value })}
                className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500" />
            </div>
            <div className="md:col-span-2">
              <label className="block text-mystic-400 text-xs mb-1">Full Description</label>
              <textarea value={form.fullDescription} onChange={(e) => setForm({ ...form, fullDescription: e.target.value })}
                rows={4} className="w-full bg-mystic-900 border border-mystic-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-gold-500 resize-none" />
            </div>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-sm text-mystic-300 cursor-pointer">
                <input type="checkbox" checked={form.popular || false} onChange={(e) => setForm({ ...form, popular: e.target.checked })}
                  className="accent-gold-500" /> Mark as Popular
              </label>
              <label className="flex items-center gap-2 text-sm text-mystic-300 cursor-pointer">
                <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })}
                  className="accent-gold-500" /> Active
              </label>
            </div>
          </div>
          <div className="flex gap-3 mt-6">
            <button onClick={handleSave} disabled={saving || !form.name}
              className="bg-gold-500 hover:bg-gold-400 disabled:bg-mystic-700 text-mystic-900 px-6 py-2 rounded-lg font-semibold text-sm transition-colors">
              {saving ? "Saving..." : "Save"}
            </button>
            <button onClick={() => { setEditing(null); setCreating(false); }}
              className="bg-mystic-700 hover:bg-mystic-600 text-white px-6 py-2 rounded-lg text-sm transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
