from google.api_core.exceptions import DeadlineExceeded

from app.cards.generate import QuotaExceeded
from app.ingest.arxiv import select_by_primary_category
from scripts.backfill import run_backfill
from tests.test_store import make_paper, make_result


def test_select_by_primary_category_and_dedupe():
    shared = make_paper("2611.00003", "cs.CL")
    candidates = {
        "cs.AI": [make_paper("2611.00001", "cs.CL"), make_paper("2611.00002", "cs.AI")],
        "cs.CL": [shared, shared, make_paper("2611.00004", "cs.CL")],
    }
    selected = select_by_primary_category(candidates, 2)
    assert [p.paper_id for p in selected["cs.AI"]] == ["2611.00002"]
    assert [p.paper_id for p in selected["cs.CL"]] == ["2611.00003", "2611.00004"]


def test_backfill_stops_at_call_limit():
    papers = [make_paper(f"2611.0000{i}") for i in range(5)]
    flagged = make_result(issues={"length": ["titleKo 41 chars"]})
    state = run_backfill(papers, lambda paper: flagged, lambda p, r: True, max_calls=5)
    assert state.calls == 4
    assert len(state.processed) == 2
    assert len(state.remaining) == 3
    assert state.stop_reason == "Gemini call limit 5 reached"


def test_backfill_stops_on_quota_error():
    papers = [make_paper(f"2611.0000{i}") for i in range(3)]

    def generate(paper):
        if paper.paper_id == "2611.00001":
            raise QuotaExceeded("HTTP 429")
        return make_result(paper_id=paper.paper_id)

    state = run_backfill(papers, generate, lambda p, r: True, max_calls=140)
    assert [p.paper.paper_id for p in state.processed] == ["2611.00000"]
    assert len(state.remaining) == 2
    assert state.writes == 4


def test_backfill_counts_existing_paper_as_skipped():
    papers = [make_paper()]
    state = run_backfill(papers, lambda p: make_result(), lambda p, r: False, 140)
    assert state.processed[0].saved is False
    assert state.writes == 0


def test_backfill_stops_on_firestore_error():
    papers = [make_paper(f"2611.0000{i}") for i in range(3)]

    def save(paper, review):
        if paper.paper_id == "2611.00001":
            raise DeadlineExceeded("timeout")
        return True

    state = run_backfill(papers, lambda p: make_result(), save, max_calls=140)
    assert len(state.processed) == 1
    assert [p.paper_id for p in state.remaining] == ["2611.00001", "2611.00002"]
    assert "Firestore error on 2611.00001" in state.stop_reason
