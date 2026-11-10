from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Request

from app.catalog import Paper
from app.ranking import parse_ids, parse_interests, rank

router = APIRouter(prefix="/v1", tags=["home"])

# Korea has no daylight saving time, so a fixed offset equals Asia/Seoul.
KST = timezone(timedelta(hours=9), "Asia/Seoul")
FALLBACK_DAYS = 3
RECOMMENDATIONS = 10


def today_in_korea() -> date:
    return datetime.now(KST).date()


def todays_batch(papers: list[Paper], today: date) -> tuple[list[Paper], bool]:
    """Papers from the latest publishedAt date in the catalog.

    Falls back to papers from the last 3 days only when that batch is empty, which in the
    demo happens only when the catalog itself is empty.
    """
    latest = max((p.published_at for p in papers), default=None)
    batch = [p for p in papers if p.published_at == latest]
    if batch:
        return batch, False
    since = (today - timedelta(days=FALLBACK_DAYS)).isoformat()
    return [p for p in papers if p.published_at >= since], True


@router.get("/home")
def home(
    request: Request,
    interests: str | None = None,
    exclude: str | None = None,
    level: Literal["easy", "basic", "expert"] = "basic",
) -> dict:
    weights = parse_interests(interests)
    excluded = set(parse_ids(exclude, "exclude", required=False))
    papers = request.app.state.catalog.papers()
    today = today_in_korea()
    batch, is_fallback = todays_batch(papers, today)

    candidates = rank([p for p in batch if p.paper_id not in excluded], weights)
    today_paper = candidates[0] if candidates else None
    skip = excluded | ({today_paper.paper_id} if today_paper else set())
    recommendations = rank([p for p in papers if p.paper_id not in skip], weights)
    return {
        "today": today.isoformat(),
        "todayPaper": today_paper.summary(level) if today_paper else None,
        "recommendations": [
            p.summary(level) for p in recommendations[:RECOMMENDATIONS]
        ],
        "isFallback": is_fallback,
    }
