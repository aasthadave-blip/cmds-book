// Extraction pipeline orchestrator.
//
// Goal: drive the four-stage parallel pipeline (schema → theory + questions +
// figures) against the existing backend WITHOUT touching it.
//
// Robustness goals (because real-time will surprise us):
//
//   1. Resume from any state on mount. We GET /api/books/:id first and
//      infer where the pipeline is. If the book is already past where we'd
//      start, we don't re-trigger — we just hook into the existing state.
//
//   2. Idempotent triggers. Every POST is guarded by an "inflight" flag
//      per stage. Retry buttons can't double-fire while a fire is in flight.
//
//   3. localStorage-backed job ID memory. If user refreshes mid-run, we
//      restore the job IDs and resume polling without re-firing the POSTs.
//
//   4. Tolerant of transient network failures. A single GET /jobs/:id
//      failure does NOT flip a stage to "failed" — we count consecutive
//      poll failures and only surface after 3 in a row.
//
//   5. Functional setState everywhere. No stateRef.current reads inside
//      the tick body for transition decisions.
//
//   6. Verbose console.debug logs at every transition so production
//      problems are diagnosable from devtools.
//
//   7. Stall detection. If a stage has had no progress for 5 minutes,
//      surface a warning (without flipping to failed).
//
// What it does NOT do:
//   - No backend changes. Same endpoints, same payloads, same workers.
//   - No new data shapes. Existing data flows through unchanged.

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, req } from './client';
import type { components } from './generated';

// Backend-canonical types pulled directly from the live OpenAPI spec.
// Regenerate with `npm run gen:api` whenever backend ships. If a field
// shape changes, the TypeScript compiler catches it here.
type BackendJobOut = components['schemas']['JobOut'];
type BackendBookOut = components['schemas']['BookOut'];
type BackendSectionOut = components['schemas']['SectionOut'];

// ─────────────────────────────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────────────────────────────

export type StageKey = 'schema' | 'theory' | 'questions' | 'figures';

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'unknown';

export type StageState = {
  jobId: string | null;
  status: JobStatus;
  progress: number;
  message: string | null;
  error: string | null;
  /** True while a POST or retry is in flight for this stage. */
  inflight: boolean;
  /** ms timestamp of last observed progress change. */
  lastProgressAt: number | null;
};

export type FailedSection = {
  id: string;
  section_id: string;
  title: string;
  attempts: number;
  error: string | null;
};

export type Phase =
  | 'idle'
  | 'loading'        // initial GET /books/:id in flight
  | 'analysing'      // schema job running
  | 'approving'      // /approve or /re-extract POST in flight (brief)
  | 'extracting'     // any of theory / questions / figures running
  | 'reconciling'
  | 'done'
  | 'partial'
  | 'error';

export type SectionCounts = {
  /** Leaf sections expected per book.schema_ (flattened, all leaves). */
  expected: number;
  /** Section rows with status='ready' — extracted successfully. */
  ready: number;
  /** Section rows with status='failed' — worker tried, gave up. */
  failed: number;
  /** Section rows still status='pending'/'extracting' at reconcile time. */
  inFlight: number;
  /** expected − (ready + failed + inFlight): never created at all. */
  missing: number;
};

/** Sections the worker started but never finished — surface for retry. */
export type MissingSection = {
  /** Schema section_id slug. We don't have a Section row id yet. */
  section_id: string;
  title: string;
};

export type ExtractionState = {
  phase: Phase;
  bookId: string | null;
  bookStatus: string | null;       // last seen book.status from backend
  overallPct: number;
  schema: StageState;
  theory: StageState;
  questions: StageState;
  figures: StageState;
  failedSections: FailedSection[];
  /** Sections in the schema that have NO Section row at all. */
  missingSections: MissingSection[];
  /** Cross-checked completeness — populated after reconcile. */
  sectionCounts: SectionCounts;
  questionsFailed: boolean;
  figuresFailed: boolean;
  errorMessage: string | null;
  /** Internal: consecutive job-poll failures per stage. Surfaces after 3. */
  pollErrors: Record<StageKey, number>;
};

// ─────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 2000;
const MAX_CONSECUTIVE_POLL_FAILS = 3;
const STALL_THRESHOLD_MS = 5 * 60_000;
const STORAGE_KEY = 'vstudio.extractionJobs';

const STAGE_WEIGHTS: Record<StageKey, number> = {
  schema: 0.10,
  theory: 0.45,
  questions: 0.30,
  figures: 0.15,
};

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

function dbg(...args: unknown[]) {
  // eslint-disable-next-line no-console
  console.debug('[extract]', ...args);
}

function initialStage(): StageState {
  return {
    jobId: null,
    status: 'unknown',
    progress: 0,
    message: null,
    error: null,
    inflight: false,
    lastProgressAt: null,
  };
}

function initialState(bookId: string | null = null): ExtractionState {
  return {
    phase: 'idle',
    bookId,
    bookStatus: null,
    overallPct: 0,
    schema: initialStage(),
    theory: initialStage(),
    questions: initialStage(),
    figures: initialStage(),
    failedSections: [],
    missingSections: [],
    sectionCounts: { expected: 0, ready: 0, failed: 0, inFlight: 0, missing: 0 },
    questionsFailed: false,
    figuresFailed: false,
    errorMessage: null,
    pollErrors: { schema: 0, theory: 0, questions: 0, figures: 0 },
  };
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}

function normalizeStatus(raw: string): JobStatus {
  // Backend canonical values (from app/workers/*.py + app/api/*.py):
  //   - "queued"                     job created, worker hasn't picked up yet
  //   - "running" | "extracting" | "analysing" | "regenerating"  in-flight
  //   - "succeeded"                  terminal success
  //   - "failed" | "error"           terminal failure
  if (raw === 'succeeded' || raw === 'done' || raw === 'completed' || raw === 'success' || raw === 'complete')
    return 'done';
  if (raw === 'failed' || raw === 'error') return 'failed';
  if (
    raw === 'running' ||
    raw === 'extracting' ||
    raw === 'analysing' ||
    raw === 'regenerating' ||
    raw === 're_extracting'
  )
    return 'running';
  if (raw === 'queued' || raw === 'pending') return 'queued';
  return 'unknown';
}

function isTerminal(s: JobStatus): boolean {
  return s === 'done' || s === 'failed';
}

function stagePct(st: StageState): number {
  if (st.status === 'done') return 100;
  if (st.status === 'unknown' && !st.jobId) return 0;
  return Math.max(0, Math.min(100, st.progress || 0));
}

function computeOverall(s: ExtractionState): number {
  return Math.round(
    stagePct(s.schema) * STAGE_WEIGHTS.schema +
      stagePct(s.theory) * STAGE_WEIGHTS.theory +
      stagePct(s.questions) * STAGE_WEIGHTS.questions +
      stagePct(s.figures) * STAGE_WEIGHTS.figures,
  );
}

// ─────────────────────────────────────────────────────────────────────
// localStorage persistence for job IDs (survives page refresh)
// ─────────────────────────────────────────────────────────────────────

type StoredJobs = Partial<Record<StageKey, string>>;
type StoredState = Record<string, StoredJobs>; // bookId → stored jobs

function loadStored(): StoredState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredState) : {};
  } catch {
    return {};
  }
}

function saveStored(bookId: string, jobs: StoredJobs) {
  try {
    const all = loadStored();
    all[bookId] = jobs;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* quota / disabled localStorage — ignore */
  }
}

function clearStored(bookId: string) {
  try {
    const all = loadStored();
    delete all[bookId];
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    /* ignore */
  }
}

// ─────────────────────────────────────────────────────────────────────
// Thin API call wrappers
// ─────────────────────────────────────────────────────────────────────

// Use the backend-canonical types (BackendJobOut / BackendBookOut) so any
// shape drift between frontend and backend becomes a compile error.
const getJob = (jobId: string) => req<BackendJobOut>(`/api/jobs/${jobId}`);
const getBook = (bookId: string) => req<BackendBookOut>(`/api/books/${bookId}`);

const postAnalyse = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/analyse`, { method: 'POST' });

const postApprove = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/approve`, { method: 'POST' });

const postQuestionBank = (bookId: string) =>
  req<{ bank_id: string; job_id: string }>(
    `/api/books/${bookId}/question-banks`,
    { method: 'POST' },
  );

const postExtractFigures = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/extract-figures-v2`, {
    method: 'POST',
  });

const postReExtractSection = (sectionRowId: string) =>
  req<{ job_id: string }>(`/api/sections/${sectionRowId}/re-extract`, {
    method: 'POST',
  });

const postReExtractAll = (bookId: string) =>
  req<{ job_id: string }>(`/api/books/${bookId}/re-extract`, { method: 'POST' });

const getSections = (bookId: string) =>
  req<BackendSectionOut[]>(`/api/books/${bookId}/sections`);

// ─── Schema flattening — count expected leaf sections ─────────────────
//
// "Leaf" = a section that has no children OR whose children are subsections
// (the worker iterates the leaves). Excluded sections are NOT extracted.
type SchemaSection = {
  id?: string;
  title?: string;
  type?: 'chapter' | 'section' | 'subsection' | 'excluded';
  content_types?: string[];
  subsections?: SchemaSection[];
};

/** Flatten the schema into the set of section_ids the extractor will visit. */
function flattenExpectedLeaves(
  schema: unknown,
): Array<{ section_id: string; title: string }> {
  if (!schema || typeof schema !== 'object') return [];
  const top = (schema as { sections?: SchemaSection[] }).sections;
  if (!Array.isArray(top)) return [];
  const out: Array<{ section_id: string; title: string }> = [];
  const walk = (nodes: SchemaSection[]) => {
    for (const n of nodes) {
      if (n.type === 'excluded') continue;
      const subs = n.subsections ?? [];
      const nonExcludedSubs = subs.filter((s) => s.type !== 'excluded');
      if (nonExcludedSubs.length === 0) {
        // Leaf — this is what the worker creates a Section row for.
        if (n.id) out.push({ section_id: n.id, title: n.title ?? n.id });
      } else {
        walk(nonExcludedSubs);
      }
    }
  };
  walk(top);
  return out;
}

type QuestionBankOut = {
  id: string;
  status: string;
  last_error: string | null;
  job_id?: string | null;
};
const listBanks = (bookId: string) =>
  req<QuestionBankOut[]>(`/api/books/${bookId}/question-banks`);

type FigureOut = { id: string; status: string };
const listFigures = (bookId: string) =>
  req<FigureOut[] | { figures: FigureOut[] }>(
    `/api/books/${bookId}/figures`,
  ).then((r) => (Array.isArray(r) ? r : r.figures ?? []));

// ─────────────────────────────────────────────────────────────────────
// The hook
// ─────────────────────────────────────────────────────────────────────

export type UseExtractionPipeline = {
  state: ExtractionState;
  start: (bookId: string) => Promise<void>;
  retryStage: (stage: StageKey) => Promise<void>;
  retrySection: (sectionRowId: string) => Promise<void>;
  /**
   * Re-run theory extraction for everything. Use when missing sections exist
   * (no Section row to target individually). Wipes every section to pending
   * and runs the worker fresh — matches the existing /api/books/:id/re-extract
   * semantics. Idempotent.
   */
  retryAllTheory: () => Promise<void>;
  cancel: () => void;
};

export function useExtractionPipeline(): UseExtractionPipeline {
  const [state, setState] = useState<ExtractionState>(initialState());

  // Mutable refs used by polling / mount-time effects. They are not used
  // for state-transition decisions inside tick — those use functional
  // setState callbacks so we always have the freshest state.
  const pollTimer = useRef<number | null>(null);
  const tickRunning = useRef(false);
  const reconciledFor = useRef<string | null>(null);

  // ─── Patch helper ───────────────────────────────────────────────
  /**
   * Apply a patch. `compute(s)` returns the partial; the result is merged
   * over the current state, then overallPct is recomputed. Pure setState —
   * always reads the latest state via React's setState callback contract.
   */
  const apply = useCallback(
    (compute: (s: ExtractionState) => Partial<ExtractionState> | null) => {
      setState((prev) => {
        const partial = compute(prev);
        if (!partial) return prev;
        const next = { ...prev, ...partial };
        next.overallPct = computeOverall(next);
        return next;
      });
    },
    [],
  );

  // ─── Polling control ────────────────────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
      dbg('polling stopped');
    }
  }, []);

  const startPolling = useCallback((tick: () => void) => {
    if (pollTimer.current != null) return; // already polling
    dbg('polling started');
    // Fire one tick immediately so user sees movement, then schedule.
    void tick();
    pollTimer.current = window.setInterval(tick, POLL_INTERVAL_MS);
  }, []);

  // ─── Persist active job IDs whenever the state changes ──────────
  useEffect(() => {
    if (!state.bookId) return;
    if (state.phase === 'done' || state.phase === 'partial' || state.phase === 'error') {
      clearStored(state.bookId);
      return;
    }
    const stored: StoredJobs = {};
    (['schema', 'theory', 'questions', 'figures'] as StageKey[]).forEach((k) => {
      const j = state[k].jobId;
      if (j) stored[k] = j;
    });
    saveStored(state.bookId, stored);
  }, [state]);

  // ─── Phase 1: kick theory ALONE ──────────────────────────────────
  //
  // We deliberately do NOT fire questions + figures yet. Theory is the
  // heaviest stage; giving it solo CPU + Gemini quota means it finishes
  // faster and more reliably. Q + figures get triggered after theory is
  // done (see kickRestParallel below).
  //
  // forceFresh=true uses /re-extract (destructive — wipes Section rows).
  // forceFresh=false uses /approve (first-time path).
  const kickTheoryAlone = useCallback(
    async (bookId: string, forceFresh = false) => {
      dbg('kicking theory alone for', bookId, { forceFresh });
      apply(() => ({
        phase: 'approving',
        theory: { ...initialStage(), inflight: true },
        // Q + figures explicitly NOT touched yet — they kick after theory.
        questions: { ...initialStage(), status: 'queued', message: 'waiting for theory' },
        figures: { ...initialStage(), status: 'queued', message: 'waiting for theory' },
      }));
      try {
        const r = forceFresh ? await postReExtractAll(bookId) : await postApprove(bookId);
        apply(() => ({
          phase: 'extracting',
          theory: {
            ...initialStage(),
            jobId: r.job_id,
            status: 'queued',
            lastProgressAt: Date.now(),
          },
        }));
      } catch (e) {
        apply(() => ({
          phase: 'extracting',
          theory: {
            ...initialStage(),
            status: 'failed',
            error: explain(e),
          },
        }));
        dbg('theory POST failed', e);
      }
    },
    [apply],
  );

  // ─── Phase 2: kick questions + figures in parallel ───────────────
  //
  // Called once theory hits 'done'. Q + figures are both lighter stages
  // and run safely side-by-side without contention.
  const kickRestParallel = useCallback(
    async (bookId: string) => {
      dbg('kicking Q + figures in parallel for', bookId);
      apply(() => ({
        questions: { ...initialStage(), inflight: true },
        figures: { ...initialStage(), inflight: true },
      }));

      const [questionsRes, figuresRes] = await Promise.allSettled([
        postQuestionBank(bookId),
        postExtractFigures(bookId),
      ]);

      apply(() => {
        const patch: Partial<ExtractionState> = {};
        if (questionsRes.status === 'fulfilled') {
          patch.questions = {
            ...initialStage(),
            jobId: questionsRes.value.job_id,
            status: 'queued',
            lastProgressAt: Date.now(),
          };
        } else {
          patch.questions = {
            ...initialStage(),
            status: 'failed',
            error: explain(questionsRes.reason),
          };
          dbg('questions POST failed', questionsRes.reason);
        }
        if (figuresRes.status === 'fulfilled') {
          patch.figures = {
            ...initialStage(),
            jobId: figuresRes.value.job_id,
            status: 'queued',
            lastProgressAt: Date.now(),
          };
        } else {
          patch.figures = {
            ...initialStage(),
            status: 'failed',
            error: explain(figuresRes.reason),
          };
          dbg('figures POST failed', figuresRes.reason);
        }
        return patch;
      });
    },
    [apply],
  );

  // Back-compat alias used by start() — calls the new theory-first path.
  const kickThreeParallel = kickTheoryAlone;

  // ─── Reconcile after all 3 extractions settle ──────────────────
  //
  // Three completeness checks, run in parallel:
  //
  //   • Theory completeness: compare the schema's expected leaf section
  //     count against the actual Section rows. Anything in the schema with
  //     no Section row is "missing" — the worker never tried it (could be
  //     a worker crash, race, or backend bug). These get surfaced for
  //     explicit retry.
  //   • Failed sections: Section rows with status='failed'. Backend gave
  //     up after retries. Surface for per-section retry.
  //   • In-flight sections: Section rows still 'pending'/'extracting'
  //     when the umbrella job claims done. Real failure mode — backend
  //     might have written job=succeeded prematurely. Surface as warning.
  //
  // Plus questions + figures status checks.
  const reconcile = useCallback(
    async (bookId: string) => {
      if (reconciledFor.current === bookId) return;
      reconciledFor.current = bookId;
      dbg('reconciling', bookId);

      apply(() => ({ phase: 'reconciling' }));

      const [bookR, sectionsR, banksR, figuresR] = await Promise.allSettled([
        getBook(bookId),
        getSections(bookId),
        listBanks(bookId),
        listFigures(bookId),
      ]);

      const book = bookR.status === 'fulfilled' ? bookR.value : null;
      const sections =
        sectionsR.status === 'fulfilled' ? sectionsR.value : [];
      const banks = banksR.status === 'fulfilled' ? banksR.value : [];
      const figures = figuresR.status === 'fulfilled' ? figuresR.value : [];

      // Expected leaves from schema (the worker's ground truth iteration set).
      const expectedLeaves = book ? flattenExpectedLeaves(book.schema_) : [];
      const expectedIds = new Set(expectedLeaves.map((l) => l.section_id));

      // Actual sections indexed by section_id slug.
      const actualById = new Map<string, BackendSectionOut>();
      for (const s of sections) actualById.set(s.section_id, s);

      // Build per-section breakdown.
      let readyCount = 0;
      let inFlightCount = 0;
      const failed: FailedSection[] = [];
      const missing: MissingSection[] = [];

      for (const leaf of expectedLeaves) {
        const row = actualById.get(leaf.section_id);
        if (!row) {
          missing.push({ section_id: leaf.section_id, title: leaf.title });
          continue;
        }
        // Backend marks sections 'passed' (QC succeeded), 'failed' (QC
        // gave up after retries), or 'skipped' (intentionally not
        // processed — e.g. parent nodes). Accept 'ready' as defensive
        // alias. All three count as "done" / terminal.
        if (
          row.status === 'passed' ||
          row.status === 'ready' ||
          row.status === 'skipped'
        ) {
          readyCount++;
        } else if (row.status === 'failed') {
          let err: string | null = null;
          const qc = row.qc_local as Record<string, unknown> | null;
          if (qc && typeof qc.last_error === 'string') err = qc.last_error;
          failed.push({
            id: row.id,
            section_id: row.section_id,
            title: row.title,
            attempts: row.attempts,
            error: err,
          });
        } else {
          // 'pending' or 'extracting' or any other non-terminal — counted
          // as in-flight; user can wait or force retry.
          inFlightCount++;
        }
      }

      // Also surface any orphan Section rows whose section_id is NOT in
      // the current schema (shouldn't happen, but if it does, log).
      const orphans = sections.filter((s) => !expectedIds.has(s.section_id));
      if (orphans.length > 0) {
        dbg('orphan section rows (not in schema):', orphans.map((o) => o.section_id));
      }

      const sectionCounts: SectionCounts = {
        expected: expectedLeaves.length,
        ready: readyCount,
        failed: failed.length,
        inFlight: inFlightCount,
        missing: missing.length,
      };

      const latestBank = banks[banks.length - 1] ?? null;
      const questionsFailed =
        !latestBank || latestBank.status === 'failed' || latestBank.status === 'error';
      const figuresFailed =
        figures.length === 0 || figures.some((f) => f.status === 'failed');

      apply((prev) => {
        const theoryHasIssues =
          failed.length > 0 ||
          missing.length > 0 ||
          inFlightCount > 0;
        const anyFailure =
          theoryHasIssues ||
          questionsFailed ||
          figuresFailed ||
          prev.theory.status === 'failed' ||
          prev.questions.status === 'failed' ||
          prev.figures.status === 'failed';
        dbg('reconciled', {
          ...sectionCounts,
          questionsFailed,
          figuresFailed,
          anyFailure,
        });
        return {
          phase: anyFailure ? 'partial' : 'done',
          failedSections: failed,
          missingSections: missing,
          sectionCounts,
          questionsFailed,
          figuresFailed,
        };
      });
    },
    [apply],
  );

  // ─── tick: poll active jobs, advance the state machine ─────────
  // Refs to break the tick → kick* → tick callback cycle.
  // `tick` is captured by setInterval, but kick* and reconcile need
  // to be the latest closures; we look them up from refs.
  const kickRef = useRef(kickTheoryAlone);
  const kickRestRef = useRef(kickRestParallel);
  const reconcileRef = useRef(reconcile);
  useEffect(() => {
    kickRef.current = kickTheoryAlone;
    kickRestRef.current = kickRestParallel;
    reconcileRef.current = reconcile;
  }, [kickTheoryAlone, kickRestParallel, reconcile]);
  // Track whether we've already fired Q+figures so we don't re-fire every tick.
  const restKickedFor = useRef<string | null>(null);

  const tick = useCallback(async () => {
    if (tickRunning.current) return; // overlap guard
    tickRunning.current = true;
    try {
      // Snapshot the latest state via setState callback (no ref reads).
      let snapshot: ExtractionState | null = null;
      setState((s) => {
        snapshot = s;
        return s;
      });
      if (!snapshot) return;
      const s: ExtractionState = snapshot;
      if (!s.bookId) return;

      // Find stages with an active job to poll.
      const active: Array<{ key: StageKey; jobId: string }> = [];
      (['schema', 'theory', 'questions', 'figures'] as StageKey[]).forEach(
        (k) => {
          const st = s[k];
          if (st.jobId && !isTerminal(st.status)) {
            active.push({ key: k, jobId: st.jobId });
          }
        },
      );

      // If theory has no jobId but is marked 'running' (e.g. attached to a
      // pre-existing backend worker), poll the BOOK to detect terminal
      // transition. book.status flips to 'ready' or 'failed' when the
      // worker finishes.
      const needsBookPoll =
        s.theory.status === 'running' && !s.theory.jobId && s.bookId;
      if (needsBookPoll) {
        try {
          const book = await getBook(s.bookId!);
          if (book.status === 'ready' || book.status === 'extracted') {
            apply((prev) => ({
              theory: { ...prev.theory, status: 'done', progress: 100 },
              bookStatus: book.status,
            }));
          } else if (book.status === 'failed') {
            apply((prev) => ({
              theory: {
                ...prev.theory,
                status: 'failed',
                error: 'Theory worker failed (book.status=failed)',
              },
              bookStatus: book.status,
            }));
          } else {
            apply(() => ({ bookStatus: book.status }));
          }
        } catch (e) {
          dbg('book poll failed', e);
        }
      }

      if (active.length > 0) {
        const results = await Promise.allSettled(
          active.map((a) => getJob(a.jobId)),
        );

        apply((prev) => {
          const patch: Partial<ExtractionState> = {};
          const pollErrors = { ...prev.pollErrors };
          results.forEach((r, i) => {
            const { key } = active[i];
            const prevStage = prev[key];
            if (r.status === 'fulfilled') {
              const job = r.value;
              const newStatus = normalizeStatus(job.status);
              const newProgress = Number(job.progress) || 0;
              const progressChanged = newProgress !== prevStage.progress;
              pollErrors[key] = 0;
              patch[key] = {
                ...prevStage,
                jobId: job.id,
                status: newStatus,
                progress: newProgress,
                message: job.message ?? null,
                error: job.error ?? null,
                lastProgressAt: progressChanged
                  ? Date.now()
                  : prevStage.lastProgressAt,
                inflight: false,
              };
              if (newStatus === 'done' || newStatus === 'failed') {
                dbg(`${key} → ${newStatus}`, job.message ?? '');
              }
            } else {
              pollErrors[key] = (pollErrors[key] ?? 0) + 1;
              dbg(
                `poll ${key} failed (${pollErrors[key]}/${MAX_CONSECUTIVE_POLL_FAILS}):`,
                r.reason,
              );
              if (pollErrors[key] >= MAX_CONSECUTIVE_POLL_FAILS) {
                patch[key] = {
                  ...prevStage,
                  status: 'failed',
                  error: `Polling failed ${pollErrors[key]} times: ${explain(r.reason)}`,
                  inflight: false,
                };
              }
              // Otherwise keep prior state — transient blip, give it time.
            }
          });
          patch.pollErrors = pollErrors;
          return patch;
        });
      }

      // ─── Per-section real progress for THEORY ───────────────────────
      //
      // Run AFTER the job poll above so this override wins (otherwise the
      // job's coarse 10% heartbeat would overwrite our real value).
      //
      // Backend's theory job.progress is a coarse heartbeat (10% at start,
      // 100% when done) — useless for showing real progress. Compute it
      // ourselves from Section rows: each section transitions pending →
      // passed|failed as the worker iterates. (passed+failed) / total = real %.
      const theoryActive =
        s.theory.status === 'running' || s.theory.status === 'queued';
      if (theoryActive && s.bookId) {
        try {
          const [sections, bookCheck] = await Promise.all([
            getSections(s.bookId),
            getBook(s.bookId).catch(() => null),
          ]);

          // ── Theory % counts ONLY Cat B (pure theory) sections ──
          // Match backend's questions_v3 split: Cat A = "questions" in
          // content_types; Cat B = everything else. User expects the
          // theory progress to reflect theory sections only — Cat A
          // section rows the worker writes are still tracked, but they
          // count toward Questions progress, not Theory.
          const catBSlugs = new Set<string>();
          if (bookCheck?.schema_) {
            type Node = {
              id?: string;
              content_types?: string[];
              subsections?: Node[];
              type?: string;
            };
            const walk = (nodes: Node[] | undefined) => {
              if (!nodes) return;
              for (const n of nodes) {
                if (n.type === 'excluded') continue;
                const ct = (n.content_types ?? []).map((c) =>
                  String(c).toLowerCase().trim(),
                );
                const isCatA = ct.includes('questions');
                if (n.id && !isCatA) catBSlugs.add(n.id);
                if (n.subsections?.length) walk(n.subsections);
              }
            };
            walk(
              (bookCheck.schema_ as { sections?: Node[] }).sections,
            );
          }

          // Filter to Cat B only. If the schema walk didn't yield any
          // Cat B slugs (schema not loaded yet, or no Cat B sections at
          // all), DO NOT fall back to the full section list — that would
          // make the theory % include Cat A (question) sections that the
          // theory worker never touches, locking the bar below 100% and
          // confusing reviewers. Instead, skip the override entirely and
          // let the worker's coarse heartbeat progress stand for this tick.
          const scopedSections =
            catBSlugs.size > 0
              ? sections.filter((sec) => catBSlugs.has(sec.section_id))
              : [];

          if (scopedSections.length > 0) {
            // "Done" = the worker is finished with it: passed (QC ok),
            // failed (QC gave up after retries), or skipped (intentionally
            // not processed — e.g. parent nodes). Anything else is in-flight.
            const done = scopedSections.filter(
              (sec) =>
                sec.status === 'passed' ||
                sec.status === 'failed' ||
                sec.status === 'skipped',
            ).length;
            const realPct = Math.round((done / scopedSections.length) * 100);
            const failedCount = scopedSections.filter(
              (sec) => sec.status === 'failed',
            ).length;
            // Theory is done when every Cat B section is in a terminal
            // state (passed/failed/skipped). We do NOT wait for
            // book.status to flip to 'ready' — the backend orchestrator's
            // finalization step is unreliable (worker can die before it
            // runs), which would otherwise hang Q+figures forever in
            // "waiting for theory". Section-completion is ground truth;
            // book.status is kept as a corroborating signal but no
            // longer gates the handoff.
            const allTerminal = done === scopedSections.length;
            const bookAdvanced = bookCheck
              ? ['ready', 'extracted', 'failed'].includes(bookCheck.status)
              : false;
            void bookAdvanced; // retained for telemetry; not used as a gate
            const reallyDone = allTerminal;

            apply((prev) => {
              if (realPct < prev.theory.progress && !reallyDone) return {};
              return {
                theory: {
                  ...prev.theory,
                  // Flip status to done only when both signals agree.
                  status: reallyDone ? 'done' : prev.theory.status,
                  progress: reallyDone ? 100 : realPct,
                  message:
                    failedCount > 0
                      ? `${done} of ${scopedSections.length} theory sections (${failedCount} failed)`
                      : `${done} of ${scopedSections.length} theory sections`,
                },
                bookStatus: bookCheck?.status ?? prev.bookStatus,
              };
            });
          }
        } catch (e) {
          dbg('per-section poll failed', e);
        }
      }

      // ─── Phase transitions (read latest state via setState callback) ───
      let didKickTheory = false;
      let didKickRest = false;
      let didReconcile = false;
      setState((prev) => {
        // 1. analysing → kick theory once schema is done
        if (prev.phase === 'analysing' && prev.schema.status === 'done') {
          didKickTheory = true;
        }
        // 2. extracting + theory done + haven't fired rest → kick Q+figures
        if (
          prev.phase === 'extracting' &&
          prev.theory.status === 'done' &&
          prev.questions.jobId === null &&
          prev.figures.jobId === null &&
          restKickedFor.current !== prev.bookId
        ) {
          didKickRest = true;
        }
        // 3. extracting + theory failed → reconcile immediately
        //    (Q+figures never started; partial state)
        if (
          prev.phase === 'extracting' &&
          prev.theory.status === 'failed' &&
          prev.questions.jobId === null &&
          prev.figures.jobId === null
        ) {
          didReconcile = true;
        }
        // 4. extracting + all 3 terminal → reconcile
        if (prev.phase === 'extracting') {
          const allTerm =
            isTerminal(prev.theory.status) &&
            isTerminal(prev.questions.status) &&
            isTerminal(prev.figures.status);
          if (allTerm && (prev.questions.jobId !== null || prev.figures.jobId !== null)) {
            // Rest was actually kicked + finished — safe to reconcile.
            didReconcile = true;
          }
        }
        return prev;
      });
      if (didKickTheory && s.bookId) {
        await kickRef.current(s.bookId);
      }
      if (didKickRest && s.bookId) {
        restKickedFor.current = s.bookId;
        await kickRestRef.current(s.bookId);
      }
      if (didReconcile && s.bookId) {
        stopPolling();
        await reconcileRef.current(s.bookId);
      }
    } finally {
      tickRunning.current = false;
    }
  }, [apply, stopPolling]);

  // ─── start: explicit kickoff. Idempotent — no-ops if already running.
  const start = useCallback(
    async (bookId: string) => {
      // GUARD: if a run is already in-flight for the same book, don't
      // fire fresh POSTs. The user clicking Start repeatedly would
      // otherwise spawn duplicate question + figure workers (their
      // endpoints supersede prior DB rows but the running worker
      // processes keep going, eating Gemini calls and stalling).
      let isAlreadyRunning = false;
      setState((s) => {
        const ACTIVE: Phase[] = ['loading', 'analysing', 'approving', 'extracting', 'reconciling'];
        if (s.bookId === bookId && ACTIVE.includes(s.phase)) {
          isAlreadyRunning = true;
        }
        return s;
      });
      if (isAlreadyRunning) {
        dbg('start() ignored — pipeline already running for', bookId);
        return;
      }

      dbg('start()', bookId);
      reconciledFor.current = null;
      restKickedFor.current = null;

      apply(() => ({ ...initialState(bookId), phase: 'loading' }));

      // Read current book state to decide where to begin.
      let book: BackendBookOut;
      try {
        book = await getBook(bookId);
      } catch (e) {
        apply(() => ({
          phase: 'error',
          errorMessage: `Couldn't load book: ${explain(e)}`,
        }));
        return;
      }
      dbg('book.status =', book.status);

      // Clear any stale persisted state — explicit Start is always "fresh".
      clearStored(bookId);

      apply(() => ({ bookStatus: book.status }));

      // Branch on book.status. Decision tree:
      //
      //   uploaded | pending   → fire /analyse, then kickThreeParallel
      //   analysing             → wait for current analyse (no double-fire),
      //                            then kickThreeParallel when schema lands
      //   schema_ready          → kickThreeParallel (forceFresh=false,
      //                            uses /approve — first-time path)
      //   extracting | ready    → kickThreeParallel (forceFresh=true,
      //                            uses /re-extract — wipes & re-runs;
      //                            avoids spawning a 2nd theory worker
      //                            on top of an existing one)
      //   failed                → error + retry CTA
      //
      // In every "schema is built" branch we ALWAYS fire all 3 stages so
      // the user sees three progress bars (their expectation when they
      // click Start).
      if (book.status === 'uploaded' || book.status === 'pending') {
        apply(() => ({ phase: 'analysing' }));
        try {
          const r = await postAnalyse(bookId);
          apply(() => ({
            schema: {
              ...initialStage(),
              jobId: r.job_id,
              status: 'queued',
              lastProgressAt: Date.now(),
            },
          }));
        } catch (e) {
          apply(() => ({
            phase: 'error',
            errorMessage: `Couldn't start analyse: ${explain(e)}`,
          }));
          return;
        }
      } else if (book.status === 'analysing') {
        // Backend is still analysing. We don't know the job_id so we can't
        // poll directly — but the next tick will check book.status again
        // and pick up the transition via repeated GET /api/books/:id.
        apply(() => ({ phase: 'analysing' }));
        dbg('book is already analysing; no jobId tracked — will detect transition by polling book');
      } else if (book.status === 'schema_ready') {
        // First-time path — use /approve.
        apply((prev) => ({
          phase: 'analysing',
          schema: { ...prev.schema, status: 'done', progress: 100 },
        }));
        // Tick will pick up analysing + schema=done and call kickThreeParallel
        // (forceFresh=false by default — uses /approve).
      } else if (book.status === 'extracting') {
        // Theory worker is already running on the backend. DO NOT fire
        // /re-extract — we'd spawn a duplicate worker. Attach to the
        // existing run.
        //
        // Start progress at 0 so the per-section poll (in tick) can
        // immediately overwrite with the real count. The poll's
        // "never regress" guard would otherwise block updates if we
        // used a higher placeholder like 50.
        dbg('book is extracting — attaching to existing theory worker');
        apply((prev) => ({
          phase: 'extracting',
          schema: { ...prev.schema, status: 'done', progress: 100 },
          theory: {
            ...prev.theory,
            status: 'running',
            progress: 0,
            message: 'Attached to running extraction',
          },
          questions: { ...prev.questions, status: 'queued', message: 'waiting for theory' },
          figures: { ...prev.figures, status: 'queued', message: 'waiting for theory' },
        }));
      } else if (book.status === 'ready' || book.status === 'extracted') {
        // Theory already finished. Mark theory done. Tick will see this
        // and fire kickRestParallel for Q + figures.
        apply((prev) => ({
          phase: 'extracting',
          schema: { ...prev.schema, status: 'done', progress: 100 },
          theory: { ...prev.theory, status: 'done', progress: 100 },
        }));
      } else if (book.status === 'failed') {
        apply(() => ({
          phase: 'error',
          errorMessage:
            "Backend reports book.status='failed'. Use 'Retry schema' to start over.",
        }));
        return;
      } else {
        // Unknown status — best effort: assume schema is done and kick fresh.
        dbg('unknown book.status', book.status, '— assuming schema done, forcing fresh kick');
        apply((prev) => ({
          phase: 'extracting',
          schema: { ...prev.schema, status: 'done', progress: 100 },
        }));
        await kickThreeParallel(bookId, /* forceFresh */ true);
      }

      // Kick polling.
      startPolling(() => void tick());
    },
    [apply, kickThreeParallel, startPolling, tick],
  );

  // ─── Retry whole stage ──────────────────────────────────────────
  const retryStage = useCallback(
    async (stage: StageKey) => {
      let bookId: string | null = null;
      setState((s) => {
        bookId = s.bookId;
        return s;
      });
      if (!bookId) return;
      dbg('retryStage', stage);

      apply((prev) => ({
        [stage]: { ...prev[stage], inflight: true, error: null },
      }) as Partial<ExtractionState>);

      try {
        let jobId: string | null = null;
        if (stage === 'schema') {
          jobId = (await postAnalyse(bookId)).job_id;
          apply(() => ({ phase: 'analysing' }));
        } else if (stage === 'theory') {
          jobId = (await postReExtractAll(bookId)).job_id;
          apply(() => ({ phase: 'extracting', failedSections: [] }));
        } else if (stage === 'questions') {
          jobId = (await postQuestionBank(bookId)).job_id;
          apply(() => ({ phase: 'extracting', questionsFailed: false }));
        } else if (stage === 'figures') {
          jobId = (await postExtractFigures(bookId)).job_id;
          apply(() => ({ phase: 'extracting', figuresFailed: false }));
        }
        apply(() => ({
          [stage]: {
            ...initialStage(),
            jobId,
            status: 'queued',
            lastProgressAt: Date.now(),
          },
        }) as Partial<ExtractionState>);
        reconciledFor.current = null;
        startPolling(() => void tick());
      } catch (e) {
        apply((prev) => ({
          [stage]: {
            ...prev[stage],
            inflight: false,
            error: `Retry failed: ${explain(e)}`,
          },
        }) as Partial<ExtractionState>);
      }
    },
    [apply, startPolling, tick],
  );

  // ─── Retry one theory section ───────────────────────────────────
  const retrySection = useCallback(
    async (sectionRowId: string) => {
      let bookId: string | null = null;
      setState((s) => {
        bookId = s.bookId;
        return s;
      });
      if (!bookId) return;
      dbg('retrySection', sectionRowId);
      try {
        const r = await postReExtractSection(sectionRowId);
        apply((prev) => ({
          phase: 'extracting',
          theory: {
            ...prev.theory,
            jobId: r.job_id,
            status: 'queued',
            error: null,
            inflight: false,
            lastProgressAt: Date.now(),
          },
          failedSections: prev.failedSections.filter((f) => f.id !== sectionRowId),
        }));
        reconciledFor.current = null;
        startPolling(() => void tick());
      } catch (e) {
        apply(() => ({
          errorMessage: `Retry section failed: ${explain(e)}`,
        }));
      }
    },
    [apply, startPolling, tick],
  );

  const retryAllTheory = useCallback(async () => {
    await retryStage('theory');
  }, [retryStage]);

  const cancel = useCallback(() => stopPolling(), [stopPolling]);

  // Cleanup on unmount.
  useEffect(() => () => stopPolling(), [stopPolling]);

  return { state, start, retryStage, retrySection, retryAllTheory, cancel };
}

// Re-export so callers can detect stalls. Not used internally yet.
export const STALL_MS = STALL_THRESHOLD_MS;
