// Extracted content review page — the "what did the pipeline produce?"
// surface for ops users.
//
// Layout:
//
//   ┌─────────────────────────────────────────────────────────────┐
//   │  Crumbs · Book hero · [Start regeneration] CTA              │
//   ├─────────────────────────────────────────────────────────────┤
//   │  TOP TABS:  [Theory] [Questions] [Figures]                  │
//   ├──────────────┬──────────────────────────────────────────────┤
//   │              │                                              │
//   │  SECTION     │  CONTENT VIEW                                │
//   │  SIDEBAR     │  (per-tab renderer)                          │
//   │              │                                              │
//   └──────────────┴──────────────────────────────────────────────┘
//
// Tab-scoped section lists (per user's request):
//   Theory    — every section the worker touched (passed | failed)
//   Questions — sections that appear in the latest question bank
//   Figures   — sections that have one or more figures
//
// Each tab's sidebar is filtered to only the sections relevant to that
// tab. The selected section state is shared across tabs (so switching
// tabs keeps the same section selected when possible).

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { SectionSidebar } from '../components/review/SectionSidebar';
import { TopTabs } from '../components/review/TopTabs';
import { TheoryView } from '../components/review/TheoryView';
import { QuestionsView } from '../components/review/QuestionsView';
import { FiguresView } from '../components/review/FiguresView';
import { SchemaViewerModal } from '../components/review/SchemaViewerModal';
import { useBook } from '../api/books';
import { useSections, type Section, type TabKey } from '../api/sections';
import { useBookQuestions } from '../api/questions';
import { useBookFigures } from '../api/figures';
import { useLatestRegeneration } from '../api/regenerations';
import { SubTabs } from '../components/review/SubTabs';

/** Sub-tab inside a Theory view: which version to show. */
type ViewVariant = 'regen' | 'original' | 'compare';

export default function ReviewPage() {
  const { bookId } = useParams();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();

  // All hooks at the top.
  const bookState = useBook(bookId);
  const sectionsState = useSections(bookId);
  const questionsState = useBookQuestions(bookId);
  const figuresState = useBookFigures(bookId);
  const regenState = useLatestRegeneration(bookId);

  const [tab, setTab] = useState<TabKey>('theory');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [schemaOpen, setSchemaOpen] = useState(false);
  // Sub-tab — defaults to 'regen' when there's a regenerated version, else
  // 'original'. Respects ?view= in URL if set.
  const initialVariant: ViewVariant = (() => {
    const q = search.get('view');
    if (q === 'regen' || q === 'original' || q === 'compare') return q;
    return 'regen';
  })();
  const [variant, setVariant] = useState<ViewVariant>(initialVariant);
  const hasRegen = regenState.kind === 'ready';
  // When regen is absent, force original view so the sub-tabs aren't lying.
  useEffect(() => {
    if (!hasRegen && variant !== 'original') setVariant('original');
  }, [hasRegen, variant]);

  // Source lists (defaulted to [] while loading so memos are stable).
  const allSections =
    sectionsState.kind === 'ready' ? sectionsState.sections : [];
  const banksDetail =
    questionsState.kind === 'ready' ? questionsState.detail : null;
  const figuresData =
    figuresState.kind === 'ready' ? figuresState.data : null;

  // ─── Schema walk: reading-order index + Category A id set ─────────
  // Backend returns /sections alphabetically, which is NOT the natural
  // reading order — we build an order map from the schema for sorting.
  //
  // ALSO during the same walk we collect Category A section IDs
  // (sections whose `content_types` includes "questions" — EXAMPLE /
  // EXERCISE rows). These belong ONLY in the Questions tab; they must
  // NOT appear in the Theory tab sidebar. The backend's theory worker
  // already correctly skips them at extraction time; this filter just
  // mirrors that on the UI so we render the same surface.
  type ExcludedInfo = {
    section_id: string;
    title: string;
    expected_question_count: number;
  };
  type SchemaWalk = {
    order: Record<string, number>;
    categoryAIds: Set<string>;
    /** All Cat A or mixed sections marked type='excluded' so the
     *  Questions tab can surface them with an "Excluded" badge. */
    excludedQs: ExcludedInfo[];
  };
  const schemaWalk = useMemo<SchemaWalk>(() => {
    if (bookState.kind !== 'ready') {
      return { order: {}, categoryAIds: new Set(), excludedQs: [] };
    }
    const order: Record<string, number> = {};
    const categoryAIds = new Set<string>();
    const excludedQs: ExcludedInfo[] = [];
    let idx = 0;
    type Node = {
      id?: string;
      title?: string;
      type?: string;
      content_types?: string[];
      expected_question_count?: number;
      subsections?: Node[];
    };
    // Category split after the "remove Mixed" change:
    //
    //   Category A (PURE questions)  = content_types is exactly ["questions"]
    //     → Questions tab only
    //     → Hidden from Theory tab sidebar
    //
    //   Category B (theory-bearing)  = content_types includes "theory"
    //     → Theory tab (and shown in sidebar)
    //
    // Mixed legacy sections (["theory","questions"]) are normalised by the
    // backend's _sanitize_schema postpass to ["theory"], but we apply the
    // same defensive filter here so the UI is correct even if a stored
    // schema slips through.
    const hasQuestionContent = (n: Node) => {
      const ct = (n.content_types ?? []).map((s) =>
        String(s).toLowerCase().trim(),
      );
      return ct.includes('questions');
    };
    const isCategoryA = (n: Node) => {
      const ct = (n.content_types ?? []).map((s) =>
        String(s).toLowerCase().trim(),
      );
      // Pure Cat A only — Mixed sections (which have theory content) are
      // shown in the Theory tab, with their Cat A nested items rendered
      // as chips inside the parent's blocks.
      return ct.includes('questions') && !ct.includes('theory');
    };
    const walk = (nodes: Node[] | undefined) => {
      if (!nodes) return;
      for (const n of nodes) {
        if (n.id && !(n.id in order)) {
          order[n.id] = idx++;
          if (isCategoryA(n)) categoryAIds.add(n.id);
          // Collect excluded question sections (end-of-chapter
          // exercises and the like) so the Questions tab can list
          // them as "Excluded — N expected Q".
          if (
            n.type === 'excluded' &&
            (hasQuestionContent(n) || (n.expected_question_count ?? 0) > 0)
          ) {
            excludedQs.push({
              section_id: n.id,
              title: n.title ?? n.id,
              expected_question_count: n.expected_question_count ?? 0,
            });
          }
        }
        if (n.subsections?.length) walk(n.subsections);
      }
    };
    const schema = (bookState.data.raw.schema_ ?? null) as
      | { sections?: Node[] }
      | null;
    if (schema?.sections) walk(schema.sections);
    return { order, categoryAIds, excludedQs };
  }, [bookState]);
  const schemaOrder = schemaWalk.order;
  const categoryAIds = schemaWalk.categoryAIds;
  const excludedQs = schemaWalk.excludedQs;

  const sortBySchema = useMemo(
    () => (a: Section, b: Section) => {
      const ia = schemaOrder[a.section_id] ?? Number.MAX_SAFE_INTEGER;
      const ib = schemaOrder[b.section_id] ?? Number.MAX_SAFE_INTEGER;
      if (ia !== ib) return ia - ib;
      // Fallback alphabetical for sections not in schema (orphans).
      return a.section_id.localeCompare(b.section_id);
    },
    [schemaOrder],
  );

  // ─── Per-tab section sets ─────────────────────────────────────────
  // For Theory: any section the worker created with a terminal status.
  // For Questions: sections that appear in the question bank's section list.
  // For Figures: sections that have at least one figure.
  //
  // We index sections by section_id slug so we can resolve a sidebar
  // selection back to a full Section row (which has the row UUID).
  const sectionsBySlug = useMemo(() => {
    const m = new Map<string, Section>();
    for (const s of allSections) m.set(s.section_id, s);
    return m;
  }, [allSections]);

  const theorySections = useMemo(
    () =>
      allSections
        // Terminal status (worker actually touched it).
        .filter((s) => s.status === 'passed' || s.status === 'failed')
        // Mirror the backend's Category A/B split: theory worker only
        // operates on Category B sections, so V-Studio's Theory tab
        // sidebar must hide Category A (EXAMPLE/EXERCISE) rows. Those
        // surface in the Questions tab instead, and inside Theory text
        // as example_ref / exercise_ref / question_ref chips.
        .filter((s) => !categoryAIds.has(s.section_id))
        .sort(sortBySchema),
    [allSections, sortBySchema, categoryAIds],
  );

  const questionSections = useMemo<Section[]>(() => {
    // Questions tab combines THREE sources, matching the OLD frontend:
    //
    //   1. fromSchema: Cat A sections from schema (examples, in-text
    //      exercises). These are the questions_v3 worker's main targets.
    //   2. synthetic-from-excluded: sections marked type='excluded' in
    //      the schema with question content (so reviewer sees what was
    //      explicitly skipped from extraction).
    //   3. synthetic-from-bank-orphans (NEW): sections that appear in
    //      the question bank's `sections[]` but have NO matching node in
    //      the schema. These are the end-of-chapter "PRACTICE QUESTIONS",
    //      "REVIEW PROBLEMS" etc. — discovered by the question worker
    //      after analyser finished, so they don't live in schema. Without
    //      this list, V-Studio silently hides them.
    const fromSchema = allSections.filter((s) =>
      categoryAIds.has(s.section_id),
    );
    const existingIds = new Set(fromSchema.map((s) => s.section_id));
    const synthetic: Section[] = excludedQs
      .filter((x) => !existingIds.has(x.section_id))
      .map(
        (x) =>
          ({
            id: `excluded:${x.section_id}`,
            book_id:
              bookState.kind === 'ready' ? bookState.data.book.id : '',
            section_id: x.section_id,
            title: x.title,
            level: null,
            blocks: [],
            qc_local: null,
            qc_llm: null,
            status: 'excluded',
            attempts: 0,
            embedded_figures: [],
          }) as Section,
      );

    // ─── End-of-chapter orphans from the question bank ─────────────
    // Build the set of section_refs that ARE in the schema tree (any
    // depth) using the same `schemaOrder` map — its keys are exactly
    // the schema node ids walked in tree order.
    const inSchemaSet = new Set<string>(Object.keys(schemaOrder));
    const orphansFromBank: Section[] = [];
    if (banksDetail?.sections) {
      for (const bs of banksDetail.sections) {
        const ref = bs.section_ref;
        if (!ref || inSchemaSet.has(ref) || existingIds.has(ref)) continue;
        orphansFromBank.push({
          id: `bank-orphan:${ref}`,
          book_id:
            bookState.kind === 'ready' ? bookState.data.book.id : '',
          section_id: ref,
          title: bs.section_title ?? ref,
          level: null,
          // Synthetic block list — store a placeholder so getStatusDot's
          // hasContent check returns true (showing amber, not red) when
          // questions were actually extracted from this orphan.
          blocks: bs.questions.length > 0 ? [{ t: 'placeholder' }] : [],
          qc_local: null,
          qc_llm: null,
          // 'passed' when questions exist, 'failed' when zero extracted —
          // matches the dot semantics of regular sections.
          status: bs.questions.length > 0 ? 'passed' : 'failed',
          attempts: 0,
          embedded_figures: [],
        } as Section);
      }
    }

    return [...fromSchema, ...synthetic, ...orphansFromBank].sort(
      sortBySchema,
    );
  }, [
    allSections,
    categoryAIds,
    sortBySchema,
    excludedQs,
    bookState,
    schemaOrder,
    banksDetail,
  ]);

  const figureSections = useMemo<Section[]>(() => {
    if (!figuresData) return [];
    // Sections with figures, split into two buckets:
    //   • inSchema: section_ref matches a real allSections row
    //   • orphans:  section_ref like "_orphan" or chapter-root ("3")
    //               that has figures but no matching DB section.
    //               Without surfacing these the Figures tab looks empty
    //               for any unattached figure.
    const allSectionIds = new Set(allSections.map((s) => s.section_id));
    const inSchema: Section[] = [];
    const orphans: Section[] = [];
    for (const fs of figuresData.sections) {
      const ref = fs.section_ref;
      const count = fs.figures?.length ?? 0;
      if (count === 0 || !ref) continue;
      if (allSectionIds.has(ref)) {
        const match = allSections.find((s) => s.section_id === ref);
        if (match) inSchema.push(match);
      } else {
        // Build a synthetic Section row so the sidebar can render it.
        // Use a friendly title for the "_orphan" bucket.
        const title =
          ref === '_orphan'
            ? `Unattached figures (${count})`
            : ref.startsWith('_')
            ? `${ref.replace(/^_/, '').replace(/-/g, ' ')} figures`
            : ref;
        orphans.push({
          id: `fig-orphan:${ref}`,
          book_id:
            bookState.kind === 'ready' ? bookState.data.book.id : '',
          section_id: ref,
          title,
          level: null,
          blocks: [{ t: 'placeholder' }],
          qc_local: null,
          qc_llm: null,
          status: 'passed',
          attempts: 0,
          embedded_figures: [],
        } as Section);
      }
    }
    return [...inSchema.sort(sortBySchema), ...orphans];
  }, [figuresData, allSections, sortBySchema, bookState]);

  const visibleSections: Section[] =
    tab === 'theory'
      ? theorySections
      : tab === 'questions'
      ? questionSections
      : figureSections;

  // ─── Selection bookkeeping ────────────────────────────────────────
  // Reset selection when switching tabs if the current selection isn't
  // visible in the new tab; pick the first visible section in that case.
  useEffect(() => {
    if (
      selectedId &&
      visibleSections.find((s) => s.id === selectedId)
    ) {
      return;
    }
    setSelectedId(visibleSections[0]?.id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, visibleSections.length]);

  // ─── Early returns AFTER all hooks ────────────────────────────────
  if (bookState.kind === 'loading' || sectionsState.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading…
          </div>
        </div>
      </div>
    );
  }
  if (bookState.kind === 'error') {
    return (
      <ErrorPanel
        title="Couldn't load book"
        message={bookState.error}
        onBack={() => navigate('/library')}
      />
    );
  }
  if (sectionsState.kind === 'error') {
    return (
      <ErrorPanel
        title="Couldn't load sections"
        message={sectionsState.error}
        onBack={() => navigate('/library')}
      />
    );
  }

  const { book } = bookState.data;

  // Counts shown in TopTabs.
  const counts = {
    theory: theorySections.length,
    questions: banksDetail?.total_questions ?? 0,
    figures: figuresData?.total_figures ?? 0,
  };

  const selected =
    visibleSections.find((s) => s.id === selectedId) ?? null;

  // Find tab-specific content for the selected section.
  const selectedIsExcluded = selected?.status === 'excluded';
  const selectedQuestions = (() => {
    if (!selected || !banksDetail) return null;
    if (selectedIsExcluded) return null;
    return (
      banksDetail.sections.find(
        (s) => s.section_ref === selected.section_id,
      ) ?? null
    );
  })();
  const selectedFigures = (() => {
    if (!selected || !figuresData) return null;
    return (
      figuresData.sections.find(
        (s) => s.section_ref === selected.section_id,
      ) ?? null
    );
  })();
  // Suppress unused-warning for sectionsBySlug — kept available for
  // future drill-throughs from Q/Figures clicks back to theory section.
  void sectionsBySlug;

  return (
    <div
      className="fade-up"
      style={{
        flex: 1,
        minHeight: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '18px 28px 14px',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
        }}
      >
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate('/library')}
          title="Back to library"
        >
          <Icon name="arrow-l" size={14} /> Back
        </button>
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
              marginBottom: 2,
            }}
          >
            Extracted content
          </div>
          <h1
            style={{
              fontSize: 22,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: 'var(--ink-900)',
              margin: 0,
            }}
          >
            {book.title}
          </h1>
        </div>
        <button
          className="top-icon-btn"
          title="Back to extraction page (per-stage progress + schema)"
          onClick={() => navigate(`/books/${bookId}/extract`)}
          style={{ width: 36, height: 36 }}
        >
          <Icon name="eye" size={16} />
        </button>
        <button
          className="top-icon-btn"
          title="View / edit schema"
          onClick={() => setSchemaOpen(true)}
          style={{ width: 36, height: 36 }}
        >
          <Icon name="layers" size={16} />
        </button>
        {hasRegen && (
          <button
            className="top-icon-btn"
            title="Open regenerated content review"
            onClick={() => navigate(`/books/${bookId}/regen-review`)}
            style={{ width: 36, height: 36 }}
          >
            <Icon name="sparkles" size={16} />
          </button>
        )}
        <button
          className="top-icon-btn"
          title="Open Composer (edit / reorder / add sections)"
          onClick={() => navigate(`/books/${bookId}/compose`)}
          style={{ width: 36, height: 36 }}
        >
          <Icon name="docx" size={16} />
        </button>
        <button
          className="top-icon-btn"
          title="Open clean preview"
          onClick={() => navigate(`/books/${bookId}/preview`)}
          style={{ width: 36, height: 36 }}
        >
          <Icon name="file" size={16} />
        </button>
        {hasRegen && (
          <button
            className="btn btn-ghost"
            onClick={() => navigate(`/books/${bookId}/regenerate`)}
            title="Re-run regeneration with new parameters / custom instructions"
          >
            <Icon name="regen" size={14} /> Regenerate again
          </button>
        )}
        {!hasRegen && (
          <button
            className="btn btn-primary"
            onClick={() => navigate(`/books/${bookId}/regenerate`)}
          >
            <Icon name="regen" size={14} /> Start regeneration
          </button>
        )}
        {hasRegen && regenState.kind === 'ready' && (
          <ApproveButton
            regenId={regenState.latest.id}
            onApproved={() => regenState.refetch()}
          />
        )}
      </div>

      {/* Top tabs */}
      <TopTabs active={tab} onChange={setTab} counts={counts} />

      {/* Sub-tabs (Regenerated / Original / Compare) */}
      <SubTabs
        active={variant}
        hasRegen={hasRegen}
        onChange={(v) => {
          setVariant(v);
          // Persist in URL so deep links to the regen view work.
          const sp = new URLSearchParams(search);
          sp.set('view', v);
          setSearch(sp, { replace: true });
        }}
      />

      {/* Sidebar + Content */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          minHeight: 0,
          background: 'var(--bg)',
        }}
      >
        <SectionSidebar
          sections={visibleSections}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
        {tab === 'theory' && (
          <TheoryContent
            selected={selected}
            variant={variant}
            regenBlocks={
              hasRegen && selected
                ? regenState.latest.blocks_by_section[selected.section_id] ??
                  null
                : null
            }
            figures={
              figuresState.kind === 'ready'
                ? figuresState.data.sections.flatMap((s) => s.figures)
                : []
            }
          />
        )}
        {tab === 'questions' && (
          <QuestionsView
            sectionRef={selected?.section_id ?? null}
            sectionQuestions={selectedQuestions}
            loading={questionsState.kind === 'loading'}
            emptyMessage={
              selectedIsExcluded
                ? `This section was marked Excluded in the schema (worker skipped it). The schema editor (⊟) lets you include it if you want to extract these questions.`
                : questionsState.kind === 'empty'
                ? 'No question bank for this book yet.'
                : questionsState.kind === 'error'
                ? `Error: ${questionsState.error}`
                : undefined
            }
          />
        )}
        {tab === 'figures' && (
          <FiguresView
            sectionRef={selected?.section_id ?? null}
            sectionFigures={selectedFigures}
            loading={figuresState.kind === 'loading'}
            emptyMessage={
              figuresState.kind === 'error'
                ? `Error: ${figuresState.error}`
                : undefined
            }
          />
        )}
      </div>

      {bookId && (
        <SchemaViewerModal
          bookId={bookId}
          open={schemaOpen}
          onClose={() => setSchemaOpen(false)}
          onSaved={() => {
            // Refresh book + sections after schema edit so the sidebar
            // and Cat A/B filter pick up new content_types.
            void bookState.refetch();
            void sectionsState.refetch();
          }}
        />
      )}
    </div>
  );
}

/**
 * Approve + save the regen output as the final draft. Backend endpoint:
 * POST /api/regenerations/:regen_id/save — same call the existing
 * frontend's composer uses.
 */
function ApproveButton({
  regenId,
  onApproved,
}: {
  regenId: string;
  onApproved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const handle = async () => {
    setBusy(true);
    setErr(null);
    try {
      const { req } = await import('../api/client');
      await req(`/api/regenerations/${regenId}/save`, { method: 'POST' });
      onApproved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        className="btn btn-accent"
        onClick={() => void handle()}
        disabled={busy}
        title="Approve regenerated content and save as the final draft"
      >
        {busy ? <span className="spinner" /> : <Icon name="check" size={14} />}
        Approve &amp; Save
      </button>
      {err && (
        <span
          style={{
            position: 'absolute',
            top: 60,
            right: 28,
            background: 'var(--red-50)',
            color: 'var(--red-700)',
            padding: '6px 10px',
            borderRadius: 8,
            fontSize: 12,
            border: '1px solid var(--red-100)',
          }}
        >
          {err}
        </span>
      )}
    </>
  );
}

function TheoryContent({
  selected,
  variant,
  regenBlocks,
  figures,
}: {
  selected: Section | null;
  variant: ViewVariant;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | null;
  figures: import('../api/figures').Figure[];
}) {
  // Cast through unknown so the TheoryView's stricter block shape doesn't
  // complain — TheoryView treats unknown block.t with a debug fallback.
  const regen = regenBlocks as unknown as never;

  if (variant === 'compare') {
    return (
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div
          style={{
            flex: 1,
            borderRight: '1px solid var(--line)',
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
          }}
        >
          <TheoryView
            section={selected}
            banner={{ label: 'Original', tone: 'original' }}
            figures={figures}
          />
        </div>
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
          }}
        >
          <TheoryView
            section={selected}
            blocksOverride={regen}
            banner={{ label: 'Regenerated', tone: 'regen' }}
            figures={figures}
          />
        </div>
      </div>
    );
  }
  if (variant === 'regen') {
    return (
      <TheoryView
        section={selected}
        blocksOverride={regen}
        banner={{ label: 'Regenerated', tone: 'regen' }}
        figures={figures}
      />
    );
  }
  return (
    <TheoryView
      section={selected}
      banner={{ label: 'Original (from extraction)', tone: 'original' }}
      figures={figures}
    />
  );
}

function ErrorPanel({
  title,
  message,
  onBack,
}: {
  title: string;
  message: string;
  onBack: () => void;
}) {
  return (
    <div className="content fade-up">
      <div className="content-narrow">
        <div
          className="card"
          style={{
            padding: 24,
            background: 'var(--red-50)',
            border: '1px solid var(--red-100)',
          }}
        >
          <div style={{ fontWeight: 700, color: 'var(--red-700)' }}>{title}</div>
          <div
            style={{
              fontSize: 12,
              marginTop: 4,
              fontFamily: 'var(--font-mono)',
              wordBreak: 'break-word',
            }}
          >
            {message}
          </div>
          <button
            className="btn btn-soft btn-sm"
            style={{ marginTop: 12 }}
            onClick={onBack}
          >
            Back to library
          </button>
        </div>
      </div>
    </div>
  );
}
