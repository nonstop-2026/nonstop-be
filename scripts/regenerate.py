"""Regenerate cards for hidden papers with the latest prompt.

A paper whose new cards pass every blocking check gets its 3 cards overwritten and becomes
ready. Otherwise the stored cards stay as they are and the paper stays hidden.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from firebase_admin import firestore
from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore_v1.base_query import FieldFilter

from app.cards.generate import (
    DEFAULT_INTERVAL_SECONDS,
    LEVELS,
    CardGenerator,
    GenerationResult,
    QuotaExceeded,
    has_blocking_issues,
)
from app.cards.prompts import LATEST_PROMPT_VERSION
from app.config import load_settings
from app.ingest.arxiv import ArxivClient
from app.store.papers import HIDDEN, PAPERS, firestore_client, replace_cards

# One attempt plus at most one transient retry.
MAX_CALLS_PER_PAPER = 2
WRITES_PER_PAPER = 1 + len(LEVELS)
CHECKPOINT_PAPERS = 10
MIN_PASS_RATIO = 0.5
ARXIV_BATCH = 50
REPORT_PATH = Path("trial-output/regenerate-report.md")


@dataclass
class HiddenPaper:
    paper_id: str
    arxiv_id: str
    title: str
    primary_category: str
    abstract: str


@dataclass
class Attempt:
    paper: HiddenPaper
    result: GenerationResult
    passed: bool


@dataclass
class RegenerateState:
    calls: int = 0
    writes: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    remaining: list[HiddenPaper] = field(default_factory=list)
    stop_reason: str | None = None


def issue_text(result: GenerationResult) -> str:
    if not result.ok:
        return result.error or "failed"
    return "; ".join(f"{k}: {', '.join(v)}" for k, v in result.issues.items() if v)


def run_regenerate(
    papers: list[HiddenPaper],
    generate: Callable[[HiddenPaper], GenerationResult],
    save: Callable[[HiddenPaper, GenerationResult], None],
    max_calls: int,
) -> RegenerateState:
    state = RegenerateState()
    for index, paper in enumerate(papers):
        if max_calls - state.calls < MAX_CALLS_PER_PAPER:
            state.stop_reason = f"Gemini call limit {max_calls} reached"
            state.remaining = papers[index:]
            break
        try:
            result = generate(paper)
        except QuotaExceeded as exc:
            state.calls += exc.api_calls
            state.stop_reason = str(exc)
            state.remaining = papers[index:]
            break
        state.calls += result.api_calls
        passed = result.ok and not has_blocking_issues(result.issues)
        if passed:
            try:
                save(paper, result)
            except GoogleAPICallError as exc:
                state.stop_reason = f"Firestore error on {paper.paper_id}: {exc.code}"
                state.remaining = papers[index:]
                break
            state.writes += WRITES_PER_PAPER
        state.attempts.append(Attempt(paper, result, passed))
        label = "ready" if passed else f"still hidden [{issue_text(result)}]"
        print(
            f"[{index + 1}/{len(papers)}] {paper.primary_category} {paper.paper_id} "
            f"{label} calls={state.calls}",
            flush=True,
        )
        if len(state.attempts) == CHECKPOINT_PAPERS:
            ratio = sum(a.passed for a in state.attempts) / CHECKPOINT_PAPERS
            print(f"Checkpoint after {CHECKPOINT_PAPERS} papers: passed {ratio:.0%}")
            if ratio < MIN_PASS_RATIO:
                state.stop_reason = f"Pass ratio {ratio:.0%} after {CHECKPOINT_PAPERS}"
                state.remaining = papers[index + 1 :]
                break
    return state


def build_report(model: str, state: RegenerateState, reads: int) -> str:
    passed = [a for a in state.attempts if a.passed]
    lines = [
        "# Regenerate report",
        "",
        f"- Model: {model}, prompt {LATEST_PROMPT_VERSION}",
        f"- Attempted: {len(state.attempts)}, now ready: {len(passed)}",
        f"- Gemini calls: {state.calls}",
        f"- Firestore reads: {reads}, writes: {state.writes}",
    ]
    if state.stop_reason:
        lines.append(
            f"- Stopped early: {state.stop_reason}. Remaining: {len(state.remaining)}"
        )
    lines += ["", "## Still hidden", ""]
    lines += [
        f"- {a.paper.paper_id} {a.paper.title}: {issue_text(a.result)}"
        for a in state.attempts
        if not a.passed
    ] or ["None"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-gemini-calls", type=int, default=120)
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
    snapshots = list(
        db.collection(PAPERS).where(filter=FieldFilter("status", "==", HIDDEN)).stream()
    )
    reads = len(snapshots)
    docs = {s.id: s.to_dict() for s in snapshots}
    print(f"Hidden papers: {len(docs)}. Firestore reads: {reads}", flush=True)
    if args.dry_run:
        for paper_id, doc in docs.items():
            print(f"{doc['primaryCategory']}\t{paper_id}\t{doc['title']}")
        return

    arxiv = ArxivClient(settings.arxiv_request_interval_seconds)
    ids = [doc["arxivId"] for doc in docs.values()]
    abstracts = {}
    for start in range(0, len(ids), ARXIV_BATCH):
        for paper in arxiv.by_ids(ids[start : start + ARXIV_BATCH]):
            abstracts[paper.arxiv_id] = paper.abstract
    papers = [
        HiddenPaper(
            pid, d["arxivId"], d["title"], d["primaryCategory"], abstracts[d["arxivId"]]
        )
        for pid, d in docs.items()
        if d["arxivId"] in abstracts
    ]

    generator = CardGenerator(settings.gemini_api_key, args.model, args.gemini_interval)
    state = run_regenerate(
        papers,
        lambda p: generator.generate(p.paper_id, p.title, p.abstract),
        lambda p, r: replace_cards(db, p.paper_id, r, firestore.SERVER_TIMESTAMP),
        args.max_gemini_calls,
    )
    report = build_report(args.model, state, reads)
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
