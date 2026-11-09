import json

import pytest
from google.genai import errors

from app.cards.generate import CardGenerator, QuotaExceeded

CARD = {
    "oneLine": "one line",
    "whyImportant": "First. Second.",
    "keyPoints": ["a"],
    "method": None,
    "dataset": None,
    "limitation": None,
}
BODY = json.dumps(
    {"titleKo": "title", "tags": ["llm"], "easy": CARD, "basic": CARD, "expert": CARD}
)


def api_error(code, status):
    return errors.APIError(code, {"error": {"code": code, "status": status}})


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("Response", (), {"text": outcome})()


def make_generator(outcomes):
    generator = CardGenerator("test-key", "test-model", interval_seconds=0)
    models = FakeModels(outcomes)
    generator._client = type("Client", (), {"models": models})()
    return generator, models


def test_retries_once_on_503():
    generator, models = make_generator([api_error(503, "UNAVAILABLE"), BODY])
    result = generator.generate("2611.00001", "Title", "Abstract.")
    assert result.ok
    assert result.api_calls == 2
    assert models.calls == 2


def test_gives_up_after_second_503():
    errors_503 = [api_error(503, "UNAVAILABLE"), api_error(503, "UNAVAILABLE")]
    generator, models = make_generator(errors_503)
    result = generator.generate("2611.00001", "Title", "Abstract.")
    assert not result.ok
    assert result.api_calls == 2
    assert models.calls == 2


def test_quota_error_is_not_retried():
    generator, models = make_generator([api_error(429, "RESOURCE_EXHAUSTED")])
    with pytest.raises(QuotaExceeded) as info:
        generator.generate("2611.00001", "Title", "Abstract.")
    assert info.value.api_calls == 1
    assert models.calls == 1
