"""Query parsing and personalized ordering.

Weights are kept as integer hundredths so sums and ties are exact.
"""

import re
from decimal import Decimal

from app.api.errors import invalid_parameter
from app.cards.schema import KEYWORD_SLUGS
from app.catalog import Paper

MAX_INTEREST_PAIRS = 15
MAX_LISTED_IDS = 50
_INTEREST_PAIR = re.compile(r"^([a-z0-9-]+):(0(?:\.[0-9]{1,2})?|1(?:\.0{1,2})?)$")
_PAPER_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def parse_interests(raw: str | None) -> dict[str, int]:
    """Parse "keyword:weight" pairs into keyword -> weight in hundredths."""
    if raw is None:
        return {}
    pairs = raw.split(",")
    if not 1 <= len(pairs) <= MAX_INTEREST_PAIRS:
        raise invalid_parameter("interests")
    interests: dict[str, int] = {}
    for pair in pairs:
        match = _INTEREST_PAIR.match(pair)
        if not match or match.group(1) not in KEYWORD_SLUGS:
            raise invalid_parameter("interests")
        if match.group(1) in interests:
            raise invalid_parameter("interests")
        interests[match.group(1)] = int(Decimal(match.group(2)) * 100)
    return interests


def parse_keywords(raw: str | None) -> list[str]:
    if raw is None:
        return []
    keywords = [k for k in raw.split(",") if k]
    if len(keywords) > len(KEYWORD_SLUGS) or any(
        k not in KEYWORD_SLUGS for k in keywords
    ):
        raise invalid_parameter("keywords")
    return list(dict.fromkeys(keywords))


def parse_ids(raw: str | None, name: str, *, required: bool) -> list[str]:
    """Comma-separated paper ids, at most 50, duplicates removed in order."""
    if raw is None and not required:
        return []
    ids = [i for i in (raw or "").split(",") if i]
    if (required and not ids) or len(ids) > MAX_LISTED_IDS:
        raise invalid_parameter(name)
    if any(not _PAPER_ID.match(i) for i in ids):
        raise invalid_parameter(name)
    return list(dict.fromkeys(ids))


def is_paper_id(value: str) -> bool:
    return bool(_PAPER_ID.match(value))


def score(paper: Paper, interests: dict[str, int]) -> int:
    return sum(interests.get(tag, 0) for tag in paper.tags)


def rank(papers: list[Paper], interests: dict[str, int]) -> list[Paper]:
    """Score high to low, then newest first, then paperId, so the order never changes."""
    ordered = sorted(papers, key=lambda p: p.paper_id)
    ordered.sort(key=lambda p: p.published_at, reverse=True)
    ordered.sort(key=lambda p: score(p, interests), reverse=True)
    return ordered
