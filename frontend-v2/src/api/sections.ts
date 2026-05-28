// Sections client — read-only views of extracted content.
//
// Surfaces SectionOut from the backend (typed via generated OpenAPI) with
// a thin filter for tab-scoped section lists.

import { useCallback, useEffect, useState } from 'react';

import { ApiError, req } from './client';
import type { components } from './generated';

export type Section = components['schemas']['SectionOut'];

// ─── HTTP ────────────────────────────────────────────────────────
export const listSections = (bookId: string) =>
  req<Section[]>(`/api/books/${bookId}/sections`);

export const getSection = (sectionId: string) =>
  req<Section>(`/api/sections/${sectionId}`);

// ─── Hooks ───────────────────────────────────────────────────────
type State =
  | { kind: 'loading' }
  | { kind: 'ready'; sections: Section[] }
  | { kind: 'error'; error: string };

export function useSections(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const sections = await listSections(bookId);
      setState({ kind: 'ready', sections });
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

// ─── Tab filtering ───────────────────────────────────────────────
//
// Per the V-Studio design: each top tab (Theory / Questions / Figures)
// shows ONLY the sections relevant to it.
//   • Theory  — every leaf section that has blocks (or status='passed')
//   • Questions — sections that produced questions
//   • Figures — sections that have figures attached
//
// For tabs other than Theory, the relevant section sets come from the
// questions and figures endpoints — wired in components that need them.

export type TabKey = 'theory' | 'questions' | 'figures';

export function filterSectionsForTheoryTab(sections: Section[]): Section[] {
  // Theory tab = anything that was extracted (passed) OR is failed but
  // expected to be theory. We include 'failed' so user sees what's broken.
  return sections.filter(
    (s) => s.status === 'passed' || s.status === 'failed',
  );
}
