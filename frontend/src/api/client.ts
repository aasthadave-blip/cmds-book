export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export type UUID = string;

export interface Book {
  id: UUID;
  title: string;
  subject: string | null;
  pdf_url: string | null;
  schema_: BookSchema | null;
  analyser: AnalyserResult | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface AnalyserResult {
  pdf_type: "digital" | "scanned" | "mixed";
  estimated_pages: number;
  estimated_words: number;
  document_title: string;
  subject: string;
  has_equations: boolean;
  has_tables: boolean;
  has_diagrams: boolean;
}

export interface BookSchema {
  document_title: string;
  subject: string;
  sections: SchemaSection[];
  exclusion_summary: string[];
}

export interface SchemaSection {
  id: string;
  level: number;
  title: string;
  type: "chapter" | "section" | "subsection" | "excluded";
  content_types: string[];
  subsections: SchemaSection[];
}

export type Block =
  | { t: "p"; c: string }
  | { t: "h3"; c: string }
  | { t: "eq"; c: string }
  | { t: "def"; term: string; c: string }
  | { t: "kp"; c: string }
  | { t: "fig"; c: string }
  | { t: "list"; items: string[] }
  | { t: "table"; caption: string; headers: string[]; rows: string[][] }
  | { t: "example"; label: string; prob: string; eqs: string[] };

export interface Section {
  id: UUID;
  book_id: UUID;
  section_id: string;
  title: string;
  level: number | null;
  blocks: Block[];
  qc_local: Record<string, unknown> | null;
  qc_llm: Record<string, unknown> | null;
  status: string;
  attempts: number;
}

export interface Job {
  id: UUID;
  book_id: UUID | null;
  type: string;
  status: string;
  progress: number;
  message: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface RegenParams {
  intensity: "light" | "moderate" | "heavy";
  tone: "academic" | "conversational" | "simplified";
  equations_handling: "preserve" | "explain";
  diagrams_handling: "preserve" | "describe";
  analogies: "none" | "add_one" | "add_multiple";
  structure: "identical" | "reorganize";
  language: string;
  target_audience?: string | null;
  custom_instructions?: string | null;
}

export interface Regeneration {
  id: UUID;
  book_id: UUID;
  params: RegenParams;
  blocks_by_section: Record<string, Block[]>;
  qc_drift: Record<string, { pass: boolean; drifted: string[] }> | null;
  created_at: string;
}

export interface Provider {
  name: string;
  handles: string[];
  cost_per_page: number;
  avg_time_per_page: number;
  configured: boolean;
  healthy: boolean;
  message: string | null;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
    },
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText}${text ? ` — ${text}` : ""}`);
  }
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

export const api = {
  listBooks: () => req<Book[]>("/api/books"),
  getBook: (id: UUID) => req<Book>(`/api/books/${id}`),
  uploadBook: (file: File, title?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (title) fd.append("title", title);
    return req<{ book_id: UUID; status: string }>("/api/books", {
      method: "POST",
      body: fd,
    });
  },
  deleteBook: (id: UUID) => req<void>(`/api/books/${id}`, { method: "DELETE" }),
  analyse: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(`/api/books/${id}/analyse`, {
      method: "POST",
    }),
  patchSchema: (id: UUID, schema: BookSchema) =>
    req<Book>(`/api/books/${id}/schema`, {
      method: "PATCH",
      body: JSON.stringify(schema),
    }),
  approve: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(`/api/books/${id}/approve`, {
      method: "POST",
    }),
  listSections: (bookId: UUID) => req<Section[]>(`/api/books/${bookId}/sections`),
  getSection: (id: UUID) => req<Section>(`/api/sections/${id}`),
  reExtractSection: (id: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(
      `/api/sections/${id}/re-extract`,
      { method: "POST" },
    ),
  reExtractBook: (bookId: UUID) =>
    req<{ book_id: UUID; job_id: UUID; status: string }>(
      `/api/books/${bookId}/re-extract`,
      { method: "POST" },
    ),
  regenerate: (bookId: UUID, params: RegenParams, sectionIds?: string[] | null) =>
    req<{ book_id: UUID; job_id: UUID; regen_id: UUID; status: string }>(
      `/api/books/${bookId}/regenerate`,
      {
        method: "POST",
        body: JSON.stringify(
          sectionIds && sectionIds.length > 0 ? { ...params, section_ids: sectionIds } : params,
        ),
      },
    ),
  listRegenerations: (bookId: UUID) => req<Regeneration[]>(`/api/books/${bookId}/regenerations`),
  getRegeneration: (id: UUID) => req<Regeneration>(`/api/regenerations/${id}`),
  rerunSection: (regenId: UUID, sectionId: string, customInstructions: string) =>
    req<{ section_id: string; blocks: Block[] }>(
      `/api/regenerations/${regenId}/sections/${sectionId}/rerun`,
      { method: "POST", body: JSON.stringify({ custom_instructions: customInstructions }) },
    ),
  saveRegeneration: (regenId: UUID, confirmedSectionIds: string[]) =>
    req<{ saved: boolean; sections_saved: number }>(
      `/api/regenerations/${regenId}/save`,
      { method: "POST", body: JSON.stringify({ confirmed_section_ids: confirmedSectionIds }) },
    ),
  exportMarkdown: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/markdown${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportJson: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/json${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportDocx: (bookId: UUID, regenId?: UUID | null) => {
    const a = document.createElement("a");
    const qs = regenId ? `?regen_id=${regenId}` : "";
    a.href = `${API_BASE}/api/books/${bookId}/export/docx${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  getJob: (id: UUID) => req<Job>(`/api/jobs/${id}`),
  listProviders: () => req<Provider[]>("/api/providers"),
  getProviderKeyStatus: (name: string) =>
    req<{ provider: string; configured: boolean }>(`/api/providers/${name}/keys`),
  saveProviderKeys: (name: string, keys: Record<string, unknown>) =>
    req<{ saved: boolean; valid: boolean; provider: string }>(
      `/api/providers/${name}/keys`,
      { method: "POST", body: JSON.stringify(keys) },
    ),
};
