import { useState, useMemo } from "react";
import {
  useApprove,
  useBook,
  useCreateQuestionBank,
  useJob,
  usePatchSchema,
  useQuestionBanks,
  useQuestionStructure,
  useReExtractSection,
  useSections,
} from "../api/hooks";
import type {
  BookSchema,
  ExcludedSection,
  QuestionStructureNode,
  SchemaSection,
  Section,
} from "../api/client";
import { api } from "../api/client";
import { useUI } from "../stores/ui";
import { JobProgress } from "../components/JobProgress";
import { WizardRail, type RailStep } from "../components/WizardRail";
import { V3SummaryTable } from "./QuestionsPage";

export function SchemaPage() {
  const { selectedBookId, setView, selectBank } = useUI();
  const { data: book } = useBook(selectedBookId);
  const patch = usePatchSchema();
  const approve = useApprove();
  const createBank = useCreateQuestionBank();
  const { data: banks } = useQuestionBanks(selectedBookId);
  const [schema, setSchema] = useState<BookSchema | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [lens, setLens] = useState<"theory" | "questions">("theory");
  const { data: job } = useJob(jobId);
  const latestBank = banks?.[0] ?? null;

  const rail: RailStep = jobId
    ? job?.status === "succeeded"
      ? "done"
      : "extract"
    : "schema";

  const current = schema ?? book?.schema_ ?? null;

  if (!book) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci a">Schema</span>
          </div>
        </div>
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">🗂️</div>
              <h3>Pick a book first</h3>
            </div>
          </div>
        </div>
      </>
    );
  }

  if (!current) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci">{book.title}</span>
            <span className="bcs">›</span>
            <span className="bci a">Schema</span>
          </div>
        </div>
        <WizardRail active="analyse" />
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">⏳</div>
              <h3>Schema not generated yet</h3>
              <p>
                Current status: <b>{book.status}</b>. Wait for Analyse to
                finish, or upload again.
              </p>
            </div>
          </div>
        </div>
      </>
    );
  }

  async function onApprove() {
    if (!selectedBookId) return;
    if (schema) await patch.mutateAsync({ bookId: selectedBookId, schema });
    const j = await approve.mutateAsync(selectedBookId);
    setJobId(j.job_id);
  }

  async function copySchema() {
    if (!current) return;
    const text = JSON.stringify(current, null, 2);
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        throw new Error("clipboard unavailable");
      }
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        /* ignore */
      }
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function downloadSchema() {
    if (!current) return;
    const safeName = (book?.title || "schema").replace(/[^\w-]+/g, "_");
    const blob = new Blob([JSON.stringify(current, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${safeName}_schema.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function mutateSchema(fn: (sections: SchemaSection[]) => SchemaSection[]) {
    if (!current) return;
    setSchema({ ...current, sections: fn(current.sections) });
  }

  function updateSection(id: string, fn: (s: SchemaSection) => SchemaSection) {
    mutateSchema((arr) => arr.map((s) => walkUpdate(s, id, fn)));
  }

  function deleteSection(id: string) {
    mutateSchema((arr) => walkDelete(arr, id));
  }

  function addRootSection() {
    if (!current) return;
    const nextId = String(
      Math.max(0, ...current.sections.map((s) => parseInt(s.id, 10) || 0)) + 1,
    );
    mutateSchema((arr) => [
      ...arr,
      {
        id: nextId,
        level: 1,
        title: "New section",
        type: "section",
        content_types: ["theory"],
        subsections: [],
      },
    ]);
  }

  const imageBased = book.analyser?.pdf_type === "scanned";
  const wordCount = book.analyser?.estimated_words ?? 0;

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">{book.title}</span>
          <span className="bcs">›</span>
          <span className="bci a">Schema</span>
        </div>
      </div>
      <WizardRail active={rail} />
      <div className="cnt">
        <div className="ci">
          {imageBased && wordCount < 50 && (
            <div
              className="card"
              style={{
                background: "var(--amber-bg)",
                borderColor: "#fde68a",
              }}
            >
              <div className="clbl" style={{ color: "var(--amber)" }}>
                Image-based PDF detected
              </div>
              <p style={{ fontSize: "0.78rem", color: "var(--text2)" }}>
                Very little text was extractable — this usually means the PDF
                is scanned images rather than digital text. Set an{" "}
                <code style={{ fontFamily: "var(--mono)" }}>
                  ANTHROPIC_API_KEY
                </code>{" "}
                in <code style={{ fontFamily: "var(--mono)" }}>.env</code> for
                real OCR, or edit the schema below manually and proceed.
              </p>
            </div>
          )}

          <div className="card">
            <div className="clbl">
              Content schema
              <div
                style={{
                  display: "flex",
                  gap: 2,
                  marginLeft: 10,
                  padding: 2,
                  background: "var(--surface2)",
                  borderRadius: 6,
                }}
              >
                <button
                  type="button"
                  onClick={() => setLens("theory")}
                  className={lens === "theory" ? "btn bp" : "btn bg"}
                  style={{
                    fontSize: "0.66rem",
                    padding: "2px 8px",
                    border: "none",
                    background: lens === "theory" ? "var(--accent)" : "transparent",
                    color: lens === "theory" ? "white" : "var(--text3)",
                  }}
                >
                  📄 Theory
                </button>
                <button
                  type="button"
                  onClick={() => setLens("questions")}
                  className={lens === "questions" ? "btn bp" : "btn bg"}
                  style={{
                    fontSize: "0.66rem",
                    padding: "2px 8px",
                    border: "none",
                    background: lens === "questions" ? "var(--purple)" : "transparent",
                    color: lens === "questions" ? "white" : "var(--text3)",
                  }}
                >
                  ❓ Questions
                </button>
              </div>
              <span
                style={{
                  fontFamily: "var(--mono)",
                  color: "var(--text3)",
                  marginLeft: "auto",
                  textTransform: "none",
                  letterSpacing: 0,
                  fontSize: "0.66rem",
                }}
              >
                {countSections(current.sections)} sections
              </span>
              <button
                type="button"
                onClick={copySchema}
                className="btn bg"
                style={{ fontSize: "0.66rem", padding: "3px 8px" }}
                title="Copy schema JSON"
              >
                {copied ? "✓ Copied" : "⧉ Copy"}
              </button>
              <button
                type="button"
                onClick={downloadSchema}
                className="btn bg"
                style={{ fontSize: "0.66rem", padding: "3px 8px" }}
                title="Download schema as JSON"
              >
                ⬇ Download
              </button>
            </div>
            {lens === "theory" ? (
              <>
                <div className="sbox">
                  <SchemaTreeView
                    sections={current.sections}
                    onRename={(id, title) => updateSection(id, (s) => ({ ...s, title }))}
                    onChangeType={(id, type) =>
                      updateSection(id, (s) => ({ ...s, type }))
                    }
                    onDelete={deleteSection}
                  />
                </div>
                <button
                  type="button"
                  className="btn bg"
                  onClick={addRootSection}
                  style={{ fontSize: "0.7rem", padding: "4px 10px" }}
                >
                  + Add section
                </button>
              </>
            ) : (
              <QuestionSchemaView bookId={selectedBookId} />
            )}
            {(current.excluded_sections?.length ?? 0) > 0 ? (
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--text2)",
                  marginTop: 12,
                  padding: 10,
                  background: "var(--bg2, #f5f5fa)",
                  borderRadius: 4,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 6, color: "var(--text2)" }}>
                  Excluded from extraction (sent to Question Bank):
                </div>
                {current.excluded_sections!.map((ex, i) => (
                  <ExcludedNode key={`${ex.title}-${i}`} node={ex} depth={0} />
                ))}
              </div>
            ) : current.exclusion_summary.length > 0 ? (
              <div
                style={{
                  fontSize: "0.68rem",
                  color: "var(--text3)",
                  marginTop: 8,
                }}
              >
                Excluded from extraction:{" "}
                <b>{current.exclusion_summary.join(", ")}</b>
              </div>
            ) : null}
          </div>

          {!jobId && (
            <div style={{ display: "flex", gap: 9, flexWrap: "wrap" }}>
              <button
                className="btn bp"
                onClick={onApprove}
                disabled={approve.isPending || patch.isPending}
                title="Run theory extraction on all non-excluded sections"
              >
                ✓ Approve & Extract Theory
              </button>
              <button
                className="btn bg"
                disabled={createBank.isPending || !selectedBookId}
                title="OCR all questions/exercises from excluded blocks (independent of theory)"
                onClick={() => {
                  if (!selectedBookId) return;
                  createBank.mutate(selectedBookId, {
                    onSuccess: (res) => {
                      selectBank(res.bank_id);
                      setView("questions");
                    },
                  });
                }}
              >
                {createBank.isPending
                  ? "Starting…"
                  : latestBank
                    ? "❓ Re-run Question Extraction"
                    : "❓ Extract Questions"}
              </button>
              <button className="btn bg" onClick={() => setView("upload")}>
                Back
              </button>
            </div>
          )}

          {jobId && (
            <>
              <JobProgress jobId={jobId} />
              <SectionProgressPanel
                bookId={selectedBookId}
                active={job?.status !== "succeeded" && job?.status !== "failed"}
                schemaOrder={flattenSchemaIds(current.sections)}
              />
              {job?.status === "succeeded" && selectedBookId && (
                <CompletionPanel bookId={selectedBookId} />
              )}
            </>
          )}

          {/* Post-extraction summary — pinned at the bottom of the schema
              page, showing the latest ready bank's totals. Moved here from
              the Questions page so all stats live in one place. */}
          {latestBank?.status === "ready" && latestBank.stats && (
            <div style={{ marginTop: 24 }}>
              <div
                style={{
                  fontSize: "0.62rem",
                  color: "var(--text3)",
                  textTransform: "uppercase",
                  letterSpacing: 0.4,
                  marginBottom: 6,
                }}
              >
                Extraction Summary
              </div>
              <V3SummaryTable stats={latestBank.stats} />
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** Read-only nested view of an excluded_section tree (e.g. "PRACTICE
 *  QUESTIONS: CLASSROOM WING" → "Very Short / Short / Essay"), shown
 *  beneath the theory schema editor so the user can verify what the
 *  Question Bank will pick up. */
function ExcludedNode({ node, depth }: { node: ExcludedSection; depth: number }) {
  const eqc = node.expected_question_count ?? 0;
  const indent = depth * 14;
  return (
    <div style={{ marginLeft: indent, padding: "2px 0" }}>
      <span style={{ fontWeight: depth === 0 ? 600 : 400 }}>
        {depth === 0 ? "📂 " : "└─ "}
        {node.title}
      </span>
      {(node.page_start || node.page_end) && (
        <span style={{ color: "var(--text3)", marginLeft: 6, fontSize: "0.66rem" }}>
          · p.{node.page_start ?? "?"}–{node.page_end ?? "?"}
        </span>
      )}
      {eqc > 0 && (
        <span style={{ color: "var(--accent, #5b6cff)", marginLeft: 6, fontSize: "0.66rem", fontWeight: 600 }}>
          · {eqc} Q
        </span>
      )}
      {(node.subsections?.length ?? 0) > 0 && (
        <div>
          {node.subsections!.map((c, i) => (
            <ExcludedNode key={`${c.title}-${i}`} node={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function SchemaTreeView({
  sections,
  onRename,
  onChangeType,
  onDelete,
  depth = 0,
}: {
  sections: SchemaSection[];
  onRename: (id: string, title: string) => void;
  onChangeType: (id: string, type: SchemaSection["type"]) => void;
  onDelete: (id: string) => void;
  depth?: number;
}) {
  return (
    <>
      {sections.map((s) => (
        <div key={s.id}>
          <div
            className={
              s.type === "excluded"
                ? "sex"
                : depth === 0
                  ? "sch"
                  : depth === 1
                    ? "sse"
                    : "ssu"
            }
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <input
              value={s.title}
              onChange={(e) => onRename(s.id, e.target.value)}
              style={{
                flex: 1,
                background: "transparent",
                border: "none",
                outline: "none",
                fontFamily: "inherit",
                color: "inherit",
                fontSize: "inherit",
                padding: "1px 4px",
                borderRadius: 3,
              }}
              onFocus={(e) => {
                e.currentTarget.style.background = "var(--surface)";
                e.currentTarget.style.outline = "1px solid var(--accent)";
              }}
              onBlur={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.outline = "none";
              }}
            />
            <select
              value={s.type}
              onChange={(e) =>
                onChangeType(s.id, e.target.value as SchemaSection["type"])
              }
              style={{
                fontSize: "0.62rem",
                padding: "1px 4px",
                fontFamily: "var(--sans)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--surface)",
                color: "var(--text3)",
              }}
            >
              <option value="chapter">chapter</option>
              <option value="section">section</option>
              <option value="subsection">subsection</option>
              <option value="excluded">excluded</option>
            </select>
            <button
              type="button"
              onClick={() => onDelete(s.id)}
              title="Delete section"
              style={{
                border: "none",
                background: "transparent",
                color: "var(--text3)",
                cursor: "pointer",
                fontSize: "0.85rem",
                padding: "0 2px",
              }}
            >
              ✕
            </button>
          </div>
          {s.subsections.length > 0 && (
            <SchemaTreeView
              sections={s.subsections}
              onRename={onRename}
              onChangeType={onChangeType}
              onDelete={onDelete}
              depth={depth + 1}
            />
          )}
        </div>
      ))}
    </>
  );
}

function QuestionSchemaView({ bookId }: { bookId: string | null }) {
  const { data, isLoading } = useQuestionStructure(bookId);

  if (!bookId) return null;
  if (isLoading) {
    return (
      <div className="sbox" style={{ color: "var(--text3)", fontSize: "0.72rem", padding: 10 }}>
        Loading question structure…
      </div>
    );
  }
  if (!data || (data.sections.length === 0 && data.unlinked_excluded.length === 0)) {
    return (
      <div className="sbox" style={{ color: "var(--text3)", fontSize: "0.72rem", padding: 10 }}>
        No excluded blocks detected — the analyser didn't find any question sections.
      </div>
    );
  }
  return (
    <div className="sbox">
      <div style={{ fontSize: "0.68rem", color: "var(--text3)", marginBottom: 8 }}>
        {data.summary.total_sections} sections · {data.summary.linked_excluded} linked question blocks
        {data.summary.unlinked_excluded > 0 && (
          <span style={{ color: "var(--warn, #c80)" }}>
            {" "}· {data.summary.unlinked_excluded} unlinked
          </span>
        )}
      </div>

      {data.sections.map((node) => (
        <QSchemaNode key={node.id} node={node} depth={0} />
      ))}
      {data.unlinked_excluded.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div
            style={{
              fontSize: "0.65rem",
              color: "var(--warn, #c80)",
              fontWeight: 600,
              marginBottom: 4,
            }}
          >
            Unlinked excluded blocks
          </div>
          {data.unlinked_excluded.map((ex) => (
            <div
              key={`u-${ex.excluded_index}`}
              style={{
                paddingLeft: 10,
                fontSize: "0.72rem",
                color: "var(--purple)",
              }}
            >
              ❓ {ex.title || `#${ex.excluded_index}`}
              <span style={{ color: "var(--text3)", marginLeft: 6, fontSize: "0.66rem" }}>
                p.{ex.page_start ?? "?"}–{ex.page_end ?? "?"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function QSchemaNode({ node, depth }: { node: QuestionStructureNode; depth: number }) {
  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          paddingLeft: depth * 12,
          fontSize: depth === 0 ? "0.78rem" : "0.74rem",
          fontWeight: depth === 0 ? 600 : 400,
          color: depth === 0 ? "var(--text1)" : "var(--text2)",
          padding: "2px 4px",
        }}
      >
        <span>{node.type === "chapter" ? "📖" : "📄"}</span>
        <span>§{node.id} {node.title}</span>
        {node.question_count > 0 && (
          <span
            style={{
              fontSize: "0.62rem",
              color: "var(--text3)",
              fontFamily: "var(--mono)",
              marginLeft: "auto",
            }}
          >
            {node.question_count} Q
          </span>
        )}
      </div>
      {node.excluded_blocks.map((ex) => {
        const conf = Math.round(ex.link_confidence * 100);
        return (
          <div
            key={`${node.id}-ex-${ex.excluded_index}`}
            style={{
              paddingLeft: (depth + 1) * 12,
              fontSize: "0.72rem",
              color: "var(--purple)",
              padding: "1px 4px",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
            title={`${ex.link_method} · ${conf}%${ex.reason ? ` · ${ex.reason}` : ""}`}
          >
            <span>❓</span>
            <span>{ex.title || `Questions #${ex.excluded_index}`}</span>
            <span style={{ color: "var(--text3)", fontSize: "0.62rem", marginLeft: "auto" }}>
              p.{ex.page_start ?? "?"}–{ex.page_end ?? "?"} · {conf}%
            </span>
          </div>
        );
      })}
      {node.subsections.map((child) => (
        <QSchemaNode key={child.id} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}

function walkUpdate(
  section: SchemaSection,
  targetId: string,
  fn: (s: SchemaSection) => SchemaSection,
): SchemaSection {
  if (section.id === targetId) return fn(section);
  return {
    ...section,
    subsections: section.subsections.map((c) => walkUpdate(c, targetId, fn)),
  };
}

function walkDelete(sections: SchemaSection[], id: string): SchemaSection[] {
  return sections
    .filter((s) => s.id !== id)
    .map((s) => ({ ...s, subsections: walkDelete(s.subsections, id) }));
}

function countSections(sections: SchemaSection[]): number {
  let n = 0;
  const walk = (arr: SchemaSection[]) => {
    for (const s of arr) {
      if (s.type !== "excluded") n += 1;
      walk(s.subsections);
    }
  };
  walk(sections);
  return n;
}

function flattenSchemaIds(sections: SchemaSection[]): string[] {
  const ids: string[] = [];
  function walk(arr: SchemaSection[]) {
    for (const s of arr) {
      if (s.type !== "excluded") ids.push(s.id);
      walk(s.subsections);
    }
  }
  walk(sections);
  return ids;
}

function SectionProgressPanel({
  bookId,
  active,
  schemaOrder,
}: {
  bookId: string | null;
  active: boolean;
  schemaOrder: string[];
}) {
  const { data: sections } = useSections(bookId, { pollMs: active ? 1200 : undefined });
  const reExtract = useReExtractSection();

  const ordered = useMemo(() => {
    if (!sections) return [];
    if (schemaOrder.length === 0) return sections;
    const map = new Map(sections.map((s) => [s.section_id, s]));
    const result = schemaOrder.map((id) => map.get(id)).filter(Boolean) as typeof sections;
    // append any sections not in schema order at the end
    const inOrder = new Set(schemaOrder);
    sections.forEach((s) => { if (!inOrder.has(s.section_id)) result.push(s); });
    return result;
  }, [sections, schemaOrder]);

  if (!ordered || ordered.length === 0) return null;
  const passed = ordered.filter((s) => s.status === "passed").length;
  const failed = ordered.filter((s) => s.status === "failed").length;
  const total = ordered.length;

  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="clbl">
        Section extraction
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--mono)",
            color: "var(--text3)",
            textTransform: "none",
            letterSpacing: 0,
            fontSize: "0.66rem",
          }}
        >
          {passed + failed}/{total} processed · {passed} passed · {failed} failed
        </span>
      </div>
      <div className="srows">
        {ordered.map((s) => (
          <SectionRow
            key={s.id}
            section={s}
            onRetry={
              s.status === "failed"
                ? () => reExtract.mutate(s.id)
                : undefined
            }
          />
        ))}

      </div>
    </div>
  );
}

function SectionRow({
  section,
  onRetry,
}: {
  section: Section;
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const qcLocal = section.qc_local as {
    pass?: boolean;
    score?: number;
    failures?: string[];
  } | null;
  const qcLlm = section.qc_llm as {
    pass?: boolean;
    verdict?: string;
    severity?: string;
  } | null;

  const score = qcLocal?.score != null ? Math.round(qcLocal.score * 100) : null;
  const failures = qcLocal?.failures ?? [];
  const hasDetail = failures.length > 0 || !!qcLlm?.verdict;

  const icon =
    section.status === "passed"
      ? "✅"
      : section.status === "failed"
        ? "⚠️"
        : section.status === "extracting"
          ? "⏳"
          : "·";

  const cls =
    section.status === "passed"
      ? "srow done"
      : section.status === "failed"
        ? "srow err"
        : "srow";

  return (
    <div>
      <div
        className={cls}
        onClick={() => hasDetail && setExpanded((v) => !v)}
        style={hasDetail ? { cursor: "pointer" } : undefined}
      >
        <span className="si">{icon}</span>
        <span style={{ flex: 1, color: "var(--text2)", fontSize: "0.75rem" }}>
          {section.title}
        </span>
        <span className="sw">
          {section.blocks.length > 0 ? `${section.blocks.length} blocks` : section.status === "pending" ? "pending" : "—"}
        </span>
        {section.status === "failed" && onRetry && (
          <button
            className="btn bg"
            style={{ padding: "1px 7px", fontSize: "0.62rem" }}
            onClick={(e) => {
              e.stopPropagation();
              onRetry();
            }}
          >
            Retry
          </button>
        )}
        {hasDetail && (
          <span style={{ color: "var(--text3)", fontSize: "0.65rem" }}>
            {expanded ? "▲" : "▼"}
          </span>
        )}
      </div>
      {expanded && hasDetail && (
        <div
          style={{
            padding: "7px 14px 9px",
            background: section.status === "failed" ? "#fef2f2" : "var(--surface2)",
            borderRadius: "0 0 7px 7px",
            fontSize: "0.7rem",
            marginTop: -4,
            borderTop: "none",
          }}
        >
          {failures.length > 0 && (
            <>
              <div
                style={{
                  fontWeight: 600,
                  color: "var(--red)",
                  marginBottom: 3,
                }}
              >
                QC failures:
              </div>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: 16,
                  lineHeight: 1.6,
                  color: "var(--text2)",
                }}
              >
                {failures.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </>
          )}
          {qcLlm?.verdict && (
            <div
              style={{
                marginTop: failures.length > 0 ? 6 : 0,
                color: "var(--text2)",
                fontStyle: "italic",
              }}
            >
              <b>LLM audit:</b> {qcLlm.verdict}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CompletionPanel({ bookId }: { bookId: string }) {
  const { data: sections } = useSections(bookId);
  const { setView, selectSection } = useUI();
  if (!sections) return null;

  const passed = sections.filter((s) => s.status === "passed").length;
  const failed = sections.filter((s) => s.status === "failed").length;
  const total = sections.length;

  return (
    <div
      className="card"
      style={{
        background: "var(--green-bg)",
        borderColor: "#bbf7d0",
        marginTop: 10,
      }}
    >
      <div className="clbl" style={{ color: "var(--green)" }}>
        ✓ Extraction complete
        <span
          style={{
            marginLeft: "auto",
            fontFamily: "var(--mono)",
            textTransform: "none",
            letterSpacing: 0,
            fontSize: "0.66rem",
            color: "var(--green)",
          }}
        >
          {passed}/{total} passed{failed > 0 && ` · ${failed} failed`}
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
        {/* Primary: go read the extracted content */}
        <button
          className="btn bp"
          style={{ width: "100%", justifyContent: "center" }}
          onClick={() => {
            if (sections[0]) selectSection(sections[0].id);
            setView("reader");
          }}
        >
          Open extracted content →
        </button>

        {/* Export options */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn bg"
            style={{ flex: 1 }}
            onClick={() => api.exportMarkdown(bookId)}
            title="Download all extracted sections as Markdown"
          >
            ⬇ Export as Markdown
          </button>
          <button
            className="btn bg"
            style={{ flex: 1 }}
            onClick={() => api.exportJson(bookId)}
            title="Download all extracted sections as JSON"
          >
            ⬇ Export as JSON
          </button>
        </div>

        <button
          className="btn bg"
          style={{ fontSize: "0.72rem" }}
          onClick={() => setView("library")}
        >
          View in Library
        </button>
      </div>
    </div>
  );
}
