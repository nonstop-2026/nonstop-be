from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.errors import ApiError, invalid_parameter
from app.cards.schema import KEYWORD_SLUGS
from app.catalog import Paper
from app.ingest.arxiv import CATEGORIES
from app.ranking import parse_keywords

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])

ONBOARDING_CARDS = 10
MAX_PER_CATEGORY = 3
MIN_KEYWORDS = 3
# Weights in hundredths: start, like and dislike steps.
START_WEIGHT = 50
LIKE_STEP = 15
DISLIKE_STEP = -10
MAX_WEIGHT = 100


def pick_onboarding(papers: list[Paper], keywords: list[str]) -> list[Paper]:
    """Up to 10 papers, at most 3 per field, spread across fields.

    Papers tagged with a chosen keyword come first, taken one field at a time so a few
    fields cannot fill every slot. Within a field: most matching tags, then newest. The
    remaining slots are filled the same way from the other papers.
    """
    wanted = set(keywords)
    newest = sorted(papers, key=lambda p: p.paper_id)
    newest.sort(key=lambda p: p.published_at, reverse=True)
    preferred = sorted(
        (p for p in newest if wanted & set(p.tags)),
        key=lambda p: len(wanted & set(p.tags)),
        reverse=True,
    )
    rest = [p for p in newest if not wanted & set(p.tags)]

    picked: list[Paper] = []
    counts = dict.fromkeys(CATEGORIES, 0)
    for group in (preferred, rest):
        queues = {c: [p for p in group if p.primary_category == c] for c in CATEGORIES}
        while len(picked) < ONBOARDING_CARDS and any(
            queues[c] and counts[c] < MAX_PER_CATEGORY for c in CATEGORIES
        ):
            for category in CATEGORIES:
                if len(picked) == ONBOARDING_CARDS:
                    break
                if queues[category] and counts[category] < MAX_PER_CATEGORY:
                    picked.append(queues[category].pop(0))
                    counts[category] += 1
    return picked


@router.get("/cards")
def onboarding_cards(request: Request, keywords: str | None = None) -> dict:
    papers = request.app.state.catalog.papers()
    chosen = pick_onboarding(papers, parse_keywords(keywords))
    return {"items": [p.summary("basic") for p in chosen]}


class Swipe(BaseModel):
    paperId: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    reaction: Literal["like", "dislike", "skip"]


class WeightsRequest(BaseModel):
    keywords: list[str] = Field(max_length=len(KEYWORD_SLUGS))
    swipes: list[Swipe] = Field(max_length=10)


def compute_weights(
    keywords: list[str], swipes: list[Swipe], papers: dict[str, Paper]
) -> list[dict]:
    weights = dict.fromkeys(keywords, START_WEIGHT)
    for swipe in swipes:
        paper = papers.get(swipe.paperId)
        if paper is None or swipe.reaction == "skip":
            continue
        step = LIKE_STEP if swipe.reaction == "like" else DISLIKE_STEP
        for tag in paper.tags:
            if tag in weights or swipe.reaction == "like":
                weights[tag] = weights.get(tag, 0) + step
    clipped = {k: min(max(v, 0), MAX_WEIGHT) for k, v in weights.items()}
    ordered = sorted(
        (k for k, v in clipped.items() if v > 0), key=lambda k: (-clipped[k], k)
    )
    return [
        {"keyword": k, "weight": clipped[k] / 100}
        for k in ordered[: len(KEYWORD_SLUGS)]
    ]


@router.post("/weights")
def onboarding_weights(request: Request, body: WeightsRequest) -> dict:
    if any(k not in KEYWORD_SLUGS for k in body.keywords) or len(
        set(body.keywords)
    ) != len(body.keywords):
        raise invalid_parameter("keywords")
    if len(body.keywords) < MIN_KEYWORDS:
        raise ApiError(400, "TOO_FEW_KEYWORDS")
    papers = {p.paper_id: p for p in request.app.state.catalog.papers()}
    return {"interests": compute_weights(body.keywords, body.swipes, papers)}
