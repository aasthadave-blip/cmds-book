"""Build COST_MODEL.xlsx with all cost-relevant numbers + a live cost calculator.

Run: .venv/bin/python build_cost_xlsx.py
Output: /Users/aastha/.claude/worktrees/keen-curran/COST_MODEL.xlsx

Sheets:
  1. Pricing       — Gemini list-price rate card
  2. UnitCosts     — per-call cost decomposition for every pipeline
  3. Inputs        — yellow editable cells (volume knobs)
  4. Calculator    — uses Inputs → outputs $/chapter, $/year (live formulas)
  5. Scenarios     — pre-computed Pilot / Steady / Scale / Aggressive rows
  6. Notes         — caveats, source citations
"""

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUT = "/Users/aastha/.claude/worktrees/keen-curran/COST_MODEL.xlsx"

YELLOW = PatternFill("solid", fgColor="FFF2CC")
BLUE = PatternFill("solid", fgColor="DEEBF7")
GREEN = PatternFill("solid", fgColor="E2F0D9")
GREY = PatternFill("solid", fgColor="EDEDED")
BOLD = Font(bold=True)
HDR = Font(bold=True, color="FFFFFF")
HDR_FILL = PatternFill("solid", fgColor="305496")
BORDER = Border(*(Side(style="thin", color="BFBFBF"),) * 4)


def style_header(row):
    for c in row:
        c.font = HDR
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="left", vertical="center")


def autosize(ws):
    for col in ws.columns:
        try:
            length = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        except ValueError:
            continue
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(length + 2, 60)


def section_title(ws, row, text, span=4):
    ws.cell(row=row, column=1, value=text).font = Font(bold=True, size=13, color="305496")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)


# ─── 1. PRICING ──────────────────────────────────────────────────────────────
def sheet_pricing(wb):
    ws = wb.create_sheet("Pricing")
    headers = ["Model", "Input $/1M tok", "Output $/1M tok", "Notes"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=1, column=i, value=h)
    style_header(ws[1])
    rows = [
        ("gemini-2.5-pro", 1.25, 10.00, "≤200K context. Above 200K: $2.50 / $15"),
        ("gemini-2.5-flash", 0.30, 2.50, "All context sizes"),
        ("gemini-2.0-flash-image-gen", 0.039, 0.0, "Per image (text+image input + image output flat fee)"),
        ("Claude (LLM QC auditor)", 3.00, 15.00, "Used on-demand for QC failure diagnosis"),
    ]
    for r, row in enumerate(rows, 2):
        for i, v in enumerate(row, 1):
            ws.cell(row=r, column=i, value=v)

    ws.cell(row=7, column=1, value="PDF token accounting (Gemini rule):").font = BOLD
    ws.cell(row=8, column=1, value="• Image portion: 258 input tokens / page")
    ws.cell(row=9, column=1, value="• Extracted text: variable; assume ~350 tokens / page for textbook density")
    ws.cell(row=10, column=1, value="• Working assumption used in this model: 600 tokens / PDF page (input)")
    ws.cell(row=12, column=1, value="Token rule of thumb: 1 token ≈ 4 chars of English").font = Font(italic=True)
    autosize(ws)
    return ws


# ─── 2. UNIT COSTS ───────────────────────────────────────────────────────────
def sheet_unit_costs(wb):
    ws = wb.create_sheet("UnitCosts")
    headers = [
        "Pipeline",
        "Model",
        "Prompt tok",
        "PDF pages",
        "PDF input tok",
        "Other input tok",
        "Total input tok",
        "Output tok (mean)",
        "Retry mult",
        "Effective input",
        "Effective output",
        "Input $",
        "Output $",
        "$/call",
        "Source",
    ]
    for i, h in enumerate(headers, 1):
        ws.cell(row=1, column=i, value=h)
    style_header(ws[1])

    # (pipeline, model_input_$/1M, model_output_$/1M, prompt_tok, pdf_pages, pdf_tok_per_page, other_tok, output_tok, retry, source)
    rows = [
        ("Theory extract",       1.25, 10.00, 1334, 6,   600, 50,  6000, 1.3, "theory_extractor.py:27,107; extractor.txt"),
        ("Theory regen",         1.25, 10.00, 2698, 0,   0,   1500, 2200, 1.0, "regenerator.py:26; regenerator.txt (no PDF)"),
        ("Question extract v3",  0.30, 2.50,  2737, 7,   600, 75,  6500, 1.4, "questions_v3.py:57,60,277"),
        ("Question regen v2",    0.30, 2.50,  875,  5,   600, 30,  6000, 1.3, "questions_v2.py:49,52; ~25 calls/chapter"),
        ("Schema analyse",       1.25, 10.00, 2365, 50,  600, 50,  6000, 1.0, "schema_builder.py:28,99 (full PDF)"),
        ("Figure extract (scan)",1.25, 10.00, 500,  6,   600, 200, 4000, 1.0, "figure_extractor.py:24,168"),
        ("Figure regen (image)", 0.039, 0.0,   0,   0,   0,   0,    0,    1.0, "figure_regenerator.py:17 — flat $0.039/image"),
        ("QA verifier",          0.30, 2.50,  585,  1,   600, 50,  3000, 1.0, "verifier.py:29,37 (per page)"),
    ]

    for r, (name, in_rate, out_rate, prompt, pages, ppp, other, output, retry, src) in enumerate(rows, 2):
        ws.cell(row=r, column=1, value=name)
        ws.cell(row=r, column=2, value="2.5-pro" if in_rate == 1.25 else ("2.5-flash" if in_rate == 0.30 else "image-gen"))
        ws.cell(row=r, column=3, value=prompt)
        ws.cell(row=r, column=4, value=pages)
        ws.cell(row=r, column=5, value=pages * ppp)
        ws.cell(row=r, column=6, value=other)
        ws.cell(row=r, column=7, value=f"=C{r}+E{r}+F{r}")
        ws.cell(row=r, column=8, value=output)
        ws.cell(row=r, column=9, value=retry)
        ws.cell(row=r, column=10, value=f"=G{r}*I{r}")
        ws.cell(row=r, column=11, value=f"=H{r}*I{r}")
        if name == "Figure regen (image)":
            ws.cell(row=r, column=12, value=0)
            ws.cell(row=r, column=13, value=0)
            ws.cell(row=r, column=14, value=0.039)
        else:
            ws.cell(row=r, column=12, value=f"=J{r}*{in_rate}/1000000")
            ws.cell(row=r, column=13, value=f"=K{r}*{out_rate}/1000000")
            ws.cell(row=r, column=14, value=f"=L{r}+M{r}")
        ws.cell(row=r, column=15, value=src)

        for col in (12, 13, 14):
            ws.cell(row=r, column=col).number_format = '"$"#,##0.0000'
            ws.cell(row=r, column=col).fill = GREEN

    autosize(ws)
    ws.column_dimensions["O"].width = 50
    return ws


# ─── 3. INPUTS (yellow editable) ─────────────────────────────────────────────
def sheet_inputs(wb):
    ws = wb.create_sheet("Inputs")
    ws.cell(row=1, column=1, value="Knob").font = HDR
    ws.cell(row=1, column=2, value="Default").font = HDR
    ws.cell(row=1, column=3, value="Description").font = HDR
    style_header(ws[1])

    # Yellow cells = user-editable. Calculator references B-column by name.
    knobs = [
        ("BOOKS_PER_YEAR",                50,    "How many books processed in a year"),
        ("CHAPTERS_PER_BOOK",             12,    "Average chapters per book"),
        ("PAGES_PER_CHAPTER",             50,    "Average pages per chapter"),
        ("SECTIONS_PER_CHAPTER",          20,    "Top-level extraction units per chapter"),
        ("LEAF_SECTIONS_PER_CHAPTER",     15,    "Leaf sections (input to theory regen)"),
        ("QUESTION_UNITS_PER_CHAPTER",    15,    "Section-aligned units for question extract v3"),
        ("FIGURES_PER_CHAPTER",           6,     "Average figures per chapter"),
        ("SCANNED_PDF_FRACTION",          1.0,   "0=all digital (free), 1=all scanned (Gemini figure call)"),
        ("THEORY_REGEN_PASSES",           2,     "How many times theory regen runs per book on average"),
        ("RE_EXTRACT_RATE",               0.20,  "Fraction of sections re-extracted manually"),
        ("QUESTION_REGEN_RUNS_PER_BOOK",  1,     "How many times question regen v2 runs per book"),
        ("QA_PAGE_FRACTION",              0.6,   "Fraction of pages that the QA verifier runs on"),
    ]
    for r, (name, val, desc) in enumerate(knobs, 2):
        ws.cell(row=r, column=1, value=name).font = BOLD
        c = ws.cell(row=r, column=2, value=val)
        c.fill = YELLOW
        ws.cell(row=r, column=3, value=desc)

    # Defined names so the Calculator sheet can use them like SECTIONS_PER_CHAPTER
    from openpyxl.workbook.defined_name import DefinedName
    for r, (name, _, _) in enumerate(knobs, 2):
        dn = DefinedName(name=name, attr_text=f"Inputs!$B${r}")
        wb.defined_names[name] = dn

    ws.cell(row=len(knobs) + 4, column=1, value="EDIT YELLOW CELLS").font = Font(bold=True, color="C00000")
    ws.cell(row=len(knobs) + 5, column=1, value="The Calculator sheet recomputes automatically.")
    autosize(ws)
    return ws


def openpyxl_defined_name(ref):
    from openpyxl.workbook.defined_name import DefinedName
    return DefinedName(name="_tmp", attr_text=ref)


# ─── 4. CALCULATOR ───────────────────────────────────────────────────────────
def sheet_calculator(wb):
    ws = wb.create_sheet("Calculator")

    # Hard-link UnitCosts $/call into a static block we can reference here.
    # Row layout:
    # 1: header
    # 2-9: per-pipeline calls/chapter and $/chapter
    # 11: chapter total
    # 12: book total
    # 13: yearly total

    headers = ["Pipeline", "$/call (UnitCosts)", "Calls / chapter (formula)", "Calls / chapter", "$/chapter"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=1, column=i, value=h)
    style_header(ws[1])

    # (label, unitcosts row #, calls-formula description, calls formula in Excel)
    pipelines = [
        ("Schema analyse",        6, "1 / CHAPTERS_PER_BOOK",                     "=1/CHAPTERS_PER_BOOK"),
        ("Theory extract",        2, "SECTIONS_PER_CHAPTER",                       "=SECTIONS_PER_CHAPTER"),
        ("Section re-extracts",   2, "RE_EXTRACT_RATE × SECTIONS_PER_CHAPTER",     "=RE_EXTRACT_RATE*SECTIONS_PER_CHAPTER"),
        ("Theory regen",          3, "LEAF_SECTIONS × THEORY_REGEN_PASSES",        "=LEAF_SECTIONS_PER_CHAPTER*THEORY_REGEN_PASSES"),
        ("Question extract v3",   4, "QUESTION_UNITS_PER_CHAPTER",                  "=QUESTION_UNITS_PER_CHAPTER"),
        ("Question regen v2",     5, "25 × QUESTION_REGEN_RUNS_PER_BOOK / CHAPTERS","=25*QUESTION_REGEN_RUNS_PER_BOOK/CHAPTERS_PER_BOOK"),
        ("Figure extract (scan)", 7, "FIGURES × SCANNED_PDF_FRACTION",              "=FIGURES_PER_CHAPTER*SCANNED_PDF_FRACTION"),
        ("Figure regen (image)",  8, "FIGURES_PER_CHAPTER",                         "=FIGURES_PER_CHAPTER"),
        ("QA verifier",           9, "PAGES × QA_PAGE_FRACTION",                    "=PAGES_PER_CHAPTER*QA_PAGE_FRACTION"),
    ]

    for r, (label, uc_row, formula_text, formula) in enumerate(pipelines, 2):
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=f"=UnitCosts!N{uc_row}")
        ws.cell(row=r, column=3, value=formula_text).font = Font(italic=True, color="6F6F6F")
        ws.cell(row=r, column=4, value=formula)
        ws.cell(row=r, column=5, value=f"=B{r}*D{r}")
        ws.cell(row=r, column=2).number_format = '"$"#,##0.0000'
        ws.cell(row=r, column=5).number_format = '"$"#,##0.0000'

    # Totals
    last = 1 + len(pipelines)
    total_row = last + 2
    ws.cell(row=total_row, column=1, value="TOTAL $/CHAPTER").font = BOLD
    ws.cell(row=total_row, column=5, value=f"=SUM(E2:E{last})")
    ws.cell(row=total_row, column=5).number_format = '"$"#,##0.0000'
    ws.cell(row=total_row, column=5).fill = BLUE

    ws.cell(row=total_row + 1, column=1, value="$/BOOK").font = BOLD
    ws.cell(row=total_row + 1, column=5, value=f"=E{total_row}*CHAPTERS_PER_BOOK")
    ws.cell(row=total_row + 1, column=5).number_format = '"$"#,##0.00'
    ws.cell(row=total_row + 1, column=5).fill = BLUE

    ws.cell(row=total_row + 2, column=1, value="$/YEAR").font = BOLD
    ws.cell(row=total_row + 2, column=5, value=f"=E{total_row+1}*BOOKS_PER_YEAR")
    ws.cell(row=total_row + 2, column=5).number_format = '"$"#,##0.00'
    ws.cell(row=total_row + 2, column=5).fill = GREEN

    ws.cell(row=total_row + 4, column=1, value="Edit knobs in the Inputs sheet to recalculate.").font = Font(italic=True)

    autosize(ws)
    return ws


# ─── 5. SCENARIOS ────────────────────────────────────────────────────────────
def sheet_scenarios(wb):
    ws = wb.create_sheet("Scenarios")
    headers = ["Scenario", "Books / yr", "Chapters / yr", "$/chapter", "$/year"]
    for i, h in enumerate(headers, 1):
        ws.cell(row=1, column=i, value=h)
    style_header(ws[1])

    chap_cost = 5.13  # realistic-with-reruns from §4 of cost analysis doc
    rows = [
        ("Pilot",            5,    60),
        ("Steady-state",     50,   600),
        ("Scale",            250,  3000),
        ("Aggressive growth",1000, 12000),
    ]
    for r, (label, books, chapters) in enumerate(rows, 2):
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=books)
        ws.cell(row=r, column=3, value=chapters)
        ws.cell(row=r, column=4, value=chap_cost)
        ws.cell(row=r, column=4).number_format = '"$"#,##0.00'
        ws.cell(row=r, column=5, value=f"=C{r}*D{r}")
        ws.cell(row=r, column=5).number_format = '"$"#,##0.00'

    ws.cell(row=8, column=1, value="$/chapter assumes 2 regen passes, 20% re-extract rate, all-scanned figures.").font = Font(italic=True)
    autosize(ws)
    return ws


# ─── 6. NOTES ────────────────────────────────────────────────────────────────
def sheet_notes(wb):
    ws = wb.create_sheet("Notes")
    notes = [
        "PIPELINE COST MODEL — keen-curran",
        "Generated: 2026-05-05",
        "",
        "How to use:",
        "  1. Open Inputs sheet, edit the YELLOW cells with your own assumptions.",
        "  2. Calculator sheet shows $/chapter, $/book, $/year live.",
        "  3. Scenarios sheet has pre-baked Pilot / Steady-state / Scale / Aggressive numbers.",
        "  4. UnitCosts sheet shows the per-call cost decomposition (token math).",
        "  5. Pricing sheet has the Gemini rate card.",
        "",
        "Models used in the codebase (verified at HEAD):",
        "  • Theory extract       → gemini-2.5-pro     (theory_extractor.py:27)",
        "  • Theory regen         → gemini-2.5-pro     (regenerator.py via gemini_client.py)",
        "  • Question extract v3  → gemini-2.5-flash   (questions_v3.py:57)",
        "  • Question regen v2    → gemini-2.5-flash   (questions_v2.py:49)",
        "  • Schema analyse       → gemini-2.5-pro     (schema_builder.py:28)",
        "  • Figure extract       → gemini-3.0-pro     (figure_extractor.py:24)",
        "  • Figure regen         → gemini-2.0-flash-image-gen (figure_regenerator.py:17)",
        "  • QA verifier          → gemini-2.5-flash   (verifier.py:29)",
        "  • LLM QC auditor       → Claude              (qc/llm.py:38)",
        "",
        "Caveats:",
        "  • Pricing is Google list price. Enterprise contracts can negotiate down.",
        "  • Output token estimates are inferred from prompt + sample inspection,",
        "    NOT measured from response logs. Adding a logger in gemini_client.py",
        "    would tighten this analysis.",
        "  • Gemini context caching not modelled (could cut input cost by 75% on",
        "    repeated context — useful for re-extracts of the same chapter).",
        "  • Figure regen uses preview-tier image gen; pricing is provisional ±50%.",
        "  • Human-review labour is NOT modelled — only LLM API spend.",
        "",
        "Highest-leverage cost reductions (ranked by $/yr saved at steady-state):",
        "  1. First-shot theory extract on Flash, escalate to Pro on QC fail",
        "     → ~$620/yr saved at 600 chapters/yr; scales linearly with volume.",
        "  2. Sunset question regen v2, use v3 only.",
        "     → ~$315/yr saved + simpler operationally.",
        "  3. Cache schema analyse output by content-hash.",
        "     → ~5% of Pro spend.",
        "  4. Tighten output cap on theory regen (16K → 6K).",
        "     → bounds tail risk; no average savings.",
        "",
        "See COST_ANALYSIS.md for the full prose write-up and methodology.",
    ]
    for i, line in enumerate(notes, 1):
        c = ws.cell(row=i, column=1, value=line)
        if line.endswith(":") or i == 1:
            c.font = BOLD
    ws.column_dimensions["A"].width = 110
    return ws


def main():
    wb = Workbook()
    wb.remove(wb.active)
    sheet_pricing(wb)
    sheet_unit_costs(wb)
    sheet_inputs(wb)
    sheet_calculator(wb)
    sheet_scenarios(wb)
    sheet_notes(wb)
    wb.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
