"""Firestore mapping and writes for papers and their cards.

The Firestore client is always passed in so tests can use a fake object.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.exceptions import Conflict

from app.cards.generate import LEVELS, GenerationResult
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
    return any(result.issues.values())


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


def save_paper(db: Any, paper: ArxivPaper, review: Review, generated_at: Any) -> bool:
    """Write the paper and its 3 cards in one batch. Returns False if it already exists."""
    ref = db.collection(PAPERS).document(paper.paper_id)
    batch = db.batch()
    # create() fails on an existing document, so a rerun never overwrites or duplicates.
    batch.create(ref, paper_document(paper, review.result.cards, review.status))
    for level, data in card_documents(review.result, generated_at).items():
        batch.create(ref.collection(CARDS).document(level), data)
    try:
        batch.commit()
    except Conflict:
        return False
    return True


def firestore_client(service_account_json: str, project_id: str) -> Any:
    cert = credentials.Certificate(json.loads(service_account_json))
    app = firebase_admin.initialize_app(cert, {"projectId": project_id})
    return firestore.client(app)
