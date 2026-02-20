#!/usr/bin/env python3
"""
Parse European Parliament committee amendments PDF into structured JSON records.
"""

import re
import json
import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional
import fitz  # PyMuPDF


# ── constants ────────────────────────────────────────────────────────────────

FOOTER_Y_THRESHOLD = 760       # points from top; lines below this are footers (true footer at y≈771)
LARGE_FONT_THRESHOLD = 20      # "EN" watermark spans
COLUMN_SPLIT_X = 244           # left < this → original; right ≥ this → amendment
AMBIGUOUS_LOW = 220            # warn if span x falls in [220, 300]
AMBIGUOUS_HIGH = 300
LINE_Y_TOLERANCE = 2           # pts; spans within this are on the same line
AMENDMENT_HEADER_X_TOLERANCE = 80  # "Amendment N" text can be at x≈71 or x≈139

AMENDMENT_RE = re.compile(r'^Amendment\s+(\d+)$', re.IGNORECASE)
LANGUAGE_MARKER_RE = re.compile(r'^Or\.\s+[a-z]{2}$', re.IGNORECASE)
DOC_CODE_RE = re.compile(r'PE\s*\d{3}[\.,]\d{3}|AM\\\d+|[A-Z]+-AM-\d', re.IGNORECASE)
# Column header labels that appear alone in the right column and should not be
# treated as amendment content (they are the right-column heading row).
TABLE_COLUMN_LABEL_RE = re.compile(
    r'^(Amendment|Proposal for rejection|Text proposed by the Commission)$',
    re.IGNORECASE,
)


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class Span:
    x: float
    y: float
    text: str
    bold: bool
    size: float
    page_num: int


@dataclass
class Line:
    y: float
    page_num: int
    spans: list  # list of Span, ordered by x


@dataclass
class Amendment:
    id: str
    authors: str = ""
    section: str = ""
    content: str = ""
    amendment: str = ""
    warnings: list = field(default_factory=list)


# ── phase 1: span extraction ──────────────────────────────────────────────────

def is_bold(flags: int) -> bool:
    """Check PyMuPDF font flags for bold (bit 4 = 16)."""
    return bool(flags & 16)


def extract_spans(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    spans = []
    for page_num, page in enumerate(doc, start=1):
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block.get("type") != 0:  # text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text:
                        continue
                    x = span["origin"][0]
                    y = span["origin"][1]
                    size = span["size"]
                    bold = is_bold(span["flags"])

                    # filter footer zone
                    if y > FOOTER_Y_THRESHOLD:
                        continue
                    # filter large-font watermark spans (e.g. "EN")
                    if size >= LARGE_FONT_THRESHOLD:
                        continue

                    spans.append(Span(x=x, y=y, text=text, bold=bold, size=size, page_num=page_num))
    doc.close()
    return spans


# ── phase 2: line assembly ────────────────────────────────────────────────────

def assemble_lines(spans: list) -> list:
    """Group spans into logical lines by (page_num, y) proximity."""
    if not spans:
        return []

    # sort by page, then y, then x
    spans_sorted = sorted(spans, key=lambda s: (s.page_num, s.y, s.x))

    lines = []
    current_spans = [spans_sorted[0]]

    for span in spans_sorted[1:]:
        prev = current_spans[-1]
        same_page = span.page_num == prev.page_num
        close_y = abs(span.y - prev.y) <= LINE_Y_TOLERANCE

        if same_page and close_y:
            current_spans.append(span)
        else:
            lines.append(_make_line(current_spans))
            current_spans = [span]

    if current_spans:
        lines.append(_make_line(current_spans))

    return lines


def _make_line(spans: list) -> Line:
    spans_sorted = sorted(spans, key=lambda s: s.x)
    return Line(y=spans_sorted[0].y, page_num=spans_sorted[0].page_num, spans=spans_sorted)


def line_text(line: Line) -> str:
    return " ".join(s.text for s in line.spans).strip()


def line_min_x(line: Line) -> float:
    return min(s.x for s in line.spans)


def line_is_bold(line: Line) -> bool:
    return all(s.bold for s in line.spans)


def line_size(line: Line) -> float:
    return max(s.size for s in line.spans)


# ── phase 3: state machine ────────────────────────────────────────────────────

def parse_amendments(lines: list) -> list:
    amendments = []
    state = "PREAMBLE"
    current: Optional[Amendment] = None
    prev_id_num = 0

    # accumulator lines for current amendment table body
    left_lines = []   # original text lines
    right_lines = []  # amendment text lines
    section_parts = []
    author_lines = []
    has_language_marker = False

    def flush_amendment():
        nonlocal current, left_lines, right_lines, section_parts, author_lines, has_language_marker
        if current is None:
            return

        # Join author lines.  When a line ends with a comma the text wrapped
        # within a single author list — continue with a space.  Otherwise
        # separate distinct entries with "; ".
        authors_joined = ""
        for part in author_lines:
            if not authors_joined:
                authors_joined = part
            elif authors_joined.endswith(","):
                authors_joined += " " + part
            else:
                authors_joined += "; " + part
        current.authors = authors_joined
        current.section = " / ".join(section_parts)
        current.content = "\n".join(left_lines).strip()
        current.amendment = "\n".join(right_lines).strip()

        # warnings
        if not current.authors:
            current.warnings.append("missing_author")
        if not current.content and not current.amendment:
            current.warnings.append("both_columns_empty")
        if (current.content and len(current.content) < 3
                and current.content.lower() != "deleted"):
            current.warnings.append("suspiciously_short_column")
        if (current.amendment and len(current.amendment) < 3
                and current.amendment.lower() != "deleted"):
            current.warnings.append("suspiciously_short_column")
        if not has_language_marker:
            current.warnings.append("no_language_marker")
        # check for footer text leakage
        if DOC_CODE_RE.search(current.content) or DOC_CODE_RE.search(current.amendment):
            current.warnings.append("footer_text_leaked")

        amendments.append(current)
        current = None
        left_lines = []
        right_lines = []
        section_parts = []
        author_lines = []
        has_language_marker = False

    def start_amendment(id_str: str, id_num: int):
        nonlocal current, prev_id_num, has_language_marker
        flush_amendment()
        has_language_marker = False
        current = Amendment(id=id_str)
        if id_num != prev_id_num + 1 and prev_id_num != 0:
            current.warnings.append(
                f"non_sequential_id: expected {prev_id_num + 1}, got {id_num}"
            )
        prev_id_num = id_num

    def add_table_text(line: Line):
        """Split a table body line into left/right column accumulators."""
        left_parts = []
        right_parts = []
        has_ambiguous = False

        ambiguous_xs = []
        for span in line.spans:
            if AMBIGUOUS_LOW <= span.x < AMBIGUOUS_HIGH:
                ambiguous_xs.append(round(span.x))
            if span.x < COLUMN_SPLIT_X:
                left_parts.append(span.text)
            else:
                right_parts.append(span.text)

        if ambiguous_xs:
            xs_str = ", ".join(str(x) for x in ambiguous_xs)
            current.warnings.append(
                f"ambiguous_column_split: {len(ambiguous_xs)} spans near boundary at x≈{xs_str}"
            )

        if left_parts:
            left_lines.append(" ".join(left_parts))
        if right_parts:
            right_lines.append(" ".join(right_parts))

    for line in lines:
        text = line_text(line)
        if not text:
            continue

        bold = line_is_bold(line)
        min_x = line_min_x(line)
        size = line_size(line)

        # ── detect amendment header anywhere ────────────────────────────────
        m = AMENDMENT_RE.match(text)
        if m and bold and size >= 11:
            id_num = int(m.group(1))
            start_amendment(text, id_num)
            state_ref = "AMENDMENT_HEADER"
            # use a mutable container to allow nested function access
            # (we'll use direct variable since Python closures read enclosing scope)
            state = "AUTHORS"
            continue

        if state == "PREAMBLE":
            continue

        # ── language marker → end of amendment ──────────────────────────────
        if LANGUAGE_MARKER_RE.match(text):
            has_language_marker = True
            state = "PREAMBLE"  # wait for next amendment header
            continue

        if state == "AUTHORS":
            # Detect transition to TABLE_BODY: any span landing in the right column
            # zone signals that the two-column table has started.  This handles all
            # known committee header variants ("Motion for a resolution / Amendment",
            # "Text proposed by the Commission / Amendment", "Proposal for rejection",
            # or no explicit header at all).
            right_spans = [s for s in line.spans if s.x >= COLUMN_SPLIT_X]
            if right_spans:
                left_spans = [s for s in line.spans if s.x < COLUMN_SPLIT_X]
                state = "TABLE_BODY"
                if not left_spans:
                    # Right-column-only line: skip if it is a known column-header
                    # label, otherwise treat as the first piece of amendment content.
                    if not TABLE_COLUMN_LABEL_RE.match(text):
                        add_table_text(line)
                # Both-column lines are header rows — transition state but skip content.
                continue

            # check for "on behalf of" pattern
            if "on behalf of" in text.lower():
                author_lines.append(text)
                continue

            # bold size-12 line = author or section identifier
            if bold and size >= 11:
                # Heuristic: lines containing known structural keywords are section
                # labels; everything else is an author name (until a section is seen).
                SECTION_KEYWORDS = (
                    "Motion for a resolution", "Paragraph", "Recital",
                    "Article", "Heading", "Title", "Annex", "Proposal",
                )
                if any(kw in text for kw in SECTION_KEYWORDS):
                    section_parts.append(text)
                else:
                    # Still in author zone, or continuation of section identifier.
                    if section_parts:
                        section_parts.append(text)
                    else:
                        author_lines.append(text)
                continue

            # non-bold lines in AUTHORS state without right-column spans:
            # additional author qualifiers or sub-section identifiers — skip.
            if not bold:
                continue

        elif state == "TABLE_BODY":
            add_table_text(line)

    flush_amendment()
    return amendments


# ── phase 4: output ───────────────────────────────────────────────────────────

def amendments_to_json(amendments: list) -> list:
    return [
        {
            "id": a.id,
            "authors": a.authors,
            "section": a.section,
            "content": a.content,
            "amendment": a.amendment,
            "warnings": a.warnings,
        }
        for a in amendments
    ]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Parse EP amendments PDF to JSON")
    parser.add_argument("pdf", help="Input PDF file")
    parser.add_argument("-o", "--output", default="amendments.json", help="Output JSON file")
    parser.add_argument("--pretty", action="store_true", default=True, help="Pretty-print JSON")
    args = parser.parse_args()

    print(f"Extracting spans from {args.pdf} …", file=sys.stderr)
    spans = extract_spans(args.pdf)
    print(f"  {len(spans)} spans extracted", file=sys.stderr)

    print("Assembling lines …", file=sys.stderr)
    lines = assemble_lines(spans)
    print(f"  {len(lines)} logical lines assembled", file=sys.stderr)

    print("Parsing amendments …", file=sys.stderr)
    amendments = parse_amendments(lines)
    print(f"  {len(amendments)} amendments parsed", file=sys.stderr)

    data = amendments_to_json(amendments)
    indent = 2 if args.pretty else None
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    print(f"Written to {args.output}", file=sys.stderr)

    warnings_count = sum(1 for a in amendments if a.warnings)
    print(f"Amendments with warnings: {warnings_count}", file=sys.stderr)


if __name__ == "__main__":
    main()
