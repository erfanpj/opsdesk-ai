"""Load the fictional handbook (data/handbook/*.md) into SourcePassage records."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .schemas import DATA_DIR, Category, SourcePassage

HANDBOOK_DIR = DATA_DIR / "handbook"
_HEADER = re.compile(r"^## (HB-[A-Z]{3}-\d{2}) \| (.+)$")


@dataclass(frozen=True)
class PassageMeta:
    action: str | None
    incident_id: str | None


def _front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing front matter")
    head, body = text[4:].split("\n---\n", 1)
    meta = {}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body


def load_handbook(directory: Path = HANDBOOK_DIR) -> tuple[dict[str, SourcePassage], dict[str, PassageMeta]]:
    passages: dict[str, SourcePassage] = {}
    metas: dict[str, PassageMeta] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("00_"):
            continue
        doc, body = _front_matter(path.read_text())
        blocks = re.split(r"\n(?=## HB-)", "\n" + body.strip())
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            m = _HEADER.match(lines[0])
            if not m:
                raise ValueError(f"{path.name}: bad passage header {lines[0]!r}")
            fields, text_lines = {}, []
            for line in lines[1:]:
                key = line.split(":", 1)[0]
                if not text_lines and key in ("keywords", "action", "superseded_by", "incident_id"):
                    fields[key] = line.split(":", 1)[1].strip()
                else:
                    text_lines.append(line)
            pid = m.group(1)
            if pid in passages:
                raise ValueError(f"duplicate passage id {pid}")
            passages[pid] = SourcePassage(
                passage_id=pid, doc_id=doc["doc_id"], doc_version=doc["doc_version"],
                effective_date=doc["effective_date"], title=m.group(2).strip(),
                text=" ".join(t.strip() for t in text_lines if t.strip()),
                category=Category(doc["category"]) if doc.get("category") else None,
                superseded_by=fields.get("superseded_by") or None,
                keywords=tuple(k.strip().lower() for k in fields.get("keywords", "").split(",") if k.strip()))
            metas[pid] = PassageMeta(action=fields.get("action") or None, incident_id=fields.get("incident_id") or None)
    for pid, p in passages.items():
        if p.superseded_by and p.superseded_by not in passages:
            raise ValueError(f"{pid} is superseded by unknown passage {p.superseded_by}")
    return passages, metas


def first_sentence(text: str) -> str:
    """Quote used for citations: the passage's first sentence, verbatim."""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:400]
