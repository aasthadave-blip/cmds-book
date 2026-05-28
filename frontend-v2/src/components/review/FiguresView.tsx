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
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 16,
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
  const url = f.has_original ? figureImageUrl(f.id) : null;
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
          height: 200,
          background: 'var(--surface-2)',
          display: 'grid',
          placeItems: 'center',
          borderBottom: '1px solid var(--line)',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {url && !imgErr ? (
          <img
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
              {imgErr ? 'Image unavailable' : 'No original'}
            </div>
          </div>
        )}
        {f.has_regen && (
          <span
            className="badge regen"
            style={{ position: 'absolute', top: 8, right: 8, fontSize: 10 }}
          >
            <Icon name="sparkles" size={10} /> regen
          </span>
        )}
      </div>
      <div style={{ padding: '12px 14px' }}>
        <div
          style={{
            fontSize: 13.5,
            fontWeight: 700,
            color: 'var(--ink-900)',
            marginBottom: 4,
          }}
        >
          {f.figure_number ?? `Figure ${f.normalized_label ?? ''}`}
        </div>
        {f.caption && (
          <div
            style={{
              fontSize: 12.5,
              color: 'var(--ink-700)',
              lineHeight: 1.5,
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
