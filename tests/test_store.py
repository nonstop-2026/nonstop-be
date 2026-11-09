import pytest
from google.api_core.exceptions import Conflict, DeadlineExceeded, ServiceUnavailable

from app.cards.generate import GenerationResult
from app.ingest.arxiv import ArxivPaper
from app.store.papers import (
    HIDDEN,
    READY,
    card_documents,
    paper_document,
    paper_exists,
    review_paper,
    save_paper,
)


class FakeSnapshot:
    def __init__(self, exists):
        self.exists = exists


class FakeRef:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def collection(self, name):
        return FakeCollection(self.db, f"{self.path}/{name}")

    def get(self):
        return FakeSnapshot(self.path in self.db.docs)


class FakeCollection:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def document(self, doc_id):
        return FakeRef(self.db, f"{self.path}/{doc_id}")


class FakeBatch:
    def __init__(self, db):
        self.db = db
        self.ops = []

    def create(self, ref, data):
        self.ops.append((ref.path, data))

    def commit(self):
        if any(path in self.db.docs for path, _ in self.ops):
            raise Conflict("document already exists")
        self.db.docs.update(self.ops)
        self.db.commits += 1


class FakeDb:
    def __init__(self):
        self.docs = {}
        self.commits = 0

    def collection(self, name):
        return FakeCollection(self, name)

    def batch(self):
        return FakeBatch(self)


def make_paper(paper_id="2611.00001", category="cs.HC", venue=None):
    return ArxivPaper(
        paper_id=paper_id,
        arxiv_id=paper_id,
        version=2,
        title="A Sample Paper",
        abstract="Secret abstract text that must not be stored.",
        authors=["Ada Example"],
        published_at="2026-11-06",
        primary_category=category,
        categories=[category],
        venue=venue,
        abstract_url=f"https://arxiv.org/abs/{paper_id}",
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
    )


CARD = {
    "oneLine": "one line",
    "whyImportant": "First. Second.",
    "keyPoints": ["a", "b", "c"],
    "method": None,
    "dataset": None,
    "limitation": None,
}


def make_result(ok=True, issues=None, paper_id="2611.00001"):
    cards = {"titleKo": "title", "tags": ["llm"]} | {
        level: CARD for level in ("easy", "basic", "expert")
    }
    return GenerationResult(
        paper_id=paper_id,
        model="test-model",
        prompt_version="v0.3",
        ok=ok,
        elapsed_seconds=1.0,
        cards=cards if ok else None,
        error=None if ok else "invalid response: 1 errors",
        issues=issues or {},
    )


def test_paper_document_fields():
    doc = paper_document(make_paper(), make_result().cards, READY)
    assert set(doc) == {
        "arxivId",
        "version",
        "title",
        "titleKo",
        "authors",
        "publishedAt",
        "categories",
        "primaryCategory",
        "venue",
        "abstractUrl",
        "pdfUrl",
        "license",
        "tags",
        "status",
    }
    assert doc["venue"] is None
    assert "Secret abstract" not in str(doc)


def test_card_documents_fields():
    docs = card_documents(make_result(), "now")
    assert set(docs) == {"easy", "basic", "expert"}
    assert docs["basic"] == CARD | {
        "model": "test-model",
        "promptVersion": "v0.3",
        "generatedAt": "now",
    }


def sequence(*results):
    calls = iter(results)
    return lambda: next(calls)


def test_review_ready_without_issues():
    review = review_paper(sequence(make_result()))
    assert review.status == READY
    assert len(review.attempts) == 1


def test_review_ready_after_regeneration():
    flagged = make_result(issues={"length": ["titleKo 41 chars"]})
    review = review_paper(sequence(flagged, make_result()))
    assert review.status == READY
    assert len(review.attempts) == 2


def test_review_hidden_when_issues_remain():
    flagged = make_result(issues={"bannedWords": ["해결"]})
    review = review_paper(sequence(flagged, flagged))
    assert review.status == HIDDEN


def test_review_not_saved_when_validation_fails_twice():
    failed = make_result(ok=False)
    review = review_paper(sequence(failed, failed))
    assert review.status is None
    assert review.result is None


def test_save_paper_writes_four_documents_once():
    db = FakeDb()
    paper = make_paper()
    review = review_paper(sequence(make_result()))
    assert save_paper(db, paper, review, "now") is True
    assert db.commits == 1
    assert len(db.docs) == 4
    assert db.docs["papers/2611.00001"]["status"] == READY
    assert paper_exists(db, "2611.00001")


def test_save_paper_skips_existing():
    db = FakeDb()
    paper = make_paper()
    review = review_paper(sequence(make_result()))
    save_paper(db, paper, review, "now")
    assert save_paper(db, paper, review, "later") is False
    assert db.commits == 1
    assert db.docs["papers/2611.00001/cards/easy"]["generatedAt"] == "now"


class FlakyBatch(FakeBatch):
    """Applies the writes but reports a timeout, like a commit whose reply was lost."""

    def commit(self):
        super().commit()
        raise DeadlineExceeded("deadline exceeded")


def test_save_paper_counts_landed_commit_after_timeout_as_saved():
    db = FakeDb()
    db.batch = lambda: FlakyBatch(db) if db.commits == 0 else FakeBatch(db)
    review = review_paper(sequence(make_result()))
    assert save_paper(db, make_paper(), review, "now") is True
    assert len(db.docs) == 4


def test_save_paper_retries_once_then_raises():
    db = FakeDb()

    class DownBatch(FakeBatch):
        def commit(self):
            db.attempts += 1
            raise ServiceUnavailable("unavailable")

    db.attempts = 0
    db.batch = lambda: DownBatch(db)
    review = review_paper(sequence(make_result()))
    with pytest.raises(ServiceUnavailable):
        save_paper(db, make_paper(), review, "now")
    assert db.attempts == 2
