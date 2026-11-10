"""Number extraction for the unsupported-number check.

Abstract and card text are both reduced to numeric values so that "six", "30M",
"3,000만" and "3천만" compare by value rather than by spelling.
"""

import re

_UNITS = {
    word: value
    for value, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
_TENS = {
    word: value * 10
    for value, word in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
        start=2,
    )
}
_LARGE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
_SUFFIXES = {"K": 1e3, "k": 1e3, "M": 1e6, "B": 1e9}
_KOREAN_SMALL = {"십": 10, "백": 100, "천": 1000}
_KOREAN_LARGE = {"만": 1e4, "억": 1e8, "조": 1e12}

_DIGITS = r"\d+(?:,\d{3})*(?:\.\d+)?"
# The suffix only counts when no other letter follows, so "30MB" reads as 30.
_ABSTRACT_NUMBER = re.compile(rf"({_DIGITS})(?:([KkMB])(?![A-Za-z]))?")
_OUTPUT_NUMBER = re.compile(
    rf"({_DIGITS})(?:([KkMB])(?![A-Za-z])|([십백천]?)([만억조]?))"
)
_TOKEN = re.compile(rf"{_DIGITS}|[a-z]+")


def _key(value: float) -> float:
    return round(value, 6)


def _to_float(digits: str) -> float:
    return float(digits.replace(",", ""))


def _joins(current: float, token: str) -> bool:
    """True when `token` continues the phrase, as in "twenty" + "five" or "hundred" + "six"."""
    rest = current % 100
    if rest == 0:
        return True
    return token in _UNITS and _UNITS[token] < 10 and rest % 10 == 0 and rest >= 20


def word_number_values(text: str) -> set[float]:
    """Values of English number phrases such as "six", "twenty-five", "1.5 million"."""
    values: set[float] = set()
    total = current = 0.0
    active = False
    previous = ""

    def flush() -> None:
        nonlocal total, current, active
        if active:
            values.add(_key(total + current))
        total = current = 0.0
        active = False

    for token in _TOKEN.findall(text.lower()):
        if token in _UNITS or token in _TENS:
            if active and not _joins(current, token):
                flush()
            current += _UNITS.get(token, 0) + _TENS.get(token, 0)
            active = True
        elif token == "hundred":
            current = (current or 1) * 100
            active = True
        elif token in _LARGE_WORDS:
            total += (current or 1) * _LARGE_WORDS[token]
            current = 0.0
            active = True
        elif token == "and" and active and previous in ("hundred", *_LARGE_WORDS):
            continue
        elif token[0].isdigit():
            flush()
            current = _to_float(token)
            active = True
        else:
            flush()
        previous = token
    flush()
    return values


def abstract_values(abstract: str) -> set[float]:
    values = word_number_values(abstract)
    for match in _ABSTRACT_NUMBER.finditer(abstract):
        raw = _to_float(match.group(1))
        values.add(_key(raw))
        if match.group(2):
            values.add(_key(raw * _SUFFIXES[match.group(2)]))
    return values


def output_numbers(text: str) -> list[tuple[str, float]]:
    """Each number in card text with its value, e.g. ("3천만", 30000000.0)."""
    found = []
    for match in _OUTPUT_NUMBER.finditer(text):
        digits, suffix, small, large = match.groups()
        value = _to_float(digits)
        if suffix:
            value *= _SUFFIXES[suffix]
        value *= _KOREAN_SMALL.get(small or "", 1) * _KOREAN_LARGE.get(large or "", 1)
        found.append((match.group(0), _key(value)))
    return found
