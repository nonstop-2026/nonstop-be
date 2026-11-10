"""Shared fake catalog and openapi.yaml validators for API tests."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from app.catalog import Catalog, Paper
from app.main import create_app

SPEC = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text("utf-8"))

CATEGORIES = ("cs.AI", "cs.CL", "cs.CV", "cs.HC", "cs.SE")
TAGS = {
    "cs.AI": ("agents", "reasoning"),
    "cs.CL": ("llm", "rag"),
    "cs.CV": ("multimodal", "image-generation"),
    "cs.HC": ("ux-research", "accessibility"),
    "cs.SE": ("code-generation", "developer-tools"),
}
DATES = ("2026-11-06", "2026-11-05", "2026-11-04")


def card(level: str, paper_id: str) -> dict:
    return {
        "oneLine": f"{level} one line for {paper_id}",
        "whyImportant": "First. Second.",
        "keyPoints": ["point"],
        "method": None,
        "dataset": "data",
        "limitation": None,
    }


def make_paper(paper_id: str, category: str, published_at: str, tags: tuple) -> Paper:
    return Paper(
        paper_id=paper_id,
        arxiv_id=paper_id,
        version=1,
        title=f"Title {paper_id}",
        title_ko=f"제목 {paper_id}",
        primary_category=category,
        venue=None,
        published_at=published_at,
        authors=("Ada Example",),
        tags=tags,
        abstract_url=f"https://arxiv.org/abs/{paper_id}",
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        license="http://creativecommons.org/licenses/by/4.0/",
        cards={lvl: card(lvl, paper_id) for lvl in ("easy", "basic", "expert")},
    )


def sample_papers() -> list[Paper]:
    """15 papers: 3 per field, one per date, each with one field tag."""
    papers = []
    for c_index, category in enumerate(CATEGORIES):
        for d_index, published_at in enumerate(DATES):
            paper_id = f"2611.0{c_index}{d_index}00"
            tag = TAGS[category][d_index % 2]
            papers.append(make_paper(paper_id, category, published_at, (tag,)))
    return papers


def make_client(papers: list[Paper] | None = None, loader=None) -> TestClient:
    catalog = Catalog(
        loader or (lambda: papers if papers is not None else sample_papers())
    )
    app = create_app(catalog, cors_origins=["http://localhost:5173"])
    return TestClient(app, raise_server_exceptions=False)


def assert_schema(instance: object, name: str) -> None:
    schema = {"$ref": f"#/components/schemas/{name}", "components": SPEC["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(instance)


def assert_problem(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert_schema(body, "Problem")
    assert body["code"] == code
    assert body["type"] == "about:blank"
