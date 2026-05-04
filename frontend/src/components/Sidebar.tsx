import { useState, useMemo } from "react";
import {
  useBooks,
  useBook,
  useSections,
  useBookRegenerations,
  useQuestionBanks,
  useQuestions,
} from "../api/hooks";
import type { Section, QuestionKind, QuestionBankSectionGroup } from "../api/client";
import { useUI } from "../stores/ui";

export function Sidebar() {
  const { data: books, isLoading } = useBooks();
  const { view, setView, selectedBookId, selectBook } = useUI();
  const [query, setQuery] = useState("");

  const SIDEBAR_STATUSES = new Set([
    "analysing", "schema_ready", "extracting", "ready", "failed",
  ]);
  const filtered = books
    ?.filter((b) => SIDEBAR_STATUSES.has(b.status))
    .filter((b) => b.title.toLowerCase().includes(query.toLowerCase()));

  return (
    <aside className="sb">
      <div className="sb-head">
        <div className="sb-top">
          <div className="sb-logo">📚</div>
          <div>
            <div className="sb-t1">Book Folder</div>
            <div className="sb-t2">Academic Content Library</div>
          </div>
        </div>
        <div className="sb-srch">
          <span style={{ fontSize: "0.7rem", color: "var(--text3)" }}>🔍</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search books…"
          />
        </div>
      </div>

      <div className="sb-scroll">
        <button className="sb-new" onClick={() => setView("upload")}>
          <span>+</span>
          <span>Upload a new book</span>
        </button>

        <div className="sb-lbl">Navigate</div>
        <button
          className={`sb-nav-btn ${view === "library" ? "active" : ""}`}
          onClick={() => setView("library")}
        >
          <span>🏠</span>
          <span>Library</span>
        </button>
        <button
          className={`sb-nav-btn ${view === "settings" ? "active" : ""}`}
          onClick={() => setView("settings")}
        >
          <span>⚙️</span>
          <span>OCR Providers</span>
        </button>

        <div className="sb-lbl">Books</div>
        {isLoading && (
          <div style={{ padding: "4px 14px", fontSize: "0.72rem", color: "var(--text3)" }}>
            Loading…
          </div>
        )}
        {filtered?.length === 0 && (
          <div style={{ padding: "4px 14px", fontSize: "0.72rem", color: "var(--text3)" }}>
            {query ? "No matches" : "No books yet"}
          </div>
        )}
        {filtered?.map((b) => (
          <div key={b.id}>
            <button
              className={`tn ${selectedBookId === b.id ? "active" : ""}`}
              onClick={() => {
                selectBook(b.id);
                setView(b.status === "schema_ready" ? "schema" : "reader");
              }}
            >
              <span className="tarr">▸</span>
              <span className="tico">📘</span>
              <span className="tlbl">{b.title}</span>
              <span className="tcnt">{shortStatus(b.status)}</span>
            </button>
            {selectedBookId === b.id && <BookFolders bookId={b.id} />}
          </div>
        ))}
      </div>
    </aside>
  );
}

function shortStatus(s: string): string {
  if (s === "schema_ready") return "sch";
  if (s === "extracting") return "ext";
  if (s === "ready") return "ok";
  if (s === "failed") return "err";
  return s.slice(0, 3);
}

function BookFolders({ bookId }: { bookId: string }) {
  const { data: regens } = useBookRegenerations(bookId);
  const { data: banks } = useQuestionBanks(bookId);
  const { bookLens, setBookLens } = useUI();

  const latestRegen = regens?.[0] ?? null;
  // Prefer the latest READY bank so the user sees results even if a retry
  // is currently in-flight. Fall back to whatever most-recent bank exists.
  const latestReady = banks?.find((b) => b.status === "ready") ?? null;
  const latestBank = latestReady ?? banks?.[0] ?? null;
  const bankReady = latestBank?.status === "ready";
  // A NEWER (retry) bank is extracting on top of the ready one
  const inFlightRetry = !!(
    latestReady &&
    banks &&
    banks.some(
      (b) =>
        (b.status === "extracting" || b.status === "pending") &&
        new Date(b.created_at).getTime() > new Date(latestReady.created_at).getTime(),
    )
  );

  return (
    <div style={{ marginLeft: 14 }}>
      {/* Lens toggle — Theory vs Questions */}
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "4px 6px",
          marginBottom: 4,
        }}
      >
        <button
          className={`sb-lens ${bookLens === "theory" ? "active" : ""}`}
          onClick={() => setBookLens("theory")}
          style={lensBtnStyle(bookLens === "theory")}
          title="Theory — extracted section content"
        >
          📄 Theory
        </button>
        <button
          className={`sb-lens ${bookLens === "questions" ? "active" : ""}`}
          onClick={() => setBookLens("questions")}
          disabled={!latestBank}
          style={lensBtnStyle(bookLens === "questions", !latestBank)}
          title={
            latestBank
              ? inFlightRetry
                ? "Questions — showing last ready bank (a re-extraction is running)"
                : "Questions — per-section folders by kind"
              : "Extract questions first from the Schema page"
          }
        >
          ❓ Questions
          {inFlightRetry && (
            <span style={{ marginLeft: 4, fontSize: "0.6rem", opacity: 0.8 }}>
              ⟳
            </span>
          )}
        </button>
      </div>
      {inFlightRetry && bookLens === "questions" && (
        <div
          style={{
            padding: "4px 8px",
            marginBottom: 4,
            fontSize: "0.66rem",
            color: "var(--text3)",
            background: "var(--bg2)",
            borderRadius: 4,
            fontStyle: "italic",
          }}
        >
          ⟳ Re-extraction running — showing last ready bank
        </div>
      )}

      {bookLens === "theory" ? (
        <>
          <SectionList bookId={bookId} regenId={null} />
          {latestRegen && (
            <>
              <div className="sb-lbl" style={{ marginTop: 6 }}>✨ Regenerated</div>
              <SectionList bookId={bookId} regenId={latestRegen.id} />
            </>
          )}
        </>
      ) : (
        <>
          {!latestBank && (
            <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
              No question bank yet — trigger extraction from the Schema page.
            </div>
          )}
          {latestBank && !bankReady && (
            <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
              {latestBank.status === "failed"
                ? `Extraction failed${latestBank.last_error ? `: ${latestBank.last_error.slice(0, 60)}…` : ""}`
                : "Extracting…"}
            </div>
          )}
          {bankReady && <QuestionsLens bankId={latestBank.id} bookId={bookId} />}
        </>
      )}
    </div>
  );
}

function lensBtnStyle(active: boolean, disabled = false): React.CSSProperties {
  return {
    flex: 1,
    padding: "4px 8px",
    fontSize: "0.7rem",
    fontWeight: 600,
    border: "1px solid var(--b1)",
    borderRadius: 5,
    background: active ? "var(--accent)" : "var(--bg2)",
    color: active ? "#fff" : disabled ? "var(--text3)" : "var(--text2)",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background 0.15s",
  };
}

// -------------------------------------------------------------------
// Theory lens — the original flat section list (unchanged behaviour)
// -------------------------------------------------------------------
function SectionList({ bookId, regenId }: { bookId: string; regenId: string | null }) {
  const { data: sections } = useSections(bookId);
  const { data: book } = useBook(bookId);
  const { data: regen } = useBookRegenerations(bookId);
  const { selectedSectionId, selectedRegenId, selectSection, setView, setRegenId } = useUI();

  const latestRegen = regen?.[0] ?? null;
  const blocksBySection = regenId && latestRegen
    ? (latestRegen.blocks_by_section as Record<string, unknown[]>) ?? {}
    : null;

  const ordered = useMemo(() => {
    if (!sections) return [];
    const schemaIds: string[] = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function walk(arr: any[]) {
      for (const s of arr ?? []) {
        if (s.type !== "excluded") schemaIds.push(s.id as string);
        walk(s.subsections ?? []);
      }
    }
    walk(book?.schema_?.sections ?? []);
    if (schemaIds.length === 0) return sections;
    const map = new Map(sections.map((s) => [s.section_id, s]));
    const result = schemaIds.map((id) => map.get(id)).filter(Boolean) as Section[];
    const inOrder = new Set(schemaIds);
    sections.forEach((s) => { if (!inOrder.has(s.section_id)) result.push(s); });
    return result;
  }, [sections, book]);

  const visible = regenId && blocksBySection
    ? ordered.filter((s) => blocksBySection[s.section_id] !== undefined)
    : ordered;

  if (!visible || visible.length === 0) return null;

  const isRegenFolder = !!regenId;

  return (
    <div>
      {visible.map((s: Section) => {
        const isActive = selectedSectionId === s.id &&
          (isRegenFolder ? selectedRegenId === regenId : selectedRegenId === null);

        return (
          <button
            key={`${regenId ?? "orig"}-${s.id}`}
            className={`tn ${isActive ? "active" : ""}`}
            onClick={() => {
              selectSection(s.id);
              setRegenId(isRegenFolder ? regenId : null);
              setView("reader");
            }}
          >
            <span className="tarr"> </span>
            <span className="tico">
              {isRegenFolder
                ? "✨"
                : s.status === "failed" ? "⚠️" : s.status === "passed" ? "✅" : "📄"}
            </span>
            <span className="tlbl">{s.title}</span>
          </button>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------
// Questions lens — schema sections with per-kind subfolders
// -------------------------------------------------------------------
const KIND_META: Record<QuestionKind, { icon: string; label: string }> = {
  example: { icon: "📘", label: "Examples" },
  problem: { icon: "🧪", label: "Problems" },
  try_it: { icon: "💡", label: "Try It" },
  exercise: { icon: "📝", label: "Exercises" },
  review: { icon: "🔁", label: "Review" },
  mcq: { icon: "🔘", label: "MCQs" },
  other: { icon: "❓", label: "Other" },
};

// Display order inside a section (matches how textbooks usually present them).
const KIND_ORDER: QuestionKind[] = [
  "example", "try_it", "problem", "mcq", "exercise", "review", "other",
];

function QuestionsLens({ bankId, bookId }: { bankId: string; bookId: string }) {
  const { data: detail } = useQuestions(bankId);
  const { data: book } = useBook(bookId);

  // Order sections by book schema so the questions lens mirrors the theory lens.
  const ordered = useMemo(() => {
    if (!detail) return [];
    const order: string[] = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function walk(arr: any[]) {
      for (const s of arr ?? []) {
        if (s.type !== "excluded") order.push(s.id as string);
        walk(s.subsections ?? []);
      }
    }
    walk(book?.schema_?.sections ?? []);
    if (order.length === 0) return detail.sections;
    const map = new Map(detail.sections.map((s) => [s.section_ref, s]));
    const result: QuestionBankSectionGroup[] = [];
    for (const id of order) {
      const grp = map.get(id);
      if (grp) result.push(grp);
    }
    const inOrder = new Set(order);
    detail.sections.forEach((s) => {
      if (!inOrder.has(s.section_ref)) result.push(s);
    });
    return result;
  }, [detail, book]);

  if (!detail) {
    return (
      <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
        Loading questions…
      </div>
    );
  }
  if (ordered.length === 0) {
    return (
      <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
        No questions extracted yet.
      </div>
    );
  }

  return (
    <div>
      {ordered
        .filter((s) => s.questions.length > 0)
        .map((s) => (
          <QuestionSectionNode key={s.section_ref} section={s} />
        ))}
    </div>
  );
}

function QuestionSectionNode({ section }: { section: QuestionBankSectionGroup }) {
  const [open, setOpen] = useState(false);
  const { selectedQuestionSectionRef, selectedKind, selectKind, setView } = useUI();

  const totalCount = section.questions.length;
  const kindsPresent = KIND_ORDER.filter(
    (k) => (section.by_kind[k]?.length ?? 0) > 0,
  );

  return (
    <div>
      <button
        className="tn"
        onClick={() => setOpen((o) => !o)}
      >
        <span className={`tarr ${open ? "o" : ""}`}>▸</span>
        <span className="tico">📖</span>
        <span className="tlbl">
          §{section.section_ref} {section.section_title}
        </span>
        <span className="tcnt">{totalCount}</span>
      </button>
      {open && (
        <div style={{ marginLeft: 12 }}>
          {kindsPresent.map((k) => {
            const meta = KIND_META[k];
            const items = section.by_kind[k] ?? [];
            const isActive =
              selectedQuestionSectionRef === section.section_ref &&
              selectedKind === k;
            return (
              <button
                key={k}
                className={`tn ${isActive ? "active" : ""}`}
                onClick={() => {
                  selectKind(section.section_ref, k);
                  setView("questions");
                }}
                style={{ color: "var(--purple)" }}
                title={`${items.length} ${meta.label.toLowerCase()} in §${section.section_ref}`}
              >
                <span className="tarr"> </span>
                <span className="tico">{meta.icon}</span>
                <span className="tlbl">{meta.label}</span>
                <span className="tcnt">{items.length}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
