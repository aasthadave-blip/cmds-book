import { useEffect, useMemo, useState } from "react";
import {
  useBook,
  useBulkDeleteRegenQuestions,
  useCreateQuestionBank,
  useDeleteQuestionBank,
  useDeleteQuestionRegeneration,
  useJob,
  useQuestionBank,
  useQuestionBanks,
  useQuestionRegen,
  useQuestionRegenerations,
  useQuestions,
  useQuestionStructure,
  useReExtractBlock,
  useRegenQuestions,
  useRetrySection,
  useSaveQuestionRegeneration,
  useStartQuestionRegeneration,
} from "../api/hooks";
import {
  api,
  type ExtractionSectionStats,
  type Question,
  type QuestionBank,
  type QuestionRegeneration,
  type UUID,
} from "../api/client";
import { useUI } from "../stores/ui";

export function QuestionsPage() {
  const {
    selectedBookId,
    selectedBankId,
    selectBank,
    selectedQuestionRegenId,
    selectQuestionRegen,
    selectedQuestionSectionRef,
    selectedExcludedBlockRef,
    selectedKind,
  } = useUI();
  const { data: book } = useBook(selectedBookId);
  const { data: banks } = useQuestionBanks(selectedBookId);
  const anyExtracting = !!banks?.some(
    (b) => b.status === "pending" || b.status === "extracting",
  );
  const { data: structure } = useQuestionStructure(selectedBookId, {
    pollWhileExtracting: anyExtracting,
  });

  // Auto-select latest bank if none picked
  useEffect(() => {
    if (!selectedBankId && banks && banks.length > 0) {
      selectBank(banks[0].id);
    }
  }, [banks, selectedBankId, selectBank]);

  const createBank = useCreateQuestionBank();
  const deleteBank = useDeleteQuestionBank();

  const { data: bank } = useQuestionBank(selectedBankId);
  const { data: detail, refetch: refetchDetail } = useQuestions(selectedBankId, {
    bankStatus: bank?.status,
  });

  // Refetch grouped questions on every bank change while extracting, and once
  // more when it flips to ready, so folders appear block-by-block.
  useEffect(() => {
    if (bank?.status === "extracting" || bank?.status === "ready") {
      void refetchDetail();
    }
  }, [bank?.status, bank?.stats?.total_extracted, refetchDetail]);

  if (!book) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci a">Questions</span>
          </div>
        </div>
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">❓</div>
              <h3>Pick a book from the sidebar</h3>
            </div>
          </div>
        </div>
      </>
    );
  }

  const isExtracting = bank?.status === "pending" || bank?.status === "extracting";
  const isReady = bank?.status === "ready";
  const isFailed = bank?.status === "failed";
  const hasBank = !!bank;
  const canCreate = !!book.schema_;

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">{book.title}</span>
          <span className="bcs">›</span>
          <span className="bci a">Questions</span>
        </div>
        {bank?.stats && (
          <span
            className="btn bg"
            title={`${bank.stats.total_extracted} extracted of ${bank.stats.total_identified} identified${
              bank.stats.missed ? ` · ${bank.stats.missed} missed` : ""
            }`}
            style={{
              cursor: "default",
              color: bank.stats.missed ? "var(--warn, #c80)" : "var(--text1)",
            }}
          >
            {bank.stats.total_extracted}/{bank.stats.total_identified}
            {bank.stats.missed ? ` · ${bank.stats.missed} missed` : ""}
          </span>
        )}
        {isReady && selectedBankId && (
          <>
            <button className="btn bg" onClick={() => api.exportQuestionsJson(selectedBankId)}>
              ⬇ .json
            </button>
            <button className="btn bg" onClick={() => api.exportQuestionsMarkdown(selectedBankId)}>
              ⬇ .md
            </button>
            <button className="btn bg" onClick={() => api.exportQuestionsDocx(selectedBankId)}>
              ⬇ .docx
            </button>
            <button
              className="btn bg"
              onClick={() => {
                if (!selectedBookId || !selectedBankId) return;
                if (!confirm("Delete this question bank and re-extract?")) return;
                deleteBank.mutate(
                  { bankId: selectedBankId, bookId: selectedBookId },
                  {
                    onSuccess: () => {
                      selectBank(null);
                      createBank.mutate(selectedBookId, {
                        onSuccess: (res) => selectBank(res.bank_id),
                      });
                    },
                  },
                );
              }}
            >
              ↺ Re-extract
            </button>
          </>
        )}
      </div>

      <div className="cnt">
        <div className="ci">
          {structure && (selectedQuestionSectionRef || selectedExcludedBlockRef) && (
            <SelectedNodeBanner
              structure={structure}
              sectionRef={selectedQuestionSectionRef}
              excludedRef={selectedExcludedBlockRef}
            />
          )}
          {!hasBank && (
            <div className="empty">
              <div className="empty-i">❓</div>
              <h3>Extract questions from this book</h3>
              <p>
                Scans every section of the approved schema and OCRs every exercise,
                problem, or Q&amp;A item verbatim via Gemini.
              </p>
              {!canCreate && (
                <p style={{ color: "var(--red)", marginTop: 12 }}>
                  Run Analyse &amp; approve the schema first.
                </p>
              )}
              <button
                className="btn primary"
                style={{ marginTop: 20 }}
                disabled={!canCreate || createBank.isPending || !selectedBookId}
                onClick={() => {
                  if (!selectedBookId) return;
                  createBank.mutate(selectedBookId, {
                    onSuccess: (res) => selectBank(res.bank_id),
                  });
                }}
              >
                {createBank.isPending ? "Starting..." : "Extract Questions"}
              </button>
            </div>
          )}

          {isExtracting && bank && (
            <>
              <BankExtractionProgress
                status={bank.status}
                activeJobId={bank.active_job_id ?? null}
                fallback={bank.active_job ?? null}
                stats={bank.stats ?? null}
              />
              {detail && detail.total_questions > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div
                    style={{
                      fontSize: "0.7rem",
                      color: "var(--text3)",
                      marginBottom: 10,
                      fontStyle: "italic",
                    }}
                  >
                    Showing {detail.total_questions} question
                    {detail.total_questions === 1 ? "" : "s"} extracted so far —
                    more will appear as each block completes.
                  </div>
                  <QuestionList
                    detail={detail}
                    bankId={selectedBankId}
                    excludedBlockRef={selectedExcludedBlockRef}
                    scopedSectionRef={selectedQuestionSectionRef}
                    scopedKind={selectedKind}
                  />
                </div>
              )}
            </>
          )}

          {isFailed && bank && (
            <div className="empty">
              <div className="empty-i">⚠️</div>
              <h3>Extraction failed</h3>
              <p>Try again — the previous bank will be replaced.</p>
              <button
                className="btn primary"
                style={{ marginTop: 20 }}
                onClick={() => {
                  if (!selectedBookId) return;
                  deleteBank.mutate(
                    { bankId: bank.id, bookId: selectedBookId },
                    {
                      onSuccess: () => {
                        selectBank(null);
                        createBank.mutate(selectedBookId, {
                          onSuccess: (res) => selectBank(res.bank_id),
                        });
                      },
                    },
                  );
                }}
              >
                Retry
              </button>
            </div>
          )}

          {isReady && detail && selectedBankId && selectedBookId && (
            <RegenRunBar
              bankId={selectedBankId}
              bookId={selectedBookId}
              detail={detail}
              activeRegenId={selectedQuestionRegenId}
              onSelectRegen={(id) => selectQuestionRegen(id)}
            />
          )}

          {isReady && selectedQuestionRegenId && selectedBookId && (
            <RegenView
              regenId={selectedQuestionRegenId}
              bookId={selectedBookId}
              originalDetail={detail ?? null}
            />
          )}

          {isReady && bank?.stats && <V3StatsStrip stats={bank.stats} />}

          {isReady && detail && !selectedQuestionRegenId && (
            <QuestionList
              detail={detail}
              bankId={selectedBankId}
              excludedBlockRef={selectedExcludedBlockRef}
              scopedSectionRef={selectedQuestionSectionRef}
              scopedKind={selectedKind}
            />
          )}
        </div>
      </div>
    </>
  );
}

function SelectedNodeBanner({
  structure,
  sectionRef,
  excludedRef,
}: {
  structure: NonNullable<ReturnType<typeof useQuestionStructure>["data"]>;
  sectionRef: string | null;
  excludedRef: string | null;
}) {
  // Find the excluded block (if selected), else the section node
  const found = useMemo(() => {
    type Ex = (typeof structure.unlinked_excluded)[number];
    type Node = (typeof structure.sections)[number];

    if (excludedRef) {
      const walk = (arr: Node[]): Ex | null => {
        for (const n of arr) {
          for (const ex of n.excluded_blocks) {
            if (ex.excluded_block_ref === excludedRef) return ex;
          }
          const found = walk(n.subsections);
          if (found) return found;
        }
        return null;
      };
      const hit = walk(structure.sections);
      if (hit) return { kind: "excluded" as const, ex: hit };
      const unlinked = structure.unlinked_excluded.find((e) => e.excluded_block_ref === excludedRef);
      if (unlinked) return { kind: "excluded" as const, ex: unlinked };
    }
    if (sectionRef) {
      const walk = (arr: Node[]): Node | null => {
        for (const n of arr) {
          if (n.id === sectionRef) return n;
          const found = walk(n.subsections);
          if (found) return found;
        }
        return null;
      };
      const hit = walk(structure.sections);
      if (hit) return { kind: "section" as const, node: hit };
    }
    return null;
  }, [structure, sectionRef, excludedRef]);

  if (!found) return null;

  if (found.kind === "excluded") {
    const ex = found.ex;
    const conf = Math.round(ex.link_confidence * 100);
    return (
      <div
        className="card"
        style={{ marginBottom: 12, borderLeft: "3px solid var(--purple)" }}
      >
        <div style={{ fontSize: "0.64rem", color: "var(--text3)", marginBottom: 4 }}>
          EXCLUDED BLOCK
        </div>
        <div style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 4 }}>
          {ex.title || `#${ex.excluded_index}`}
        </div>
        <div style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
          {ex.page_start ? `p. ${ex.page_start}` : ""}
          {ex.page_end && ex.page_end !== ex.page_start ? `–${ex.page_end}` : ""}
          {" · "}linked via <code>{ex.link_method}</code> ({conf}%)
          {ex.section_ref ? <> → <code>{ex.section_ref}</code></> : " · unlinked"}
        </div>
        {ex.reason && (
          <div style={{ fontSize: "0.7rem", color: "var(--text3)", marginTop: 4, fontStyle: "italic" }}>
            {ex.reason}
          </div>
        )}
      </div>
    );
  }

  const node = found.node;
  return (
    <div className="card" style={{ marginBottom: 12, borderLeft: "3px solid var(--accent)" }}>
      <div style={{ fontSize: "0.64rem", color: "var(--text3)", marginBottom: 4 }}>
        SECTION · {node.id}
      </div>
      <div style={{ fontSize: "0.82rem", fontWeight: 600, marginBottom: 4 }}>
        {node.title}
      </div>
      <div style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
        {node.page_start ? `p. ${node.page_start}` : ""}
        {node.page_end && node.page_end !== node.page_start ? `–${node.page_end}` : ""}
        {" · "}{node.excluded_blocks.length} excluded block
        {node.excluded_blocks.length === 1 ? "" : "s"}
        {node.question_count > 0 ? ` · ${node.question_count} questions extracted` : ""}
      </div>
    </div>
  );
}

function V3StatsStrip({ stats }: { stats: NonNullable<QuestionBank["stats"]> }) {
  if (!stats.totals) return null; // legacy v2 banks → no strip
  const { complete, partial, empty, failed, expected_total, extracted_total } = stats.totals;
  const dropped = stats.dedup?.dropped ?? 0;
  const pill = (label: string, n: number, color: string) => (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 10,
        fontSize: "0.68rem",
        background: "var(--bg2, #f5f5fa)",
        color,
      }}
    >
      <span style={{ fontWeight: 600 }}>{n}</span> {label}
    </span>
  );
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
        alignItems: "center",
        marginBottom: 12,
        padding: "6px 10px",
        background: "var(--bg2, #f5f5fa)",
        borderRadius: 6,
        fontSize: "0.7rem",
      }}
    >
      <span style={{ color: "var(--text3)", marginRight: 4 }}>
        {extracted_total}/{expected_total || "?"} extracted ·
      </span>
      {pill("complete", complete, "var(--green, #2a9d5e)")}
      {pill("partial", partial, "var(--warn, #c80)")}
      {pill("empty", empty, "var(--text3)")}
      {pill("failed", failed, "var(--red, #d33)")}
      {dropped > 0 && (
        <span style={{ color: "var(--text3)", marginLeft: 6, fontStyle: "italic" }}>
          · deduped {dropped}
        </span>
      )}
    </div>
  );
}

function BankExtractionProgress({
  status,
  activeJobId,
  fallback,
  stats,
}: {
  status: string;
  activeJobId: UUID | null;
  fallback:
    | { id: UUID; status: string; progress: number | null; message: string | null }
    | null;
  stats:
    | {
        total_identified: number;
        total_extracted: number;
        missed: number;
        blocks: {
          excluded_block_index: number;
          title: string;
          page_start: number | null;
          page_end: number | null;
          identified: number;
          extracted: number;
          status: string;
        }[];
      }
    | null;
}) {
  // Poll the live extraction job every second so the heartbeat message
  // ("Extracting Exercise 1.1 — 45s elapsed") shows up in real time.
  const { data: job } = useJob(activeJobId, { pollMs: 1000 });
  const live = job ?? fallback;

  const progress = Math.max(0, Math.min(100, live?.progress ?? 0));
  const message =
    live?.message ||
    (status === "pending"
      ? "Queued…"
      : "Scanning sections — this can take a few minutes for a full book");

  const blocks = stats?.blocks ?? [];
  const done = blocks.filter((b) => b.status !== "failed").length;
  const totalBlocks = blocks.length;
  const lastFew = blocks.slice(-5).reverse();

  return (
    <div className="card">
      <div className="clbl">
        Extracting questions{" "}
        <span style={{ color: "var(--text3)", fontWeight: 500 }}>— {status}</span>
        {stats && (
          <span style={{ color: "var(--text3)", fontWeight: 500, marginLeft: 8 }}>
            · {stats.total_extracted}/{stats.total_identified} questions
            {totalBlocks > 0 ? ` · ${done}/${totalBlocks} blocks` : ""}
          </span>
        )}
      </div>
      <div className="prog">
        <div
          className="progb"
          style={{
            width: `${progress || 2}%`,
            minWidth: progress > 0 ? undefined : 40,
            background: progress > 0
              ? "var(--accent)"
              : "linear-gradient(90deg, var(--accent) 0%, var(--accent) 50%, transparent 100%)",
            animation: progress > 0 ? undefined : "progressShimmer 1.4s linear infinite",
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <div className="progr" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>{message}</span>
        <span style={{ color: "var(--text3)", fontFamily: "var(--mono)", fontSize: "0.7rem" }}>
          {progress}%
        </span>
      </div>
      {lastFew.length > 0 && (
        <div style={{ marginTop: 12, fontSize: "0.7rem", color: "var(--text3)" }}>
          <div style={{ marginBottom: 4, fontWeight: 600 }}>Recent blocks</div>
          {lastFew.map((b) => {
            const ok = b.status === "ok";
            const empty = b.status === "empty";
            const color = ok
              ? "var(--green, #2a9d5e)"
              : empty
              ? "var(--text3)"
              : "var(--warn, #c80)";
            const icon = ok ? "✓" : empty ? "–" : "!";
            return (
              <div
                key={b.excluded_block_index}
                style={{
                  display: "flex",
                  gap: 8,
                  lineHeight: 1.5,
                  fontFamily: "var(--mono)",
                  fontSize: "0.68rem",
                }}
              >
                <span style={{ color, width: 12 }}>{icon}</span>
                <span style={{ flex: 1 }}>{b.title}</span>
                <span style={{ color: "var(--text3)" }}>
                  {b.page_start ? `p.${b.page_start}` : ""}
                  {b.page_end && b.page_end !== b.page_start ? `–${b.page_end}` : ""}
                </span>
                <span style={{ color }}>
                  {b.extracted}/{b.identified}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  example: "Examples",
  problem: "Problems",
  try_it: "Try It",
  exercise: "Exercises",
  review: "Review",
  mcq: "MCQs",
  other: "Other",
};

// Matches prompt-emitted figure placeholders: {{fig: Fig 4.5 — caption}}.
// Case-insensitive on the prefix, tolerant of whitespace around the colon.
const FIG_RE = /\{\{\s*fig\s*:\s*([^}]+?)\s*\}\}/gi;

/** Split a raw_text on {{fig: ...}} tokens so we can render each figure as a
 *  visible chip inside the question body. */
function renderWithFigures(text: string): (string | JSX.Element)[] {
  if (!text) return [text];
  const parts: (string | JSX.Element)[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  FIG_RE.lastIndex = 0;
  let key = 0;
  while ((match = FIG_RE.exec(text)) !== null) {
    const [full, inner] = match;
    const start = match.index;
    if (start > lastIndex) parts.push(text.slice(lastIndex, start));
    parts.push(
      <span
        key={`fig-${key++}`}
        style={{
          display: "inline-block",
          padding: "1px 6px",
          margin: "0 2px",
          borderRadius: 4,
          background: "var(--bg2, #f2eaff)",
          color: "var(--purple, #6b3fd4)",
          fontSize: "0.7rem",
          fontFamily: "var(--mono)",
          border: "1px solid var(--purple, #b9a1eb)",
        }}
        title="Figure placeholder — exact position in the book"
      >
        🖼 {inner.trim()}
      </span>,
    );
    lastIndex = start + full.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function QuestionList({
  detail,
  bankId,
  excludedBlockRef,
  scopedSectionRef,
  scopedKind,
}: {
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  bankId: UUID | null;
  excludedBlockRef: string | null;
  scopedSectionRef: string | null;
  scopedKind: string | null;
}) {
  const { selectExcludedBlock, selectKind } = useUI();

  // -------- Kind-scoped view (from Questions lens sidebar) --------
  const kindScoped = useMemo(() => {
    if (!scopedKind || !scopedSectionRef) return null;
    const sec = detail.sections.find((s) => s.section_ref === scopedSectionRef);
    if (!sec) return null;
    const items = sec.by_kind?.[scopedKind as keyof typeof sec.by_kind] ?? [];
    return { sec, items };
  }, [detail, scopedKind, scopedSectionRef]);

  if (kindScoped) {
    const label = KIND_LABEL[scopedKind!] ?? scopedKind!;
    return (
      <>
        <div className="cvh">
          <div className="cvt">
            Scoped to{" "}
            <b>
              {label} · §{kindScoped.sec.section_ref} {kindScoped.sec.section_title}
            </b>
            <button
              className="btn bg"
              style={{
                marginLeft: 10,
                fontSize: "0.66rem",
                padding: "2px 8px",
              }}
              onClick={() => selectKind(null, null)}
            >
              Clear filter
            </button>
          </div>
        </div>
        {kindScoped.items.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            <div className="empty-i">📝</div>
            <h3>No {label.toLowerCase()} in this section</h3>
          </div>
        ) : (
          <SectionBlock
            sec={{ ...kindScoped.sec, questions: kindScoped.items }}
            blocks={[]}
            v3Stat={null}
            bankId={bankId}
          />
        )}
      </>
    );
  }

  // Index block stats by section_ref for per-section retry context
  type BlockStat = NonNullable<typeof detail.stats>["blocks"][number];
  const blocksBySection = useMemo(() => {
    const blocks: BlockStat[] = detail.stats?.blocks ?? [];
    const out: Record<string, BlockStat[]> = {};
    for (const b of blocks) {
      const key = b.section_ref || "__unlinked__";
      (out[key] ||= []).push(b);
    }
    return out;
  }, [detail.stats]);

  // v3 per-section stats by section_ref — drives status badge + rejected list + retry CTA
  const v3StatsBySection = useMemo(() => {
    const out: Record<string, ExtractionSectionStats> = {};
    for (const s of detail.stats?.sections ?? []) {
      out[s.section_ref] = s;
    }
    return out;
  }, [detail.stats]);

  // When a specific excluded block is selected, scope everything to questions
  // that came from that block (and the stats row for it).
  const scoped = useMemo(() => {
    if (!excludedBlockRef) return null;
    const filteredSections = detail.sections
      .map((sec) => ({
        ...sec,
        questions: sec.questions.filter(
          (q) => q.excluded_block_ref === excludedBlockRef,
        ),
      }))
      .filter((sec) => sec.questions.length > 0);
    const blockStat = (detail.stats?.blocks ?? []).find(
      (b) => `ex-${b.excluded_block_index}` === excludedBlockRef,
    );
    return { sections: filteredSections, blockStat };
  }, [detail, excludedBlockRef]);

  if (scoped) {
    return (
      <>
        <div className="cvh">
          <div className="cvt">
            Scoped to{" "}
            <b>
              {scoped.blockStat?.title ?? excludedBlockRef}
            </b>
            <button
              className="btn bg"
              style={{
                marginLeft: 10,
                fontSize: "0.66rem",
                padding: "2px 8px",
              }}
              onClick={() => selectExcludedBlock(null, null)}
            >
              Clear filter
            </button>
          </div>
        </div>
        {scoped.sections.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            <div className="empty-i">📝</div>
            <h3>No questions extracted from this block</h3>
            <p>
              {scoped.blockStat
                ? `Status: ${scoped.blockStat.status} · ${scoped.blockStat.extracted}/${scoped.blockStat.identified} extracted`
                : "Not part of the latest extraction."}
            </p>
            {bankId && scoped.blockStat && (
              <BlockRetryCTA bankId={bankId} blockIdx={scoped.blockStat.excluded_block_index} />
            )}
          </div>
        ) : (
          scoped.sections.map((sec) => (
            <SectionBlock
              key={sec.section_ref}
              sec={sec}
              blocks={
                scoped.blockStat
                  ? [scoped.blockStat]
                  : blocksBySection[sec.section_ref] ?? []
              }
              v3Stat={v3StatsBySection[sec.section_ref] ?? null}
              bankId={bankId}
            />
          ))
        )}
      </>
    );
  }

  const total = detail.total_questions;
  const nonEmptySections = detail.sections.filter((s) => s.questions.length > 0);
  const emptySections = detail.sections.filter((s) => s.questions.length === 0);

  if (total === 0) {
    return (
      <div className="empty">
        <div className="empty-i">📝</div>
        <h3>No questions found</h3>
        <p>Gemini didn't find any questions printed in this book's sections.</p>
      </div>
    );
  }

  return (
    <>
      <div className="cvh">
        <div className="cvt">
          {total} questions across {nonEmptySections.length} sections
          {detail.stats && detail.stats.missed > 0 && (
            <span style={{ color: "var(--warn, #c80)", marginLeft: 8 }}>
              · {detail.stats.missed} missed
            </span>
          )}
        </div>
      </div>

      {nonEmptySections.map((sec) => (
        <SectionBlock
          key={sec.section_ref}
          sec={sec}
          blocks={blocksBySection[sec.section_ref] ?? []}
          v3Stat={v3StatsBySection[sec.section_ref] ?? null}
          bankId={bankId}
        />
      ))}

      {emptySections.length > 0 && (
        <div
          style={{
            marginTop: 24,
            fontSize: "0.7rem",
            color: "var(--text3)",
            fontStyle: "italic",
          }}
        >
          No questions in: {emptySections.map((s) => s.section_ref).join(", ")}
        </div>
      )}
    </>
  );
}

function BlockRetryCTA({ bankId, blockIdx }: { bankId: UUID; blockIdx: number }) {
  const reExtract = useReExtractBlock();
  return (
    <button
      className="btn primary"
      disabled={reExtract.isPending}
      style={{ marginTop: 16 }}
      onClick={() => reExtract.mutate({ bankId, blockIdx })}
    >
      {reExtract.isPending ? "Re-extracting…" : "↺ Re-extract this block"}
    </button>
  );
}

function SectionBlock({
  sec,
  blocks,
  v3Stat,
  bankId,
}: {
  sec: NonNullable<ReturnType<typeof useQuestions>["data"]>["sections"][number];
  blocks: NonNullable<NonNullable<ReturnType<typeof useQuestions>["data"]>["stats"]>["blocks"];
  v3Stat: ExtractionSectionStats | null;
  bankId: UUID | null;
}) {
  const reExtract = useReExtractBlock();
  const retrySection = useRetrySection();
  const [pendingJobId, setPendingJobId] = useState<UUID | null>(null);
  const [showRejected, setShowRejected] = useState(false);
  const { data: pendingJob } = useJob(pendingJobId);
  useEffect(() => {
    if (pendingJob?.status === "succeeded" || pendingJob?.status === "failed") {
      setPendingJobId(null);
    }
  }, [pendingJob?.status]);

  const missed = sec.missed ?? 0;
  const identified = sec.identified ?? sec.questions.length;
  const extracted = sec.extracted ?? sec.questions.length;
  const v3Status = v3Stat?.status ?? null;
  const showRetry =
    !!v3Stat && !!bankId && (v3Status === "partial" || v3Status === "failed");
  const rejectedItems = v3Stat?.rejected_items ?? [];
  const statusColor =
    v3Status === "complete"
      ? "var(--green, #2a9d5e)"
      : v3Status === "partial"
        ? "var(--warn, #c80)"
        : v3Status === "failed"
          ? "var(--red, #d33)"
          : "var(--text3)";

  return (
    <div style={{ marginBottom: 28 }}>
      <h3
        style={{
          fontSize: "0.82rem",
          fontWeight: 700,
          color: "var(--text1)",
          marginBottom: 10,
          paddingBottom: 6,
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span>
          {sec.section_ref} {sec.section_title}
        </span>
        <span style={{ color: "var(--text3)", fontWeight: 400, fontSize: "0.7rem" }}>
          · {extracted}/{identified} extracted
          {missed > 0 && (
            <span style={{ color: "var(--warn, #c80)", marginLeft: 4 }}>
              · {missed} missed
            </span>
          )}
        </span>
        {v3Status && (
          <span
            style={{
              fontSize: "0.62rem",
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: 8,
              background: "var(--bg2, #f5f5fa)",
              color: statusColor,
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {v3Status}
          </span>
        )}
        {showRetry && (
          <button
            className="btn bg"
            disabled={retrySection.isPending || !!pendingJobId}
            style={{
              fontSize: "0.66rem",
              padding: "2px 8px",
              marginLeft: "auto",
              color: "var(--warn, #c80)",
              borderColor: "var(--warn, #c80)",
            }}
            onClick={() => {
              if (!bankId) return;
              retrySection.mutate(
                { bankId, sectionRef: sec.section_ref },
                { onSuccess: (res) => setPendingJobId(res.job_id) },
              );
            }}
          >
            ↺ Retry section
          </button>
        )}
      </h3>

      {rejectedItems.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <button
            onClick={() => setShowRejected((v) => !v)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text3)",
              cursor: "pointer",
              fontSize: "0.68rem",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {showRejected ? "Hide" : "Show"} {rejectedItems.length} rejected
          </button>
          {showRejected && (
            <div
              style={{
                marginTop: 6,
                padding: 8,
                background: "var(--bg2, #f5f5fa)",
                borderRadius: 4,
                fontSize: "0.68rem",
                color: "var(--text2)",
              }}
            >
              {rejectedItems.map((it, i) => (
                <div
                  key={i}
                  style={{
                    padding: "4px 0",
                    borderTop: i === 0 ? "none" : "1px solid var(--border)",
                  }}
                >
                  <div style={{ color: "var(--warn, #c80)", fontFamily: "var(--mono)", fontSize: "0.62rem" }}>
                    {String(it._reject_reason ?? "rejected")}
                  </div>
                  <div style={{ whiteSpace: "pre-wrap", marginTop: 2 }}>
                    {String(it.raw_text ?? "")}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Per-block retry row — one button per excluded block that fed this section */}
      {blocks.length > 0 && bankId && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
          {blocks.map((b) => {
            const warn = b.status !== "ok" && b.status !== "empty";
            const busy = reExtract.isPending || !!pendingJobId;
            return (
              <button
                key={b.excluded_block_index}
                className="btn bg"
                disabled={busy}
                title={
                  `${b.title} · pages ${b.page_start ?? "?"}–${b.page_end ?? "?"}\n` +
                  `${b.extracted}/${b.identified} extracted (${b.status})\n` +
                  `link: ${b.link_method} (${Math.round(b.link_confidence * 100)}%)`
                }
                style={{
                  fontSize: "0.68rem",
                  padding: "3px 8px",
                  color: warn ? "var(--warn, #c80)" : "var(--text2)",
                  borderColor: warn ? "var(--warn, #c80)" : undefined,
                }}
                onClick={() => {
                  if (!bankId) return;
                  reExtract.mutate(
                    { bankId, blockIdx: b.excluded_block_index },
                    {
                      onSuccess: (res) => setPendingJobId(res.job_id),
                    },
                  );
                }}
              >
                ↺ {b.title}
                {warn ? ` · ${b.extracted}/${b.identified}` : ""}
              </button>
            );
          })}
        </div>
      )}

      {pendingJob && pendingJob.status === "running" && (
        <div style={{ fontSize: "0.7rem", color: "var(--text3)", marginBottom: 8 }}>
          Re-extracting… {pendingJob.message ?? ""}
        </div>
      )}

      {sec.questions.map((q, i) => (
        <div key={q.id} className="card" style={{ marginBottom: 8 }}>
          <div
            style={{
              fontSize: "0.64rem",
              color: "var(--text3)",
              marginBottom: 4,
              fontFamily: "var(--mono)",
            }}
          >
            Q{q.question_number ?? i + 1}
            {q.exercise_ref ? ` · ${q.exercise_ref}` : ""}
            {q.page_start ? ` · p.${q.page_start}` : ""}
            {q.question_type ? ` · ${q.question_type}` : ""}
          </div>
          <div
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "0.82rem",
              lineHeight: 1.55,
              color: "var(--text1)",
            }}
          >
            {renderWithFigures(q.raw_text)}
          </div>
          {q.has_solution && q.solution_text && (
            <details style={{ marginTop: 8 }}>
              <summary
                style={{
                  fontSize: "0.68rem",
                  color: "var(--text3)",
                  cursor: "pointer",
                }}
              >
                Solution
              </summary>
              <div
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: "0.78rem",
                  lineHeight: 1.5,
                  color: "var(--text2)",
                  marginTop: 4,
                  paddingLeft: 8,
                  borderLeft: "2px solid var(--border)",
                }}
              >
                {renderWithFigures(q.solution_text)}
              </div>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Regeneration UI ──────────────────────────────────────────────────────

function statusColor(status: string): string {
  if (status === "ready") return "var(--accent)";
  if (status === "saved") return "var(--green, #2a9d5e)";
  if (status === "failed") return "var(--red, #d33)";
  return "var(--text3)";
}

function regenLabel(r: QuestionRegeneration, idx: number): string {
  return r.label || `Regen-${idx + 1}`;
}

function RegenRunBar({
  bankId,
  bookId,
  detail,
  activeRegenId,
  onSelectRegen,
}: {
  bankId: UUID;
  bookId: UUID;
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  activeRegenId: UUID | null;
  onSelectRegen: (id: UUID | null) => void;
}) {
  const { data: regens } = useQuestionRegenerations(bookId, { pollMs: 2000 });
  const [showModal, setShowModal] = useState(false);

  const sortedRegens = useMemo(() => {
    if (!regens) return [];
    return [...regens].sort((a, b) =>
      (a.created_at ?? "").localeCompare(b.created_at ?? ""),
    );
  }, [regens]);

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 6,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 12,
          padding: "8px 10px",
          background: "var(--bg2, #f5f5fa)",
          borderRadius: 6,
          fontSize: "0.72rem",
        }}
      >
        <span style={{ color: "var(--text3)", marginRight: 4 }}>Run:</span>
        <button
          className="btn bg"
          onClick={() => onSelectRegen(null)}
          style={{
            fontSize: "0.7rem",
            padding: "3px 10px",
            background: !activeRegenId ? "var(--accent)" : undefined,
            color: !activeRegenId ? "white" : undefined,
            borderColor: !activeRegenId ? "var(--accent)" : undefined,
          }}
        >
          Original
        </button>
        {sortedRegens.map((r, idx) => {
          const active = activeRegenId === r.id;
          return (
            <button
              key={r.id}
              className="btn bg"
              onClick={() => onSelectRegen(r.id)}
              title={`${r.scope}${r.section_refs?.length ? ` · ${r.section_refs.join(", ")}` : ""}${r.custom_instructions ? ` · custom: ${r.custom_instructions.slice(0, 80)}` : ""}`}
              style={{
                fontSize: "0.7rem",
                padding: "3px 10px",
                background: active ? "var(--accent)" : undefined,
                color: active ? "white" : undefined,
                borderColor: active ? "var(--accent)" : undefined,
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: active ? "white" : statusColor(r.status),
                  marginRight: 6,
                  verticalAlign: "middle",
                }}
              />
              {regenLabel(r, idx)}
              <span
                style={{
                  marginLeft: 8,
                  fontSize: "0.62rem",
                  color: active ? "white" : "var(--text3)",
                }}
              >
                {r.status === "ready" || r.status === "saved"
                  ? `${r.question_count}q`
                  : r.status}
              </span>
            </button>
          );
        })}
        <button
          className="btn primary"
          style={{ fontSize: "0.7rem", padding: "3px 10px", marginLeft: "auto" }}
          onClick={() => setShowModal(true)}
        >
          + Regenerate
        </button>
      </div>

      {showModal && (
        <RegenerateModal
          bankId={bankId}
          detail={detail}
          onClose={() => setShowModal(false)}
          onStarted={(id) => {
            setShowModal(false);
            onSelectRegen(id);
          }}
        />
      )}
    </>
  );
}

function RegenerateModal({
  bankId,
  detail,
  onClose,
  onStarted,
}: {
  bankId: UUID;
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  onClose: () => void;
  onStarted: (id: UUID) => void;
}) {
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"bank" | "sections">("bank");
  const [sectionRefs, setSectionRefs] = useState<string[]>([]);
  const [customInstructions, setCustomInstructions] = useState("");
  const start = useStartQuestionRegeneration();

  const allSections = detail.sections.map((s) => ({
    ref: s.section_ref,
    title: s.section_title,
  }));

  const submit = () => {
    if (scope === "sections" && sectionRefs.length === 0) return;
    start.mutate(
      {
        bankId,
        params: {
          scope,
          section_refs: scope === "sections" ? sectionRefs : null,
          custom_instructions: customInstructions.trim() || null,
          label: label.trim() || null,
        },
      },
      { onSuccess: (res) => onStarted(res.regen_id) },
    );
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg1, white)",
          padding: 20,
          borderRadius: 8,
          minWidth: 480,
          maxWidth: 600,
          maxHeight: "80vh",
          overflow: "auto",
        }}
      >
        <h3 style={{ marginTop: 0, fontSize: "0.9rem" }}>Regenerate questions</h3>
        <p style={{ fontSize: "0.72rem", color: "var(--text3)", marginBottom: 16 }}>
          Re-runs the 3-pass extractor against the same PDF. Originals stay intact.
        </p>

        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Label (optional)</span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. v2 — stricter math"
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.78rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
            maxLength={64}
          />
        </label>

        <div style={{ marginBottom: 12 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Scope</span>
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              className="btn bg"
              onClick={() => setScope("bank")}
              style={{
                fontSize: "0.72rem",
                padding: "5px 12px",
                background: scope === "bank" ? "var(--accent)" : undefined,
                color: scope === "bank" ? "white" : undefined,
                borderColor: scope === "bank" ? "var(--accent)" : undefined,
              }}
            >
              Whole bank
            </button>
            <button
              className="btn bg"
              onClick={() => setScope("sections")}
              style={{
                fontSize: "0.72rem",
                padding: "5px 12px",
                background: scope === "sections" ? "var(--accent)" : undefined,
                color: scope === "sections" ? "white" : undefined,
                borderColor: scope === "sections" ? "var(--accent)" : undefined,
              }}
            >
              Specific sections
            </button>
          </div>
        </div>

        {scope === "sections" && (
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
              Sections ({sectionRefs.length} selected)
            </span>
            <div
              style={{
                marginTop: 4,
                maxHeight: 180,
                overflow: "auto",
                border: "1px solid var(--border)",
                borderRadius: 4,
                padding: 6,
                background: "var(--bg1)",
              }}
            >
              {allSections.map((s) => {
                const checked = sectionRefs.includes(s.ref);
                return (
                  <label
                    key={s.ref}
                    style={{
                      display: "flex",
                      gap: 6,
                      padding: "3px 4px",
                      fontSize: "0.72rem",
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        setSectionRefs((prev) =>
                          checked ? prev.filter((x) => x !== s.ref) : [...prev, s.ref],
                        );
                      }}
                    />
                    <span>
                      §{s.ref} {s.title}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        <label style={{ display: "block", marginBottom: 16 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
            Custom instructions (HIGHEST PRIORITY OVERRIDE)
          </span>
          <textarea
            value={customInstructions}
            onChange={(e) => setCustomInstructions(e.target.value)}
            placeholder="e.g. Capture every sub-part separately, even if printed inline."
            rows={4}
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.78rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              fontFamily: "inherit",
              resize: "vertical",
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
          />
        </label>

        {start.isError && (
          <div style={{ color: "var(--red)", fontSize: "0.72rem", marginBottom: 10 }}>
            {(start.error as Error).message}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn bg" onClick={onClose} disabled={start.isPending}>
            Cancel
          </button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={start.isPending || (scope === "sections" && sectionRefs.length === 0)}
          >
            {start.isPending ? "Starting..." : "Start regeneration"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RegenView({
  regenId,
  bookId,
  originalDetail,
}: {
  regenId: UUID;
  bookId: UUID;
  originalDetail: NonNullable<ReturnType<typeof useQuestions>["data"]> | null;
}) {
  const { data: regen } = useQuestionRegen(regenId, { pollMs: 2000 });
  const isExtracting = regen?.status === "pending" || regen?.status === "extracting";
  const { data: regenData } = useRegenQuestions(regenId, {
    pollMs: isExtracting ? 2500 : undefined,
  });
  const { data: regenJob } = useJob(regen?.job_id ?? null, { pollMs: 1000 });

  const saveRegen = useSaveQuestionRegeneration();
  const deleteRegen = useDeleteQuestionRegeneration();
  const bulkDelete = useBulkDeleteRegenQuestions();
  const { selectQuestionRegen } = useUI();

  const originalsBySection = useMemo(() => {
    const m: Record<string, Question[]> = {};
    for (const sec of originalDetail?.sections ?? []) {
      m[sec.section_ref] = sec.questions;
    }
    return m;
  }, [originalDetail]);

  if (!regen) {
    return <div style={{ color: "var(--text3)" }}>Loading regen…</div>;
  }

  return (
    <>
      <div
        className="card"
        style={{
          marginBottom: 12,
          borderLeft: `3px solid ${statusColor(regen.status)}`,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <div>
            <div style={{ fontSize: "0.62rem", color: "var(--text3)" }}>REGENERATION</div>
            <div style={{ fontSize: "0.84rem", fontWeight: 600 }}>
              {regen.label || `Regen run`}
              <span style={{ color: "var(--text3)", fontWeight: 400, marginLeft: 8, fontSize: "0.7rem" }}>
                · {regen.scope}
                {regen.scope === "sections" && regen.section_refs.length > 0
                  ? ` · ${regen.section_refs.join(", ")}`
                  : ""}
                {" · "}
                <span style={{ color: statusColor(regen.status) }}>{regen.status}</span>
                {" · "}
                {regen.question_count} questions
              </span>
            </div>
            {regen.custom_instructions && (
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--text2)",
                  marginTop: 4,
                  fontStyle: "italic",
                  maxWidth: 600,
                }}
                title={regen.custom_instructions}
              >
                ⚡ {regen.custom_instructions}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 6, marginLeft: "auto" }}>
            {regen.status === "ready" && (
              <button
                className="btn primary"
                style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                disabled={saveRegen.isPending}
                onClick={() =>
                  saveRegen.mutate({ regenId, bookId })
                }
              >
                {saveRegen.isPending ? "Saving…" : "💾 Save"}
              </button>
            )}
            <button
              className="btn bg"
              style={{
                fontSize: "0.72rem",
                padding: "4px 10px",
                color: "var(--red, #d33)",
              }}
              disabled={deleteRegen.isPending}
              onClick={() => {
                if (!confirm("Delete this regeneration run? Originals are unaffected.")) return;
                deleteRegen.mutate(
                  { regenId, bookId },
                  {
                    onSuccess: () => selectQuestionRegen(null),
                  },
                );
              }}
            >
              🗑 Delete run
            </button>
          </div>
        </div>

        {isExtracting && (
          <div style={{ marginTop: 10 }}>
            <div className="prog">
              <div
                className="progb"
                style={{
                  width: `${Math.max(2, regenJob?.progress ?? 0)}%`,
                  transition: "width 0.4s ease",
                }}
              />
            </div>
            <div style={{ fontSize: "0.7rem", color: "var(--text3)", marginTop: 4 }}>
              {regenJob?.message ?? "Extracting…"}{" "}
              <span style={{ fontFamily: "var(--mono)" }}>{regenJob?.progress ?? 0}%</span>
            </div>
          </div>
        )}

        {regen.status === "failed" && regen.last_error && (
          <div style={{ color: "var(--red)", fontSize: "0.72rem", marginTop: 8 }}>
            {regen.last_error}
          </div>
        )}
      </div>

      {regenData && regenData.sections.length === 0 && !isExtracting && (
        <div className="empty" style={{ padding: 30 }}>
          <div className="empty-i">📝</div>
          <h3>No questions in this regeneration yet</h3>
        </div>
      )}

      {regenData?.sections.map((sec) => {
        const refKey = sec.section_ref ?? "";
        const originals = originalsBySection[refKey] ?? [];
        return (
          <div key={refKey || "_unsectioned"} style={{ marginBottom: 28 }}>
            <h3
              style={{
                fontSize: "0.82rem",
                fontWeight: 700,
                marginBottom: 10,
                paddingBottom: 6,
                borderBottom: "1px solid var(--border)",
              }}
            >
              {sec.section_ref ? `${sec.section_ref} ${sec.section_title ?? ""}` : "Unsectioned"}
              <span style={{ color: "var(--text3)", fontWeight: 400, fontSize: "0.7rem", marginLeft: 8 }}>
                · {originals.length} original · {sec.questions.length} regen
              </span>
            </h3>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
              }}
            >
              <div>
                <div className="clbl" style={{ marginBottom: 6 }}>
                  Original
                </div>
                {originals.length === 0 && (
                  <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                    No originals in this section.
                  </div>
                )}
                {originals.map((q, i) => (
                  <QuestionCard key={q.id} q={q} index={i} />
                ))}
              </div>
              <div>
                <div className="clbl" style={{ marginBottom: 6 }}>
                  Regen
                </div>
                {sec.questions.length === 0 && (
                  <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                    No regen questions in this section.
                  </div>
                )}
                {sec.questions.map((q, i) => (
                  <QuestionCard
                    key={q.id}
                    q={q}
                    index={i}
                    onDelete={() =>
                      bulkDelete.mutate({ regenId, questionIds: [q.id] })
                    }
                  />
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}

function QuestionCard({
  q,
  index,
  onDelete,
}: {
  q: Question;
  index: number;
  onDelete?: () => void;
}) {
  return (
    <div className="card" style={{ marginBottom: 8, position: "relative" }}>
      <div
        style={{
          fontSize: "0.62rem",
          color: "var(--text3)",
          marginBottom: 4,
          fontFamily: "var(--mono)",
          display: "flex",
          gap: 6,
          alignItems: "center",
        }}
      >
        <span>
          Q{q.question_number ?? index + 1}
          {q.exercise_ref ? ` · ${q.exercise_ref}` : ""}
          {q.page_start ? ` · p.${q.page_start}` : ""}
          {q.question_type ? ` · ${q.question_type}` : ""}
        </span>
        {onDelete && (
          <button
            onClick={onDelete}
            title="Delete this regen question"
            style={{
              marginLeft: "auto",
              background: "transparent",
              border: "none",
              color: "var(--text3)",
              cursor: "pointer",
              fontSize: "0.78rem",
              padding: "0 4px",
            }}
          >
            ✕
          </button>
        )}
      </div>
      <div
        style={{
          whiteSpace: "pre-wrap",
          fontSize: "0.78rem",
          lineHeight: 1.5,
          color: "var(--text1)",
        }}
      >
        {renderWithFigures(q.raw_text)}
      </div>
      {q.has_solution && q.solution_text && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ fontSize: "0.66rem", color: "var(--text3)", cursor: "pointer" }}>
            Solution
          </summary>
          <div
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "0.74rem",
              lineHeight: 1.45,
              color: "var(--text2)",
              marginTop: 4,
              paddingLeft: 8,
              borderLeft: "2px solid var(--border)",
            }}
          >
            {renderWithFigures(q.solution_text)}
          </div>
        </details>
      )}
    </div>
  );
}
