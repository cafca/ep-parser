"""
Integration tests for parse_amendments.py.

All tests run against the bundled sample document
(JURI-AM-776972_EN.pdf, 161 pages, 344 amendments).
The parsed result is computed once per session via a module-level fixture.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SAMPLE_PDF = Path(__file__).parent / "JURI-AM-776972_EN.pdf"
PARSER = Path(__file__).parent.parent / "parse_amendments.py"


@pytest.fixture(scope="session")
def amendments(tmp_path_factory):
    out = tmp_path_factory.mktemp("output") / "amendments.json"
    result = subprocess.run(
        [sys.executable, str(PARSER), str(SAMPLE_PDF), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Parser exited with code {result.returncode}:\n{result.stderr}"
    data = json.loads(out.read_text(encoding="utf-8"))
    return {a["id"]: a for a in data}


# ── basic sanity ──────────────────────────────────────────────────────────────

def test_total_count(amendments):
    assert len(amendments) == 344


def test_sequential_ids(amendments):
    ids = sorted(int(k.split()[-1]) for k in amendments)
    assert ids == list(range(1, 345)), "Amendment IDs are not a clean 1–344 sequence"


def test_no_unexpected_warning_types(amendments):
    known = {
        "non_sequential_id",
        "missing_author",
        "both_columns_empty",
        "suspiciously_short_column",
        "no_language_marker",
        "ambiguous_column_split",
        "footer_text_leaked",
    }
    for a in amendments.values():
        for w in a["warnings"]:
            key = w.split(":")[0].strip()
            assert key in known, f"{a['id']}: unknown warning type {key!r}"


# ── known amendment spot-checks ───────────────────────────────────────────────

def test_amendment_1_author(amendments):
    assert "Kira Marie Peter-Hansen" in amendments["Amendment 1"]["authors"]


def test_amendment_1_section(amendments):
    assert amendments["Amendment 1"]["section"] == "Motion for a resolution / Heading 1"


def test_amendment_1_amendment_text(amendments):
    assert "start-ups and scale-ups" in amendments["Amendment 1"]["amendment"]


def test_amendment_100_author(amendments):
    assert "Axel Voss" in amendments["Amendment 100"]["authors"]


def test_amendment_100_content_empty(amendments):
    assert amendments["Amendment 100"]["content"] == ""


def test_amendment_100_amendment_text(amendments):
    assert "company house platform" in amendments["Amendment 100"]["amendment"]


def test_amendment_292_author(amendments):
    # Author line falls immediately before a page break; must not be dropped.
    a = amendments["Amendment 292"]
    assert "Axel Voss" in a["authors"]
    assert "Angelika Niebler" in a["authors"]
    assert "missing_author" not in a["warnings"]


# ── structural completeness ───────────────────────────────────────────────────

def test_all_have_ids(amendments):
    for key, a in amendments.items():
        assert a["id"] == key


def test_all_have_sections(amendments):
    empty_sections = [a["id"] for a in amendments.values() if not a["section"]]
    assert not empty_sections, f"Amendments with no section: {empty_sections}"


def test_no_both_columns_empty(amendments):
    flagged = [a["id"] for a in amendments.values() if "both_columns_empty" in a["warnings"]]
    assert not flagged, f"Amendments with both columns empty: {flagged}"


def test_clean_amendment_ratio(amendments):
    """At least 70 % of amendments should parse without any warnings."""
    clean = sum(1 for a in amendments.values() if not a["warnings"])
    ratio = clean / len(amendments)
    assert ratio >= 0.70, f"Only {ratio:.0%} of amendments are warning-free (expected ≥ 70 %)"
