from app.cards.generate import (
    count_sentences,
    find_banned_words,
    find_forbidden_chars,
    find_formal_endings,
    find_korean_numerals,
    find_unsupported_numbers,
)
from app.cards.schema import clean_tags


def test_clean_tags_drops_unknown_values():
    kept, dropped = clean_tags(["llm", "quantum", "rag"])
    assert kept == ["llm", "rag"]
    assert dropped == ["quantum"]


def test_clean_tags_keeps_at_most_three():
    kept, dropped = clean_tags(["llm", "rag", "agents", "xr"])
    assert kept == ["llm", "rag", "agents"]
    assert dropped == ["xr"]


def test_unsupported_numbers():
    abstract = "Accuracy improves by 12.5 points on 3 benchmarks with 1,000 users."
    texts = ["12.5점 올랐다고 보고했어요.", "3개 벤치마크, 1000명", "약 40% 줄었어요."]
    assert find_unsupported_numbers(texts, abstract) == ["40"]


def test_forbidden_chars():
    texts = ["좋아요—정말", "끝이에요!", "괜찮아요 \U0001f600", "문제없어요."]
    assert find_forbidden_chars(texts) == ["—", "!", "\U0001f600"]


def test_count_sentences():
    assert count_sentences("첫 문장이에요. 두 번째 문장이에요.") == 2


def test_formal_endings_by_field():
    labeled = [
        ("easy.oneLine", "오개입이 27% 줄었다고 보고"),
        ("easy.whyImportant", "첫 문장이에요. 두 번째 문장입니다."),
        ("basic.method", "새 방식을 제안합니다"),
    ]
    assert find_formal_endings(labeled) == ["easy.whyImportant", "basic.method"]


def test_korean_numerals_before_units():
    texts = [
        "일천 개의 문서",
        "육천사백명이 참여",
        "백 명을 조사했어요",
        "이 개념은 1,000개",
    ]
    assert find_korean_numerals(texts) == ["일천 개", "육천사백명", "백 명"]


def test_banned_words():
    texts = ["효율을 극대화해요", "문제를 해결했다고 보고", "개선했다고 보고해요"]
    assert find_banned_words(texts) == ["해결", "극대화"]


def test_banned_words_ignore_unresolved():
    texts = ["미해결 질문을 잇는 경로", "문제를 해결한다고 보고해요"]
    assert find_banned_words(texts[:1]) == []
    assert find_banned_words(texts) == ["해결"]
