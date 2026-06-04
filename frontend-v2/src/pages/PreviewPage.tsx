// Preview — read-only render of the FinalDraft items (NOT final-merge).
//
// CRITICAL: this page must consume the SAME endpoint as Composer
// (/api/books/:id/final-draft) so that every composer edit (reorder, edit,
// remove, insert custom text, merge regen) reflects here instantly.
//
// Matches OLD prod FinalPreviewPage byte-for-byte for rendering logic.

import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { API_BASE, ApiError, req } from '../api/client';
import { useBook } from '../api/books';
import { Icon } from '../components/Icon';
import { MathMarkdown } from '../components/MathMarkdown';
import { stripFigPlaceholders } from '../lib/questionText';

type Block = { t: string; [k: string]: unknown };

type SectionHeadingItem = {
  id: string;
  type: 'section_heading';
  parent_section_id: string | null;
  section_id: string;
  title: string;
  level: number;
  regen: boolean;
};
type BlockItem = {
  id: string;
  type: 'block';
  parent_section_id: string | null;
  block: Block;
};
type FigureItem = {
  id: string;
  type: 'figure';
  parent_section_id: string | null;
  figure: {
    ref_id: string;
    figure_id: string;
    label: string;
    caption: string;
    variant: 'original' | 'regen';
    image_url: string;
  };
};
type QuestionItem = {
  id: string;
  type: 'question';
  parent_section_id: string | null;
  question: {
    id: string;
    raw_text?: string;
    solution_text?: string | null;
    question_number?: string | null;
    exercise_ref?: string | null;
  };
};
type CustomTextItem = {
  id: string;
  type: 'custom_text';
  parent_section_id: string | null;
  content: string;
};
type FinalDraftItem =
  | SectionHeadingItem
  | BlockItem
  | FigureItem
  | QuestionItem
  | CustomTextItem;

type FinalDraftResponse = {
  id: string;
  book_id: string;
  items: FinalDraftItem[];
  item_count: number;
};

export default function PreviewPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();
  const bookState = useBook(bookId);

  const [items, setItems] = useState<FinalDraftItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    setError(null);
    try {
      // SAME endpoint Composer uses — guarantees Preview reflects edits.
      const d = await req<FinalDraftResponse>(
        `/api/books/${bookId}/final-draft?prefer_regen=true`,
      );
      setItems(d.items ?? []);
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Load failed',
      );
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => { void load(); }, [load]);

  const exportAs = (fmt: 'json' | 'markdown' | 'docx') => {
    if (!bookId) return;
    const a = document.createElement('a');
    a.href = `${API_BASE}/api/books/${bookId}/final-draft/export/${fmt}`;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  if (bookState.kind === 'loading') {
    return <div className="content fade-up"><div className="content-narrow"><div className="card" style={{ padding: 28 }}>Loading…</div></div></div>;
  }

  const title = bookState.kind === 'ready' ? bookState.data.book.title : '—';

  return (
    <div
      className="fade-up"
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        background: '#faf8f4',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '14px 28px',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexShrink: 0,
        }}
      >
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/compose`)}
          title="Back to Composer"
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
              color: 'var(--indigo-700)',
              marginBottom: 2,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icon name="eye" size={11} /> Preview
          </div>
          <h1 style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-900)', margin: 0 }}>
            {title}
          </h1>
          <div style={{ fontSize: 11, color: 'var(--ink-500)', marginTop: 2 }}>
            {items.length} items · reflects current Composer draft
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/compose`)}
          title="Back to Composer to edit"
        >
          <Icon name="arrow-l" size={14} /> Edit in Composer
        </button>
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => exportAs('json')}
            title="Download as JSON"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="download" size={12} /> .json
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => exportAs('markdown')}
            title="Download as Markdown"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="md" size={12} /> .md
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => exportAs('docx')}
            title="Download Word DOCX"
            style={{ padding: '4px 10px' }}
          >
            <Icon name="docx" size={12} /> .docx
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '8px 28px', background: 'var(--red-50)', color: 'var(--red-700)', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto', padding: '32px 48px' }}>
        <div
          style={{
            maxWidth: 860,
            margin: '0 auto',
            background: '#ffffff',
            padding: '48px 56px',
            border: '1px solid var(--line)',
            borderRadius: 8,
            boxShadow: '0 1px 4px rgba(15, 23, 42, 0.06)',
          }}
        >
          {loading && <div style={{ color: 'var(--ink-500)' }}>Loading preview…</div>}
          {!loading && items.length === 0 && (
            <div style={{ padding: 48, color: 'var(--ink-500)', textAlign: 'center' }}>
              Draft is empty. Open Composer to start authoring.
            </div>
          )}
          {/* Dedupe: drop the first section_heading item if it matches the page title (book title). */}
          {(() => {
            const norm = (s: string) =>
              s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
            let displayItems = items;
            if (
              items.length > 0 &&
              items[0].type === 'section_heading' &&
              title &&
              norm(items[0].title) === norm(title)
            ) {
              displayItems = items.slice(1);
            }
            return displayItems.map((item) => renderItem(item));
          })()}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// renderItem — one item per type. Matches OLD prod FinalPreviewPage.
// ─────────────────────────────────────────────────────────────────

function renderItem(item: FinalDraftItem): React.ReactElement | null {
  if (item.type === 'section_heading') {
    const lvl = Math.max(0, Math.min(5, item.level));
    const sizes = [26, 22, 18, 16, 14, 13];
    return (
      <h3
        key={item.id}
        style={{
          margin: lvl <= 1 ? '24px 0 12px 0' : '16px 0 8px 0',
          fontSize: sizes[lvl],
          fontWeight: 800,
          color: 'var(--ink-900)',
          paddingBottom: lvl <= 1 ? 6 : 0,
          borderBottom: lvl <= 1 ? '1px solid var(--line-2)' : 'none',
          letterSpacing: '-0.01em',
        }}
      >
        {item.title}
        {item.regen && (
          <span style={{ marginLeft: 10, fontSize: 11, color: 'var(--indigo-700)', fontWeight: 700, verticalAlign: 'middle' }}>
            ✨ regen
          </span>
        )}
      </h3>
    );
  }
  if (item.type === 'block') {
    return (
      <div key={item.id} style={{ margin: '6px 0' }}>
        <BlockRow block={item.block} />
      </div>
    );
  }
  if (item.type === 'figure') {
    const src = item.figure.image_url.startsWith('http')
      ? item.figure.image_url
      : `${API_BASE}${item.figure.image_url}`;
    return (
      // display:block on the figure (was the default) lets two adjacent
      // <figure> items align side-by-side via flex; explicitly stack
      // them so trailing figures never render as a 2-up row.
      <figure
        key={item.id}
        style={{
          display: 'block',
          margin: '16px auto',
          maxWidth: 640,
          textAlign: 'center',
        }}
      >
        <img
          src={src}
          alt={item.figure.label}
          style={{
            display: 'block',
            margin: '0 auto',
            maxWidth: '100%',
            maxHeight: 420,
            borderRadius: 6,
            border: '1px solid var(--line-2)',
          }}
          onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
        />
        <figcaption style={{ fontSize: 12, color: 'var(--ink-500)', marginTop: 6, fontStyle: 'italic' }}>
          <strong>{item.figure.label}</strong>{item.figure.caption && ` — ${item.figure.caption}`}
        </figcaption>
      </figure>
    );
  }
  if (item.type === 'question') {
    const q = item.question;
    // Embedded figures attached to this question (e.g. "see Figure 4.7"
    // resolved by the figure embedder). These should render inside the
    // question card — losing them in preview was a real bug.
    const embedded = (q as { embedded_figures?: Array<{
      ref_id?: string;
      figure_id: string;
      label?: string;
      caption?: string;
      variant?: string;
      image_url: string;
    }> }).embedded_figures ?? [];
    return (
      <div
        key={item.id}
        style={{
          marginBottom: 14,
          padding: '12px 14px',
          background: '#fbf9f3',
          borderLeft: '3px solid #D4DBFA',
          borderRadius: 4,
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: 'var(--indigo-700)',
            marginBottom: 4,
            letterSpacing: '0.06em',
          }}
        >
          {q.question_number ? `Q${q.question_number}` : (q.exercise_ref || 'Question')}
        </div>
        <div style={{ fontSize: 14, color: 'var(--ink-900)', lineHeight: 1.55 }}>
          <MathMarkdown>{stripFigPlaceholders(q.raw_text) || '(no text)'}</MathMarkdown>
        </div>
        {/* Embedded figures inline at the bottom of the question text */}
        {embedded.length > 0 && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {embedded.map((ef) => {
              const src = ef.image_url.startsWith('http')
                ? ef.image_url
                : `${API_BASE}${ef.image_url}`;
              return (
                <figure key={ef.ref_id ?? ef.figure_id} style={{ margin: 0, textAlign: 'center' }}>
                  <img
                    src={src}
                    alt={ef.caption || ef.label || 'Figure'}
                    style={{
                      maxWidth: '100%',
                      maxHeight: 320,
                      borderRadius: 6,
                      border: '1px solid var(--line-2)',
                      background: 'var(--surface-2)',
                    }}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                  />
                  {(ef.label || ef.caption) && (
                    <figcaption
                      style={{
                        fontSize: 12,
                        color: 'var(--ink-500)',
                        marginTop: 4,
                        fontStyle: 'italic',
                      }}
                    >
                      {ef.label && <strong>{ef.label}</strong>}
                      {ef.label && ef.caption && ' — '}
                      {ef.caption}
                    </figcaption>
                  )}
                </figure>
              );
            })}
          </div>
        )}
        {q.solution_text && (
          <details style={{ marginTop: 8 }}>
            <summary style={{ fontSize: 12, color: 'var(--ink-500)', cursor: 'pointer' }}>
              Solution
            </summary>
            <div
              style={{
                marginTop: 6,
                padding: '6px 10px',
                background: 'var(--bg-tint)',
                fontSize: 13,
                lineHeight: 1.55,
              }}
            >
              {/* Same renderer as the question body so math + markdown
                  + tables (the "| X | 0 | 1 |" raw pipes bug) all
                  display consistently. */}
              <MathMarkdown>{q.solution_text}</MathMarkdown>
            </div>
          </details>
        )}
      </div>
    );
  }
  if (item.type === 'custom_text') {
    return (
      <div
        key={item.id}
        style={{
          margin: '8px 0',
          padding: '10px 14px',
          background: '#F7FFF6',
          borderLeft: '3px solid var(--success)',
          borderRadius: 4,
          fontSize: 14,
          color: 'var(--ink-900)',
          lineHeight: 1.6,
          whiteSpace: 'pre-wrap',
        }}
      >
        {item.content || <em style={{ color: 'var(--ink-400)' }}>(empty custom text)</em>}
      </div>
    );
  }
  return null;
}

function BlockRow({ block }: { block: Block }) {
  const t = String(block.t ?? '');
  const c = String((block as { c?: string }).c ?? '');
  if (t === 'h3') return <h3 style={{ fontSize: 17, fontWeight: 700, marginTop: 18, marginBottom: 8, color: 'var(--ink-900)' }}>{c}</h3>;
  if (t === 'p') return (
    <div style={{ marginBottom: 12, lineHeight: 1.65, fontSize: 15, color: 'var(--ink-900)' }}>
      <MathMarkdown>{c}</MathMarkdown>
    </div>
  );
  if (t === 'kp') return (
    <div style={{ background: '#FFF9E5', border: '1px solid #FFE7A1', borderLeft: '4px solid #C28000', padding: '12px 16px', borderRadius: 4, marginBottom: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: '#8A5300', letterSpacing: '0.1em', marginBottom: 4, textTransform: 'uppercase' }}>Key Point</div>
      <div style={{ fontSize: 14, lineHeight: 1.55 }}>
        <MathMarkdown>{c}</MathMarkdown>
      </div>
    </div>
  );
  if (t === 'eq') {
    // RAW OCR rendering — render the eq block content EXACTLY as it
    // was extracted from the PDF. No math wrapping, no LaTeX rendering,
    // no transformations. KaTeX's math-mode `%` comment behavior was
    // truncating equations like "10/100 = 10%, 25/100 = 25%, ..." at
    // the first `%`. Source-faithful display is the priority.
    return (
      <div style={{ background: 'var(--bg-tint)', padding: '10px 14px', borderRadius: 6, marginBottom: 12, fontSize: 14, color: 'var(--indigo-700)', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap' }}>
        {c}
      </div>
    );
  }
  if (t === 'def') {
    const term = String((block as { term?: string }).term ?? '');
    return (
      <div style={{ marginBottom: 12, padding: '8px 12px', background: '#F3F6FF', borderLeft: '3px solid var(--indigo-700)', borderRadius: 4 }}>
        <strong style={{ color: 'var(--indigo-700)' }}>{term}: </strong>
        <span>{c}</span>
      </div>
    );
  }
  if (t === 'list') {
    const items = ((block as { items?: string[] }).items ?? []);
    return <ol style={{ marginBottom: 12, paddingLeft: 22 }}>
      {items.map((it, k) => <li key={k} style={{ marginBottom: 4, lineHeight: 1.55, fontSize: 14, whiteSpace: 'pre-wrap' }}>{it}</li>)}
    </ol>;
  }
  if (t === 'fig') {
    return <div style={{ marginBottom: 12, padding: '12px 14px', background: 'var(--bg-tint)', borderRadius: 6, fontSize: 12, color: 'var(--ink-500)' }}>
      📷 {c || 'Figure placeholder'}
    </div>;
  }
  if (t === 'example_ref' || t === 'exercise_ref' || t === 'question_ref') {
    const label = String((block as { label?: string }).label ?? '');
    return <span style={{ display: 'inline-block', padding: '3px 10px', background: 'var(--indigo-50)', color: 'var(--indigo-700)', borderRadius: 12, fontSize: 12, fontWeight: 700, marginRight: 6, marginBottom: 6 }}>{label}</span>;
  }
  return null;
}
