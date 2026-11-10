import re
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.cards.numbers import abstract_values, output_numbers
from app.cards.prompts import LATEST_PROMPT_VERSION, get_prompt, v0_2
from app.cards.schema import GeneratedPaperCards, clean_tags

DEFAULT_INTERVAL_SECONDS = 4.5
TEMPERATURE = 0.3
TITLE_KO_MAX_CHARS = 40
ONE_LINE_MAX_CHARS = 60
# Default for prompts that do not define WHY_IMPORTANT_SENTENCES (v0.1).
WHY_IMPORTANT_SENTENCES = (2, 3)
BANNED_WORDS = v0_2.BANNED_WORDS
# Words that contain a banned word but are neutral, such as "unresolved".
BANNED_WORD_EXCEPTIONS = ("미해결",)
LEVELS = ("easy", "basic", "expert")
CARD_TEXT_FIELDS = ("oneLine", "whyImportant", "method", "dataset", "limitation")

_SENTENCE_END = re.compile(r"(?<=[.?!])\s+")
_FORMAL_ENDING = re.compile(r"니다[.?!]?$")
# A Korean numeral containing a magnitude (ten, hundred, ...) right before a unit, as in
# "il-cheon gae" (one thousand items). Single-digit numerals are skipped because the
# demonstrative "i" (this) followed by "gae" would match.
_KOREAN_NUMERAL = re.compile(
    r"(?<![가-힣0-9,.])([일이삼사오육칠팔구]*[십백천만억][일이삼사오육칠팔구십백천만억]*)"
    r"\s?(개월|개|명|편|건|배|년|시간|분|초|회|장|종|가지|점|차|쌍|층|단계|퍼센트)"
)
_FORBIDDEN = re.compile(
    "[—–!！☀-➿\U0001f000-\U0001faff️]",
)


# Gemini status codes that get exactly one retry. 429 is never retried.
TRANSIENT_CODES = (503,)


class QuotaExceeded(Exception):
    """Raised on HTTP 429 so the caller can stop without retrying."""

    def __init__(self, message: str, api_calls: int = 1):
        super().__init__(message)
        self.api_calls = api_calls


@dataclass
class GenerationResult:
    paper_id: str
    model: str
    prompt_version: str
    ok: bool
    elapsed_seconds: float
    cards: dict | None = None
    error: str | None = None
    issues: dict[str, list[str]] = field(default_factory=dict)
    # Requests sent to Gemini for this result, including a transient retry.
    api_calls: int = 1


def output_texts(cards: GeneratedPaperCards) -> list[str]:
    texts = [cards.titleKo]
    for level in LEVELS:
        card = getattr(cards, level)
        texts.extend(getattr(card, name) or "" for name in CARD_TEXT_FIELDS)
        texts.extend(card.keyPoints)
    return texts


def find_unsupported_numbers(texts: list[str], abstract: str) -> list[str]:
    """Numbers in the cards whose value never appears in the abstract.

    The abstract may spell values as words ("six") or with K, M, B suffixes ("30M"),
    and cards may use Korean magnitudes ("3,000만", "3천만").
    """
    allowed = abstract_values(abstract)
    found: list[str] = []
    for text in texts:
        for number, value in output_numbers(text):
            if value not in allowed and number not in found:
                found.append(number)
    return found


def find_forbidden_chars(texts: list[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        for char in _FORBIDDEN.findall(text):
            if char not in found:
                found.append(char)
    return found


def labeled_texts(cards: GeneratedPaperCards) -> list[tuple[str, str]]:
    labeled = [("titleKo", cards.titleKo)]
    for level in LEVELS:
        card = getattr(cards, level)
        labeled += [
            (f"{level}.{name}", getattr(card, name))
            for name in CARD_TEXT_FIELDS
            if getattr(card, name)
        ]
        labeled += [
            (f"{level}.keyPoints[{i}]", point) for i, point in enumerate(card.keyPoints)
        ]
    return labeled


def find_formal_endings(labeled: list[tuple[str, str]]) -> list[str]:
    """Return field labels that contain a sentence ending in the formal "-nida"."""
    return [
        label
        for label, text in labeled
        if any(_FORMAL_ENDING.search(s) for s in _SENTENCE_END.split(text.strip()))
    ]


def find_korean_numerals(texts: list[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in _KOREAN_NUMERAL.finditer(text):
            if match.group(0) not in found:
                found.append(match.group(0))
    return found


def find_banned_words(texts: list[str]) -> list[str]:
    cleaned = []
    for text in texts:
        for allowed in BANNED_WORD_EXCEPTIONS:
            text = text.replace(allowed, "")
        cleaned.append(text)
    return [word for word in BANNED_WORDS if any(word in text for text in cleaned)]


def count_sentences(text: str) -> int:
    return len([part for part in _SENTENCE_END.split(text.strip()) if part])


def find_length_issues(
    cards: GeneratedPaperCards,
    why_sentences: tuple[int, int] = WHY_IMPORTANT_SENTENCES,
) -> list[str]:
    issues = []
    if len(cards.titleKo) > TITLE_KO_MAX_CHARS:
        issues.append(f"titleKo {len(cards.titleKo)} chars")
    low, high = why_sentences
    for level in LEVELS:
        card = getattr(cards, level)
        if len(card.oneLine) > ONE_LINE_MAX_CHARS:
            issues.append(f"{level}.oneLine {len(card.oneLine)} chars")
        sentences = count_sentences(card.whyImportant)
        if not low <= sentences <= high:
            issues.append(f"{level}.whyImportant {sentences} sentences")
    return issues


CHECK_NAMES = (
    "unsupportedNumbers",
    "length",
    "forbiddenChars",
    "droppedTags",
    "formalEndings",
    "koreanNumerals",
    "bannedWords",
)


def run_checks(
    cards: GeneratedPaperCards,
    abstract: str,
    dropped_tags: list[str],
    why_sentences: tuple[int, int] = WHY_IMPORTANT_SENTENCES,
) -> dict[str, list[str]]:
    texts = output_texts(cards)
    return {
        "unsupportedNumbers": find_unsupported_numbers(texts, abstract),
        "length": find_length_issues(cards, why_sentences),
        "forbiddenChars": find_forbidden_chars(texts),
        "droppedTags": dropped_tags,
        "formalEndings": find_formal_endings(labeled_texts(cards)),
        "koreanNumerals": find_korean_numerals(texts),
        "bannedWords": find_banned_words(texts),
    }


def why_sentences_for(prompt_version: str) -> tuple[int, int]:
    return getattr(
        get_prompt(prompt_version), "WHY_IMPORTANT_SENTENCES", WHY_IMPORTANT_SENTENCES
    )


class CardGenerator:
    def __init__(
        self,
        api_key: str,
        model: str,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        prompt_version: str = LATEST_PROMPT_VERSION,
    ):
        self.model = model
        self._prompt = get_prompt(prompt_version)
        self.prompt_version = self._prompt.PROMPT_VERSION
        self._interval = interval_seconds
        self._last_call: float | None = None
        # attempts=1 disables SDK retries so a quota error is never repeated.
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )
        self._config = types.GenerateContentConfig(
            system_instruction=self._prompt.SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GeneratedPaperCards,
            temperature=TEMPERATURE,
        )

    def _wait(self) -> None:
        if self._last_call is None:
            return
        wait = self._interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def _request(self, title: str, abstract: str):
        self._wait()
        try:
            return self._client.models.generate_content(
                model=self.model,
                contents=self._prompt.build_prompt(title, abstract),
                config=self._config,
            )
        finally:
            self._last_call = time.monotonic()

    def generate(self, paper_id: str, title: str, abstract: str) -> GenerationResult:
        started = time.monotonic()
        calls = 0
        while True:
            calls += 1
            try:
                response = self._request(title, abstract)
                break
            except errors.APIError as exc:
                if exc.code == 429:
                    raise QuotaExceeded(
                        f"{self.model}: HTTP 429 {exc.status}", api_calls=calls
                    ) from None
                if exc.code in TRANSIENT_CODES and calls == 1:
                    continue
                return self._failure(
                    paper_id, started, f"API error {exc.code} {exc.status}", calls
                )
        try:
            cards = GeneratedPaperCards.model_validate_json(response.text or "")
        except ValidationError as exc:
            return self._failure(
                paper_id,
                started,
                f"invalid response: {exc.error_count()} errors",
                calls,
            )
        kept, dropped = clean_tags(cards.tags)
        cards.tags = kept
        return GenerationResult(
            paper_id=paper_id,
            model=self.model,
            prompt_version=self.prompt_version,
            ok=True,
            elapsed_seconds=round(self._last_call - started, 2),
            cards=cards.model_dump(),
            issues=run_checks(
                cards, abstract, dropped, why_sentences_for(self.prompt_version)
            ),
            api_calls=calls,
        )

    def _failure(
        self, paper_id: str, started: float, error: str, api_calls: int
    ) -> GenerationResult:
        return GenerationResult(
            paper_id=paper_id,
            model=self.model,
            prompt_version=self.prompt_version,
            ok=False,
            elapsed_seconds=round(time.monotonic() - started, 2),
            error=error,
            api_calls=api_calls,
        )
