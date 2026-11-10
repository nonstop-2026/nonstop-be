from app.cards.generate import QuotaExceeded
from scripts.regenerate import HiddenPaper, run_regenerate
from tests.test_store import make_result


def hidden(i):
    return HiddenPaper(
        f"2611.000{i:02d}", f"2611.000{i:02d}", "Title", "cs.HC", "Abstract."
    )


def test_saves_only_passing_cards():
    saved = []
    results = {
        "2611.00000": make_result(),
        "2611.00001": make_result(issues={"bannedWords": ["해결"]}),
    }
    state = run_regenerate(
        [hidden(0), hidden(1)],
        lambda p: results[p.paper_id],
        lambda p, r: saved.append(p.paper_id),
        max_calls=120,
    )
    assert saved == ["2611.00000"]
    assert [a.passed for a in state.attempts] == [True, False]
    assert state.writes == 4


def test_dropped_tags_alone_still_pass():
    saved = []
    result = make_result(issues={"droppedTags": ["ll5"]})
    run_regenerate([hidden(0)], lambda p: result, lambda p, r: saved.append(p), 120)
    assert len(saved) == 1


def test_stops_when_pass_ratio_is_below_half_at_checkpoint():
    papers = [hidden(i) for i in range(12)]
    failing = make_result(issues={"length": ["expert.oneLine 70 chars"]})
    state = run_regenerate(papers, lambda p: failing, lambda p, r: None, 120)
    assert len(state.attempts) == 10
    assert len(state.remaining) == 2
    assert state.stop_reason == "Pass ratio 0% after 10"


def test_stops_on_quota_and_call_limit():
    def generate(paper):
        raise QuotaExceeded("HTTP 429")

    state = run_regenerate([hidden(0), hidden(1)], generate, lambda p, r: None, 120)
    assert state.calls == 1
    assert len(state.remaining) == 2

    state = run_regenerate(
        [hidden(0), hidden(1)], lambda p: make_result(), lambda p, r: None, 2
    )
    assert state.calls == 1
    assert len(state.remaining) == 1
