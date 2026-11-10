import random

import pytest

from app.api.errors import ApiError
from app.cards.schema import KEYWORD_SLUGS
from app.ranking import parse_ids, parse_interests, rank
from tests.api_fixtures import (
    assert_problem,
    make_paper,
    sample_papers,
)


def test_parse_interests_valid():
    assert parse_interests("llm:0.62,ux-research:1,rag:0") == {
        "llm": 62,
        "ux-research": 100,
        "rag": 0,
    }
    assert parse_interests(None) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "llm:1.01",
        "llm:1.5",
        "llm:-0.1",
        "quantum:0.5",
        "hci:0.5",
        "llm",
        "llm:0.555",
        "llm:.5",
        "llm:0.5,",
        "",
        "llm:0.5,llm:0.4",
        ",".join(f"llm{i}:0.1" for i in range(16)),
    ],
)
def test_parse_interests_rejects(raw):
    with pytest.raises(ApiError) as info:
        parse_interests(raw)
    assert info.value.code == "INVALID_PARAMETER"


def test_parse_interests_pair_count():
    all_slugs = ",".join(f"{s}:0.1" for s in KEYWORD_SLUGS)
    assert len(parse_interests(all_slugs)) == 15
    with pytest.raises(ApiError):
        parse_interests(all_slugs + ",llm:0.2")


def test_list_limits(client):
    fifty_one = ",".join(f"2611.{i:05d}" for i in range(51))
    assert (
        len(
            parse_ids(
                ",".join(f"2611.{i:05d}" for i in range(50)), "ids", required=True
            )
        )
        == 50
    )
    assert_problem(
        client.get("/v1/home", params={"exclude": fifty_one}), 400, "INVALID_PARAMETER"
    )
    assert_problem(
        client.get("/v1/papers", params={"ids": fifty_one}), 400, "INVALID_PARAMETER"
    )
    assert_problem(
        client.get("/v1/papers", params={"ids": ""}), 400, "INVALID_PARAMETER"
    )
    assert_problem(client.get("/v1/papers"), 400, "INVALID_PARAMETER")


def test_rank_is_deterministic():
    papers = sample_papers()
    interests = {"llm": 50, "rag": 50, "agents": 50}
    expected = [p.paper_id for p in rank(papers, interests)]
    for seed in range(5):
        shuffled = papers[:]
        random.Random(seed).shuffle(shuffled)
        assert [p.paper_id for p in rank(shuffled, interests)] == expected


def test_rank_ties_break_by_date_then_id():
    a = make_paper("2611.00002", "cs.CL", "2026-11-05", ("llm",))
    b = make_paper("2611.00001", "cs.CL", "2026-11-05", ("llm",))
    c = make_paper("2611.00003", "cs.CL", "2026-11-06", ("llm",))
    d = make_paper("2611.00004", "cs.CL", "2026-11-04", ("llm", "rag"))
    ordered = rank([a, b, c, d], {"llm": 50, "rag": 10})
    assert [p.paper_id for p in ordered] == [
        "2611.00004",
        "2611.00003",
        "2611.00001",
        "2611.00002",
    ]
