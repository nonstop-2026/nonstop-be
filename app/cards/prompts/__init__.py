"""Versioned prompts. Never edit a released version; add a new module and register it here."""

from types import ModuleType

from app.cards.prompts import v0_1, v0_2, v0_3

PROMPTS: dict[str, ModuleType] = {m.PROMPT_VERSION: m for m in (v0_1, v0_2, v0_3)}
LATEST_PROMPT_VERSION = v0_3.PROMPT_VERSION


def get_prompt(version: str) -> ModuleType:
    try:
        return PROMPTS[version]
    except KeyError:
        raise ValueError(f"Unknown prompt version: {version}") from None
