// Questions client — fetch the latest question bank for a book + walk
// the section-grouped question list returned by the backend.

import { useCallback, useEffect, useState } from 'react';

import { ApiError, req } from './client';

export type QuestionBank = {
  id: string;
  book_id: string;
  title: string | null;
  subject: string | null;
  status: string;
  question_count?: number;
  last_error?: string | null;
};

export type ExtractedQuestion = {
  id: string;
  section_ref: string;
  section_title: string | null;
  page_start: number | null;
  page_end: number | null;
  raw_text: string;
  status: string;
  question_number?: string | null;
  exercise_ref?: string | null;
  question_type?: string | null;
  has_options: boolean;
  solution_text?: string | null;
  has_solution: boolean;
  kind?: string | null; // 'example' | 'question' | etc.
  is_hidden?: boolean;
};

export type SectionQuestions = {
  section_ref: string;
  section_title: string | null;
  questions: ExtractedQuestion[];
  extracted: number;
  identified: number;
  missed: number;
  by_kind?: Record<string, number>;
};

export type QuestionBankDetail = {
  bank_id: string;
  book_id: string;
  title: string | null;
  status: string;
  total_questions: number;
  sections: SectionQuestions[];
};

// ─── HTTP ─────────────────────────────────────────────────────────
export const listBanks = (bookId: string) =>
  req<QuestionBank[]>(`/api/books/${bookId}/question-banks`);

export const getBankQuestions = (bankId: string) =>
  req<QuestionBankDetail>(`/api/question-banks/${bankId}/questions`);

// ─── Hook ─────────────────────────────────────────────────────────
type State =
  | { kind: 'loading' }
  | { kind: 'empty' } // no bank yet
  | { kind: 'ready'; bank: QuestionBank; detail: QuestionBankDetail }
  | { kind: 'error'; error: string };

/**
 * Loads the LATEST question bank for the book and pulls its full
 * section-grouped question list. Returns 'empty' when no bank exists.
 */
export function useBookQuestions(bookId: string | undefined) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!bookId) {
      setState({ kind: 'error', error: 'No book id' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const banks = await listBanks(bookId);
      if (banks.length === 0) {
        setState({ kind: 'empty' });
        return;
      }
      // Pick the latest ready bank, or fall back to the newest row.
      const ready = banks.filter((b) => b.status === 'ready');
      const bank = ready[ready.length - 1] ?? banks[banks.length - 1];
      const detail = await getBankQuestions(bank.id);
      setState({ kind: 'ready', bank, detail });
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
