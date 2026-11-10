"""Generate cards for recent papers and write a side-by-side comparison report.

Compare two models (--compare-model) or the latest prompt against an earlier prompt
version whose results are already saved (--compare-prompt). Nothing is written to Firestore.
"""

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from app.cards.generate import (
    CHECK_NAMES,
    DEFAULT_INTERVAL_SECONDS,
    LEVELS,
    CardGenerator,
    GenerationResult,
    QuotaExceeded,
    run_checks,
    why_sentences_for,
)
from app.cards.prompts import LATEST_PROMPT_VERSION
from app.cards.schema import GeneratedPaperCards
from app.config import load_settings
from app.ingest.arxiv import (
    CATEGORIES,
    ArxivClient,
    ArxivPaper,
    select_by_primary_category,
)

# Extra results per category so cross-listed and duplicate papers can be skipped.
CATEGORY_HEADROOM = 20
OUTPUT_DIR = Path("trial-output")
CARD_ROWS = ("oneLine", "whyImportant", "keyPoints", "method", "dataset", "limitation")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def prompt_dir(version: str) -> Path:
    return OUTPUT_DIR / version.replace(".", "_")


def select_papers(client: ArxivClient, per_category: int) -> list[ArxivPaper]:
    candidates = {
        category: client.latest(category, per_category + CATEGORY_HEADROOM)
        for category in CATEGORIES
    }
    selected = select_by_primary_category(candidates, per_category)
    for category, papers in selected.items():
        if len(papers) < per_category:
            print(f"Only {len(papers)} papers with primary category {category}")
    return [paper for papers in selected.values() for paper in papers]


def load_papers(path: Path) -> list[ArxivPaper]:
    return [ArxivPaper(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def load_baseline(
    model: str, version: str, papers: list[ArxivPaper]
) -> dict[str, GenerationResult]:
    """Load saved results for `version` and re-run the current checks on them."""
    name = f"{safe_name(model)}.json"
    for path in (prompt_dir(version) / name, OUTPUT_DIR / name):
        if not path.exists():
            continue
        saved = [
            GenerationResult(**item)
            for item in json.loads(path.read_text(encoding="utf-8"))
        ]
        if saved and all(r.prompt_version == version for r in saved):
            break
    else:
        sys.exit(f"No saved {version} results for {model} in {OUTPUT_DIR}")
    abstracts = {p.paper_id: p.abstract for p in papers}
    why_sentences = why_sentences_for(version)
    for result in saved:
        if result.ok and result.paper_id in abstracts:
            cards = GeneratedPaperCards.model_validate(result.cards)
            dropped = result.issues.get("droppedTags", [])
            result.issues = run_checks(
                cards, abstracts[result.paper_id], dropped, why_sentences
            )
    return {r.paper_id: r for r in saved}


def run_generator(
    generator: CardGenerator, papers: list[ArxivPaper]
) -> tuple[list[GenerationResult], str | None, int]:
    """Return results, the stop reason and the number of Gemini calls made."""
    results = []
    for paper in papers:
        try:
            results.append(
                generator.generate(paper.paper_id, paper.title, paper.abstract)
            )
        except QuotaExceeded as exc:
            return results, str(exc), sum(r.api_calls for r in results) + exc.api_calls
    return results, None, sum(r.api_calls for r in results)


def cell(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        value = (
            "<br>".join(f"{i}. {item}" for i, item in enumerate(value, 1)) or "(none)"
        )
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def issue_count(result: GenerationResult, check: str | None = None) -> int:
    if check is not None:
        return len(result.issues.get(check, []))
    return sum(len(items) for items in result.issues.values())


def first_sentence(text: str) -> str:
    return re.split(r"(?<=\.)\s+", text, maxsplit=1)[0]


def table_row(label: str, per_variant: list[GenerationResult | None], pick) -> str:
    values = []
    for result in per_variant:
        if result is None:
            values.append("(not run)")
        elif not result.ok:
            values.append(f"FAILED: {result.error}")
        else:
            values.append(cell(pick(result)))
    return f"| {label} | " + " | ".join(values) + " |"


def build_report(
    papers: list[ArxivPaper],
    results: dict[str, dict[str, GenerationResult]],
    calls: int,
    stop_reason: str | None,
) -> str:
    variants = list(results)
    lines = ["# Card generation trial", ""]
    header = "| Field | " + " | ".join(variants) + " |"
    divider = "| --- |" + " --- |" * len(variants)
    for paper in papers:
        per_variant = [results[v].get(paper.paper_id) for v in variants]
        lines += [
            f"## {paper.paper_id} ({paper.primary_category})",
            "",
            f"- Title: {paper.title}",
            f"- Abstract, first sentence: {first_sentence(paper.abstract)}",
            f"- Venue: {paper.venue or 'null'} / License: {paper.license or 'null'}",
            "",
            header,
            divider,
        ]
        rows = [
            ("titleKo", lambda r: r.cards["titleKo"]),
            ("tags", lambda r: ", ".join(r.cards["tags"]) or "(none)"),
        ]
        for level in LEVELS:
            for name in CARD_ROWS:
                rows.append(
                    (f"{level}.{name}", lambda r, lv=level, n=name: r.cards[lv][n])
                )
        for check in CHECK_NAMES:
            rows.append(
                (
                    f"check: {check}",
                    lambda r, c=check: ", ".join(r.issues.get(c, [])) or "ok",
                )
            )
        rows.append(("seconds", lambda r: r.elapsed_seconds))
        lines += [table_row(label, per_variant, pick) for label, pick in rows]
        lines.append("")

    lines += [
        "## Summary",
        "",
        "| Variant | Success | Failure | Check issues | Mean seconds |",
        "| --- | --- | --- | --- | --- |",
    ]
    for variant in variants:
        done = list(results[variant].values())
        ok = [r for r in done if r.ok]
        mean = sum(r.elapsed_seconds for r in done) / len(done) if done else 0
        lines.append(
            f"| {variant} | {len(ok)} | {len(done) - len(ok)} | "
            f"{sum(issue_count(r) for r in ok)} | {mean:.2f} |"
        )
    lines += ["", "| Check | " + " | ".join(variants) + " |", divider]
    for check in CHECK_NAMES:
        counts = [
            str(sum(issue_count(r, check) for r in results[v].values() if r.ok))
            for v in variants
        ]
        lines.append(f"| {check} | " + " | ".join(counts) + " |")
    lines += ["", f"Gemini calls in this run: {calls}"]
    if stop_reason:
        lines.append(f"Stopped early: {stop_reason}")
    return "\n".join(lines) + "\n"


def write_json(path: Path, items: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(i) for i in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    compare = parser.add_mutually_exclusive_group()
    compare.add_argument("--compare-model")
    compare.add_argument("--compare-prompt")
    parser.add_argument("--papers-from", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select papers from arXiv and print them without calling Gemini.",
    )
    parser.add_argument("--per-category", type=int, default=1)
    parser.add_argument(
        "--gemini-interval", type=float, default=DEFAULT_INTERVAL_SECONDS
    )
    parser.add_argument("--prompt-version", default=LATEST_PROMPT_VERSION)
    args = parser.parse_args()
    if not args.dry_run and not (args.compare_model or args.compare_prompt):
        parser.error("--compare-model or --compare-prompt is required")

    settings = load_settings()
    if args.dry_run:
        client = ArxivClient(settings.arxiv_request_interval_seconds)
        for paper in select_papers(client, args.per_category):
            print(f"{paper.primary_category}\t{paper.paper_id}\t{paper.title}")
        return
    print(f"Generating with {settings.gemini_model}, prompt {args.prompt_version}")
    model = settings.gemini_model
    if args.compare_model == model or args.compare_prompt == args.prompt_version:
        sys.exit("The compared variant must differ from the generated one")
    out_dir = prompt_dir(args.prompt_version)

    if args.papers_from:
        papers = load_papers(args.papers_from)
    else:
        client = ArxivClient(settings.arxiv_request_interval_seconds)
        papers = select_papers(client, args.per_category)
        for paper in papers:
            paper.license = client.fetch_license(paper.arxiv_id)
        write_json(out_dir / "papers.json", papers)

    if args.compare_prompt:
        jobs = [(model, args.prompt_version)]
        labels = [f"{model} {args.prompt_version}", f"{model} {args.compare_prompt}"]
    else:
        jobs = [(model, args.prompt_version), (args.compare_model, args.prompt_version)]
        labels = [model, args.compare_model]

    results: dict[str, dict[str, GenerationResult]] = {label: {} for label in labels}
    calls = 0
    stop_reason = None
    for label, (job_model, version) in zip(labels, jobs, strict=False):
        generator = CardGenerator(
            settings.gemini_api_key, job_model, args.gemini_interval, version
        )
        done, stop_reason, used = run_generator(generator, papers)
        calls += used
        results[label] = {r.paper_id: r for r in done}
        write_json(out_dir / f"{safe_name(job_model)}.json", done)
        if stop_reason:
            break
    if args.compare_prompt:
        results[labels[1]] = load_baseline(model, args.compare_prompt, papers)

    report = build_report(papers, results, calls, stop_reason)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report[report.index("## Summary") :])


if __name__ == "__main__":
    main()
