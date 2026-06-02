// Renders a section's extracted theory blocks.
//
// Block schema (from Section.blocks):
//   { t: 'p',   c: string }              → paragraph
//   { t: 'h3',  c: string }              → heading
//   { t: 'eq',  c: string }              → equation
//   { t: 'def', term: string, c: string} → definition
//   { t: 'kp',  c: string }              → key point
//   { t: 'fig', c: string, label?: str } → figure caption / reference
//   ... other types passed through as raw JSON for now

import type { Section } from '../../api/sections';
import type { Figure } from '../../api/figures';
import { figureImageUrl } from '../../api/figures';
import { Icon } from '../Icon';
import { MathMarkdown } from '../MathMarkdown';

type Block =
  | { t: 'p'; c: string }
  | { t: 'h3'; c: string }
  | { t: 'eq'; c: string }
  | { t: 'def'; term?: string; c: string }
  | { t: 'kp'; c: string }
  | { t: 'fig'; c?: string; label?: string }
  | { t: 'list'; items: string[]; ordered?: boolean }
  | { t: 'table'; headers?: string[]; rows?: string[][]; caption?: string }
  | { t: 'example_ref' | 'exercise_ref' | 'question_ref'; label?: string; ref?: string }
  | { t: 'example'; label?: string; prob?: string; sol?: string; eqs?: string[] }
  | { t: string; [k: string]: unknown };

const CHIP_TYPES = new Set([
  'example_ref',
  'exercise_ref',
  'question_ref',
]);

const STRUCTURAL_TYPES = new Set([
  // "Structural" blocks reset chip-suppression state — they signal
  // a return to genuine theory content after an example body.
  'h3',
  'eq',
  'def',
  'kp',
  'fig',
  'table',
  'example',
]);

/**
 * Theory-tab view cleanup for mixed (theory+questions) sections.
 *
 * The backend's theory worker extracts the FULL page range of a section
 * — including any worked-example problem statements + solutions that
 * are visually inline in the textbook. Those belong to the questions
 * pipeline (and appear in the Questions tab), so showing them inside
 * the Theory tab would duplicate the same content twice and make the
 * theory read like a question paper.
 *
 * The cleanup:
 *   1. Dedupe consecutive chips referencing the same example
 *      (the linker emits both example_ref + question_ref for one example).
 *   2. After a chip, suppress paragraph / list blocks (they're the
 *      worked-example body — "By what percent…" + "SOLUTION" + steps).
 *   3. Any structural block (h3 / eq / def / kp / fig / table / example)
 *      resets the suppression — that signals a return to theory content.
 *
 * Pure theory sections (no chips) flow through unchanged.
 */
function filterTheoryBlocks(blocks: Block[]): Block[] {
  const out: Block[] = [];
  let inExampleBody = false;
  let lastChipLabel: string | null = null;
  for (const b of blocks) {
    if (CHIP_TYPES.has(b.t)) {
      const label = (b as { label?: string }).label ?? null;
      // Dedupe — same chip reference emitted twice in a row.
      if (label && label === lastChipLabel) continue;
      lastChipLabel = label;
      inExampleBody = true;
      out.push(b);
      continue;
    }
    if (STRUCTURAL_TYPES.has(b.t)) {
      // Back to real theory — keep rendering.
      inExampleBody = false;
      lastChipLabel = null;
      out.push(b);
      continue;
    }
    // Paragraph / list — only keep when NOT inside an example body.
    if (!inExampleBody) out.push(b);
  }
  return out;
}

type TheoryViewProps = {
  section: Section | null;
  /** When provided, overrides section.blocks (e.g. regenerated blocks). */
  blocksOverride?: Block[] | null;
  /** Banner shown above the content (e.g. "Regenerated content" or compare). */
  banner?: { label: string; tone: 'regen' | 'original' } | null;
  /** All figures for the book — used to inline images on `fig` blocks. */
  figures?: Figure[];
  /** Hide the internal section header (id + title + status badge). Used by
   *  RegenReviewPage where the parent already renders a section header —
   *  avoids duplicate title/slug stacks. */
  hideHeader?: boolean;
  /** Hide the outer container padding (parent provides its own spacing). */
  flat?: boolean;
};

/** Normalize a figure label / number for matching against block labels.
 *  Backend uses many shapes: "Figure 8.10" / "Fig. 8.10" / "8.10". */
function normLabel(s: string | null | undefined): string {
  if (!s) return '';
  return s
    .toLowerCase()
    .replace(/figure|fig\.?/g, '')
    .replace(/[\s.]/g, '')
    .trim();
}

export function TheoryView({
  section,
  blocksOverride,
  banner,
  figures = [],
  hideHeader = false,
  flat = false,
}: TheoryViewProps) {
  if (!section) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
          fontSize: 14,
        }}
      >
        Pick a section from the left to see its extracted content.
      </div>
    );
  }

  // Render raw backend output — same blocks the existing frontend's
  // BlockRenderer sees.
  // If a regen variant is being viewed, blocksOverride supplies the
  // regenerated blocks instead of the original Section.blocks.
  const rawBlocks = (blocksOverride ?? (section.blocks ?? [])) as Block[];

  // Dedupe linker duplicates: when the example linker emits BOTH an
  // `example_ref` (from the theory OCR pass) AND a `question_ref` (from
  // the downstream linker that connects to the actual extracted question)
  // for the same label, we want to render only the `question_ref` — it's
  // the linked, clickable chip. Drop any `example_ref` whose label also
  // appears as a `question_ref` (or as a sibling `exercise_ref`).
  const linkedLabels = new Set<string>();
  for (const b of rawBlocks) {
    if (b.t === 'question_ref' || b.t === 'exercise_ref') {
      const label = (b as { label?: string }).label?.trim();
      if (label) linkedLabels.add(label);
    }
  }
  let blocks = rawBlocks.filter((b) => {
    if (b.t !== 'example_ref') return true;
    const label = (b as { label?: string }).label?.trim();
    // If a sibling question_ref/exercise_ref exists with the same label
    // → drop this example_ref (duplicate chip).
    return !(label && linkedLabels.has(label));
  });

  // Dedupe duplicate heading: when the FIRST block is a heading whose text
  // matches the section title (case/punctuation-insensitive), drop it. The
  // section title is already shown above (by SectionHeader or by the parent
  // page header). The same applies to a leading `h3` block — common when the
  // extractor emits an h3 with the section name at the top of the section.
  const normHeading = (s: string) =>
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  if (blocks.length > 0) {
    const first = blocks[0] as { t: string; c?: string };
    if (
      (first.t === 'heading' || first.t === 'h3' || first.t === 'h2') &&
      typeof first.c === 'string' &&
      section.title &&
      normHeading(first.c) === normHeading(section.title)
    ) {
      blocks = blocks.slice(1);
    }
  }

  // Build a {normalized label → Figure} map for inline image rendering.
  const figureByLabel = new Map<string, Figure>();
  for (const f of figures) {
    for (const key of [
      f.normalized_label,
      f.figure_number,
      f.figure_id_text,
    ]) {
      const k = normLabel(key);
      if (k) figureByLabel.set(k, f);
    }
  }
  // Also embedded_figures on the Section (set by figure_embedder).
  for (const ef of section.embedded_figures ?? []) {
    const candidates = [
      (ef as unknown as { normalized_label?: string }).normalized_label,
      (ef as unknown as { figure_number?: string }).figure_number,
      (ef as unknown as { figure_id_text?: string }).figure_id_text,
    ];
    for (const c of candidates) {
      const k = normLabel(c);
      if (k && !figureByLabel.has(k)) {
        figureByLabel.set(k, ef as unknown as Figure);
      }
    }
  }

  return (
    <div
      style={{
        flex: flat ? 'unset' : 1,
        overflowY: flat ? 'visible' : 'auto',
        padding: flat ? 0 : '28px 40px 56px',
        background: flat ? 'transparent' : 'var(--bg)',
      }}
    >
      <div style={{ maxWidth: flat ? 'unset' : 760, margin: '0 auto' }}>
        {banner && (
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              marginBottom: 12,
              background:
                banner.tone === 'regen' ? 'var(--red-50)' : 'var(--bg-tint)',
              color:
                banner.tone === 'regen'
                  ? 'var(--red-700)'
                  : 'var(--ink-700)',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
            }}
          >
            {banner.label}
          </div>
        )}
        {!hideHeader && <SectionHeader section={section} />}

        {blocks.length === 0 ? (
          <div
            style={{
              padding: '40px 24px',
              textAlign: 'center',
              color: 'var(--ink-500)',
              background: 'var(--surface)',
              border: '1px dashed var(--line)',
              borderRadius: 12,
              marginTop: 24,
            }}
          >
            No content extracted for this section.
            {section.status === 'failed' && (
              <div style={{ marginTop: 8, color: 'var(--red-700)', fontSize: 13 }}>
                Extraction failed after {section.attempts} attempts.
              </div>
            )}
          </div>
        ) : (
          <div style={{ marginTop: 20 }}>
            {blocks.map((b, i) => (
              <BlockRender key={i} block={b} figureByLabel={figureByLabel} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SectionHeader({ section }: { section: Section }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        {section.section_id}
      </div>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 800,
          letterSpacing: '-0.02em',
          color: 'var(--ink-900)',
          margin: '6px 0 0',
          lineHeight: 1.2,
        }}
      >
        {section.title || section.section_id}
      </h1>
      <div
        style={{
          fontSize: 12,
          color: 'var(--ink-500)',
          marginTop: 6,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        {section.status === 'passed' && (
          <span className="badge ok">
            <span className="dot" /> Extracted
          </span>
        )}
        {section.status === 'failed' && (
          <span className="badge regen">
            <span className="dot" /> Failed
          </span>
        )}
        {section.status !== 'passed' && section.status !== 'failed' && (
          <span className="badge">{section.status}</span>
        )}
        <span>{(section.blocks?.length ?? 0)} blocks</span>
      </div>
    </div>
  );
}

function BlockRender({
  block,
  figureByLabel,
}: {
  block: Block;
  figureByLabel: Map<string, Figure>;
}) {
  const t = block.t;
  if (t === 'h3') {
    return (
      <h3
        style={{
          fontSize: 18,
          fontWeight: 700,
          color: 'var(--ink-900)',
          letterSpacing: '-0.01em',
          margin: '24px 0 10px',
        }}
      >
        {(block as { c?: string }).c}
      </h3>
    );
  }
  if (t === 'p') {
    return (
      <p
        style={{
          fontSize: 15,
          lineHeight: 1.7,
          color: 'var(--ink-800)',
          margin: '0 0 14px',
        }}
      >
        {(block as { c?: string }).c}
      </p>
    );
  }
  if (t === 'eq') {
    const c = (block as { c?: string }).c ?? '';
    // Same prose-vs-math heuristic as PreviewPage / ComposerPage.
    const looksLikeProse = (() => {
      if (c.includes('$')) return false;
      // LaTeX-command override — force math when these appear.
      if (/\\(frac|times|cdot|sum|int|sqrt|sin|cos|tan|log|ln|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|to|Rightarrow|leftarrow|rightarrow|leq|geq|neq|approx|infty|partial|nabla)\b/.test(c)) return false;
      if (/[\^_]\{/.test(c)) return false;
      const wordTokens = c.match(/\b[a-zA-Z]{3,}\b/g) || [];
      if (wordTokens.length >= 3) return true;
      if (/[a-z],\s+[a-z]/i.test(c)) return true;
      if (/\.\s+[A-Z]/.test(c)) return true;
      return false;
    })();
    const rendered = looksLikeProse ? c : (c.includes('$') ? c : `$$${c}$$`);
    return (
      <div
        style={{
          padding: '12px 18px',
          background: 'var(--indigo-50)',
          border: '1px solid var(--indigo-100)',
          borderRadius: 10,
          fontFamily: looksLikeProse ? 'inherit' : 'var(--font-mono)',
          fontSize: 14,
          color: 'var(--indigo-700)',
          margin: '10px 0 14px',
        }}
      >
        <MathMarkdown>{rendered}</MathMarkdown>
      </div>
    );
  }
  if (t === 'def') {
    const b = block as { term?: string; c?: string };
    return (
      <div
        style={{
          padding: '14px 18px',
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 10,
          margin: '10px 0 14px',
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
            marginBottom: 4,
          }}
        >
          Definition
        </div>
        {b.term && (
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--ink-900)',
              marginBottom: 4,
            }}
          >
            {b.term}
          </div>
        )}
        <div style={{ fontSize: 14, lineHeight: 1.65, color: 'var(--ink-800)' }}>
          {b.c}
        </div>
      </div>
    );
  }
  if (t === 'kp') {
    return (
      <div
        style={{
          padding: '12px 14px 12px 16px',
          background: 'var(--warning-bg)',
          borderLeft: '3px solid var(--warning)',
          borderRadius: 8,
          margin: '10px 0 14px',
          fontSize: 14,
          lineHeight: 1.65,
          color: 'var(--ink-800)',
        }}
      >
        <div
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: '#8A5300',
            marginBottom: 4,
          }}
        >
          Key Point
        </div>
        {(block as { c?: string }).c}
      </div>
    );
  }
  if (t === 'fig') {
    const b = block as { c?: string; label?: string };
    // Look up the actual Figure row for inline image rendering.
    const key = normLabel(b.label) || normLabel(b.c);
    const fig = key ? figureByLabel.get(key) : undefined;
    return (
      <figure
        style={{
          margin: '14px 0 18px',
          padding: 0,
          border: '1px solid var(--line)',
          borderRadius: 10,
          overflow: 'hidden',
          background: 'var(--surface)',
        }}
      >
        {fig?.has_original ? (
          <div
            style={{
              background: 'var(--surface-2)',
              padding: 12,
              display: 'grid',
              placeItems: 'center',
              borderBottom: '1px solid var(--line)',
            }}
          >
            <img
              src={figureImageUrl(fig.id)}
              alt={b.label ?? fig.figure_number ?? b.c ?? 'Figure'}
              style={{
                maxWidth: '100%',
                maxHeight: 360,
                objectFit: 'contain',
                display: 'block',
              }}
            />
          </div>
        ) : (
          <div
            style={{
              padding: 14,
              background: 'var(--surface-2)',
              borderBottom: '1px dashed var(--line)',
              display: 'flex',
              gap: 12,
              alignItems: 'center',
              fontSize: 13,
              color: 'var(--ink-500)',
            }}
          >
            <Icon name="image" size={18} className="muted" />
            <span>Figure not available inline</span>
          </div>
        )}
        <figcaption style={{ padding: '10px 14px' }}>
          {(b.label || fig?.figure_number) && (
            <div
              style={{
                fontWeight: 700,
                color: 'var(--ink-900)',
                marginBottom: 4,
                fontSize: 13.5,
              }}
            >
              {b.label ?? fig?.figure_number}
            </div>
          )}
          {(b.c || fig?.caption) && (
            <div style={{ fontSize: 12.5, color: 'var(--ink-700)', lineHeight: 1.5 }}>
              {b.c ?? fig?.caption}
            </div>
          )}
        </figcaption>
      </figure>
    );
  }
  if (t === 'list') {
    const b = block as { items?: string[]; ordered?: boolean };
    const items = b.items ?? [];
    if (items.length === 0) return null;
    // Backend often gives items with baked-in "1. " / "(2) " prefixes —
    // strip them so the <ol> numbers don't double up.
    const strip = (s: string) =>
      s.replace(/^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+/, '').trim();
    const Tag = b.ordered === false ? 'ul' : 'ol';
    return (
      <Tag
        style={{
          margin: '4px 0 14px',
          paddingLeft: 24,
          fontSize: 15,
          lineHeight: 1.7,
          color: 'var(--ink-800)',
        }}
      >
        {items.map((it, i) => (
          <li key={i} style={{ marginBottom: 6 }}>
            <MathMarkdown inline>{strip(it)}</MathMarkdown>
          </li>
        ))}
      </Tag>
    );
  }
  if (t === 'table') {
    const b = block as {
      headers?: string[];
      rows?: string[][];
      caption?: string;
    };
    return (
      <div style={{ margin: '10px 0 16px' }}>
        {b.caption && (
          <div
            style={{
              fontSize: 12,
              color: 'var(--ink-500)',
              fontStyle: 'italic',
              marginBottom: 6,
            }}
          >
            {b.caption}
          </div>
        )}
        <div
          style={{
            overflowX: 'auto',
            border: '1px solid var(--line)',
            borderRadius: 8,
          }}
        >
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 13,
            }}
          >
            {b.headers && b.headers.length > 0 && (
              <thead>
                <tr>
                  {b.headers.map((h, i) => (
                    <th
                      key={i}
                      style={{
                        padding: '8px 12px',
                        background: 'var(--surface-2)',
                        textAlign: 'left',
                        fontWeight: 700,
                        color: 'var(--ink-900)',
                        borderBottom: '1px solid var(--line)',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {(b.rows ?? []).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      style={{
                        padding: '8px 12px',
                        borderTop: '1px solid var(--line-2)',
                        verticalAlign: 'top',
                        color: 'var(--ink-800)',
                      }}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }
  if (t === 'example_ref' || t === 'exercise_ref' || t === 'question_ref') {
    const b = block as { label?: string; ref?: string };
    return (
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          background: 'var(--indigo-50)',
          border: '1px solid var(--indigo-100)',
          borderRadius: 999,
          margin: '8px 8px 8px 0',
          fontSize: 12.5,
          color: 'var(--indigo-700)',
          fontWeight: 600,
        }}
      >
        <Icon name="layers" size={12} />
        <span>{b.label ?? b.ref ?? t.replace('_', ' ')}</span>
      </div>
    );
  }
  if (t === 'example') {
    const b = block as {
      label?: string;
      prob?: string;
      sol?: string;
      eqs?: string[];
    };
    return (
      <div
        style={{
          padding: '16px 18px',
          border: '1px solid var(--line)',
          borderLeft: '3px solid var(--indigo-700)',
          borderRadius: 10,
          background: 'var(--surface)',
          margin: '12px 0 16px',
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            color: 'var(--indigo-700)',
            marginBottom: 6,
          }}
        >
          {b.label ?? 'Example'}
        </div>
        {b.prob && (
          <div
            style={{
              fontSize: 14,
              lineHeight: 1.65,
              color: 'var(--ink-900)',
              marginBottom: b.sol || (b.eqs?.length ?? 0) > 0 ? 12 : 0,
            }}
          >
            {b.prob}
          </div>
        )}
        {b.eqs?.map((eq, i) => (
          <div
            key={i}
            style={{
              padding: '8px 12px',
              background: 'var(--indigo-50)',
              borderRadius: 6,
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              color: 'var(--indigo-700)',
              margin: '6px 0',
            }}
          >
            {eq}
          </div>
        ))}
        {b.sol && (
          <div
            style={{
              fontSize: 13.5,
              lineHeight: 1.65,
              color: 'var(--ink-700)',
              marginTop: 8,
              paddingTop: 8,
              borderTop: '1px solid var(--line-2)',
            }}
          >
            <strong style={{ color: 'var(--ink-800)' }}>Solution: </strong>
            {b.sol}
          </div>
        )}
      </div>
    );
  }
  // Unknown — collapsible debug fallback (only seen if backend ships a
  // new block type we haven't added yet).
  return (
    <details
      style={{
        padding: '8px 12px',
        background: 'var(--bg-tint)',
        border: '1px dashed var(--line)',
        borderRadius: 8,
        margin: '8px 0',
        fontSize: 11,
        color: 'var(--ink-500)',
      }}
    >
      <summary style={{ cursor: 'pointer', userSelect: 'none' }}>
        Unknown block type: <code>{t}</code>
      </summary>
      <pre
        style={{
          marginTop: 8,
          fontFamily: 'var(--font-mono)',
          overflow: 'auto',
        }}
      >
        {JSON.stringify(block, null, 2)}
      </pre>
    </details>
  );
}
