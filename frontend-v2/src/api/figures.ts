// Figures client — fetches all figures for a book grouped by section.

import { useCallback, useEffect, useState } from 'react';

import { API_BASE, ApiError, req } from './client';

export type Figure = {
  id: string;
  book_id: string;
  section_id: string | null;
  figure_id_text: string | null;
  figure_number: string | null;
  normalized_label: string | null;
  caption: string | null;
  description: string | null;
  page_number: number | null;
  bounding_box: number[] | null;
  semantic_type: string | null;
  status: string;
  regen_status: string;
  has_original: boolean;
  has_regen: boolean;
  context_hint: string | null;
  is_approved: boolean;
};

export type SectionFigures = {
  section_ref: string;
  figures: Figure[];
  n_theory: number;
  n_question: number;
};

export type BookFigures = {
  book_id: string;
  total_figures: number;
  sections: SectionFigures[];
};

// ─── HTTP ─────────────────────────────────────────────────────────
export const getBookFigures = (bookId: string) =>
  req<BookFigures>(`/api/books/${bookId}/figures`);

// Image bytes endpoint — returns the figure image bytes. Use in <img src>.
// Backend variant strings are "regenerated" | "original" | "auto"; we send
// the explicit one when caller asked for regen so we never depend on the
// auto-fallback. Omitting the param lets the backend choose (auto).
export const figureImageUrl = (figureId: string, regen = false) =>
  `${API_BASE}/api/figures/${figureId}/image${regen ? '?variant=regenerated' : ''}`;

// Per-section figure regen — POSTs to backend with optional custom instructions.
// Backend dispatches an async worker. Caller should refetch figures after.
export const regenerateSectionFigures = (
  bookId: string,
  sectionRef: string,
  body: { style?: string; custom_instructions?: string | null } = {},
) =>
  req(
    `/api/books/${bookId}/sections/${encodeURIComponent(sectionRef)}/regenerate-figures`,
    { method: 'POST', body: JSON.stringify(body) },
  );

// ─── Hook ─────────────────────────────────────────────────────────
type State =
  | { kind: 'loading' }
  | { kind: 'ready'; data: BookFigures }
  | { kind: 'error'; error: string };

export function useBookFigures(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const data = await getBookFigures(bookId);
      setState({ kind: 'ready', data });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `Backend ${err.status}: ${err.message}`
          : err instanceof Error
          ? err.message
          : 'Unknown error';
      setState({ kind: 'error', error: msg });
    }
  }, [bookId]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...state, refetch: load };
}
