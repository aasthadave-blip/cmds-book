import { useState, useMemo } from "react";
import { useBooks, useBook, useSections, useBookRegenerations } from "../api/hooks";
import type { Section } from "../api/client";
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
  const [origOpen, setOrigOpen] = useState(true);
  const [regenOpen, setRegenOpen] = useState(true);

  const hasRegens = regens && regens.length > 0;
  const latestRegen = regens?.[0] ?? null;

  return (
    <div style={{ marginLeft: 14 }}>
      {/* Original folder */}
      <button
        className="tn"
        onClick={() => setOrigOpen((o) => !o)}
        style={{ paddingLeft: 4, color: "var(--text2)", fontWeight: 600, fontSize: "0.72rem" }}
      >
        <span className={`tarr ${origOpen ? "o" : ""}`}>▸</span>
        <span className="tico">📄</span>
        <span className="tlbl">Original</span>
      </button>
      {origOpen && <SectionList bookId={bookId} regenId={null} />}

      {/* Regenerated folder — only if regens exist */}
      {hasRegens && latestRegen && (
        <>
          <button
            className="tn"
            onClick={() => setRegenOpen((o) => !o)}
            style={{ paddingLeft: 4, color: "var(--purple)", fontWeight: 600, fontSize: "0.72rem" }}
          >
            <span className={`tarr ${regenOpen ? "o" : ""}`}>▸</span>
            <span className="tico">✨</span>
            <span className="tlbl">Regenerated</span>
          </button>
          {regenOpen && <SectionList bookId={bookId} regenId={latestRegen.id} />}
        </>
      )}
    </div>
  );
}

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

  // For regen folder, only show sections that have regen blocks
  const visible = regenId && blocksBySection
    ? ordered.filter((s) => blocksBySection[s.section_id] !== undefined)
    : ordered;

  if (!visible || visible.length === 0) return null;

  const isRegenFolder = !!regenId;

  return (
    <div style={{ marginLeft: 14 }}>
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
