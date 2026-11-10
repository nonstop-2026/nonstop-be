from pydantic import BaseModel, Field

# Must match KeywordSlug in openapi.yaml.
KEYWORD_SLUGS = (
    "agents",
    "reasoning",
    "planning",
    "llm",
    "rag",
    "translation",
    "image-generation",
    "multimodal",
    "3d-vision",
    "ux-research",
    "xr",
    "accessibility",
    "code-generation",
    "software-testing",
    "developer-tools",
)
MAX_TAGS = 3


class CardContent(BaseModel):
    oneLine: str
    whyImportant: str
    keyPoints: list[str] = Field(min_length=1, max_length=5)
    method: str | None
    dataset: str | None
    limitation: str | None


class GeneratedPaperCards(BaseModel):
    titleKo: str
    # Free strings here; clean_tags filters them after generation.
    tags: list[str]
    easy: CardContent
    basic: CardContent
    expert: CardContent


def clean_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Return (kept, dropped). Keeps known slugs in order, without duplicates, up to MAX_TAGS."""
    kept: list[str] = []
    dropped: list[str] = []
    for tag in tags:
        if tag in KEYWORD_SLUGS and tag not in kept and len(kept) < MAX_TAGS:
            kept.append(tag)
        else:
            dropped.append(tag)
    return kept, dropped
