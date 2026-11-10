import logging
import threading
import time

from google.api_core.exceptions import ServiceUnavailable

from app.cards.schema import KEYWORD_SLUGS
from app.catalog import Catalog
from tests.api_fixtures import (
    SPEC,
    assert_problem,
    assert_schema,
    make_client,
    make_paper,
    sample_papers,
)

LIKE_AND_DISLIKE = {
    "keywords": ["llm", "rag", "agents"],
    "swipes": [
        {"paperId": "2611.01000", "reaction": "like"},
        {"paperId": "2611.01100", "reaction": "dislike"},
        {"paperId": "2611.00000", "reaction": "skip"},
        {"paperId": "9999.99999", "reaction": "like"},
    ],
}


def test_keyword_constant_matches_openapi():
    assert list(KEYWORD_SLUGS) == SPEC["components"]["schemas"]["KeywordSlug"]["enum"]


def test_onboarding_cards_schema_and_mix(client):
    response = client.get("/v1/onboarding/cards", params={"keywords": "llm,rag"})
    assert response.status_code == 200
    body = response.json()
    assert_schema(body, "PaperList")
    items = body["items"]
    assert len(items) == 10
    assert {i["level"] for i in items} == {"basic"}
    counts = {}
    for item in items:
        counts[item["primaryCategory"]] = counts.get(item["primaryCategory"], 0) + 1
    assert max(counts.values()) <= 3
    assert len(counts) == 5
    assert set(items[0]["tags"]) & {"llm", "rag"}


def test_onboarding_weights_example_like_and_dislike(client):
    response = client.post("/v1/onboarding/weights", json=LIKE_AND_DISLIKE)
    assert response.status_code == 200
    body = response.json()
    assert_schema(body, "OnboardingWeightsResponse")
    # 2611.01000 is tagged llm (+0.15), 2611.01100 is tagged rag (-0.1).
    assert body["interests"] == [
        {"keyword": "llm", "weight": 0.65},
        {"keyword": "agents", "weight": 0.5},
        {"keyword": "rag", "weight": 0.4},
    ]


def test_onboarding_weights_example_clip_and_new_keyword(client):
    swipes = [{"paperId": "2611.01000", "reaction": "like"}] * 4
    swipes += [{"paperId": "2611.00000", "reaction": "like"}]
    swipes += [{"paperId": "2611.01100", "reaction": "dislike"}] * 5
    body = {"keywords": ["llm", "rag", "xr"], "swipes": swipes}
    response = client.post("/v1/onboarding/weights", json=body)
    # llm: 0.5 + 4 x 0.15 clipped to 1. rag: 0.5 - 5 x 0.1 = 0, dropped.
    # agents was not chosen but a liked paper carries it.
    assert response.json()["interests"] == [
        {"keyword": "llm", "weight": 1.0},
        {"keyword": "xr", "weight": 0.5},
        {"keyword": "agents", "weight": 0.15},
    ]


def test_onboarding_weights_errors(client):
    few = client.post(
        "/v1/onboarding/weights", json={"keywords": ["llm"], "swipes": []}
    )
    assert_problem(few, 400, "TOO_FEW_KEYWORDS")
    unknown = {"keywords": ["llm", "rag", "quantum"], "swipes": []}
    assert_problem(
        client.post("/v1/onboarding/weights", json=unknown), 400, "INVALID_PARAMETER"
    )
    missing = client.post("/v1/onboarding/weights", json={"keywords": ["llm"]})
    assert_problem(missing, 400, "INVALID_PARAMETER")


def test_home_schema_and_rules(client):
    params = {"interests": "rag:0.9,llm:0.4", "exclude": "2611.01000", "level": "easy"}
    response = client.get("/v1/home", params=params)
    assert response.status_code == 200
    body = response.json()
    assert_schema(body, "HomeResponse")
    assert body["isFallback"] is False
    today = body["todayPaper"]
    # Latest batch is 2026-11-06; 2611.01000 (llm) is excluded.
    assert today["publishedAt"] == "2026-11-06"
    assert today["paperId"] != "2611.01000"
    assert today["oneLine"].startswith("easy")
    ids = [p["paperId"] for p in body["recommendations"]]
    assert len(ids) == 10
    assert today["paperId"] not in ids
    assert "2611.01000" not in ids
    assert ids[0] == "2611.01100"


def test_home_today_paper_null_when_batch_excluded(client):
    latest = [p.paper_id for p in sample_papers() if p.published_at == "2026-11-06"]
    response = client.get("/v1/home", params={"exclude": ",".join(latest)})
    body = response.json()
    assert_schema(body, "HomeResponse")
    assert body["todayPaper"] is None


def test_feed_pages_cover_everything_once(client):
    params = {
        "interests": "llm:0.7,ux-research:0.3",
        "exclude": "2611.00000",
        "limit": 4,
    }
    seen, cursor = [], None
    while True:
        page = client.get(
            "/v1/feed", params=params | ({"cursor": cursor} if cursor else {})
        )
        assert page.status_code == 200
        assert_schema(page.json(), "FeedResponse")
        seen += [p["paperId"] for p in page.json()["items"]]
        cursor = page.json()["nextCursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 14
    assert "2611.00000" not in seen
    assert seen[:3] == ["2611.01000", "2611.01200", "2611.03000"]


def test_feed_rejects_bad_cursor_and_limit(client):
    assert_problem(
        client.get("/v1/feed", params={"cursor": "!!"}), 400, "INVALID_PARAMETER"
    )
    assert_problem(
        client.get("/v1/feed", params={"limit": 21}), 400, "INVALID_PARAMETER"
    )


def test_papers_by_ids_keeps_order_and_skips_unknown(client):
    response = client.get(
        "/v1/papers", params={"ids": "2611.04200,9999.00001,2611.00000"}
    )
    assert response.status_code == 200
    body = response.json()
    assert_schema(body, "PaperList")
    assert [p["paperId"] for p in body["items"]] == ["2611.04200", "2611.00000"]


def test_paper_detail_schema_and_not_found(client):
    response = client.get("/v1/papers/2611.02100", params={"level": "expert"})
    assert response.status_code == 200
    body = response.json()
    assert_schema(body, "PaperDetail")
    assert body["card"]["oneLine"].startswith("expert")
    assert "model" not in body["card"]
    assert_problem(client.get("/v1/papers/9999.00001"), 404, "PAPER_NOT_FOUND")
    assert_problem(client.get("/v1/papers/bad%20id"), 400, "INVALID_PARAMETER")
    assert_problem(
        client.get("/v1/papers/2611.02100", params={"level": "hard"}),
        400,
        "INVALID_PARAMETER",
    )


def test_firestore_outage_returns_503_everywhere():
    def broken():
        raise ServiceUnavailable("down")

    outage = make_client(loader=broken)
    assert_problem(outage.get("/v1/home"), 503, "SERVICE_UNAVAILABLE")
    assert_problem(outage.get("/v1/feed"), 503, "SERVICE_UNAVAILABLE")
    weights = outage.post("/v1/onboarding/weights", json=LIKE_AND_DISLIKE)
    assert_problem(weights, 503, "SERVICE_UNAVAILABLE")
    assert "down" not in weights.text


def test_unexpected_error_hides_details():
    def crash():
        raise RuntimeError("secret path /Users/someone/key.json")

    response = make_client(loader=crash).get("/v1/home")
    assert_problem(response, 500, "INTERNAL_ERROR")
    assert "secret" not in response.text


def test_catalog_loads_once_under_concurrency():
    calls = []

    def slow_loader():
        calls.append(1)
        time.sleep(0.05)
        return sample_papers()

    catalog = Catalog(slow_loader)
    threads = [threading.Thread(target=catalog.papers) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1


def test_access_log_has_path_but_no_query(client, caplog):
    caplog.set_level(logging.INFO, logger="nonstop.access")
    client.get("/v1/home", params={"interests": "llm:0.5", "exclude": "2611.01000"})
    text = caplog.text
    assert "GET /v1/home 200" in text
    assert "interests" not in text
    assert "llm" not in text
    assert "2611.01000" not in text


def test_cors_allows_configured_origin_only(client):
    allowed = client.get("/v1/home", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/v1/home", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_onboarding_spreads_preferred_papers_across_fields():
    papers = [
        make_paper(f"2611.1{i:04d}", category, "2026-11-06", tags)
        for i, (category, tags) in enumerate(
            [("cs.AI", ("agents", "llm"))] * 4
            + [("cs.HC", ("ux-research",))] * 4
            + [("cs.SE", ("agents",))] * 4
            + [("cs.CL", ("llm",))]
            + [("cs.CV", ())] * 2
        )
    ]
    response = make_client(papers).get(
        "/v1/onboarding/cards", params={"keywords": "agents,ux-research,llm"}
    )
    categories = [item["primaryCategory"] for item in response.json()["items"]]
    assert "cs.CL" in categories[:5]
    assert all(categories.count(c) <= 3 for c in set(categories))
    assert len(categories) == 10


def test_openapi_documents_503_for_every_endpoint():
    for path, methods in SPEC["paths"].items():
        for method, operation in methods.items():
            assert "503" in operation["responses"], (method, path)
