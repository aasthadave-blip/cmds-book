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
            <FigureCard
              key={f.id}
              figure={f}
              sectionRef={sectionFigures.section_ref}
              sectionTitle={null}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function FigureCard({
  figure: f,
  sectionRef,
  sectionTitle,
}: {
  figure: Figure;
  sectionRef: string;
  sectionTitle: string | null;
}) {
  const [imgErr, setImgErr] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);

  // Default rule: if a regenerated variant exists, show it. Else fall back
  // to the original. No more Original/Regenerated toggle — the side-by-side
  // comparison lives behind a single ↔ button so the default card is clean.
  const showingRegen = f.has_regen;
  const url = showingRegen
    ? figureImageUrl(f.id, true)
    : f.has_original
      ? figureImageUrl(f.id)
      : null;

  const canCompare = f.has_original && f.has_regen;

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
            key={`${f.id}-${showingRegen ? 'regen' : 'orig'}`}
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
              {imgErr ? 'Image unavailable' : 'No image yet'}
            </div>
          </div>
        )}
        {/* Variant indicator — small chip top-right so reviewers know
            whether they're looking at original or regen by default. */}
        <span
          className={showingRegen ? 'badge regen' : 'badge'}
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            fontSize: 10,
            background: showingRegen ? undefined : 'var(--surface)',
          }}
        >
          {showingRegen ? (
            <>
              <Icon name="sparkles" size={10} /> regenerated
            </>
          ) : (
            'original'
          )}
        </span>
        {/* ↔ Compare button — only useful when both variants exist */}
        {canCompare && (
          <button
            onClick={() => setCompareOpen(true)}
            title="Compare original vs regenerated"
            style={{
              position: 'absolute',
              top: 8,
              left: 8,
              fontSize: 11,
              padding: '4px 10px',
              border: '1px solid var(--line)',
              borderRadius: 6,
              background: 'var(--surface)',
              color: 'var(--ink-900)',
              cursor: 'pointer',
              fontWeight: 700,
            }}
          >
            ↔ Compare
          </button>
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
        {/* Section anchor — always visible so reviewers can confirm the
            figure is filed under the right section without scrolling. */}
        <div
          style={{
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
            letterSpacing: '0.04em',
            marginBottom: 6,
          }}
        >
          {sectionRef}
          {sectionTitle ? ` · ${sectionTitle}` : ''}
        </div>
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
        </div>
      </div>

      {/* Compare modal — side-by-side Original vs Regenerated */}
      {compareOpen && canCompare && (
        <div
          onClick={() => setCompareOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.6)',
            zIndex: 1000,
            display: 'grid',
            placeItems: 'center',
            padding: 24,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--bg)',
              borderRadius: 12,
              padding: 20,
              maxWidth: 1200,
              width: '100%',
              maxHeight: '90vh',
              overflow: 'auto',
              boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12,
              }}
            >
              <div style={{ fontWeight: 800, fontSize: 16 }}>
                {f.figure_number ?? 'Figure'} — Original vs Regenerated
              </div>
              <button
                onClick={() => setCompareOpen(false)}
                style={{
                  border: 'none',
                  background: 'transparent',
                  fontSize: 20,
                  cursor: 'pointer',
                  color: 'var(--ink-500)',
                }}
              >
                ✕
              </button>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    color: 'var(--ink-500)',
                    marginBottom: 6,
                  }}
                >
                  Original
                </div>
                <img
                  src={figureImageUrl(f.id, false)}
                  alt="original"
                  style={{
                    width: '100%',
                    maxHeight: '70vh',
                    objectFit: 'contain',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                  }}
                />
              </div>
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: '0.1em',
                    textTransform: 'uppercase',
                    color: 'var(--indigo-700)',
                    marginBottom: 6,
                  }}
                >
                  ✨ Regenerated
                </div>
                <img
                  src={figureImageUrl(f.id, true)}
                  alt="regenerated"
                  style={{
                    width: '100%',
                    maxHeight: '70vh',
                    objectFit: 'contain',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
