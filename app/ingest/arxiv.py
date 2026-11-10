import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

# Demo whitelist. See docs/spec.md P-01.
CATEGORIES = ("cs.AI", "cs.CL", "cs.CV", "cs.HC", "cs.SE")
ATOM_URL = "https://export.arxiv.org/api/query"
OAI_URL = "https://oaipmh.arxiv.org/oai"
# Unversioned links always open the latest version, matching paper_id.
ABS_URL = "https://arxiv.org/abs/"
PDF_URL = "https://arxiv.org/pdf/"
USER_AGENT = "nonstop-be/0.1"
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arXiv": "http://arxiv.org/OAI/arXiv/",
}
_VERSION_SUFFIX = re.compile(r"v(\d+)$")


@dataclass
class ArxivPaper:
    paper_id: str
    arxiv_id: str
    version: int
    title: str
    abstract: str
    authors: list[str]
    published_at: str
    primary_category: str
    categories: list[str]
    # Taken from arxiv:journal_ref only. Never guessed.
    venue: str | None
    abstract_url: str
    pdf_url: str
    license: str | None = None


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def split_version(versioned_id: str) -> tuple[str, int]:
    match = _VERSION_SUFFIX.search(versioned_id)
    if not match:
        return versioned_id, 1
    return versioned_id[: match.start()], int(match.group(1))


def to_paper_id(arxiv_id: str) -> str:
    base, _ = split_version(arxiv_id)
    return base.replace("/", "_")


def _text(element: ET.Element, path: str, ns: dict[str, str]) -> str | None:
    found = element.find(path, ns)
    if found is None or found.text is None:
        return None
    return collapse_whitespace(found.text) or None


def parse_atom(xml_text: str) -> list[ArxivPaper]:
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = _text(entry, "atom:id", ATOM_NS) or ""
        arxiv_id, version = split_version(entry_id.split("/abs/", 1)[-1])
        primary = entry.find("arxiv:primary_category", ATOM_NS)
        papers.append(
            ArxivPaper(
                paper_id=to_paper_id(arxiv_id),
                arxiv_id=arxiv_id,
                version=version,
                title=_text(entry, "atom:title", ATOM_NS) or "",
                abstract=_text(entry, "atom:summary", ATOM_NS) or "",
                authors=[
                    _text(author, "atom:name", ATOM_NS) or ""
                    for author in entry.findall("atom:author", ATOM_NS)
                ],
                published_at=(_text(entry, "atom:published", ATOM_NS) or "")[:10],
                primary_category=primary.get("term", "") if primary is not None else "",
                categories=[
                    c.get("term", "") for c in entry.findall("atom:category", ATOM_NS)
                ],
                venue=_text(entry, "arxiv:journal_ref", ATOM_NS),
                abstract_url=f"{ABS_URL}{arxiv_id}",
                pdf_url=f"{PDF_URL}{arxiv_id}",
            )
        )
    return papers


def select_by_primary_category(
    candidates: dict[str, list[ArxivPaper]], per_category: int
) -> dict[str, list[ArxivPaper]]:
    """Pick the newest papers whose primary category is the searched one.

    `candidates` maps each searched category to results in newest-first order.
    A paper is used once even if it shows up under several categories.
    """
    seen: set[str] = set()
    selected: dict[str, list[ArxivPaper]] = {}
    for category, papers in candidates.items():
        picked: list[ArxivPaper] = []
        for paper in papers:
            if len(picked) == per_category:
                break
            if paper.primary_category == category and paper.paper_id not in seen:
                seen.add(paper.paper_id)
                picked.append(paper)
        selected[category] = picked
    return selected


def parse_oai_license(xml_text: str) -> str | None:
    root = ET.fromstring(xml_text)
    return _text(root, ".//arXiv:arXiv/arXiv:license", OAI_NS)


class ArxivClient:
    """Sequential arXiv client that keeps at least `interval_seconds` between requests."""

    def __init__(self, interval_seconds: float):
        self._interval = interval_seconds
        self._last_request: float | None = None
        self._http = httpx.Client(
            timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def _get(self, url: str, params: dict[str, str | int]) -> str:
        if self._last_request is not None:
            wait = self._interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        try:
            response = self._http.get(url, params=params)
        finally:
            self._last_request = time.monotonic()
        response.raise_for_status()
        return response.text

    def latest(self, category: str, max_results: int) -> list[ArxivPaper]:
        xml_text = self._get(
            ATOM_URL,
            {
                "search_query": f"cat:{category}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
        )
        return parse_atom(xml_text)

    def by_ids(self, arxiv_ids: list[str]) -> list[ArxivPaper]:
        if not arxiv_ids:
            return []
        xml_text = self._get(
            ATOM_URL, {"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}
        )
        return parse_atom(xml_text)

    def fetch_license(self, arxiv_id: str) -> str | None:
        try:
            xml_text = self._get(
                OAI_URL,
                {
                    "verb": "GetRecord",
                    "identifier": f"oai:arXiv.org:{arxiv_id}",
                    "metadataPrefix": "arXiv",
                },
            )
            return parse_oai_license(xml_text)
        except (httpx.HTTPError, ET.ParseError):
            return None
