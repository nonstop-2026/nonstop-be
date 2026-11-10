from typing import Literal

from fastapi import APIRouter, Request

from app.api.errors import ApiError, invalid_parameter
from app.ranking import is_paper_id, parse_ids

router = APIRouter(prefix="/v1/papers", tags=["papers"])


@router.get("")
def papers_by_ids(
    request: Request,
    ids: str | None = None,
    level: Literal["easy", "basic", "expert"] = "basic",
) -> dict:
    wanted = parse_ids(ids, "ids", required=True)
    catalog = request.app.state.catalog
    found = [catalog.get(paper_id) for paper_id in wanted]
    return {"items": [p.summary(level) for p in found if p is not None]}


@router.get("/{paper_id}")
def paper_detail(
    request: Request,
    paper_id: str,
    level: Literal["easy", "basic", "expert"] = "basic",
) -> dict:
    if not is_paper_id(paper_id):
        raise invalid_parameter("paperId")
    paper = request.app.state.catalog.get(paper_id)
    if paper is None:
        raise ApiError(404, "PAPER_NOT_FOUND")
    return paper.detail(level)
