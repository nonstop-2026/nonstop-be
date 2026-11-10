from scripts.recheck import recheck_paper

STORED = {
    "oneLine": "6개 모델을 비교했다고 보고",
    "whyImportant": "첫 문장이에요. 두 번째 문장이에요.",
    "keyPoints": ["3,000만 매개변수 모델을 썼다고 보고"],
    "method": None,
    "dataset": None,
    "limitation": None,
    "model": "test-model",
    "promptVersion": "v0.3",
    "generatedAt": "now",
}
PAPER = {"titleKo": "제목", "tags": ["llm"], "status": "hidden"}
ABSTRACT = "We compare six models using a 30M-parameter encoder."


def test_recheck_turns_number_word_false_positive_ready():
    cards = dict.fromkeys(("easy", "basic", "expert"), STORED)
    status, issues = recheck_paper(PAPER, cards, ABSTRACT)
    assert status == "ready"
    assert not any(issues.values())


def test_recheck_keeps_real_issue_hidden():
    flagged = STORED | {"whyImportant": "문제를 해결해요. 두 번째 문장이에요."}
    cards = {"easy": flagged, "basic": STORED, "expert": STORED}
    status, issues = recheck_paper(PAPER, cards, ABSTRACT)
    assert status == "hidden"
    assert issues["bannedWords"] == ["해결"]
