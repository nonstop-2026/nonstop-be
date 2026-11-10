"""In-memory catalog of ready papers, loaded from Firestore once per process.

Data only changes through the backfill and regenerate scripts, so there is no refresh.
Restarting the server reloads it.
"""

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1.base_query import FieldFilter

from app.cards.generate import LEVELS
from app.store.papers import CARD_FIELDS, firestore_client

logger = logging.getLogger("nonstop.catalog")


class CatalogUnavailable(Exception):
    """Firestore could not be read (outage, timeout or exhausted quota)."""


@dataclass(frozen=True)
class Paper:
    paper_id: str
    arxiv_id: str
    version: int
    title: str
    title_ko: str
    primary_category: str
    venue: str | None
    published_at: str
    authors: tuple[str, ...]
    tags: tuple[str, ...]
    abstract_url: str
    pdf_url: str
    license: str | None
    cards: dict[str, dict[str, Any]]

    def summary(self, level: str) -> dict[str, Any]:
        return {
            "paperId": self.paper_id,
            "arxivId": self.arxiv_id,
            "version": self.version,
            "title": self.title,
            "titleKo": self.title_ko,
            "primaryCategory": self.primary_category,
            "venue": self.venue,
            "publishedAt": self.published_at,
            "authors": list(self.authors),
            "tags": list(self.tags),
            "level": level,
            "oneLine": self.cards[level]["oneLine"],
        }

    def detail(self, level: str) -> dict[str, Any]:
        card = {name: self.cards[level][name] for name in CARD_FIELDS}
        return self.summary(level) | {
            "abstractUrl": self.abstract_url,
            "pdfUrl": self.pdf_url,
            "license": self.license,
            "card": card,
        }


def paper_from_documents(paper_id: str, doc: dict, cards: dict[str, dict]) -> Paper:
    return Paper(
        paper_id=paper_id,
        arxiv_id=doc["arxivId"],
        version=doc["version"],
        title=doc["title"],
        title_ko=doc["titleKo"],
        primary_category=doc["primaryCategory"],
        venue=doc.get("venue"),
        published_at=doc["publishedAt"],
        authors=tuple(doc["authors"]),
        tags=tuple(doc["tags"]),
        abstract_url=doc["abstractUrl"],
        pdf_url=doc["pdfUrl"],
        license=doc.get("license"),
        cards=cards,
    )


def load_from_firestore(db: Any) -> list[Paper]:
    """Read every ready paper and its 3 cards: one query plus one batched get."""
    snapshots = list(
        db.collection("papers")
        .where(filter=FieldFilter("status", "==", "ready"))
        .stream()
    )
    refs = [
        s.reference.collection("cards").document(level)
        for s in snapshots
        for level in LEVELS
    ]
    cards: dict[str, dict[str, dict]] = {}
    for card in db.get_all(refs):
        if card.exists:
            cards.setdefault(card.reference.parent.parent.id, {})[card.id] = (
                card.to_dict()
            )
    papers = [
        paper_from_documents(s.id, s.to_dict(), cards[s.id])
        for s in snapshots
        if set(cards.get(s.id, {})) == set(LEVELS)
    ]
    logger.info(
        "Catalog loaded: %d papers, %d Firestore reads",
        len(papers),
        len(snapshots) + len(refs),
    )
    return papers


def default_loader() -> list[Paper]:
    # Reads only the Firebase variables, so the API server never needs the Gemini key.
    db = firestore_client(
        os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"], os.environ["FIREBASE_PROJECT_ID"]
    )
    return load_from_firestore(db)


class Catalog:
    def __init__(self, loader: Callable[[], list[Paper]] = default_loader):
        self._loader = loader
        self._papers: list[Paper] | None = None
        self._by_id: dict[str, Paper] = {}
        self._lock = threading.Lock()

    def papers(self) -> list[Paper]:
        if self._papers is None:
            # Concurrent first requests wait here so Firestore is read only once.
            with self._lock:
                if self._papers is None:
                    try:
                        papers = self._loader()
                    except GoogleAPICallError as exc:
                        raise CatalogUnavailable(type(exc).__name__) from exc
                    self._by_id = {p.paper_id: p for p in papers}
                    self._papers = papers
        return self._papers

    def get(self, paper_id: str) -> Paper | None:
        self.papers()
        return self._by_id.get(paper_id)
