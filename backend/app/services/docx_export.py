"""Native python-docx exporter for theory + questions + regen.

Replaces the previous pandoc-based docx pipeline so we can control:
  - exact heading styles, font sizes, paragraph spacing
  - no-duplicate-headings invariant (a `last_heading` tracker silently
    drops adjacent identical headings — covers case/punct differences)
  - per-question label format (Question 1: / Options: / Solution: with
    bold colon-suffixed labels, hanging indent so wrapped lines align)
  - empty fields are SKIPPED entirely (no "Answer:" stub when there's
    no answer key)
  - explicit gaps between sections / questions for predictable layout

Pandoc is no longer required for docx export. (Markdown export still
uses the existing builders in the API routers.)

Public entry points:
  - build_questions_docx(title, sections, section_only=None) -> bytes
  - build_regen_docx(regen_label, custom_instructions, sections, ...) -> bytes
  - build_theory_docx(book_title, sections) -> bytes
"""

from __future__ import annotations

import io
import re
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------

NAVY = RGBColor(0x1A, 0x36, 0x6E)
KEYPOINT_ORANGE = RGBColor(0xC8, 0x7C, 0x00)
MUTED = RGBColor(0x66, 0x66, 0x66)

# Inline `$...$` and display `$$...$$` LaTeX chunks. python-docx can't
# render OMML equations directly without significant effort, so for v1
# we wrap math chunks in italic runs — preserves placement and reads
# correctly. (Future: write OMML XML directly for native equations.)
MATH_RE = re.compile(r"\$\$?(.+?)\$\$?", re.DOTALL)

# MCQ option marker inside raw_text (case-insensitive, A-D or 1-4 inside
# parens). Used to (a) strip options off the stem so the Question line
# doesn't repeat them, and (b) lay them out cleanly under Options:.
OPTION_RE = re.compile(r"\(([A-Da-d1-4])\)")

# Figure placeholders embedded by the extractor: {{fig: <label> — <caption>}}
FIG_RE = re.compile(r"\{\{\s*fig\s*:\s*([^}]+?)\s*\}\}", re.IGNORECASE)

# Markdown-style tables embedded in raw_text — Gemini emits these for
# value tables (x/y), match-the-column, etc. We detect 2+ consecutive
# lines starting with `|`, where line 2 is a `|---|---|` separator,
# and emit a proper Word table.
_PIPE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_PIPE_DIV = re.compile(r"^\s*\|[\s:|\-]+\|\s*$")

# Common LaTeX commands that leak out of `$...$` chunks because Gemini
# sometimes uses them in prose. Map to Unicode so the docx reads
# naturally instead of showing the raw backslash sequence.
_LATEX_TO_UNICODE: list[tuple[str, str]] = [
    # Order matters — longer prefixes first to avoid `\rightarrow`
    # being half-substituted by `\right`.
    (r"\Leftrightarrow", "⇔"),
    (r"\Rightarrow", "⇒"),
    (r"\Leftarrow", "⇐"),
    (r"\rightarrow", "→"),
    (r"\leftarrow", "←"),
    (r"\therefore", "∴"),
    (r"\because", "∵"),
    (r"\approx", "≈"),
    (r"\equiv", "≡"),
    (r"\infty", "∞"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\div", "÷"),
    (r"\pm", "±"),
    (r"\mp", "∓"),
    (r"\leq", "≤"),
    (r"\geq", "≥"),
    (r"\neq", "≠"),
    (r"\ne", "≠"),
    (r"\to", "→"),
    # Set theory
    (r"\cup", "∪"),
    (r"\cap", "∩"),
    (r"\subseteq", "⊆"),
    (r"\supseteq", "⊇"),
    (r"\subset", "⊂"),
    (r"\supset", "⊃"),
    (r"\notin", "∉"),
    (r"\in", "∈"),
    (r"\emptyset", "∅"),
    (r"\varnothing", "∅"),
    # Logic / quantifiers
    (r"\forall", "∀"),
    (r"\exists", "∃"),
    (r"\lnot", "¬"),
    (r"\neg", "¬"),
    (r"\land", "∧"),
    (r"\lor", "∨"),
    # More misc
    (r"\partial", "∂"),
    (r"\nabla", "∇"),
    (r"\sum", "∑"),
    (r"\prod", "∏"),
    (r"\int", "∫"),
    (r"\oint", "∮"),
    (r"\ldots", "…"),
    (r"\cdots", "⋯"),
    # Greek letters most commonly seen in math prose
    (r"\alpha", "α"), (r"\beta", "β"), (r"\gamma", "γ"),
    (r"\delta", "δ"), (r"\epsilon", "ε"), (r"\theta", "θ"),
    (r"\lambda", "λ"), (r"\mu", "μ"), (r"\pi", "π"),
    (r"\sigma", "σ"), (r"\phi", "φ"), (r"\omega", "ω"),
    (r"\Delta", "Δ"), (r"\Theta", "Θ"), (r"\Lambda", "Λ"),
    (r"\Sigma", "Σ"), (r"\Phi", "Φ"), (r"\Omega", "Ω"),
    # Common spacing / punctuation
    (r"\,", " "), (r"\;", " "), (r"\:", " "), (r"\!", ""),
]

# Single-char super/subscripts: x^2, x^{n}, x_1, x_{i}. Only handles
# single digits/letters that have Unicode super/sub forms. Multi-char
# expressions like ^{n+1} are left as-is.
_SUP_MAP = str.maketrans({
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ",
})
_SUB_MAP = str.maketrans({
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "n": "ₙ", "i": "ᵢ", "a": "ₐ", "e": "ₑ", "o": "ₒ", "x": "ₓ",
})


def _normalise_math_prose(text: str) -> str:
    """Substitute common LaTeX commands → Unicode and basic
    super/subscripts so the rendered docx reads naturally. Applied to
    text after stripping `$...$` math wrappers (math chunks go through
    this too — the rendered italic stays, just with proper symbols)."""
    # Structural LaTeX commands that don't have a single Unicode codepoint
    # but can be rewritten plainly. Done BEFORE the simple substitutions so
    # any greek letters inside survive.
    #   \frac{a}{b}        → (a)/(b)         (parens added when needed)
    #   \dfrac, \tfrac     → same as \frac
    #   \sqrt[n]{x}        → ⁿ√(x)
    #   \sqrt{x}           → √(x)
    #   \text{x}, \mathrm{x}, \mathbf{x}, \operatorname{x} → x
    #   \left, \right      → dropped (display-size hints only)
    def _drop_text(m: re.Match[str]) -> str:
        return m.group(1)

    text = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}", _drop_text, text)
    text = re.sub(r"\\left\b", "", text)
    text = re.sub(r"\\right\b", "", text)

    def _frac(m: re.Match[str]) -> str:
        num = m.group(1).strip()
        den = m.group(2).strip()
        wrap = lambda s: s if len(s) == 1 and s.isalnum() else f"({s})"
        return f"{wrap(num)}/{wrap(den)}"

    def _sqrt(m: re.Match[str]) -> str:
        return f"√({m.group(1)})"

    def _nthroot(m: re.Match[str]) -> str:
        n = m.group(1)
        body = m.group(2)
        sup = {"2": "²", "3": "³", "4": "⁴", "5": "⁵"}.get(n.strip(), n.strip())
        return f"{sup}√({body})"

    # super/subscript helpers (defined here so they can run INSIDE the
    # fixpoint loop below — running them first unblocks sqrt/frac when
    # the args contain `x^{2}` style braces).
    def _sup_inner(m: re.Match[str]) -> str:
        body = m.group(1) or m.group(2) or ""
        if body and all(ord(ch) in _SUP_MAP for ch in body):
            return body.translate(_SUP_MAP)
        return m.group(0)

    def _sub_inner(m: re.Match[str]) -> str:
        body = m.group(1) or m.group(2) or ""
        if body and all(ord(ch) in _SUB_MAP for ch in body):
            return body.translate(_SUB_MAP)
        return m.group(0)

    # Single fixpoint loop covering super/subscripts + sqrt + frac.
    # ORDER MATTERS: super/subscripts must run FIRST so `\sqrt{b^{2}-4ac}`
    # becomes `\sqrt{b²-4ac}` (no inner braces) and the sqrt regex
    # (`[^{}]*`) can then match it. Same logic for frac with `\sqrt`
    # inside. Iterating to fixpoint handles arbitrary nesting.
    for _ in range(8):
        prev = text
        # 1. single-char super/subscripts (e.g. b^{2} → b²)
        text = re.sub(
            r"\^\{([^{}]{1,4})\}|\^([0-9a-zA-Z+\-=()])", _sup_inner, text
        )
        text = re.sub(
            r"_\{([^{}]{1,4})\}|_([0-9a-zA-Z+\-=()])", _sub_inner, text
        )
        # 2. roots
        text = re.sub(r"\\sqrt\[([^\]]+)\]\{([^{}]*)\}", _nthroot, text)
        text = re.sub(r"\\sqrt\{([^{}]*)\}", _sqrt, text)
        # 3. fractions
        text = re.sub(r"\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}", _frac, text)
        if text == prev:
            break

    for tex, uni in _LATEX_TO_UNICODE:
        text = text.replace(tex, uni)

    # super/subscript handling is now done INSIDE the fixpoint loop above
    # — needs to run before sqrt/frac so inner braces from `x^{2}` don't
    # block the outer regexes. Keeping a second pass here is redundant.

    # Final XML safety pass — python-docx rejects control characters that
    # XML 1.0 disallows. JSON-escape collisions in the source data leave
    # behind \v / \f / \b / \x01-\x08 / \x0E-\x1F etc. These are usually
    # the tail of stripped LaTeX commands (e.g. `\vec` → `\x0B + ec`).
    # Strip them so the docx renders cleanly.
    return _sanitize_xml(text)


# XML 1.0 valid chars: \t \n \r and >= \x20 (except surrogates / FFFE / FFFF)
_XML_INVALID_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)

def _sanitize_xml(text: str) -> str:
    """Strip characters that python-docx / XML 1.0 can't accept. Replaces
    them with a single space so the surrounding text stays readable."""
    if not text:
        return text
    return _XML_INVALID_RE.sub(" ", text)


def _extract_tables_from_text(text: str):
    """Yield ('text', str) or ('table', (headers, rows)) tuples.

    Detects a Markdown table as:
        | header1 | header2 | ...|
        |---------|---------|----|
        | r1c1    | r1c2    | ...|
        | r2c1    | r2c2    | ...|
    Anything between tables (or before/after) is yielded as 'text'.
    """
    lines = text.split("\n")
    i = 0
    buf: list[str] = []
    while i < len(lines):
        line = lines[i]
        # Detect start: this line is a pipe row AND next line is a divider
        if (
            _PIPE_LINE.match(line)
            and i + 1 < len(lines)
            and _PIPE_DIV.match(lines[i + 1])
        ):
            # Flush prose buffer first
            if buf:
                yield ("text", "\n".join(buf).strip("\n"))
                buf = []
            # Parse headers (split on |, trim, drop empty leading/trailing)
            def _cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            headers = _cells(line)
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and _PIPE_LINE.match(lines[j]):
                rows.append(_cells(lines[j]))
                j += 1
            yield ("table", (headers, rows))
            i = j
            continue
        buf.append(line)
        i += 1
    if buf:
        yield ("text", "\n".join(buf).strip("\n"))


def _norm(s: str) -> str:
    """Loose comparison key for heading de-dup — lowercased, alphanumerics
    only. Catches case and punctuation differences."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _set_default_font(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10)


def _render_inline(p, text: str) -> None:
    """Add inline runs to paragraph p. Math chunks render italic;
    figure placeholders render as muted bracketed callouts."""
    # Defensive XML safety — strip any control chars before passing
    # anything to python-docx (which raises ValueError on them). Also
    # caught by _normalise_math_prose's tail, but inline paths can
    # bypass that (e.g. table cells) so we do it here too.
    text = _sanitize_xml(text or "")
    # First substitute figure placeholders → bracketed callouts (still
    # processed inline so they stay in flow with surrounding prose).
    parts: list[tuple[str, str]] = []  # (kind, content) kind in {text, math, fig}
    cursor = 0
    # Combined scan: math has priority over figure (math placeholders are
    # syntactically tighter; figures live in plain prose).
    spans: list[tuple[int, int, str, str]] = []  # (start, end, kind, payload)
    for m in MATH_RE.finditer(text):
        spans.append((m.start(), m.end(), "math", m.group(1)))
    for m in FIG_RE.finditer(text):
        # Skip if inside a math span
        if any(s <= m.start() < e for s, e, _, _ in spans):
            continue
        spans.append((m.start(), m.end(), "fig", m.group(1).strip()))
    spans.sort()
    for s, e, kind, payload in spans:
        if s > cursor:
            parts.append(("text", text[cursor:s]))
        parts.append((kind, payload))
        cursor = e
    if cursor < len(text):
        parts.append(("text", text[cursor:]))

    for kind, payload in parts:
        if kind == "text":
            r = p.add_run(_normalise_math_prose(payload))
            r.font.size = Pt(10)
        elif kind == "math":
            r = p.add_run(_normalise_math_prose(payload))
            r.italic = True
            r.font.size = Pt(10)
        elif kind == "fig":
            # Strip the placeholder silently. The actual figure is rendered
            # separately below the question text via the embedded_figures
            # pipeline, so an inline "[Figure: ...]" muted callout here is
            # redundant. Keeping the callout caused visible duplication
            # ("[Figure: A, B, C, D - (unlabelled diagram)]" + image of
            # the same figure rendered right below).
            pass


def _strip_options_from_stem(raw_text: str) -> str:
    """Return the question text with the trailing option block removed."""
    m = OPTION_RE.search(raw_text)
    if not m:
        return raw_text.strip()
    return raw_text[: m.start()].strip()


def _parse_options(raw_text: str) -> list[tuple[str, str]]:
    """Split out lettered options from raw_text. Returns [(letter, body)]."""
    parts = OPTION_RE.split(raw_text)
    if len(parts) < 5:
        return []
    opts: list[tuple[str, str]] = []
    # parts is [stem, 'A', text_A, 'B', text_B, …]; iterate over pairs
    for i in range(1, len(parts) - 1, 2):
        letter = parts[i].upper()
        body = parts[i + 1].strip()
        # Strip an orphan opening paren left from the next letter
        body = body.rstrip("(").rstrip().rstrip(",").strip()
        opts.append((letter, body))
    return opts


def _split_answer_from_solution(sol: str) -> tuple[str, str]:
    """If the printed solution begins with 'Ans.' / 'Answer:' / '(B)',
    pull that first line off as the Answer; rest is the Solution."""
    sol = (sol or "").strip()
    if not sol:
        return "", ""
    first_line = sol.split("\n", 1)[0].strip()
    if re.match(r"^(Ans\.?\:?|Answer\:?\.?|\([A-Da-d]\))", first_line, re.I):
        rest = sol[len(first_line):].lstrip("\n .:")
        return first_line, rest
    return "", sol


# ---------------------------------------------------------------------------
# Heading + spacing helpers (with de-dup guard)
# ---------------------------------------------------------------------------

class _DocBuilder:
    """Holds a Document plus a last-heading tracker so de-dup works
    across the whole document."""

    def __init__(self) -> None:
        self.doc = Document()
        _set_default_font(self.doc)
        self._last_heading = ""

    def title(self, text: str) -> None:
        text = _sanitize_xml(text or "")
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(10)
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(14)
        self.doc.add_paragraph()  # one-line breather

    def group_header(self, text: str) -> None:
        """Big divider — WORKED EXAMPLES, JEE SPECIAL WING, etc."""
        text = _sanitize_xml(text or "")
        if _norm(text) == _norm(self._last_heading):
            return
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(20)
        p.paragraph_format.space_after = Pt(10)
        p.paragraph_format.keep_with_next = True
        r = p.add_run(text.upper())
        r.bold = True
        r.font.size = Pt(14)
        r.font.color.rgb = NAVY
        self._last_heading = text

    def section_heading(self, text: str) -> None:
        """Section-level heading — EXAMPLE 4.7, Introduction, etc."""
        text = _sanitize_xml(text or "")
        if _norm(text) == _norm(self._last_heading):
            return
        h = self.doc.add_paragraph()
        h.paragraph_format.space_before = Pt(14)
        h.paragraph_format.space_after = Pt(6)
        h.paragraph_format.keep_with_next = True
        r = h.add_run(text)
        r.bold = True
        r.font.size = Pt(13)
        r.font.color.rgb = NAVY
        self._last_heading = text

    def sub_heading(self, text: str) -> None:
        text = _sanitize_xml(text or "")
        if _norm(text) == _norm(self._last_heading):
            return
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.keep_with_next = True
        r = p.add_run(text)
        r.bold = True
        r.font.size = Pt(11)
        self._last_heading = text

    def paragraph(self, text: str, *, space_after_pt: int = 3,
                  left_indent_cm: float = 0.0) -> None:
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(space_after_pt)
        if left_indent_cm:
            p.paragraph_format.left_indent = Cm(left_indent_cm)
        _render_inline(p, (text or "").strip())

    def equation(self, text: str) -> None:
        text = _sanitize_xml(text or "")
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(4)
        r = p.add_run(text.strip())
        r.italic = True
        r.font.size = Pt(10)

    def labeled(self, label: str, body: str) -> None:
        """A 'Label: body' paragraph with hanging indent so wrapped lines
        align under body. Used for Question/Options/Answer/Solution.

        If the body contains a Markdown-style table (`| h1 | h2 |` +
        `|---|---|` separator), the table is rendered as a real Word
        table directly below the label paragraph instead of being
        dumped as raw pipe-text.
        """
        chunks = list(_extract_tables_from_text(body.strip()))
        # Label paragraph always exists; first 'text' chunk (if any)
        # becomes the inline body next to the bold label.
        p = self.doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(2.4)
        p.paragraph_format.first_line_indent = Cm(-2.4)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.space_before = Pt(0)
        r = p.add_run(f"{label}:  ")
        r.bold = True
        r.font.size = Pt(10)
        first_text_consumed = False
        for kind, payload in chunks:
            if kind == "text":
                if not payload.strip():
                    continue
                if not first_text_consumed:
                    _render_inline(p, payload)
                    first_text_consumed = True
                else:
                    cp = self.doc.add_paragraph()
                    cp.paragraph_format.left_indent = Cm(2.4)
                    cp.paragraph_format.space_after = Pt(2)
                    _render_inline(cp, payload)
            else:
                headers, rows = payload
                self.table(headers, rows)

    def options(self, opts: list[tuple[str, str]]) -> None:
        """Render MCQ options. Inline if short, stacked otherwise.

        Each option body goes through `_render_inline` so embedded
        `$...$` math and bare LaTeX commands render the same way
        as Question/Solution bodies — no raw `\\Rightarrow` or `x^2`.
        """
        if not opts:
            return
        inline = all(len(b) <= 28 for _, b in opts)
        p = self.doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(2.4)
        p.paragraph_format.first_line_indent = Cm(-2.4)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run("Options:  ")
        r.bold = True
        r.font.size = Pt(10)
        if inline:
            # Build a single string with separators, then route through
            # _render_inline so each option's `$...$` math + LaTeX
            # commands get the same Unicode treatment.
            joined = "    ".join(f"({L}) {b}" for L, b in opts)
            _render_inline(p, joined)
        else:
            # First option on the label line, rest stacked
            for idx, (L, b) in enumerate(opts):
                if idx == 0:
                    _render_inline(p, f"({L}) {b}")
                else:
                    p2 = self.doc.add_paragraph()
                    p2.paragraph_format.left_indent = Cm(2.4)
                    p2.paragraph_format.space_after = Pt(1)
                    _render_inline(p2, f"({L}) {b}")

    def keypoint(self, body: str) -> None:
        p = self.doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.6)
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(4)
        r = p.add_run("Key Point:  ")
        r.bold = True
        r.font.size = Pt(10)
        r.font.color.rgb = KEYPOINT_ORANGE
        _render_inline(p, body)

    def bullets(self, items: list[str]) -> None:
        for item in items:
            p = self.doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_after = Pt(2)
            _render_inline(p, item)

    def table(self, headers: list[str], rows: list[list[str]]) -> None:
        if not headers and not rows:
            return
        cols = max(len(headers), max((len(r) for r in rows), default=0))
        if cols == 0:
            return
        tbl = self.doc.add_table(rows=(1 if headers else 0) + len(rows), cols=cols)
        try:
            tbl.style = "Light Grid Accent 1"
        except KeyError:  # built-in style missing on some Word versions
            pass
        row_offset = 0
        if headers:
            for i, h in enumerate(headers):
                cell = tbl.rows[0].cells[i]
                cell.text = ""
                run = cell.paragraphs[0].add_run(_normalise_math_prose(h))
                run.bold = True
                run.font.size = Pt(10)
            row_offset = 1
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                if ci >= cols:
                    continue
                cell = tbl.rows[ri + row_offset].cells[ci]
                cell.text = ""
                run = cell.paragraphs[0].add_run(_normalise_math_prose(str(val)))
                run.font.size = Pt(10)

    def question_gap(self) -> None:
        """Visual breather between two questions in the same group."""
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(6)

    def section_gap(self) -> None:
        """Bigger breather between adjacent sections."""
        p = self.doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(10)

    def figure_callout(self, label: str, caption: str = "") -> None:
        label = _sanitize_xml(label or "")
        caption = _sanitize_xml(caption or "")
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        text = f"[Figure: {label}"
        if caption:
            text += f" — {caption}"
        text += "]"
        r = p.add_run(text)
        r.italic = True
        r.font.size = Pt(9)
        r.font.color.rgb = MUTED

    def image(self, image_bytes: bytes, label: str = "", caption: str = "",
              max_width_inches: float = 5.0) -> None:
        """Embed a figure binary in the doc. Centered, capped at
        ``max_width_inches`` so big images don't overflow the page.
        Label + caption render as a centred italic figcaption below."""
        if not image_bytes:
            return
        label = _sanitize_xml(label or "")
        caption = _sanitize_xml(caption or "")
        from docx.shared import Inches
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run()
        try:
            run.add_picture(io.BytesIO(image_bytes), width=Inches(max_width_inches))
        except Exception:
            # Fall back to text callout if python-docx fails to read the bytes
            self.figure_callout(label or "image", caption)
            return
        if label or caption:
            cap = self.doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_after = Pt(6)
            if label:
                r1 = cap.add_run(label)
                r1.bold = True
                r1.italic = True
                r1.font.size = Pt(9)
                r1.font.color.rgb = MUTED
            if label and caption:
                r2 = cap.add_run(" — ")
                r2.italic = True
                r2.font.size = Pt(9)
                r2.font.color.rgb = MUTED
            if caption:
                r3 = cap.add_run(caption)
                r3.italic = True
                r3.font.size = Pt(9)
                r3.font.color.rgb = MUTED

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Questions / Regen rendering
# ---------------------------------------------------------------------------

def _render_question(b: _DocBuilder, q: dict, *, label: str | None = None) -> None:
    """Render a single question as Question/Options/Answer/Solution."""
    raw = q.get("raw_text") or ""
    stem = _strip_options_from_stem(raw)
    has_options = bool(q.get("has_options"))

    # Question (with optional numeric label like "Question 1")
    question_label = "Question" if not label else f"Question {label}"
    b.labeled(question_label, stem)

    # Options — only when raw_text contains them
    if has_options:
        opts = _parse_options(raw)
        b.options(opts)

    # Answer / Solution — split if printed solution starts with "Ans."
    sol_text = q.get("solution_text") or ""
    answer, solution = _split_answer_from_solution(sol_text)
    if answer:
        b.labeled("Answer", answer)
    if solution:
        b.labeled("Solution", solution)
    b.question_gap()


def build_questions_docx(
    title: str,
    sections: list[dict[str, Any]],
    *,
    section_only: str | None = None,
) -> bytes:
    """Build the bank-export Word doc.

    `sections` shape: list of {section_ref, section_title, questions: [...]}.
    When `section_only` is set, restrict to that section_ref and skip the
    cover title (single-section export).
    """
    b = _DocBuilder()
    if not section_only:
        b.title(f"{title} — Question Bank")

    # Partition sections into "single example" vs "grouped" buckets so
    # the doc reads naturally: WORKED EXAMPLES heading then each example,
    # then any non-example sections under their own heading.
    example_secs: list[dict] = []
    other_secs: list[dict] = []
    for s in sections:
        if section_only and s.get("section_ref") != section_only:
            continue
        title_l = (s.get("section_title") or "").lower()
        if "example" in title_l:
            example_secs.append(s)
        else:
            other_secs.append(s)

    if example_secs:
        b.group_header("Worked Examples")
        for s in example_secs:
            qs = s.get("questions") or []
            if not qs:
                continue
            b.section_heading(s.get("section_title") or s.get("section_ref") or "")
            for q in qs:
                _render_question(b, q)
            b.section_gap()

    for s in other_secs:
        qs = s.get("questions") or []
        if not qs:
            continue
        b.group_header(s.get("section_title") or s.get("section_ref") or "")
        for idx, q in enumerate(qs, start=1):
            qnum = (q.get("question_number") or "").strip() or str(idx)
            _render_question(b, q, label=qnum)
        b.section_gap()

    return b.to_bytes()


def build_regen_docx(
    label_text: str,
    custom_instructions: str | None,
    sections: list[dict[str, Any]],
    *,
    section_only: str | None = None,
) -> bytes:
    """Build the regen-export Word doc — same layout as questions, plus
    a header note for custom instructions when present."""
    b = _DocBuilder()
    if not section_only:
        b.title(f"{label_text} — Regenerated Questions")
        if custom_instructions:
            p = b.doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
            r = p.add_run(f"⚡ Custom instructions: {custom_instructions}")
            r.italic = True
            r.font.size = Pt(10)
            r.font.color.rgb = MUTED

    # Re-use the questions partitioning logic
    example_secs: list[dict] = []
    other_secs: list[dict] = []
    for s in sections:
        if section_only and s.get("section_ref") != section_only:
            continue
        title_l = (s.get("section_title") or "").lower()
        if "example" in title_l:
            example_secs.append(s)
        else:
            other_secs.append(s)

    if example_secs:
        b.group_header("Worked Examples (Regenerated)")
        for s in example_secs:
            qs = s.get("questions") or []
            if not qs:
                continue
            b.section_heading(s.get("section_title") or s.get("section_ref") or "")
            for q in qs:
                _render_question(b, q)
            b.section_gap()

    for s in other_secs:
        qs = s.get("questions") or []
        if not qs:
            continue
        b.group_header(s.get("section_title") or s.get("section_ref") or "")
        for idx, q in enumerate(qs, start=1):
            qnum = (q.get("question_number") or "").strip() or str(idx)
            _render_question(b, q, label=qnum)
        b.section_gap()

    return b.to_bytes()


# ---------------------------------------------------------------------------
# Theory rendering
# ---------------------------------------------------------------------------

def _render_theory_block(b: _DocBuilder, block: dict, section_title_key: str,
                         last_sub: list[str]) -> None:
    t = block.get("t")
    c = block.get("c", "")
    if t in ("h2", "h3", "h4"):
        if _norm(c) == section_title_key:
            return  # adjacent duplicate of section heading
        if _norm(c) == _norm(last_sub[0]):
            return
        b.sub_heading(c)
        last_sub[0] = c
    elif t == "p":
        b.paragraph(c)
    elif t == "eq":
        b.equation(c)
    elif t == "example":
        # Worked example inside theory body
        lbl = (block.get("label") or "Example").strip()
        prob = (block.get("prob") or "").strip()
        sol = (block.get("sol") or "").strip()
        b.sub_heading(lbl)
        if prob:
            b.paragraph(prob, left_indent_cm=0.4)
        if sol:
            p = b.doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            p.paragraph_format.space_after = Pt(4)
            r = p.add_run("Solution:  ")
            r.bold = True
            r.font.size = Pt(10)
            _render_inline(p, sol)
    elif t in ("kp", "remember", "keypoint"):
        b.keypoint(c)
    elif t == "list":
        items = block.get("items") or []
        b.bullets(items)
    elif t == "table":
        b.table(block.get("headers") or [], block.get("rows") or [])
    elif t == "figure":
        b.figure_callout(block.get("label") or "unlabelled",
                         block.get("caption") or "")
    else:
        if c:
            b.paragraph(c)


def build_theory_docx(
    book_title: str,
    sections: list[dict[str, Any]],
) -> bytes:
    """Build the theory Word doc.

    `sections` shape: list of {section_id, title, blocks: [...]}.
    """
    b = _DocBuilder()
    b.title(book_title)
    for s in sections:
        title = s.get("title") or s.get("section_id") or ""
        blocks = s.get("blocks") or []
        if not blocks:
            continue
        b.section_heading(title)
        title_key = _norm(title)
        last_sub = [""]
        for blk in blocks:
            _render_theory_block(b, blk, title_key, last_sub)
        b.section_gap()
    return b.to_bytes()


# ---------------------------------------------------------------------------
# Final Draft rendering — Phase 3 Composer output
# ---------------------------------------------------------------------------
# Walks the FinalDraft.items ordered list and routes each item to the
# right _DocBuilder method, reusing the polished formatting that
# build_theory_docx + build_questions_docx use:
#   - Section headings styled consistently
#   - Theory blocks (p/h3/eq/def/kp/list/table/example) routed through
#     _render_theory_block so paragraphs, equations, key-points, lists,
#     tables, and worked-examples all look identical to the standalone
#     theory export.
#   - Questions routed through _render_question so MCQs split options,
#     solutions get the right typography, embedded figures embed as
#     images via the new _DocBuilder.image method.
#   - Figures embed binaries (not text callouts) at section level.
#   - Custom text blocks render as plain paragraphs.

def _render_paragraph_with_tables(b: _DocBuilder, text: str) -> None:
    """Render a paragraph that MAY contain embedded pipe-tables. Splits
    on the table markers via `_extract_tables_from_text` so each table
    becomes a real Word table; surrounding prose becomes plain paragraphs.
    Used for `p` blocks (which often carry OCR'd value-tables inline)."""
    if not text or not text.strip():
        return
    chunks = list(_extract_tables_from_text(text.strip()))
    if not any(k == "table" for k, _ in chunks):
        b.paragraph(text.strip())
        return
    for kind, payload in chunks:
        if kind == "text":
            if payload.strip():
                b.paragraph(payload.strip())
        else:  # table
            headers, rows = payload
            b.table(headers, rows)


def _render_block_item(b: _DocBuilder, block: dict[str, Any],
                       title_key: str, last_sub: list[str]) -> None:
    """Like _render_theory_block but tolerates the v2 block types the
    Composer might receive (kp / def / *_ref). Also extracts inline
    pipe-tables from `p` blocks so they render as real Word tables."""
    t = block.get("t")
    if t == "p":
        _render_paragraph_with_tables(b, block.get("c") or "")
        return
    if t == "def":
        # Definition: term in bold, body underneath
        term = (block.get("term") or "").strip()
        body = (block.get("c") or "").strip()
        if term:
            b.sub_heading(f"Definition — {term}")
        if body:
            b.paragraph(body, left_indent_cm=0.4)
        return
    if t in ("example_ref", "exercise_ref", "question_ref"):
        # A3 fix — SUPPRESS unmatched chips in the final DOCX. Matched chips
        # have already been removed from the blocks list by
        # `_merge_chips_with_questions` (their target question gets inlined
        # at the chip's position). Any chip surviving to this point is
        # either orphan (no matching question anywhere) or garbage bled
        # into a non-question section by theory over-extraction. Emitting
        # "→ Exercise: N" placeholders for those clutters the document
        # (e.g. Shortcuts polluted with 74 empty chips). The reader can
        # always inspect the source PDF if a question is genuinely
        # missing. To restore the old "spot me" rendering, look up
        # commit 58c5e1e in git history.
        return
    # Delegate to the existing theory renderer for the standard types
    _render_theory_block(b, block, title_key, last_sub)


def _render_custom_text(b: _DocBuilder, content: str) -> None:
    """Render a user-added custom_text item. Splits on blank lines so
    each paragraph is its own block. Inline markdown ($math$, bold) is
    handled by _render_inline which mirrors what paragraphs use."""
    if not content:
        return
    for chunk in re.split(r"\n\s*\n", content.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        p = b.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(6)
        _render_inline(p, chunk)


def build_final_draft_docx(
    book_title: str,
    items: list[dict[str, Any]],
    figure_bytes_map: dict[str, bytes],
) -> bytes:
    """Walk the FinalDraft items list and render a polished Word doc.

    ``figure_bytes_map`` is keyed by figure_id (str). The caller is
    responsible for materialising the image bytes (variant=regen if
    approved, else original) before invoking this function so this
    module stays sync-only.
    """
    b = _DocBuilder()
    b.title(book_title or "Final Draft")

    current_section_key = ""
    last_sub = [""]
    for it in items:
        t = it.get("type")
        if t == "section_heading":
            title = (it.get("title") or it.get("section_id") or "").strip()
            # Use section_heading for ALL section items so the typography
            # matches the theory/questions exports (13pt NAVY, normal-case,
            # consistent spacing). The composer's level field is positional
            # info; the visual style stays uniform — Word handles outline
            # hierarchy via paragraph styles, not size escalation.
            b.section_heading(title)
            current_section_key = _norm(title)
            last_sub = [""]
            continue
        if t == "block":
            _render_block_item(b, it.get("block") or {}, current_section_key, last_sub)
            continue
        if t == "figure":
            f = it.get("figure") or {}
            fid = str(f.get("figure_id") or "")
            data = figure_bytes_map.get(fid)
            if data:
                b.image(data, label=f.get("label") or "", caption=f.get("caption") or "")
            else:
                b.figure_callout(f.get("label") or "image", f.get("caption") or "")
            continue
        if t == "question":
            q = dict(it.get("question") or {})
            # Embed any question-attached figures right after the prompt
            # so they live with the question card in the output. We do
            # this by injecting them inline via _DocBuilder.image after
            # _render_question runs.
            _render_question(b, q)
            for f in q.get("embedded_figures") or []:
                fid = str(f.get("figure_id") or "")
                data = figure_bytes_map.get(fid)
                if data:
                    b.image(data, label=f.get("label") or "", caption=f.get("caption") or "")
                else:
                    b.figure_callout(f.get("label") or "image", f.get("caption") or "")
            continue
        if t == "custom_text":
            _render_custom_text(b, it.get("content") or "")
            continue
        # Unknown item type → ignore silently
    return b.to_bytes()
