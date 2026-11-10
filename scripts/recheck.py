"""Re-run the current content checks on stored cards and update each paper's status.

Abstracts are fetched again from arXiv because Firestore does not store them.
Gemini is never called and no document is deleted.
"""

import argparse
from typing import Any

from app.cards.generate import run_checks, why_sentences_for
from app.cards.schema import GeneratedPaperCards
from app.config import load_settings
from app.ingest.arxiv import ArxivClient
from app.store.papers import CARDS, HIDDEN, LEVELS, PAPERS, READY, firestore_client

# arXiv id_list lookups per request.
ARXIV_BATCH = 50


def recheck_paper(
    paper: dict, cards: dict[str, dict], abstract: str
) -> tuple[str, dict[str, list[str]]]:
    """Return the status the stored cards deserve under the current checks."""
    generated = GeneratedPaperCards(
        titleKo=paper["titleKo"], tags=paper["tags"], **cards
    )
    # Stored tags were already filtered, so there is nothing left to drop.
    issues = run_checks(
        generated, abstract, [], why_sentences_for(cards["basic"]["promptVersion"])
    )
    return (HIDDEN if any(issues.values()) else READY), issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    db = firestore_client(
        settings.firebase_service_account_json, settings.firebase_project_id
    )
    arxiv = ArxivClient(settings.arxiv_request_interval_seconds)

    snapshots: list[Any] = list(db.collection(PAPERS).stream())
    reads = len(snapshots)
    ids = [s.to_dict()["arxivId"] for s in snapshots]
    abstracts = {}
    for start in range(0, len(ids), ARXIV_BATCH):
        for paper in arxiv.by_ids(ids[start : start + ARXIV_BATCH]):
            abstracts[paper.arxiv_id] = paper.abstract

    changed = writes = 0
    for snapshot in snapshots:
        paper = snapshot.to_dict()
        abstract = abstracts.get(paper["arxivId"])
        if abstract is None:
            print(f"{snapshot.id}: abstract not found on arXiv, skipped")
            continue
        cards = {
            level: snapshot.reference.collection(CARDS).document(level).get().to_dict()
            for level in LEVELS
        }
        reads += len(LEVELS)
        status, issues = recheck_paper(paper, cards, abstract)
        detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in issues.items() if v)
        if status == paper["status"]:
            print(f"{snapshot.id}: {status} (unchanged) {detail}".rstrip())
            continue
        changed += 1
        print(f"{snapshot.id}: {paper['status']} -> {status} {detail}".rstrip())
        if not args.dry_run:
            snapshot.reference.update({"status": status})
            writes += 1

    mode = "dry run, nothing written" if args.dry_run else "updated"
    print(
        f"{changed} of {len(snapshots)} papers change status ({mode}). "
        f"Firestore reads: {reads}, writes: {writes}"
    )


if __name__ == "__main__":
    main()
