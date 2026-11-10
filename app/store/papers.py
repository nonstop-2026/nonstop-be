"""Firestore mapping and writes for papers and their cards.

The Firestore client is always passed in so tests can use a fake object.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.exceptions import Conflict, DeadlineExceeded, ServiceUnavailable

from app.cards.generate import LEVELS, GenerationResult, has_blocking_issues
from app.ingest.arxiv import ArxivPaper

PAPERS = "papers"
CARDS = "cards"
CARD_FIELDS = (
    "oneLine",
    "whyImportant",
    "keyPoints",
    "method",
    "dataset",
    "limitation",
)
READY = "ready"
HIDDEN = "hidden"


def paper_document(paper: ArxivPaper, cards: dict, status: str) -> dict[str, Any]:
    # The abstract is deliberately left out; only generated cards are stored.
    return {
        "arxivId": paper.arxiv_id,
        "version": paper.version,
        "title": paper.title,
        "titleKo": cards["titleKo"],
        "authors": paper.authors,
        "publishedAt": paper.published_at,
        "categories": paper.categories,
        "primaryCategory": paper.primary_category,
        "venue": paper.venue,
        "abstractUrl": paper.abstract_url,
        "pdfUrl": paper.pdf_url,
        "license": paper.license,
        "tags": cards["tags"],
        "status": status,
    }


def card_documents(result: GenerationResult, generated_at: Any) -> dict[str, dict]:
    documents = {}
    for level in LEVELS:
        card = result.cards[level]
        documents[level] = {name: card[name] for name in CARD_FIELDS} | {
            "model": result.model,
            "promptVersion": result.prompt_version,
            "generatedAt": generated_at,
        }
    return documents


@dataclass
class Review:
    """Outcome of generating one paper. `status` is None when nothing should be saved."""

    status: str | None
    result: GenerationResult | None
    attempts: list[GenerationResult] = field(default_factory=list)


def has_issues(result: GenerationResult) -> bool:
    return has_blocking_issues(result.issues)


def review_paper(generate: Callable[[], GenerationResult]) -> Review:
    """Generate once, and once more if the first result failed or has check issues."""
    first = generate()
    if first.ok and not has_issues(first):
        return Review(READY, first, [first])
    second = generate()
    attempts = [first, second]
    if second.ok:
        return Review(READY if not has_issues(second) else HIDDEN, second, attempts)
    if first.ok:
        return Review(HIDDEN, first, attempts)
    return Review(None, None, attempts)


def paper_exists(db: Any, paper_id: str) -> bool:
    return db.collection(PAPERS).document(paper_id).get().exists


def cards_complete(db: Any, paper_id: str) -> bool:
    ref = db.collection(PAPERS).document(paper_id)
    return all(ref.collection(CARDS).document(level).get().exists for level in LEVELS)


def _create_batch(
    db: Any, paper: ArxivPaper, review: Review, generated_at: Any
) -> None:
    ref = db.collection(PAPERS).document(paper.paper_id)
    batch = db.batch()
    # create() fails on an existing document, so a rerun never overwrites or duplicates.
    batch.create(ref, paper_document(paper, review.result.cards, review.status))
    for level, data in card_documents(review.result, generated_at).items():
        batch.create(ref.collection(CARDS).document(level), data)
    batch.commit()


def save_paper(db: Any, paper: ArxivPaper, review: Review, generated_at: Any) -> bool:
    """Write the paper and its 3 cards in one batch. Returns False if it already exists.

    A 503 or 504 is retried once. If the retry finds the paper already there, the first
    commit landed, which counts as saved when all 3 cards exist.
    """
    for attempt in (1, 2):
        try:
            _create_batch(db, paper, review, generated_at)
            return True
        except Conflict:
            return attempt == 2 and cards_complete(db, paper.paper_id)
        except (ServiceUnavailable, DeadlineExceeded):
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def replace_cards(
    db: Any, paper_id: str, result: GenerationResult, generated_at: Any
) -> None:
    """Overwrite the 3 cards and mark the paper ready, all in one batch.

    The writes are idempotent, so a 503 or 504 is simply retried once.
    """
    ref = db.collection(PAPERS).document(paper_id)
    for attempt in (1, 2):
        batch = db.batch()
        batch.update(
            ref,
            {
                "titleKo": result.cards["titleKo"],
                "tags": result.cards["tags"],
                "status": READY,
            },
        )
        for level, data in card_documents(result, generated_at).items():
            batch.set(ref.collection(CARDS).document(level), data)
        try:
            batch.commit()
            return
        except (ServiceUnavailable, DeadlineExceeded):
            if attempt == 2:
                raise


def firestore_client(service_account_json: str, project_id: str) -> Any:
    try:
        # Reuse the default app so a second call in the same process does not fail.
        app = firebase_admin.get_app()
    except ValueError:
        cert = credentials.Certificate(json.loads(service_account_json))
        app = firebase_admin.initialize_app(cert, {"projectId": project_id})
    return firestore.client(app)
