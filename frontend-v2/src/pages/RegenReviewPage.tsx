// Dedicated regenerated-content review page.
//
// Distinct from /books/:id/review (extracted content):
//   • Scroll-based full-chapter view (not click-to-show-one-section)
//   • Side rail TOC auto-jumps as the reader scrolls
//   • Per-section composer toolbar: reseed (regen with custom instruction),
//     preview (clean full-screen), QC warnings panel
//   • Top tabs: Theory | Questions | Figures
//   • Sub tabs: Regenerated (default) | Original | Comparison
//   • Top CTAs: Back to params · Export DOCX · Approve & Save
//
// Backend ALREADY supports:
//   POST /api/regenerations/:id/sections/:section_ref/retry → reseed
//   POST /api/regenerations/:id/save                        → approve
//   GET  /api/books/:id/export/docx                         → DOCX dl
//
// What's intentionally deferred (need new backend endpoints):
//   • Inline edit of blocks (PATCH section blocks)
//   • Section reorder / delete / add (PATCH regen blocks)
// These are post-demo work.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { ApiError, req } from '../api/client';
import { useBook } from '../api/books';
import { useSections, type Section } from '../api/sections';
import { useBookQuestions, type QuestionBankDetail } from '../api/questions';
import { useBookFigures, type BookFigures } from '../api/figures';
import { useLatestRegeneration } from '../api/regenerations';

import { Icon } from '../components/Icon';
import { TheoryView } from '../components/review/TheoryView';
import { QuestionsView } from '../components/review/QuestionsView';
import { FiguresView } from '../components/review/FiguresView';

type TopTab = 'theory' | 'questions' | 'figures';
type SubTab = 'regenerated' | 'original' | 'compare';

type SchemaNode = {
  id?: string;
  title?: string;
  type?: string;
  content_types?: string[];
  subsections?: SchemaNode[];
};

export default function RegenReviewPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();

  const bookState = useBook(bookId);
  const sectionsState = useSections(bookId);
  const questionsState = useBookQuestions(bookId);
  const figuresState = useBookFigures(bookId);
  const regenState = useLatestRegeneration(bookId);

  const [topTab, setTopTab] = useState<TopTab>('theory');
  const [subTab, setSubTab] = useState<SubTab>('regenerated');
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [reseedModal, setReseedModal] = useState<{
    open: boolean;
    sectionRef: string;
    sectionTitle: string;
  } | null>(null);
  const [previewModal, setPreviewModal] = useState<{
    open: boolean;
    section: Section | null;
  }>({ open: false, section: null });
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Schema walk for ordering + Cat A/B ─────────────────────────
  const { schemaOrder, catBIds, catAIds } = useMemo(() => {
    const order: Record<string, number> = {};
    const catB = new Set<string>();
    const catA = new Set<string>();
    if (bookState.kind !== 'ready') return { schemaOrder: order, catBIds: catB, catAIds: catA };
    let idx = 0;
    const walk = (nodes: SchemaNode[] | undefined) => {
      if (!nodes) return;
      for (const n of nodes) {
        if (n.type === 'excluded') continue;
        if (n.id && !(n.id in order)) {
          order[n.id] = idx++;
          const ct = (n.content_types ?? []).map((c) => String(c).toLowerCase().trim());
          if (ct.includes('questions')) catA.add(n.id);
          else catB.add(n.id);
        }
        walk(n.subsections);
      }
    };
    const schema = bookState.data.raw.schema_ as { sections?: SchemaNode[] } | null;
    walk(schema?.sections);
    return { schemaOrder: order, catBIds: catB, catAIds: catA };
  }, [bookState]);

  const allSections = sectionsState.kind === 'ready' ? sectionsState.sections : [];
  const banksDetail = questionsState.kind === 'ready' ? questionsState.detail : null;
  const figuresData = figuresState.kind === 'ready' ? figuresState.data : null;

  const sortBySchema = useCallback(
    (a: Section, b: Section) => {
      const ai = schemaOrder[a.section_id] ?? Number.MAX_SAFE_INTEGER;
      const bi = schemaOrder[b.section_id] ?? Number.MAX_SAFE_INTEGER;
      if (ai !== bi) return ai - bi;
      return a.section_id.localeCompare(b.section_id);
    },
    [schemaOrder],
  );

  const visibleSections: Section[] = useMemo(() => {
    if (topTab === 'theory') {
      return allSections
        .filter((s) => s.status === 'passed' || s.status === 'failed')
        .filter((s) => catBIds.has(s.section_id))
        .sort(sortBySchema);
    }
    if (topTab === 'questions') {
      return allSections.filter((s) => catAIds.has(s.section_id)).sort(sortBySchema);
    }
    // figures
    if (!figuresData) return [];
    const slugs = new Set(
      figuresData.sections
        .filter((s) => (s.figures?.length ?? 0) > 0)
        .map((s) => s.section_ref),
    );
    return allSections.filter((s) => slugs.has(s.section_id)).sort(sortBySchema);
  }, [topTab, allSections, catBIds, catAIds, sortBySchema, figuresData]);

  // ── Regen blocks by section (for "Regenerated" + "Compare") ──────
  const regenBlocksBySection: Record<string, Array<{ t: string; [k: string]: unknown }>> =
    regenState.kind === 'ready' ? regenState.latest.blocks_by_section || {} : {};

  // ── IntersectionObserver for auto-highlight + auto-scroll TOC ───
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const sidebarItemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const mainScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const inView = entries.filter((e) => e.isIntersecting);
        if (inView.length > 0) {
          // Pick the one closest to the top of the viewport
          const top = inView.sort(
            (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
          )[0];
          const id = top.target.id;
          setActiveSectionId(id);
          // auto-scroll the sidebar to keep active item visible
          const sidebarEl = sidebarItemRefs.current.get(id);
          if (sidebarEl) {
            sidebarEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        }
      },
      {
        root: mainScrollRef.current,
        rootMargin: '-30% 0px -60% 0px',
        threshold: 0,
      },
    );
    sectionRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [visibleSections.length, topTab, subTab]);

  const scrollToSection = useCallback((sectionId: string) => {
    const el = sectionRefs.current.get(sectionId);
    if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, []);

  // ── Approve & Save ─────────────────────────────────────────────
  const handleApprove = useCallback(async () => {
    if (regenState.kind !== 'ready') return;
    setApproving(true);
    setError(null);
    try {
      await req(`/api/regenerations/${regenState.latest.id}/save`, {
        method: 'POST',
      });
      regenState.refetch();
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Save failed',
      );
    } finally {
      setApproving(false);
    }
  }, [regenState]);

  // ── Reseed (per-section regen with custom instruction) ─────────
  const submitReseed = useCallback(
    async (instruction: string) => {
      if (!reseedModal || regenState.kind !== 'ready') return;
      try {
        await req(
          `/api/regenerations/${regenState.latest.id}/sections/${encodeURIComponent(reseedModal.sectionRef)}/retry`,
          {
            method: 'POST',
            body: JSON.stringify({ custom_instruction: instruction }),
          },
        );
        setReseedModal(null);
        // Refetch regen after a short delay to let backend kick off the task
        setTimeout(() => regenState.refetch(), 1000);
      } catch (e) {
        setError(
          e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
          e instanceof Error ? e.message : 'Reseed failed',
        );
      }
    },
    [reseedModal, regenState],
  );

  // ── Export DOCX ────────────────────────────────────────────────
  const exportDocx = useCallback(() => {
    if (!bookId) return;
    window.open(`${req.length ? '' : ''}/api/books/${bookId}/export/docx`, '_blank');
  }, [bookId]);

  // ── Render ────────────────────────────────────────────────────
  if (bookState.kind === 'loading' || sectionsState.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading regenerated content…
          </div>
        </div>
      </div>
    );
  }
  if (bookState.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--red-700)' }}>
            Couldn't load book: {bookState.error}
          </div>
        </div>
      </div>
    );
  }
  if (regenState.kind === 'empty') {
    return (
      <div className="content fade-up">
        <div className="content-narrow" style={{ maxWidth: 720 }}>
          <div className="card" style={{ padding: 32, textAlign: 'center' }}>
            <Icon name="regen" size={32} />
            <h2 style={{ marginTop: 14, marginBottom: 6 }}>
              No regenerated content yet
            </h2>
            <div style={{ color: 'var(--ink-500)', fontSize: 14, marginBottom: 18 }}>
              Run regeneration first to see the regenerated content here.
            </div>
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/books/${bookId}/regenerate`)}
            >
              <Icon name="regen" size={14} /> Configure regeneration
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (regenState.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--red-700)' }}>
            Couldn't load regeneration: {regenState.error}
          </div>
        </div>
      </div>
    );
  }

  const { book } = bookState.data;
  const hasRegen = regenState.kind === 'ready';

  return (
    <div
      className="fade-up"
      style={{
        flex: 1,
        minHeight: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        background: '#faf8f4',  // warm paper feel — distinct from review page
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
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--indigo-700)',
              marginBottom: 2,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icon name="regen" size={11} /> Regenerated content
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
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/review`)}
          title="Back to extracted (original) content"
        >
          <Icon name="arrow-l" size={14} /> Original review
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/regenerate`)}
          title="Re-run regeneration with new parameters"
        >
          <Icon name="regen" size={14} /> Regen again
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={exportDocx}
          title="Download regenerated chapter as DOCX"
        >
          <Icon name="download" size={14} /> Export DOCX
        </button>
        {hasRegen && (
          <button
            className="btn btn-primary"
            onClick={() => void handleApprove()}
            disabled={approving}
            title="Approve regenerated content and save as the final draft"
          >
            {approving ? <span className="spinner" /> : <Icon name="check" size={14} />}
            Approve &amp; Save
          </button>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: '8px 28px',
            background: 'var(--red-50)',
            borderBottom: '1px solid var(--red-100)',
            color: 'var(--red-700)',
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {/* Top tabs */}
      <div
        style={{
          padding: '10px 28px 0',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          gap: 2,
          flexShrink: 0,
        }}
      >
        {(['theory', 'questions', 'figures'] as TopTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTopTab(t)}
            style={{
              padding: '10px 16px',
              background: 'transparent',
              border: 'none',
              borderBottom:
                topTab === t ? '2px solid var(--indigo-700)' : '2px solid transparent',
              color: topTab === t ? 'var(--ink-900)' : 'var(--ink-500)',
              fontWeight: topTab === t ? 700 : 500,
              fontSize: 13,
              cursor: 'pointer',
              textTransform: 'capitalize',
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Sub tabs */}
      <div
        style={{
          padding: '10px 28px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--line)',
          display: 'flex',
          gap: 8,
          flexShrink: 0,
        }}
      >
        {(
          [
            { id: 'regenerated', label: '✨ Regenerated' },
            { id: 'original', label: 'Original' },
            { id: 'compare', label: 'Comparison' },
          ] as Array<{ id: SubTab; label: string }>
        ).map((sub) => (
          <button
            key={sub.id}
            onClick={() => setSubTab(sub.id)}
            className={`btn btn-sm ${subTab === sub.id ? 'btn-soft' : 'btn-ghost'}`}
          >
            {sub.label}
          </button>
        ))}
      </div>

      {/* Layout: sidebar + main scrollable */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Side TOC */}
        <aside
          style={{
            width: 280,
            flexShrink: 0,
            background: 'var(--surface)',
            borderRight: '1px solid var(--line)',
            overflowY: 'auto',
            padding: '14px 0',
          }}
        >
          <div
            style={{
              padding: '0 18px 8px',
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
            }}
          >
            Sections · {visibleSections.length}
          </div>
          {visibleSections.map((s) => {
            const active = s.section_id === activeSectionId;
            return (
              <button
                key={s.id}
                ref={(el) => {
                  if (el) sidebarItemRefs.current.set(s.section_id, el);
                  else sidebarItemRefs.current.delete(s.section_id);
                }}
                onClick={() => scrollToSection(s.section_id)}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '8px 18px',
                  background: active ? 'var(--indigo-50)' : 'transparent',
                  borderLeft: active
                    ? '3px solid var(--indigo-700)'
                    : '3px solid transparent',
                  color: active ? 'var(--ink-900)' : 'var(--ink-700)',
                  fontWeight: active ? 600 : 500,
                  fontSize: 13,
                  textAlign: 'left',
                  cursor: 'pointer',
                  border: 'none',
                  borderBottom: 'none',
                }}
              >
                <div
                  style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {s.title || s.section_id}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: 'var(--ink-400)',
                    fontFamily: 'var(--font-mono)',
                    marginTop: 2,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {s.section_id}
                </div>
              </button>
            );
          })}
        </aside>

        {/* Main scrollable */}
        <div
          ref={mainScrollRef}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '24px 32px',
            background: '#faf8f4',
          }}
        >
          {visibleSections.length === 0 && (
            <div style={{ padding: 48, color: 'var(--ink-500)', textAlign: 'center' }}>
              No sections to show in this tab.
            </div>
          )}
          {visibleSections.map((section) => (
            <SectionBlock
              key={section.id}
              section={section}
              topTab={topTab}
              subTab={subTab}
              regenBlocks={regenBlocksBySection[section.section_id]}
              banksDetail={banksDetail}
              figuresData={figuresData}
              refSetter={(el) => {
                if (el) sectionRefs.current.set(section.section_id, el);
                else sectionRefs.current.delete(section.section_id);
              }}
              onReseed={() =>
                setReseedModal({
                  open: true,
                  sectionRef: section.section_id,
                  sectionTitle: section.title || section.section_id,
                })
              }
              onPreview={() => setPreviewModal({ open: true, section })}
            />
          ))}
        </div>
      </div>

      {/* Reseed modal */}
      {reseedModal?.open && (
        <ReseedModal
          sectionTitle={reseedModal.sectionTitle}
          onSubmit={(instruction) => void submitReseed(instruction)}
          onClose={() => setReseedModal(null)}
        />
      )}

      {/* Preview modal */}
      {previewModal.open && previewModal.section && (
        <PreviewModal
          section={previewModal.section}
          regenBlocks={regenBlocksBySection[previewModal.section.section_id]}
          subTab={subTab}
          onClose={() => setPreviewModal({ open: false, section: null })}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// SectionBlock — one section rendered in the scroll-based main area.
// ─────────────────────────────────────────────────────────────────

function SectionBlock({
  section,
  topTab,
  subTab,
  regenBlocks,
  banksDetail,
  figuresData,
  refSetter,
  onReseed,
  onPreview,
}: {
  section: Section;
  topTab: TopTab;
  subTab: SubTab;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  banksDetail: QuestionBankDetail | null;
  figuresData: BookFigures | null;
  refSetter: (el: HTMLDivElement | null) => void;
  onReseed: () => void;
  onPreview: () => void;
}) {
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;

  // Compute simple QC delta for THIS section (block count + word count)
  const qc = useMemo(() => {
    if (subTab !== 'compare' && subTab !== 'regenerated') return null;
    const origBlocks = (section.blocks || []) as Array<{ t: string; c?: string }>;
    if (!hasRegen) return null;
    const origFree = origBlocks.filter(
      (b) => !['eq', 'def', 'fig', 'table', 'example', 'example_ref', 'exercise_ref', 'question_ref'].includes(b.t),
    );
    const regenFree = (regenBlocks ?? []).filter(
      (b) => !['eq', 'def', 'fig', 'table', 'example', 'example_ref', 'exercise_ref', 'question_ref'].includes(b.t),
    );
    const blockDelta = origFree.length - regenFree.length;
    const origWords = origBlocks.reduce(
      (n, b) => n + String(b.c ?? '').split(/\s+/).filter(Boolean).length,
      0,
    );
    const regenWords = (regenBlocks ?? []).reduce(
      (n, b) =>
        n + String((b as { c?: string }).c ?? '').split(/\s+/).filter(Boolean).length,
      0,
    );
    const ratio = origWords > 0 ? regenWords / origWords : 1;
    const warnings: string[] = [];
    if (blockDelta !== 0) warnings.push(`Block count drift: orig ${origFree.length} → regen ${regenFree.length}`);
    if (origWords > 50 && ratio < 0.7) warnings.push(`Shrunk to ${Math.round(ratio * 100)}% of original`);
    if (origWords > 50 && ratio > 1.3) warnings.push(`Expanded to ${Math.round(ratio * 100)}% of original`);
    return { ratio, blockDelta, origWords, regenWords, warnings };
  }, [section, regenBlocks, hasRegen, subTab]);

  return (
    <div
      id={section.section_id}
      ref={refSetter}
      style={{
        marginBottom: 40,
        background: 'var(--surface)',
        border: '1px solid var(--line)',
        borderRadius: 10,
        scrollMarginTop: 20,
      }}
    >
      {/* Section header with composer */}
      <div
        style={{
          padding: '14px 20px',
          borderBottom: '1px solid var(--line-2)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          background: '#fdfbf6',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3
            style={{
              fontSize: 17,
              fontWeight: 700,
              color: 'var(--ink-900)',
              margin: 0,
              lineHeight: 1.25,
            }}
          >
            {section.title || section.section_id}
          </h3>
          <div
            style={{
              fontSize: 11,
              color: 'var(--ink-500)',
              fontFamily: 'var(--font-mono)',
              marginTop: 4,
              display: 'flex',
              gap: 10,
              alignItems: 'center',
            }}
          >
            <span>{section.section_id}</span>
            {qc && (
              <>
                <span style={{ opacity: 0.4 }}>·</span>
                <WordRatioBadge ratio={qc.ratio} />
              </>
            )}
          </div>
        </div>

        {/* Per-section composer (Tier A icons) */}
        {(topTab === 'theory' || topTab === 'questions') && (
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={onPreview}
              title="Open this section in a full-screen preview"
            >
              <Icon name="eye" size={13} />
            </button>
            {hasRegen && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={onReseed}
                title="Reseed (regenerate this section with custom instruction)"
              >
                <Icon name="regen" size={13} />
              </button>
            )}
          </div>
        )}
      </div>

      {/* QC warnings panel */}
      {qc && qc.warnings.length > 0 && (
        <div
          style={{
            padding: '8px 20px',
            background: 'var(--amber-50, #FFF9E5)',
            borderBottom: '1px solid var(--line-2)',
            color: '#8A5300',
            fontSize: 12,
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
          }}
        >
          <span style={{ fontSize: 14 }}>⚠</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>QC warnings for this section:</div>
            {qc.warnings.map((w, i) => (
              <div key={i}>• {w}</div>
            ))}
          </div>
        </div>
      )}

      {/* Body content based on subTab */}
      <div style={{ padding: '18px 22px' }}>
        {topTab === 'theory' && (
          <TheoryBody
            section={section}
            regenBlocks={regenBlocks}
            subTab={subTab}
          />
        )}
        {topTab === 'questions' && (
          <QuestionsBody
            section={section}
            banksDetail={banksDetail}
          />
        )}
        {topTab === 'figures' && (
          <FiguresBody
            section={section}
            figuresData={figuresData}
          />
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Body renderers — reuse existing view components.
// ─────────────────────────────────────────────────────────────────

function TheoryBody({
  section,
  regenBlocks,
  subTab,
}: {
  section: Section;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  subTab: SubTab;
}) {
  const origBlocks = (section.blocks || []) as Array<{ t: string; [k: string]: unknown }>;
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;
  // Local alias for TheoryView's Block union — its actual definition is in
  // the TheoryView module; we treat blocks as opaque here.
  type Block = { t: string; [k: string]: unknown };

  if (subTab === 'original' || !hasRegen) {
    return <TheoryView section={section} />;
  }

  if (subTab === 'regenerated') {
    return (
      <TheoryView
        section={section}
        blocksOverride={regenBlocks as Block[]}
        banner={{ label: 'Regenerated', tone: 'regen' }}
      />
    );
  }

  // compare
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 18,
      }}
    >
      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
            marginBottom: 8,
          }}
        >
          Original
        </div>
        <TheoryView
          section={section}
          blocksOverride={origBlocks as Block[]}
          banner={{ label: 'Original', tone: 'original' }}
        />
      </div>
      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--indigo-700)',
            marginBottom: 8,
          }}
        >
          ✨ Regenerated
        </div>
        <TheoryView
          section={section}
          blocksOverride={regenBlocks as Block[]}
          banner={{ label: 'Regenerated', tone: 'regen' }}
        />
      </div>
    </div>
  );
}

function QuestionsBody({
  section,
  banksDetail,
}: {
  section: Section;
  banksDetail: QuestionBankDetail | null;
}) {
  const sectionQuestions =
    banksDetail?.sections.find((s) => s.section_ref === section.section_id) ?? null;
  return (
    <QuestionsView
      sectionRef={section.section_id}
      sectionQuestions={sectionQuestions}
    />
  );
}

function FiguresBody({
  section,
  figuresData,
}: {
  section: Section;
  figuresData: BookFigures | null;
}) {
  const sectionFigures =
    figuresData?.sections.find((s) => s.section_ref === section.section_id) ?? null;
  return (
    <FiguresView
      sectionRef={section.section_id}
      sectionFigures={sectionFigures}
    />
  );
}

// ─────────────────────────────────────────────────────────────────
// WordRatioBadge — green/amber/red pill showing regen/orig word ratio.
// ─────────────────────────────────────────────────────────────────

function WordRatioBadge({ ratio }: { ratio: number }) {
  const pct = Math.round(ratio * 100);
  const color =
    ratio >= 0.85 && ratio <= 1.15 ? 'var(--success)'
    : ratio >= 0.7 && ratio <= 1.3 ? 'var(--warning, #C28000)'
    : 'var(--red-600)';
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 8px',
        borderRadius: 10,
        background: 'var(--bg-tint)',
        color,
      }}
      title="Regenerated length as a percentage of original. Green = healthy (85-115%), amber = somewhat off (70-130%), red = drift outside ±30%."
    >
      {pct}% length
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────
// ReseedModal — opens a small text input to regenerate ONE section with
// a custom instruction. Backend already supports per-section retry.
// ─────────────────────────────────────────────────────────────────

function ReseedModal({
  sectionTitle,
  onSubmit,
  onClose,
}: {
  sectionTitle: string;
  onSubmit: (instruction: string) => void;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    onSubmit(text.trim());
    setBusy(false);
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.45)',
          zIndex: 200,
        }}
      />
      <div
        className="card fade-up"
        style={{
          position: 'fixed',
          top: '15vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(560px, 92vw)',
          padding: 22,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <Icon name="regen" size={16} />
          <h3 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
            Reseed this section
          </h3>
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-500)', marginBottom: 4 }}>
          Section: <strong>{sectionTitle}</strong>
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-700)', marginBottom: 10 }}>
          Add a custom instruction. The current global parameters still apply;
          this instruction is layered on top.
        </div>
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g., Add a real-world analogy. Use simpler vocabulary. Re-derive the key equation step-by-step."
          rows={5}
          style={{
            width: '100%',
            padding: 10,
            border: '1px solid var(--line)',
            borderRadius: 8,
            font: 'inherit',
            fontSize: 13,
            color: 'var(--ink-900)',
            resize: 'vertical',
          }}
        />
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            marginTop: 14,
          }}
        >
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={!text.trim() || busy}
          >
            {busy ? <span className="spinner" /> : <Icon name="regen" size={14} />}
            Regenerate this section
          </button>
        </div>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────
// PreviewModal — full-screen clean view of one section's content.
// ─────────────────────────────────────────────────────────────────

function PreviewModal({
  section,
  regenBlocks,
  subTab,
  onClose,
}: {
  section: Section;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  subTab: SubTab;
  onClose: () => void;
}) {
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;
  const blocks = subTab === 'original' || !hasRegen ? undefined : regenBlocks;
  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.65)',
          zIndex: 200,
        }}
      />
      <div
        className="fade-up"
        style={{
          position: 'fixed',
          top: '5vh',
          bottom: '5vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(900px, 94vw)',
          background: '#faf8f4',
          borderRadius: 12,
          padding: 28,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
          overflowY: 'auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            marginBottom: 20,
            borderBottom: '1px solid var(--line)',
            paddingBottom: 14,
          }}
        >
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'var(--indigo-700)',
                marginBottom: 4,
              }}
            >
              {subTab === 'original' || !hasRegen ? 'Original' : '✨ Regenerated'} · Preview
            </div>
            <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>
              {section.title || section.section_id}
            </h2>
          </div>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <TheoryView
          section={section}
          blocksOverride={(blocks ?? null) as Array<{ t: string; [k: string]: unknown }> | null}
          banner={blocks ? { label: 'Regenerated', tone: 'regen' } : { label: 'Original', tone: 'original' }}
        />
      </div>
    </>
  );
}
