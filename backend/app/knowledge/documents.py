"""Layer A of the knowledge base: curated markdown documents under ``knowledge/``.

A document's path is its id (``working-capital/receivables.md`` -> ``working-capital/receivables``), which is
the value rules and metrics use in their ``kb`` field. Standard section headings are mapped to a section kind
so that retrieval can target, for example, only the benign explanations for a red flag.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from app.domain.enums import DocType, Sector


class KnowledgeError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("invalid knowledge base:\n  - " + "\n  - ".join(problems))
        self.problems = problems


class SectionKind(StrEnum):
    OVERVIEW = "overview"
    INTERPRETATION = "interpretation"
    RED_FLAGS = "red_flags"
    BENIGN_EXPLANATIONS = "benign_explanations"
    SOURCES = "sources"
    QUESTIONS = "questions"
    GENERAL = "general"


STANDARD_HEADINGS: dict[str, SectionKind] = {
    "what it measures": SectionKind.OVERVIEW,
    "how to read it": SectionKind.INTERPRETATION,
    "red flags": SectionKind.RED_FLAGS,
    "benign explanations": SectionKind.BENIGN_EXPLANATIONS,
    "where to find it in indian filings": SectionKind.SOURCES,
    "questions for management": SectionKind.QUESTIONS,
}

_FRONT_MATTER = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*$", re.MULTILINE)


class Section(BaseModel):
    model_config = ConfigDict(frozen=True)

    heading: str
    kind: SectionKind
    text: str


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    summary: str
    doc_types: tuple[DocType, ...] = ()  # empty: relevant to every document type
    sectors: tuple[Sector, ...] = ()  # empty: sector-agnostic
    regulations: tuple[str, ...] = ()
    sections: tuple[Section, ...]

    def section(self, kind: SectionKind) -> Section | None:
        return next((section for section in self.sections if section.kind is kind), None)


class _FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    doc_types: tuple[DocType, ...] = ()
    sectors: tuple[Sector, ...] = ()
    regulations: tuple[str, ...] = ()


def parse_document(doc_id: str, text: str) -> KnowledgeDocument:
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not match:
        raise ValueError(f"{doc_id}: missing front matter")
    front = _FrontMatter.model_validate(yaml.safe_load(match["yaml"]) or {})
    body = match["body"]

    headings = list(_HEADING.finditer(body))
    sections: list[Section] = []
    for index, heading in enumerate(headings):
        if heading.group(1) == "#":
            continue  # the document title
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        content = body[heading.end() : end].strip()
        if not content:
            raise ValueError(f"{doc_id}: section {heading.group(2)!r} is empty")
        name = heading.group(2)
        sections.append(
            Section(heading=name, kind=STANDARD_HEADINGS.get(name.lower(), SectionKind.GENERAL), text=content)
        )
    if not sections:
        raise ValueError(f"{doc_id}: no '## ' sections")
    return KnowledgeDocument(id=doc_id, sections=tuple(sections), **front.model_dump())


def load_documents(root: Path) -> dict[str, KnowledgeDocument]:
    documents: dict[str, KnowledgeDocument] = {}
    problems: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if path.parent == root:
            continue  # top-level files (README) describe the knowledge base itself
        doc_id = path.relative_to(root).with_suffix("").as_posix()
        try:
            documents[doc_id] = parse_document(doc_id, path.read_text(encoding="utf-8"))
        except (ValueError, ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{doc_id}: {exc}")
    if problems:
        raise KnowledgeError(problems)
    return documents


def content_version(documents: dict[str, KnowledgeDocument]) -> str:
    """Stable hash of every document, recorded with each analysis as ``kb_version``."""
    digest = hashlib.sha256()
    for doc_id in sorted(documents):
        digest.update(documents[doc_id].model_dump_json().encode())
    return digest.hexdigest()[:12]
