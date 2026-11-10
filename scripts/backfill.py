"""Backfill the newest papers per category into Firestore with generated cards.

Papers already in Firestore are skipped, so the script can be rerun to resume.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from firebase_admin import firestore
from google.api_core.exceptions import GoogleAPICallError

from app.cards.generate import (
    DEFAULT_INTERVAL_SECONDS,
    LEVELS,
    CardGenerator,
    GenerationResult,
    QuotaExceeded,
)
from app.cards.prompts import LATEST_PROMPT_VERSION
from app.config import load_settings
from app.ingest.arxiv import (
    CATEGORIES,
    ArxivClient,
    ArxivPaper,
    select_by_primary_category,
)
from app.store.papers import (
    HIDDEN,
    READY,
    Review,
    firestore_client,
    paper_exists,
    review_paper,
    save_paper,
)

CANDIDATES_PER_CATEGORY = 100
# Two attempts (first plus one regeneration), each with at most one transient retry.
MAX_CALLS_PER_PAPER = 4
CHECKPOINT_PAPERS = 10
MAX_HIDDEN_RATIO = 0.5
WRITES_PER_PAPER = 1 + len(LEVELS)
REPORT_PATH = Path("trial-output/backfill-report.md")


@dataclass
class Processed:
    paper: ArxivPaper
    review: Review
    saved: bool


@dataclass
class BackfillState:
    calls: int = 0
    writes: int = 0
    processed: list[Processed] = field(default_factory=list)
    remaining: list[ArxivPaper] = field(default_factory=list)
    stop_reason: str | None = None


def run_backfill(
    papers: list[ArxivPaper],
    generate: Callable[[ArxivPaper], GenerationResult],
    save: Callable[[ArxivPaper, Review], bool],
    max_calls: int,
) -> BackfillState:
    state = BackfillState()
    for index, paper in enumerate(papers):
        if max_calls - state.calls < MAX_CALLS_PER_PAPER:
            state.stop_reason = f"Gemini call limit {max_calls} reached"
            state.remaining = papers[index:]
            break

        def counted(paper: ArxivPaper = paper) -> GenerationResult:
            try:
                result = generate(paper)
            except QuotaExceeded as exc:
                state.calls += exc.api_calls
                raise
            state.calls += result.api_calls
            return result

        try:
            review = review_paper(counted)
        except QuotaExceeded as exc:
            state.stop_reason = str(exc)
            state.remaining = papers[index:]
            break
        try:
            saved = review.status is not None and save(paper, review)
        except GoogleAPICallError as exc:
            # The write may still have landed; a rerun skips papers already stored.
            state.stop_reason = f"Firestore error on {paper.paper_id}: {exc.code}"
            state.remaining = papers[index:]
            break
        if saved:
            state.writes += WRITES_PER_PAPER
        state.processed.append(Processed(paper, review, saved))
        label = review.status or "failed"
        if review.status and not saved:
            label += " (already stored, skipped)"
        if review.status == HIDDEN:
            label += f" [{issue_text(review.result)}]"
        print(
            f"[{index + 1}/{len(papers)}] {paper.primary_category} "
            f"{paper.paper_id} {label} calls={state.calls}",
            flush=True,
        )
        if len(state.processed) == CHECKPOINT_PAPERS:
            hidden = sum(1 for p in state.processed if p.review.status == HIDDEN)
            ratio = hidden / CHECKPOINT_PAPERS
            print(
                f"Checkpoint after {CHECKPOINT_PAPERS} papers: hidden {ratio:.0%}, "
                f"{state.calls / CHECKPOINT_PAPERS:.2f} calls per paper",
                flush=True,
            )
            if ratio > MAX_HIDDEN_RATIO:
                state.stop_reason = (
                    f"Hidden ratio {ratio:.0%} after {CHECKPOINT_PAPERS}"
                )
                state.remaining = papers[index + 1 :]
                break
    return state


def issue_text(result: GenerationResult) -> str:
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in result.issues.items() if v)


def build_report(
    model: str, state: BackfillState, reads: int, already_stored: int
) -> str:
    lines = [
        "# Backfill report",
        "",
        f"- Model: {model}, prompt {LATEST_PROMPT_VERSION}",
        f"- Gemini calls: {state.calls}",
        f"- Firestore reads: {reads}, writes: {state.writes}",
        f"- Already stored before this run: {already_stored}",
    ]
    if state.stop_reason:
        lines.append(
            f"- Stopped early: {state.stop_reason}. Remaining papers: "
            f"{len(state.remaining)}"
        )
    lines += [
        "",
        "## By category",
        "",
        "| Category | ready | hidden | failed |",
        "| --- | --- | --- | --- |",
    ]
    for category in CATEGORIES:
        items = [p for p in state.processed if p.paper.primary_category == category]
        counts = [
            sum(1 for p in items if p.saved and p.review.status == status)
            for status in (READY, HIDDEN)
        ]
        failed = sum(1 for p in items if p.review.status is None)
        lines.append(f"| {category} | {counts[0]} | {counts[1]} | {failed} |")

    lines += ["", "## Hidden", ""]
    hidden = [p for p in state.processed if p.saved and p.review.status == HIDDEN]
    lines += [
        f"- {p.paper.paper_id} {p.paper.title}: {issue_text(p.review.result)}"
        for p in hidden
    ] or ["None"]

    lines += ["", "## Failed", ""]
    failed = [p for p in state.processed if p.review.status is None]
    lines += [
        f"- {p.paper.paper_id} {p.paper.title}: "
        + "; ".join(a.error or "" for a in p.review.attempts)
        for p in failed
    ] or ["None"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--per-category", type=int, default=24)
    parser.add_argument("--max-gemini-calls", type=int, default=140)
    parser.add_argument(
        "--gemini-interval", type=float, default=DEFAULT_INTERVAL_SECONDS
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    print(f"Model: {args.model}, prompt {LATEST_PROMPT_VERSION}", flush=True)
    db = firestore_client(
        settings.firebase_service_account_json, settings.firebase_project_id
    )
    arxiv = ArxivClient(settings.arxiv_request_interval_seconds)

    candidates = {c: arxiv.latest(c, CANDIDATES_PER_CATEGORY) for c in CATEGORIES}
    selected = select_by_primary_category(candidates, args.per_category)
    short = {c: len(p) for c, p in selected.items() if len(p) < args.per_category}
    if short:
        sys.exit(f"Not enough papers with a matching primary category: {short}")

    papers = [paper for group in selected.values() for paper in group]
    stored = {p.paper_id for p in papers if paper_exists(db, p.paper_id)}
    reads = len(papers)
    todo = [p for p in papers if p.paper_id not in stored]
    print(
        f"Selected {len(papers)} papers, {len(stored)} already stored, "
        f"{len(todo)} to generate. Firestore reads: {reads}",
        flush=True,
    )
    if args.dry_run:
        for paper in papers:
            mark = "stored" if paper.paper_id in stored else "new"
            print(f"{paper.primary_category}\t{paper.paper_id}\t{mark}\t{paper.title}")
        return

    generator = CardGenerator(settings.gemini_api_key, args.model, args.gemini_interval)

    def generate(paper: ArxivPaper) -> GenerationResult:
        return generator.generate(paper.paper_id, paper.title, paper.abstract)

    def save(paper: ArxivPaper, review: Review) -> bool:
        paper.license = arxiv.fetch_license(paper.arxiv_id)
        return save_paper(db, paper, review, firestore.SERVER_TIMESTAMP)

    state = run_backfill(todo, generate, save, args.max_gemini_calls)
    report = build_report(args.model, state, reads, len(stored))
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
