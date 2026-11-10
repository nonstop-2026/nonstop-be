import pytest

from app.cards.generate import find_korean_numerals, find_unsupported_numbers
from app.cards.numbers import abstract_values, output_numbers, word_number_values


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We evaluate six models", {6}),
        ("twenty-five tasks", {25}),
        ("one hundred and twenty instances", {120}),
        ("two thousand twelve", {2012}),
        ("1.5 million windows", {1_500_000}),
        ("three four", {3, 4}),
        ("three and four", {3, 4}),
    ],
)
def test_word_number_values(text, expected):
    assert word_number_values(text) == expected


def test_abstract_values_scale_unit_suffixes():
    values = abstract_values("a 30M model, 1.5K users, 2B tokens and 30MB files")
    assert {30_000_000, 1_500, 2_000_000_000, 30} <= values


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("3,000만", 30_000_000),
        ("3천만", 30_000_000),
        ("1,500만 개", 15_000_000),
        ("30M", 30_000_000),
        ("61.4%", 61.4),
    ],
)
def test_output_numbers(text, value):
    assert output_numbers(text)[0][1] == value


def test_unsupported_numbers_accept_words_and_units():
    abstract = "We study six models with a 30M-parameter encoder on 15M windows."
    texts = ["6개 모델", "3,000만 매개변수", "1,500만 개 윈도우", "3천만", "7개 과제"]
    assert find_unsupported_numbers(texts, abstract) == ["7"]


def test_korean_numerals_skip_magnitude_after_digits():
    texts = ["1,500만 개 윈도우", "3천만 개", "일천 개"]
    assert find_korean_numerals(texts) == ["일천 개"]
