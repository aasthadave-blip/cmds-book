import { useState } from "react";
import { useBook, useSections, useRegenerate, useRegeneration, useJob } from "../api/hooks";
import type { RegenParams, Block, UUID } from "../api/client";
import { api } from "../api/client";
import { useUI } from "../stores/ui";
import { JobProgress } from "../components/JobProgress";
import { BlockRenderer } from "../components/BlockRenderer";
import { RegenReviewPage } from "./RegenReviewPage";

const DEFAULT: RegenParams = {
  intensity: "moderate",
  tone: "academic",
  equations_handling: "preserve",
  diagrams_handling: "preserve",
  analogies: "none",
  structure: "identical",
  language: "en",
  target_audience: null,
  custom_instructions: null,
};

const INTENSITY_OPTS: [RegenParams["intensity"], string, string][] = [
  ["light", "Light", "20–30% — vocabulary only"],
  ["moderate", "Moderate", "40–60% — restructured sentences"],
  ["heavy", "Heavy", "70–90% — full rewrite"],
];

const TONE_OPTS: [RegenParams["tone"], string, string][] = [
  ["academic", "Academic", "Formal, third-person"],
  ["conversational", "Conversational", "Friendly, accessible"],
  ["simplified", "Simplified", "Short sentences, examples"],
];

const EQ_OPTS: [RegenParams["equations_handling"], string, string][] = [
  ["preserve", "Preserve", "Keep equations verbatim"],
  ["explain", "Explain", "Add brief explanation"],
];

const ANALOGY_OPTS: [RegenParams["analogies"], string, string][] = [
  ["none", "No analogies", "Stay literal"],
  ["add_one", "One analogy", "Up to one per section"],
  ["add_multiple", "Multiple", "Where helpful"],
];

const STRUCTURE_OPTS: [RegenParams["structure"], string, string][] = [
  ["identical", "Identical", "Match original headers + order"],
  ["reorganize", "Reorganize", "Minor pedagogical reflow"],
];

const DIAG_OPTS: [RegenParams["diagrams_handling"], string, string][] = [
  ["preserve", "Preserve", "Caption stays verbatim"],
  ["describe", "Describe", "Add one-sentence description"],
];

const LANG_OPTS = [
  ["en", "English"],
  ["hi", "Hindi"],
  ["ta", "Tamil"],
  ["te", "Telugu"],
  ["mr", "Marathi"],
  ["bn", "Bengali"],
  ["gu", "Gujarati"],
];

export function RegenPage() {
  const { selectedBookId } = useUI();
  const { data: book } = useBook(selectedBookId);
  const [p, setP] = useState<RegenParams>(DEFAULT);
  const [jobId, setJobId] = useState<string | null>(null);
  const [regenId, setRegenId] = useState<string | null>(null);
  const [showResults, setShowResults] = useState(false);
  const [showReview, setShowReview] = useState(false);
  const regen = useRegenerate();
  const { data: job } = useJob(jobId);

  if (!book) {
    return (
      <>
        <div className="topbar">
          <div className="bc"><span className="bci a">Regenerate</span></div>
        </div>
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">✨</div>
              <h3>Pick a book first</h3>
            </div>
          </div>
        </div>
      </>
    );
  }

  const jobDone = job?.status === "succeeded";
  const jobFailed = job?.status === "failed";

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedBookId) return;
    setShowResults(false);
    setShowReview(false);
    setRegenId(null);
    const res = await regen.mutateAsync({ bookId: selectedBookId, params: p });
    setJobId(res.job_id);
    setRegenId(res.regen_id ?? null);
  }

  // Full-screen review mode — no padding/max-width
  if (showReview && regenId && selectedBookId) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci">{book.title}</span>
            <span className="bcs">›</span>
            <span className="bci">Regenerate</span>
            <span className="bcs">›</span>
            <span className="bci a">Review</span>
          </div>
          <button className="btn bg" onClick={() => { setShowReview(false); setShowResults(false); setJobId(null); setRegenId(null); }}>
            ✨ New regen
          </button>
        </div>
        <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
          <RegenReviewPage
            bookId={selectedBookId}
            regenId={regenId}
            onBack={() => setShowReview(false)}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">{book.title}</span>
          <span className="bcs">›</span>
          <span className="bci a">Regenerate</span>
        </div>
        {showResults && regenId && selectedBookId && (
          <>
            <button className="btn bg" onClick={() => api.exportMarkdown(selectedBookId, regenId)} title="Export regenerated as Markdown">⬇ .md</button>
            <button className="btn bg" onClick={() => api.exportJson(selectedBookId, regenId)} title="Export regenerated as JSON">⬇ .json</button>
          </>
        )}
        {showResults && (
          <button className="btn bg" onClick={() => { setShowResults(false); setJobId(null); setRegenId(null); }}>
            ✨ New regen
          </button>
        )}
      </div>

      <div className="cnt">
        <div className="ci">
          {!showResults ? (
            <>
              <form onSubmit={onSubmit}>
                <div className="regen-grid">
                  <RadioCard icon="⚡" title="Intensity" value={p.intensity} options={INTENSITY_OPTS} onChange={(v) => setP({ ...p, intensity: v })} />
                  <RadioCard icon="🎙️" title="Tone" value={p.tone} options={TONE_OPTS} onChange={(v) => setP({ ...p, tone: v })} />
                  <RadioCard icon="∑" title="Equations" value={p.equations_handling} options={EQ_OPTS} onChange={(v) => setP({ ...p, equations_handling: v })} />
                  <RadioCard icon="📊" title="Diagrams" value={p.diagrams_handling} options={DIAG_OPTS} onChange={(v) => setP({ ...p, diagrams_handling: v })} />
                  <RadioCard icon="💡" title="Analogies" value={p.analogies} options={ANALOGY_OPTS} onChange={(v) => setP({ ...p, analogies: v })} />
                  <RadioCard icon="🧱" title="Structure" value={p.structure} options={STRUCTURE_OPTS} onChange={(v) => setP({ ...p, structure: v })} />
                </div>

                <div className="card">
                  <div className="clbl">Language</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {LANG_OPTS.map(([code, label]) => (
                      <label key={code} className={`rp-option ${p.language === code ? "selected" : ""}`} style={{ padding: "5px 12px" }}>
                        <input type="radio" name="language" checked={p.language === code} onChange={() => setP({ ...p, language: code })} />
                        <span style={{ fontSize: "0.78rem" }}>{label}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="card">
                  <div className="clbl">Target audience (optional)</div>
                  <input className="inp" value={p.target_audience ?? ""} onChange={(e) => setP({ ...p, target_audience: e.target.value || null })} placeholder="e.g. CBSE class 12, JEE aspirant" />
                </div>

                <div className="card">
                  <div className="clbl">Custom instructions (optional)</div>
                  <textarea className="inp" value={p.custom_instructions ?? ""} onChange={(e) => setP({ ...p, custom_instructions: e.target.value || null })} rows={3} placeholder="e.g. Emphasise real-world applications, use SI units only" />
                </div>

                <div style={{ display: "flex", gap: 9 }}>
                  <button type="submit" className="btn bp" disabled={regen.isPending}>
                    {regen.isPending ? "Starting…" : "✨ Start regeneration"}
                  </button>
                </div>
              </form>

              {jobId && (
                <div style={{ marginTop: 16 }}>
                  <JobProgress jobId={jobId} />
                  {jobDone && regenId && (
                    <div style={{ marginTop: 12, display: "flex", gap: 9 }}>
                      <button className="btn bp" onClick={() => setShowReview(true)}>
                        🔍 Review section by section →
                      </button>
                    </div>
                  )}
                  {jobFailed && (
                    <div style={{ marginTop: 8, color: "var(--red)", fontSize: "0.8rem" }}>
                      Regeneration failed. Check the error above and try again.
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            regenId && selectedBookId && (
              <RegenResultsView
                bookId={selectedBookId}
                regenId={regenId}
                params={p}
              />
            )
          )}
        </div>
      </div>
    </>
  );
}

// ── Results Viewer ─────────────────────────────────────────────────────────────

function RegenResultsView({
  bookId,
  regenId,
  params,
}: {
  bookId: UUID;
  regenId: UUID;
  params: RegenParams;
}) {
  const { data: regen } = useRegeneration(regenId);
  const { data: sections } = useSections(bookId);
  const { setView: setUIView, setRegenId, selectSection } = useUI();
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [view, setView] = useState<"regen" | "original">("regen");

  if (!regen || !sections) {
    return (
      <div className="empty">
        <div className="empty-i">⏳</div>
        <h3>Loading results…</h3>
      </div>
    );
  }

  const blocksBySection = regen.blocks_by_section as Record<string, Block[]>;
  const qcDrift = (regen.qc_drift ?? {}) as Record<string, { pass: boolean; drifted: string[] }>;

  const totalSections = Object.keys(blocksBySection).length;
  const passedQC = Object.values(qcDrift).filter((r) => r.pass).length;
  const failedQC = Object.values(qcDrift).filter((r) => !r.pass).length;
  const driftedSections = Object.entries(qcDrift).filter(([, r]) => !r.pass);

  // Map section_id → section for original blocks
  const sectionMap = new Map(sections.map((s) => [s.section_id, s]));

  // List of sections that have regenerated blocks
  const regenSectionIds = Object.keys(blocksBySection);
  const activeId = activeSectionId ?? regenSectionIds[0] ?? null;

  const activeSection = activeId ? sectionMap.get(activeId) : null;
  const activeRegenBlocks: Block[] = activeId ? (blocksBySection[activeId] ?? []) : [];
  const activeOrigBlocks: Block[] = activeSection?.blocks ?? [];
  const activeQC = activeId ? qcDrift[activeId] : null;

  return (
    <div>
      {/* Summary */}
      <div className="mg" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
        <div className="mgb">
          <div className="mgv">{totalSections}</div>
          <div className="mgl">Sections regenerated</div>
        </div>
        <div className="mgb">
          <div className="mgv" style={{ color: "var(--green)" }}>{passedQC}</div>
          <div className="mgl">QC passed</div>
        </div>
        <div className="mgb">
          <div className="mgv" style={{ color: failedQC > 0 ? "var(--amber)" : "var(--green)" }}>{failedQC}</div>
          <div className="mgl">QC drift warnings</div>
        </div>
      </div>

      {/* Params summary */}
      <div className="card" style={{ padding: "11px 15px", marginBottom: 13 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: "0.72rem", color: "var(--text2)" }}>
          <span className="cvc regen">✨ {params.intensity}</span>
          <span className="cvc regen">{params.tone}</span>
          <span className="cvc regen">eq: {params.equations_handling}</span>
          <span className="cvc regen">{params.analogies === "none" ? "no analogies" : params.analogies}</span>
          <span className="cvc regen">lang: {params.language}</span>
          {params.target_audience && <span className="cvc regen">for: {params.target_audience}</span>}
        </div>
      </div>

      {/* QC drift warnings */}
      {driftedSections.length > 0 && (
        <div className="card" style={{ borderColor: "#fde68a", background: "var(--amber-bg)", marginBottom: 13 }}>
          <div className="clbl" style={{ color: "var(--amber)" }}>⚠ QC drift warnings</div>
          {driftedSections.map(([sid, res]) => {
            const sec = sectionMap.get(sid);
            return (
              <div key={sid} style={{ fontSize: "0.78rem", marginBottom: 6 }}>
                <span style={{ fontWeight: 600, color: "var(--text)" }}>{sec?.title ?? sid}</span>
                {" — values that drifted: "}
                {res.drifted.map((v) => (
                  <code key={v} style={{ background: "#fef3c7", padding: "1px 5px", borderRadius: 4, marginRight: 4, fontFamily: "var(--mono)", fontSize: "0.72rem" }}>{v}</code>
                ))}
              </div>
            );
          })}
        </div>
      )}

      {/* Section list + viewer */}
      <div style={{ display: "flex", gap: 13, alignItems: "flex-start" }}>
        {/* Left: section list */}
        <div style={{ width: 200, flexShrink: 0 }}>
          <div className="clbl" style={{ marginBottom: 6 }}>Sections</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {regenSectionIds.map((sid) => {
              const sec = sectionMap.get(sid);
              const qc = qcDrift[sid];
              const isActive = sid === activeId;
              return (
                <button
                  key={sid}
                  onClick={() => setActiveSectionId(sid)}
                  style={{
                    display: "flex", alignItems: "center", gap: 6,
                    padding: "6px 9px", borderRadius: 7, border: "1px solid",
                    borderColor: isActive ? "var(--accent)" : "var(--border)",
                    background: isActive ? "var(--accent-bg)" : "var(--surface)",
                    cursor: "pointer", fontSize: "0.72rem", textAlign: "left",
                    color: isActive ? "var(--accent)" : "var(--text2)",
                    fontWeight: isActive ? 600 : 400,
                  }}
                >
                  <span style={{ flexShrink: 0 }}>
                    {!qc ? "—" : qc.pass ? "✅" : "⚠️"}
                  </span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {sec?.title ?? sid}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right: content viewer */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {activeId && (
            <>
              {/* Section header */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <div style={{ flex: 1, fontWeight: 700, fontSize: "1rem", color: "var(--text)" }}>
                  {activeSection?.title ?? activeId}
                </div>
                {activeQC && (
                  <span className={`cvc ${activeQC.pass ? "ok" : "fail"}`}>
                    {activeQC.pass ? "QC pass" : `QC fail · ${activeQC.drifted.length} drifted`}
                  </span>
                )}
              </div>

              {/* Toggle */}
              <div style={{ display: "flex", gap: 0, marginBottom: 14, border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", width: "fit-content" }}>
                <button
                  onClick={() => setView("regen")}
                  style={{
                    padding: "6px 16px", border: "none", cursor: "pointer", fontSize: "0.75rem", fontWeight: 600,
                    background: view === "regen" ? "var(--purple)" : "var(--surface2)",
                    color: view === "regen" ? "#fff" : "var(--text2)",
                    fontFamily: "var(--sans)",
                  }}
                >
                  ✨ Regenerated
                </button>
                <button
                  onClick={() => setView("original")}
                  style={{
                    padding: "6px 16px", border: "none", cursor: "pointer", fontSize: "0.75rem", fontWeight: 600,
                    background: view === "original" ? "var(--accent)" : "var(--surface2)",
                    color: view === "original" ? "#fff" : "var(--text2)",
                    fontFamily: "var(--sans)",
                  }}
                >
                  Original
                </button>
              </div>

              {/* Blocks */}
              <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, padding: "16px 18px", minHeight: 120 }}>
                {view === "regen" ? (
                  activeRegenBlocks.length > 0
                    ? <BlockRenderer blocks={activeRegenBlocks} />
                    : <div style={{ color: "var(--text3)", fontSize: "0.8rem" }}>No regenerated blocks for this section.</div>
                ) : (
                  activeOrigBlocks.length > 0
                    ? <BlockRenderer blocks={activeOrigBlocks} />
                    : <div style={{ color: "var(--text3)", fontSize: "0.8rem" }}>No original blocks found.</div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Open in reader CTA */}
      <div style={{ marginTop: 22, paddingTop: 18, borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 12 }}>
        <button
          className="btn bp"
          onClick={() => {
            // Set regen context and open first section in reader
            const firstSectionId = regenSectionIds[0];
            const firstSection = firstSectionId ? sectionMap.get(firstSectionId) : null;
            setRegenId(regenId);
            if (firstSection) selectSection(firstSection.id);
            setUIView("reader");
          }}
          style={{ background: "linear-gradient(135deg, var(--purple), #6d28d9)" }}
        >
          ✨ Open in reader
        </button>
        <span style={{ fontSize: "0.73rem", color: "var(--text3)" }}>
          Opens the ✨ Regenerated folder in the sidebar — original content is preserved
        </span>
      </div>
    </div>
  );
}

// ── RadioCard ──────────────────────────────────────────────────────────────────

function RadioCard<T extends string>({
  icon, title, value, options, onChange,
}: {
  icon: string; title: string; value: T; options: [T, string, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="rp-card">
      <h4><span>{icon}</span> {title}</h4>
      {options.map(([v, lbl, hint]) => (
        <label key={v} className={`rp-option ${value === v ? "selected" : ""}`}>
          <input type="radio" name={title} checked={value === v} onChange={() => onChange(v)} />
          <span>{lbl}<small>{hint}</small></span>
        </label>
      ))}
    </div>
  );
}
