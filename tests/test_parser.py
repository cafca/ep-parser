"""
Integration tests for parse_amendments.py.

Four bundled sample documents are tested:

  JURI-AM-776972_EN.pdf  161 pages, 344 amendments (ids 1–344)
  IMCO-AM-773238_EN.pdf  120 pages, 217 amendments (ids 1–217)
  ENVI-AM-776927_EN.pdf  131 pages, 235 amendments (ids 172–406)
  TRAN-AM-777048_EN.pdf  122 pages, 197 amendments (ids 5–201)

Each document uses the parsed result computed once per session via a
module-level fixture.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PARSER = TESTS_DIR.parent / "parse_amendments.py"

JURI_PDF = TESTS_DIR / "JURI-AM-776972_EN.pdf"
IMCO_PDF = TESTS_DIR / "IMCO-AM-773238_EN.pdf"
ENVI_PDF = TESTS_DIR / "ENVI-AM-776927_EN.pdf"
TRAN_PDF = TESTS_DIR / "TRAN-AM-777048_EN.pdf"


def _parse(pdf_path: Path, tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("output") / "amendments.json"
    result = subprocess.run(
        [sys.executable, str(PARSER), str(pdf_path), "-o", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Parser exited with code {result.returncode}:\n{result.stderr}"
    data = json.loads(out.read_text(encoding="utf-8"))
    return {a["id"]: a for a in data}


@pytest.fixture(scope="session")
def amendments(tmp_path_factory):
    return _parse(JURI_PDF, tmp_path_factory)


@pytest.fixture(scope="session")
def imco(tmp_path_factory):
    return _parse(IMCO_PDF, tmp_path_factory)


@pytest.fixture(scope="session")
def envi(tmp_path_factory):
    return _parse(ENVI_PDF, tmp_path_factory)


@pytest.fixture(scope="session")
def tran(tmp_path_factory):
    return _parse(TRAN_PDF, tmp_path_factory)


KNOWN_WARNING_TYPES = {
    "non_sequential_id",
    "missing_author",
    "both_columns_empty",
    "suspiciously_short_column",
    "no_language_marker",
    "ambiguous_column_split",
    "footer_text_leaked",
}


def _assert_structural(by_id: dict, expected_count: int, expected_ids: range) -> None:
    """Shared structural assertions for any parsed document."""
    assert len(by_id) == expected_count

    ids = sorted(int(k.split()[-1]) for k in by_id)
    assert ids == list(expected_ids), "Amendment IDs are not a clean sequential range"

    for a in by_id.values():
        for w in a["warnings"]:
            key = w.split(":")[0].strip()
            assert key in KNOWN_WARNING_TYPES, f"{a['id']}: unknown warning type {key!r}"

    empty_sections = [a["id"] for a in by_id.values() if not a["section"]]
    assert not empty_sections, f"Amendments with no section: {empty_sections}"

    flagged = [a["id"] for a in by_id.values() if "both_columns_empty" in a["warnings"]]
    assert not flagged, f"Amendments with both columns empty: {flagged}"

    clean = sum(1 for a in by_id.values() if not a["warnings"])
    ratio = clean / len(by_id)
    assert ratio >= 0.70, f"Only {ratio:.0%} of amendments are warning-free (expected ≥ 70 %)"


# ── JURI ──────────────────────────────────────────────────────────────────────

def test_juri_structure(amendments):
    _assert_structural(amendments, expected_count=344, expected_ids=range(1, 345))


def test_juri_amendment_1_author(amendments):
    assert "Kira Marie Peter-Hansen" in amendments["Amendment 1"]["authors"]


def test_juri_amendment_1_section(amendments):
    assert amendments["Amendment 1"]["section"] == "Motion for a resolution / Heading 1"


def test_juri_amendment_1_amendment_text(amendments):
    assert "start-ups and scale-ups" in amendments["Amendment 1"]["amendment"]


def test_juri_amendment_100_author(amendments):
    assert "Axel Voss" in amendments["Amendment 100"]["authors"]


def test_juri_amendment_100_content_empty(amendments):
    assert amendments["Amendment 100"]["content"] == ""


def test_juri_amendment_100_amendment_text(amendments):
    assert "company house platform" in amendments["Amendment 100"]["amendment"]


def test_juri_amendment_292_author(amendments):
    # Author line falls immediately before a page break; must not be dropped.
    a = amendments["Amendment 292"]
    assert "Axel Voss" in a["authors"]
    assert "Angelika Niebler" in a["authors"]
    assert "missing_author" not in a["warnings"]


def test_juri_all_have_ids(amendments):
    for key, a in amendments.items():
        assert a["id"] == key


# ── IMCO ──────────────────────────────────────────────────────────────────────

def test_imco_structure(imco):
    _assert_structural(imco, expected_count=217, expected_ids=range(1, 218))


def test_imco_amendment_1_author(imco):
    assert "Elisabeth Grossmann" in imco["Amendment 1"]["authors"]


def test_imco_amendment_1_section(imco):
    assert imco["Amendment 1"]["section"] == "Motion for a resolution / Citation 1 a (new)"


def test_imco_amendment_100_author(imco):
    assert "Arba Kokalari" in imco["Amendment 100"]["authors"]


def test_imco_amendment_100_section(imco):
    assert imco["Amendment 100"]["section"] == "Motion for a resolution / Paragraph 7"


# ── ENVI ──────────────────────────────────────────────────────────────────────

def test_envi_structure(envi):
    # ENVI is an opinion document; amendment numbers begin at 172.
    _assert_structural(envi, expected_count=235, expected_ids=range(172, 407))


def test_envi_amendment_172_author(envi):
    assert "Anna Zalewska" in envi["Amendment 172"]["authors"]


def test_envi_amendment_172_section(envi):
    assert envi["Amendment 172"]["section"] == (
        "Proposal for a regulation / Article 1 – paragraph 1 – point 2"
    )


def test_envi_amendment_200_content_empty(envi):
    # New text amendment — left column is empty.
    assert envi["Amendment 200"]["content"] == ""


def test_envi_amendment_200_amendment_text(envi):
    assert "2040 climate target" in envi["Amendment 200"]["amendment"]


# ── TRAN ──────────────────────────────────────────────────────────────────────

def test_tran_structure(tran):
    # TRAN is an opinion document; amendment numbers begin at 5.
    _assert_structural(tran, expected_count=197, expected_ids=range(5, 202))


def test_tran_amendment_5_content_empty(tran):
    # Proposal-for-rejection amendment has no original text column.
    assert tran["Amendment 5"]["content"] == ""


def test_tran_amendment_5_amendment_text(tran):
    assert "The Committee on Transport" in tran["Amendment 5"]["amendment"]


def test_tran_amendment_11_section(tran):
    assert tran["Amendment 11"]["section"] == "Proposal for a regulation / Recital 1 a (new)"


def test_tran_amendment_100_author(tran):
    assert "Ana Vasconcelos" in tran["Amendment 100"]["authors"]
