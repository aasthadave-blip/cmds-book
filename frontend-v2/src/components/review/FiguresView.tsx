// Real Figures view — shows extracted figures with thumbnails for the
// selected section.

import { useState } from 'react';

import { Icon } from '../Icon';
import { figureImageUrl, type Figure, type SectionFigures } from '../../api/figures';

type Props = {
  sectionRef: string | null;
  sectionFigures: SectionFigures | null;
  loading?: boolean;
  emptyMessage?: string;
};

export function FiguresView({
  sectionRef,
  sectionFigures,
  loading,
  emptyMessage,
}: Props) {
  if (loading) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Loading figures…
      </div>
    );
  }

  if (!sectionRef) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Pick a section from the left to see its figures.
      </div>
    );
  }

  if (emptyMessage) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        {emptyMessage}
      </div>
    );
  }

  if (!sectionFigures || sectionFigures.figures.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        No figures in this section.
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '28px 40px 56px',
        background: 'var(--bg)',
      }}
    >
      <div style={{ maxWidth: 980, margin: '0 auto' }}>
        <div style={{ marginBottom: 18 }}>
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
            {sectionFigures.section_ref}
          </div>
          <h1
            style={{
              fontSize: 24,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: 'var(--ink-900)',
              margin: '6px 0 0',
            }}
          >
            {sectionFigures.figures.length} figure
            {sectionFigures.figures.length === 1 ? '' : 's'}
          </h1>
        </div>
        <div
          style={{
            display: 'grid',
            // Bigger cards — 1 column up to ~720px, 2 columns above that.
            // Each card is full-width so the image is genuinely big.
            gridTemplateColumns: 'repeat(auto-fill, minmax(420px, 1fr))',
            gap: 20,
          }}
        >
          {sectionFigures.figures.map((f) => (
            <FigureCard key={f.id} figure={f} />
          ))}
        </div>
      </div>
    </div>
  );
}

function FigureCard({ figure: f }: { figure: Figure }) {
  const [imgErr, setImgErr] = useState(false);
  // Tab state: which variant to display. Default to Regenerated when one
  // exists, otherwise show Original. Reset imgErr whenever variant changes.
  const [variant, setVariant] = useState<'original' | 'regen'>(
    f.has_regen ? 'regen' : 'original',
  );
  const url =
    variant === 'regen' && f.has_regen
      ? figureImageUrl(f.id, true)
      : f.has_original
        ? figureImageUrl(f.id)
        : null;
  return (
    <div
      className="card"
      style={{
        padding: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Original / Regenerated toggle — only visible when both exist */}
      {f.has_original && f.has_regen && (
        <div
          style={{
            display: 'flex',
            borderBottom: '1px solid var(--line)',
            background: 'var(--surface-2)',
          }}
        >
          <button
            onClick={() => { setVariant('original'); setImgErr(false); }}
            style={{
              flex: 1,
              padding: '8px 12px',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              background: variant === 'original' ? 'var(--surface)' : 'transparent',
              color: variant === 'original' ? 'var(--ink-900)' : 'var(--ink-500)',
              border: 'none',
              borderBottom: variant === 'original' ? '2px solid var(--ink-900)' : '2px solid transparent',
              cursor: 'pointer',
            }}
          >
            Original
          </button>
          <button
            onClick={() => { setVariant('regen'); setImgErr(false); }}
            style={{
              flex: 1,
              padding: '8px 12px',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              background: variant === 'regen' ? 'var(--surface)' : 'transparent',
              color: variant === 'regen' ? 'var(--indigo-700)' : 'var(--ink-500)',
              border: 'none',
              borderBottom: variant === 'regen' ? '2px solid var(--indigo-700)' : '2px solid transparent',
              cursor: 'pointer',
            }}
          >
            ✨ Regenerated
          </button>
        </div>
      )}
      <div
        style={{
          minHeight: 420,
          background: 'var(--surface-2)',
          display: 'grid',
          placeItems: 'center',
          borderBottom: '1px solid var(--line)',
          position: 'relative',
          overflow: 'hidden',
          padding: 12,
        }}
      >
        {url && !imgErr ? (
          <img
            // Key forces re-mount on variant change so cached error state is cleared
            key={`${f.id}-${variant}`}
            src={url}
            alt={f.caption ?? f.figure_number ?? 'Figure'}
            onError={() => setImgErr(true)}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
            }}
          />
        ) : (
          <div
            style={{
              color: 'var(--ink-400)',
              fontSize: 12,
              textAlign: 'center',
              padding: 16,
            }}
          >
            <Icon name="image" size={28} />
            <div style={{ marginTop: 6 }}>
              {imgErr
                ? 'Image unavailable'
                : variant === 'regen'
                  ? 'No regenerated image'
                  : 'No original'}
            </div>
          </div>
        )}
        {/* If both exist but toggle is hidden (no toggle shown for single-variant figures),
            still show a small badge for the regen-only case. */}
        {f.has_regen && !f.has_original && (
          <span
            className="badge regen"
            style={{ position: 'absolute', top: 8, right: 8, fontSize: 10 }}
          >
            <Icon name="sparkles" size={10} /> regen
          </span>
        )}
      </div>
      <div style={{ padding: '14px 16px' }}>
        {/* Figure label — bold heading row */}
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            marginBottom: 6,
            flexWrap: 'wrap',
          }}
        >
          <div
            style={{
              fontSize: 15,
              fontWeight: 800,
              color: 'var(--ink-900)',
              letterSpacing: '-0.01em',
            }}
          >
            {f.figure_number ?? (f.normalized_label ? `Figure ${f.normalized_label}` : 'Figure')}
          </div>
          {/* Where the figure is embedded — theory or question */}
          {f.context_hint && (
            <span
              className="kbd"
              style={{
                fontSize: 10,
                padding: '2px 8px',
                background:
                  f.context_hint === 'question'
                    ? 'var(--red-50)'
                    : 'var(--indigo-50)',
                color:
                  f.context_hint === 'question'
                    ? 'var(--red-700)'
                    : 'var(--indigo-700)',
                fontWeight: 700,
                letterSpacing: '0.05em',
                textTransform: 'uppercase',
              }}
            >
              {f.context_hint}
            </span>
          )}
        </div>
        {/* Caption / figure name */}
        {f.caption && (
          <div
            style={{
              fontSize: 13,
              color: 'var(--ink-700)',
              lineHeight: 1.5,
              marginBottom: 4,
            }}
          >
            {f.caption}
          </div>
        )}
        <div
          style={{
            marginTop: 6,
            fontSize: 11,
            color: 'var(--ink-500)',
            display: 'flex',
            gap: 10,
          }}
        >
          {f.page_number && <span>p.{f.page_number}</span>}
          {f.semantic_type && <span>{f.semantic_type}</span>}
          {f.context_hint && (
            <span className="kbd" style={{ fontSize: 10 }}>
              {f.context_hint}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
