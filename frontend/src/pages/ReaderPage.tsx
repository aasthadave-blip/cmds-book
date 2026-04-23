import { useEffect } from "react";
import {
  useBook,
  useReExtractSection,
  useReExtractBook,
  useSection,
  useSections,
  useRegeneration,
} from "../api/hooks";
import { api, type Block } from "../api/client";
import { BlockRenderer } from "../components/BlockRenderer";
import { useUI } from "../stores/ui";

export function ReaderPage() {
  const { selectedBookId, selectedSectionId, selectedRegenId, selectSection, setView } = useUI();
  const { data: book } = useBook(selectedBookId);
  const { data: sections } = useSections(selectedBookId);
  const { data: section } = useSection(selectedSectionId);
  const { data: regen } = useRegeneration(selectedRegenId);
  const reExtract = useReExtractSection();
  const reExtractBook = useReExtractBook();

  const isRegenView = !!selectedRegenId;

  // Resolve blocks: regen folder shows regenerated blocks, original shows section.blocks
  const regenBlocksBySection = regen?.blocks_by_section as Record<string, Block[]> | undefined;
  const regenBlocks: Block[] | null = isRegenView && section && regenBlocksBySection
    ? (regenBlocksBySection[section.section_id] ?? null)
    : null;
  const displayBlocks: Block[] = isRegenView && regenBlocks ? regenBlocks : (section?.blocks ?? []);

  useEffect(() => {
    if (!selectedSectionId && sections && sections.length > 0) {
      selectSection(sections[0].id);
    }
  }, [sections, selectedSectionId, selectSection]);

  if (!book) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci a">Reader</span>
          </div>
        </div>
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">📖</div>
              <h3>Pick a book from the sidebar</h3>
            </div>
          </div>
        </div>
      </>
    );
  }

  const qcLocal =
    (section?.qc_local as {
      pass?: boolean;
      score?: number;
      failures?: string[];
    } | null) ?? null;
  const qcLlm =
    (section?.qc_llm as {
      pass?: boolean;
      verdict?: string;
      severity?: string;
      issues?: Record<string, unknown>;
    } | null) ?? null;
  const qcScore =
    qcLocal?.score != null ? Math.round(qcLocal.score * 100) : null;
  const qcBadge = section
    ? qcLocal?.pass === true || section.status === "passed"
      ? { cls: "cvc ok", label: qcScore !== null ? `QC pass · ${qcScore}%` : "QC pass" }
      : section.status === "failed"
        ? { cls: "cvc fail", label: qcScore !== null ? `QC fail · ${qcScore}%` : "QC fail" }
        : { cls: "cvc", label: section.status }
    : null;

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">{book.title}</span>
          <span className="bcs">›</span>
          {isRegenView && <span className="bci" style={{ color: "var(--purple)" }}>✨ Regenerated</span>}
          {isRegenView && <span className="bcs">›</span>}
          <span className="bci a">
            {section ? `${section.section_id} ${section.title}` : "Reader"}
          </span>
        </div>
        {selectedBookId && (
          <>
            <button className="btn bg" onClick={() => api.exportMarkdown(selectedBookId, selectedRegenId)} title={isRegenView ? "Export regenerated sections as Markdown" : "Export original sections as Markdown"}>
              ⬇ .md
            </button>
            <button className="btn bg" onClick={() => api.exportJson(selectedBookId, selectedRegenId)} title={isRegenView ? "Export regenerated sections as JSON" : "Export original sections as JSON"}>
              ⬇ .json
            </button>
            <button className="btn bg" onClick={() => api.exportDocx(selectedBookId, selectedRegenId)} title={isRegenView ? "Export regenerated sections as Word (.docx) — equations preserved as native Word math" : "Export original sections as Word (.docx) — equations preserved as native Word math"}>
              ⬇ .docx
            </button>
          </>
        )}
        {selectedBookId && !isRegenView && (
          <button
            className="btn bg"
            onClick={() => reExtractBook.mutate(selectedBookId)}
            disabled={reExtractBook.isPending}
            title="Re-run extraction on all sections with the latest logic"
          >
            {reExtractBook.isPending ? "Re-extracting..." : "↺ Re-extract"}
          </button>
        )}
        <button className="btn bg" onClick={() => setView("regen")}>
          ✨ Regenerate
        </button>
      </div>

      <div className="cnt">
        <div className="ci">
          {!section && (
            <div className="empty">
              <div className="empty-i">📄</div>
              <h3>Select a section</h3>
              <p>Use the sidebar tree to navigate extracted sections.</p>
            </div>
          )}

          {section && (
            <>
              <div className="cvh">
                <div className="cvt">{section.title}</div>
                <div className="cvchips">
                  {isRegenView
                    ? <span className="cvc regen">✨ regenerated</span>
                    : <span className="cvc orig">original</span>
                  }
                  {!isRegenView && qcBadge && <span className={qcBadge.cls}>{qcBadge.label}</span>}
                  {!isRegenView && <span className="cvc">attempts: {String(section.attempts)}</span>}
                  {!isRegenView && section.status === "failed" && (
                    <button
                      onClick={() => reExtract.mutate(section.id)}
                      disabled={reExtract.isPending}
                      className="btn bg"
                      style={{ padding: "2px 8px", fontSize: "0.64rem" }}
                    >
                      Re-extract
                    </button>
                  )}
                </div>
              </div>

              {displayBlocks.length === 0 ? (
                <div className="empty">
                  <div className="empty-i">⏳</div>
                  <h3>{isRegenView ? "No regenerated content" : "No blocks yet"}</h3>
                  <p>
                    {isRegenView
                      ? "This section has no regenerated blocks."
                      : "Extraction hasn't run on this section yet."}
                  </p>
                </div>
              ) : (
                <BlockRenderer blocks={displayBlocks} />
              )}

              {!isRegenView && qcLocal?.failures && qcLocal.failures.length > 0 && (
                <div className="card" style={{ marginTop: 24 }}>
                  <div className="clbl" style={{ color: "var(--red)" }}>
                    Local QC failures
                    {qcScore !== null && (
                      <span className="cvc fail" style={{ marginLeft: 8 }}>{qcScore}%</span>
                    )}
                  </div>
                  <ul style={{ fontSize: "0.76rem", color: "var(--text2)", paddingLeft: 16, lineHeight: 1.6 }}>
                    {qcLocal.failures.map((f, i) => <li key={i}>{f}</li>)}
                  </ul>
                </div>
              )}
              {!isRegenView && qcLlm && (
                <div className="card" style={{ marginTop: 12 }}>
                  <div className="clbl" style={{ color: qcLlm.pass === false ? "var(--red)" : "var(--green)" }}>
                    LLM audit
                    <span className={`cvc ${qcLlm.pass === false ? "fail" : "ok"}`} style={{ marginLeft: 8 }}>
                      {qcLlm.pass === false ? `fail · ${qcLlm.severity ?? "?"}` : "pass"}
                    </span>
                  </div>
                  {qcLlm.verdict && (
                    <p style={{ fontSize: "0.76rem", color: "var(--text2)", margin: "6px 0 0", fontStyle: "italic" }}>
                      {qcLlm.verdict}
                    </p>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
