import base64
import binascii
from typing import Literal

from fastapi import APIRouter, Query, Request

from app.api.errors import invalid_parameter
from app.ranking import parse_ids, parse_interests, rank

router = APIRouter(prefix="/v1", tags=["feed"])


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise invalid_parameter("cursor") from None
    if offset < 0:
        raise invalid_parameter("cursor")
    return offset


@router.get("/feed")
def feed(
    request: Request,
    interests: str | None = None,
    exclude: str | None = None,
    level: Literal["easy", "basic", "expert"] = "basic",
    cursor: str | None = None,
    limit: int = Query(default=10, ge=1, le=20),
) -> dict:
    weights = parse_interests(interests)
    excluded = set(parse_ids(exclude, "exclude", required=False))
    start = decode_cursor(cursor)
    papers = request.app.state.catalog.papers()
    ordered = rank([p for p in papers if p.paper_id not in excluded], weights)
    page = ordered[start : start + limit]
    end = start + len(page)
    return {
        "items": [p.summary(level) for p in page],
        "nextCursor": encode_cursor(end) if end < len(ordered) else None,
    }
