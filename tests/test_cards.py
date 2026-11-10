from app.cards.generate import (
    count_sentences,
    find_banned_words,
    find_forbidden_chars,
    find_formal_endings,
    find_korean_numerals,
    find_unsupported_numbers,
    has_blocking_issues,
    run_checks,
)
from app.cards.schema import CardContent, GeneratedPaperCards, clean_tags

CARD = CardContent(
    oneLine="한 줄",
    whyImportant="첫 문장이에요. 두 번째 문장이에요.",
    keyPoints=["요점"],
    method=None,
    dataset=None,
    limitation=None,
)


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
    texts = ["효율을 극대화해요.", "문제를 해결했어요.", "개선했다고 보고해요."]
    assert find_banned_words(texts) == ["해결", "극대화"]


def test_banned_words_ignore_unresolved():
    assert find_banned_words(["미해결 질문을 잇는 경로"]) == []


def test_banned_words_allow_attributed_claims():
    texts = [
        "지연을 해결했다고 보고해요.",
        "성능 향상을 입증했다고 주장해요.",
        "한계를 완벽하게 밝혀요.",
        "새 기준을 제시하며 문제를 해결해요.",
    ]
    assert find_banned_words(texts) == []


def test_banned_words_judged_per_sentence():
    text = "저자들은 결과를 보고해요. 이 방법은 문제를 해결해요."
    assert find_banned_words([text]) == ["해결"]


def test_banned_words_allow_purpose_forms():
    texts = [
        "병목을 해결하기 위해 캐시를 써요.",
        "지연을 해결하려 했어요.",
        "오류를 해결하고자 해요.",
    ]
    assert find_banned_words(texts) == []


def test_proof_is_not_banned():
    assert find_banned_words(["정리를 증명하는 과정을 다뤄요."]) == []


def test_title_numbers_are_supported():
    cards = GeneratedPaperCards(
        titleKo="VocabulARy 2.0: 단어 학습",
        tags=[],
        easy=CARD,
        basic=CARD,
        expert=CARD,
    )
    issues = run_checks(
        cards, "An abstract without numbers.", [], title="VocabulARy 2.0"
    )
    assert issues["unsupportedNumbers"] == []
    assert run_checks(cards, "An abstract without numbers.", [])[
        "unsupportedNumbers"
    ] == ["2.0"]


def test_dropped_tags_do_not_block():
    assert not has_blocking_issues({"droppedTags": ["ll5"], "length": []})
    assert has_blocking_issues({"droppedTags": [], "length": ["titleKo 41 chars"]})
