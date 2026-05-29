// Real Questions view — renders the extracted question rows for the
// selected section.

import { Icon } from '../Icon';
import { API_BASE } from '../../api/client';
import type {
  ExtractedQuestion,
  SectionQuestions,
} from '../../api/questions';

type Props = {
  sectionRef: string | null;
  sectionQuestions: SectionQuestions | null;
  loading?: boolean;
  emptyMessage?: string;
};

export function QuestionsView({
  sectionRef,
  sectionQuestions,
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
        Loading questions…
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
        Pick a section from the left to see its questions.
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

  if (!sectionQuestions || sectionQuestions.questions.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        No questions extracted from this section.
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
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        <SectionHeader sectionQuestions={sectionQuestions} />
        <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {sectionQuestions.questions.map((q) => (
            <QuestionCard key={q.id} q={q} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SectionHeader({
  sectionQuestions: sq,
}: {
  sectionQuestions: SectionQuestions;
}) {
  return (
    <div>
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
        {sq.section_ref}
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
        {sq.section_title || sq.section_ref}
      </h1>
      <div
        style={{
          fontSize: 12,
          color: 'var(--ink-500)',
          marginTop: 6,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <span className="badge ok">
          <span className="dot" />
          {sq.extracted} extracted
        </span>
        {sq.identified > sq.extracted && (
          <span>
            {sq.identified} identified · {sq.missed} missed
          </span>
        )}
      </div>
    </div>
  );
}

function QuestionCard({ q }: { q: ExtractedQuestion }) {
  const isExample = q.kind === 'example';
  const accent = isExample ? 'var(--indigo-700)' : 'var(--red-600)';
  const label = isExample
    ? `Example ${q.question_number ?? ''}`.trim()
    : `Q${q.question_number ?? ''}`.trim();

  return (
    <div
      style={{
        padding: '16px 18px',
        border: '1px solid var(--line)',
        borderLeft: `3px solid ${accent}`,
        borderRadius: 10,
        background: 'var(--surface)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <span
          className="qnum"
          style={{
            background: isExample ? 'var(--indigo-50)' : 'var(--red-50)',
            color: accent,
          }}
        >
          {label || (isExample ? 'EX' : 'Q')}
        </span>
        {q.question_type && (
          <span className="badge">{q.question_type}</span>
        )}
        {q.status === 'failed' && (
          <span className="badge regen">
            <span className="dot" />
            Failed
          </span>
        )}
        {q.is_hidden && (
          <span className="badge idle">
            <span className="dot" />
            Hidden
          </span>
        )}
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {q.page_start ? `p.${q.page_start}` : ''}
        </span>
      </div>
      {/* Section anchor — always shown below the Q label so reviewers
          can confirm at a glance which section the question is filed
          under. Falls back to just the ref when no title available. */}
      {(q.section_title || q.section_ref) && (
        <div
          style={{
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
            letterSpacing: '0.04em',
            marginBottom: 8,
            paddingBottom: 6,
            borderBottom: '1px dashed var(--line)',
          }}
        >
          {q.section_ref}
          {q.section_title ? ` · ${q.section_title}` : ''}
        </div>
      )}
      <div
        style={{
          fontSize: 14.5,
          lineHeight: 1.65,
          color: 'var(--ink-900)',
          whiteSpace: 'pre-wrap',
        }}
      >
        {q.raw_text}
      </div>
      {/* Embedded figures — render at the BOTTOM of the question text
          (closest the UI can get without doing char-offset splicing).
          Each figure shows its label, image, and caption. */}
      {(q.embedded_figures ?? []).length > 0 && (
        <div
          style={{
            marginTop: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {(q.embedded_figures ?? []).map((ef) => (
            <div
              key={ef.ref_id}
              style={{
                border: '1px solid var(--line)',
                borderRadius: 10,
                overflow: 'hidden',
                background: 'var(--surface)',
              }}
            >
              <div
                style={{
                  padding: '6px 12px',
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  color: 'var(--ink-700)',
                  background: 'var(--surface-2)',
                  borderBottom: '1px solid var(--line)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                {ef.label || 'Figure'}
                {ef.variant === 'regen' && (
                  <span
                    style={{
                      fontSize: 10,
                      color: 'var(--indigo-700)',
                      fontWeight: 700,
                    }}
                  >
                    ✨ regen
                  </span>
                )}
              </div>
              <img
                src={
                  ef.image_url.startsWith('http')
                    ? ef.image_url
                    : `${API_BASE}${ef.image_url}`
                }
                alt={ef.caption || ef.label || 'Figure'}
                style={{
                  width: '100%',
                  maxHeight: 360,
                  objectFit: 'contain',
                  background: 'var(--surface-2)',
                  display: 'block',
                }}
              />
              {ef.caption && (
                <div
                  style={{
                    padding: '8px 12px',
                    fontSize: 12.5,
                    color: 'var(--ink-700)',
                    lineHeight: 1.5,
                  }}
                >
                  {ef.caption}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {q.has_solution && q.solution_text && (
        <details
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: '1px dashed var(--line)',
          }}
        >
          <summary
            style={{
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
              userSelect: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icon name="check" size={12} /> Solution
          </summary>
          <div
            style={{
              marginTop: 8,
              padding: '12px 14px',
              background: 'var(--surface-2)',
              borderRadius: 8,
              fontSize: 13.5,
              lineHeight: 1.65,
              color: 'var(--ink-800)',
              whiteSpace: 'pre-wrap',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {q.solution_text}
          </div>
        </details>
      )}
    </div>
  );
}
