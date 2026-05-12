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
  excluded_sections?: ExcludedSection[];
}

export interface ExcludedSection {
  title: string;
  reason?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  expected_question_count?: number;
  subsections?: ExcludedSection[];
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
  | { t: "fig"; c: string; label?: string }
  | { t: "list"; items: string[] }
  | { t: "table"; caption: string; headers: string[]; rows: string[][] }
  | { t: "example"; label: string; prob: string; eqs: string[] }
  | { t: "example_ref"; label: string; number?: string; section_id?: string; question_id?: string }
  | { t: "exercise_ref"; label: string; number?: string; section_id?: string; question_id?: string }
  | { t: "question_ref"; label: string; number?: string; section_id?: string; question_id?: string };

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

export interface ExtractionBlockStats {
  excluded_block_index: number;
  title: string;
  page_start: number | null;
  page_end: number | null;
  section_ref: string | null;
  link_method: string;
  link_confidence: number;
  identified: number;
  extracted: number;
  attempts: number;
  missed: number;
  status: "ok" | "partial" | "empty" | "failed";
  failures: string[];
}

export interface ExtractionStats {
  total_identified: number;
  total_extracted: number;
  missed: number;
  blocks: ExtractionBlockStats[];
  // v3-only (optional — present when worker_version === "v3")
  worker_version?: "v2" | "v3";
  totals?: {
    expected_total: number;
    extracted_total: number;
    complete: number;
    partial: number;
    empty: number;
    failed: number;
  };
  sections?: ExtractionSectionStats[];
  dedup?: {
    checked: number;
    kept: number;
    dropped: number;
    groups: { fingerprint: string; kept_id: string; dropped_ids: string[] }[];
  };
}

export interface ExtractionRejectedItem {
  raw_text?: string;
  _reject_reason?: string;
  [k: string]: unknown;
}

export interface ExtractionSectionStats {
  section_ref: string;
  section_title: string;
  kind: "section" | "excluded";
  page_start: number | null;
  page_end: number | null;
  expected: number | null;
  identified: number;
  extracted: number;
  rejected: number;
  rejected_items: ExtractionRejectedItem[];
  status: "complete" | "partial" | "empty" | "failed" | "skipped";
  attempts: number;
  error: string | null;
}

export interface QuestionBank {
  id: UUID;
  book_id: UUID;
  title: string;
  subject: string | null;
  status: "pending" | "extracting" | "ready" | "failed";
  question_count: number;
  stats: ExtractionStats | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  active_job_id?: UUID | null;
  active_job?: {
    id: UUID;
    status: string;
    progress: number | null;
    message: string | null;
  } | null;
}

export interface Question {
  id: UUID;
  regen_id?: UUID | null;
  source_question_id?: UUID | null;
  section_ref: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  status: string;
  // Phase 1 linking context
  excluded_block_ref: string;
  excluded_block_index: number | null;
  link_method: string | null;
  link_confidence: number | null;
  // Stage 2 OCR metadata
  question_number: string | null;
  exercise_ref: string | null;
  chapter_ref: string | null;
  sub_part: string | null;
  question_type: string | null;
  has_options: boolean;
  solution_text: string | null;
  has_solution: boolean;
  kind: string;
  is_hidden: boolean;
}

export interface RejectedQuestion {
  id: UUID;
  section_ref: string | null;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  reject_reason: string | null;
  payload: Record<string, unknown> | null;
  status: "pending" | "restored" | "discarded";
  created_at: string | null;
}

export type QuestionKind = "exercise" | "example" | "problem" | "try_it" | "review" | "mcq" | "other";

export interface QuestionBankSectionGroup {
  section_ref: string;
  section_title: string;
  questions: Question[];
  by_kind: Partial<Record<QuestionKind, Question[]>>;
  rejected: RejectedQuestion[];
  identified: number;
  extracted: number;
  missed: number;
}

export interface QuestionBankDetail {
  bank_id: UUID;
  book_id: UUID;
  title: string;
  status: QuestionBank["status"];
  total_questions: number;
  stats: ExtractionStats | null;
  sections: QuestionBankSectionGroup[];
}

export interface QuestionStructureExcludedBlock {
  title: string;
  page_start: number | null;
  page_end: number | null;
  reason: string;
  excluded_index: number;
  excluded_block_ref: string;
  link_method: string;
  link_confidence: number;
  section_ref: string | null;
}

export interface QuestionStructureNode {
  id: string;
  title: string;
  level: number;
  type: string;
  page_start: number | null;
  page_end: number | null;
  question_count: number;
  excluded_blocks: QuestionStructureExcludedBlock[];
  subsections: QuestionStructureNode[];
}

export interface QuestionStructureResponse {
  book_id: UUID;
  document_title: string;
  sections: QuestionStructureNode[];
  unlinked_excluded: QuestionStructureExcludedBlock[];
  summary: {
    total_sections: number;
    total_excluded: number;
    linked_excluded: number;
    unlinked_excluded: number;
  };
}

export interface QuestionRegeneration {
  id: UUID;
  bank_id: UUID;
  book_id: UUID;
  source_regen_id: UUID | null;
  label: string | null;
  scope: "bank" | "sections";
  section_refs: string[];
  custom_instructions: string | null;
  // "partial" is set by the v3 worker when any section failed but at least
  // one section succeeded — the run is usable but not fully complete.
  status: "pending" | "extracting" | "ready" | "partial" | "failed" | "saved";
  job_id: UUID | null;
  question_count: number;
  stats: ExtractionStats | null;
  last_error: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
}

// 0014 — variants grouped by source_question_id for the new theory-style UI.
export interface QuestionRegenSourceGroup {
  source_id: UUID | null;
  source: Question | null;
  variants: Question[];
}

export interface QuestionRegenSectionGroup {
  section_ref: string | null;
  section_title: string | null;
  questions: Question[];   // flat list — backward-compat
  sources?: QuestionRegenSourceGroup[];  // grouped by source (new)
}

export interface QuestionRegenQuestionsResponse {
  regen: QuestionRegeneration;
  sections: QuestionRegenSectionGroup[];
}

export interface RegenerateQuestionsParams {
  scope: "bank" | "sections";
  section_refs?: string[] | null;
  custom_instructions?: string | null;
  source_regen_id?: UUID | null;
  label?: string | null;
  // R4 — v3 regen params. All optional with worker-side defaults.
  similarity_level?:
    | "numbers_only"
    | "numbers_and_rephrase"
    | "new_question_same_topic"
    | "same_topic_add_one_concept"
    | "same_chapter_any_topic"
    | null;
  count?: number | null;
  question_type?: string | null;
  priority_mode?: "override" | "layer_on_top" | "specific_aspects" | null;
}

// R10 — section-level retry params
export interface RetryRegenSectionParams {
  regen_id: UUID;
  section_ref: string;
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
  getQuestionStructure: (bookId: UUID) =>
    req<QuestionStructureResponse>(`/api/books/${bookId}/question-structure`),
  createQuestionBank: (bookId: UUID) =>
    req<{ bank_id: UUID; job_id: UUID; status: string }>(
      `/api/books/${bookId}/question-banks`,
      { method: "POST" },
    ),
  listQuestionBanks: (bookId: UUID) =>
    req<QuestionBank[]>(`/api/books/${bookId}/question-banks`),
  getQuestionBank: (bankId: UUID) =>
    req<QuestionBank>(`/api/question-banks/${bankId}`),
  deleteQuestionBank: (bankId: UUID) =>
    req<void>(`/api/question-banks/${bankId}`, { method: "DELETE" }),
  retrySection: (bankId: UUID, sectionRef: string) =>
    req<{ bank_id: UUID; section_ref: string; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/sections/${encodeURIComponent(sectionRef)}/retry`,
      { method: "POST" },
    ),
  reExtractBlock: (bankId: UUID, blockIdx: number) =>
    req<{ bank_id: UUID; block_idx: number; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/blocks/${blockIdx}/re-extract`,
      { method: "POST" },
    ),
  listQuestions: (bankId: UUID) =>
    req<QuestionBankDetail>(`/api/question-banks/${bankId}/questions`),
  restoreRejected: (bankId: UUID, rejectedId: UUID) =>
    req<{ ok: boolean; question_id: UUID; rejected_id: UUID }>(
      `/api/question-banks/${bankId}/rejected/${rejectedId}/restore`,
      { method: "POST" },
    ),
  discardRejected: (bankId: UUID, rejectedId: UUID) =>
    req<{ ok: boolean; rejected_id: UUID }>(
      `/api/question-banks/${bankId}/rejected/${rejectedId}/discard`,
      { method: "POST" },
    ),
  hideQuestion: (questionId: UUID) =>
    req<{ ok: boolean; question_id: UUID; is_hidden: boolean }>(
      `/api/question-banks/questions/${questionId}/hide`,
      { method: "PATCH" },
    ),
  unhideQuestion: (questionId: UUID) =>
    req<{ ok: boolean; question_id: UUID; is_hidden: boolean }>(
      `/api/question-banks/questions/${questionId}/unhide`,
      { method: "PATCH" },
    ),
  exportQuestionsJson: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/json`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportQuestionsMarkdown: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/markdown`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportQuestionsDocx: (bankId: UUID) => {
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-banks/${bankId}/export/docx`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  startQuestionRegeneration: (bankId: UUID, params: RegenerateQuestionsParams) =>
    req<{ regen_id: UUID; job_id: UUID; status: string }>(
      `/api/question-banks/${bankId}/regenerate`,
      { method: "POST", body: JSON.stringify(params) },
    ),
  listQuestionRegenerations: (bookId: UUID) =>
    req<QuestionRegeneration[]>(`/api/books/${bookId}/question-regenerations`),
  getQuestionRegeneration: (regenId: UUID) =>
    req<QuestionRegeneration>(`/api/question-regenerations/${regenId}`),
  listRegenQuestions: (regenId: UUID) =>
    req<QuestionRegenQuestionsResponse>(`/api/question-regenerations/${regenId}/questions`),
  saveQuestionRegeneration: (regenId: UUID) =>
    req<QuestionRegeneration>(`/api/question-regenerations/${regenId}/save`, { method: "POST" }),
  deleteQuestionRegeneration: (regenId: UUID) =>
    req<void>(`/api/question-regenerations/${regenId}`, { method: "DELETE" }),
  bulkDeleteRegenQuestions: (regenId: UUID, questionIds: UUID[]) =>
    req<{ deleted: number }>(`/api/question-regenerations/${regenId}/questions`, {
      method: "DELETE",
      body: JSON.stringify({ question_ids: questionIds }),
    }),
  // R6 — section-level retry
  retryRegenSection: (regenId: UUID, sectionRef: string) =>
    req<{ regen_id: UUID; section_ref: string; job_id: UUID; status: string }>(
      `/api/question-regenerations/${regenId}/retry-section`,
      { method: "POST", body: JSON.stringify({ section_ref: sectionRef }) },
    ),
  // R10 — regen exports (overall, or per-section via section_ref query param)
  exportRegenJson: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/json${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportRegenMarkdown: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/markdown${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  exportRegenDocx: (regenId: UUID, sectionRef?: string) => {
    const qs = sectionRef ? `?section_ref=${encodeURIComponent(sectionRef)}` : "";
    const a = document.createElement("a");
    a.href = `${API_BASE}/api/question-regenerations/${regenId}/export/docx${qs}`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  listProviders: () => req<Provider[]>("/api/providers"),
  getProviderKeyStatus: (name: string) =>
    req<{ provider: string; configured: boolean }>(`/api/providers/${name}/keys`),
  saveProviderKeys: (name: string, keys: Record<string, unknown>) =>
    req<{ saved: boolean; valid: boolean; provider: string }>(
      `/api/providers/${name}/keys`,
      { method: "POST", body: JSON.stringify(keys) },
    ),
};
