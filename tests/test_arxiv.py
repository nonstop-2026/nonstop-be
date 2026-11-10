from pathlib import Path

import pytest

from app.ingest.arxiv import parse_atom, parse_oai_license, to_paper_id

FIXTURE = Path(__file__).parent / "fixtures" / "atom_sample.xml"


@pytest.fixture(scope="module")
def papers():
    return parse_atom(FIXTURE.read_text(encoding="utf-8"))


def test_parses_entry_with_journal_ref(papers):
    paper = papers[0]
    assert paper.paper_id == "2611.00001"
    assert paper.arxiv_id == "2611.00001"
    assert paper.version == 2
    assert paper.title == "A Sample Paper With a Wrapped Title"
    assert paper.abstract == (
        "We study a sample problem. Our method improves accuracy by 12.5 points on 3 benchmarks."
    )
    assert paper.authors == ["Ada Example", "Bo Sample"]
    assert paper.published_at == "2026-11-06"
    assert paper.primary_category == "cs.HC"
    assert paper.categories == ["cs.HC", "cs.AI"]
    assert paper.venue == "CHI 2026"
    assert paper.abstract_url == "https://arxiv.org/abs/2611.00001"
    assert paper.pdf_url == "https://arxiv.org/pdf/2611.00001"
    assert paper.license is None


def test_parses_entry_without_journal_ref(papers):
    paper = papers[1]
    assert paper.venue is None
    assert paper.arxiv_id == "hep-th/9901001"
    assert paper.paper_id == "hep-th_9901001"
    assert paper.abstract_url == "https://arxiv.org/abs/hep-th/9901001"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2611.08877", "2611.08877"),
        ("2611.08877v3", "2611.08877"),
        ("hep-th/9901001v2", "hep-th_9901001"),
    ],
)
def test_to_paper_id(raw, expected):
    assert to_paper_id(raw) == expected


def test_parse_oai_license():
    xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord><record><metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
      <id>2611.00001</id>
      <license>http://creativecommons.org/licenses/by/4.0/</license>
    </arXiv>
  </metadata></record></GetRecord>
</OAI-PMH>"""
    assert parse_oai_license(xml_text) == "http://creativecommons.org/licenses/by/4.0/"
