from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .db import Database, now_iso


SENTENCE_RE = re.compile(r".+?(?:[。！？!?]+[”’\"']?|$)", re.S)


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _hard_chunks(text: str, maximum: int) -> list[str]:
    return [text[index : index + maximum] for index in range(0, len(text), maximum)]


def _sentence_chunks(paragraph: str, maximum: int) -> list[str]:
    sentences = [match.group(0).strip() for match in SENTENCE_RE.finditer(paragraph)]
    atoms: list[str] = []
    for sentence in sentences or [paragraph]:
        if len(sentence) <= maximum:
            atoms.append(sentence)
        else:
            atoms.extend(_hard_chunks(sentence, maximum))
    return atoms


def semantic_paragraph_fragments(
    text: str, minimum: int = 300, maximum: int = 800
) -> list[str]:
    paragraphs = [
        part.strip() for part in re.split(r"\n\s*\n|\n", text) if part.strip()
    ]
    atoms: list[str] = []
    for paragraph in paragraphs:
        atoms.extend(
            [paragraph]
            if len(paragraph) <= maximum
            else _sentence_chunks(paragraph, maximum)
        )
    if not atoms:
        return []

    fragments: list[str] = []
    current: list[str] = []
    size = 0
    for atom in atoms:
        separator = 2 if current else 0
        if current and size + separator + len(atom) > maximum:
            fragments.append("\n\n".join(current))
            current = []
            size = 0
            separator = 0
        current.append(atom)
        size += separator + len(atom)
    if current:
        fragments.append("\n\n".join(current))
    if (
        len(fragments) > 1
        and len(fragments[-1]) < minimum
        and len(fragments[-2]) + 2 + len(fragments[-1]) <= maximum
    ):
        fragments[-2] = f"{fragments[-2]}\n\n{fragments[-1]}"
        fragments.pop()
    if compact_text("\n\n".join(fragments)) != compact_text(text):
        raise ValueError("原文片段切分未能无损还原正文")
    return fragments


def fragment_content_index(
    book_id: str, section_id: str, text: str, occurrence: int
) -> str:
    digest = hashlib.sha1(
        "\x1f".join(
            (book_id, section_id, normalized_text(text), str(occurrence))
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"content_{digest}"


@dataclass(frozen=True)
class FragmentRecord:
    content_index: str
    book_id: str
    source_section_id: str
    root_section_id: str
    section_path: list[str]
    book_position: int
    section_position: int
    text: str


def build_fragment_records(
    book_id: str, sections: list[dict[str, Any]]
) -> list[FragmentRecord]:
    by_id = {section["id"]: section for section in sections}

    def lineage(section: dict[str, Any]) -> list[dict[str, Any]]:
        path = [section]
        while path[-1].get("parent_id") in by_id:
            path.append(by_id[path[-1]["parent_id"]])
        path.reverse()
        return path

    records: list[FragmentRecord] = []
    book_position = 0
    for section in sorted(sections, key=lambda item: item["position"]):
        if section.get("status") != "confirmed":
            continue
        path = lineage(section)
        root = path[0]
        occurrences: dict[str, int] = {}
        for section_position, text in enumerate(
            semantic_paragraph_fragments(section["content"]), start=1
        ):
            normalized = normalized_text(text)
            occurrences[normalized] = occurrences.get(normalized, 0) + 1
            book_position += 1
            records.append(
                FragmentRecord(
                    content_index=fragment_content_index(
                        book_id,
                        section["id"],
                        text,
                        occurrences[normalized],
                    ),
                    book_id=book_id,
                    source_section_id=section["id"],
                    root_section_id=root["id"],
                    section_path=[item["title"] for item in path],
                    book_position=book_position,
                    section_position=section_position,
                    text=text,
                )
            )
    return records


class EvidenceService:
    def __init__(self, database: Database):
        self.database = database

    def ensure_current_fragment_set(self, book_id: str) -> dict[str, Any]:
        if not self.database.row("SELECT id FROM books WHERE id = ?", (book_id,)):
            raise KeyError(book_id)
        sections = self.database.rows(
            "SELECT * FROM sections WHERE book_id = ? ORDER BY position",
            (book_id,),
        )
        records = build_fragment_records(book_id, sections)
        fingerprint = hashlib.sha256(
            "\n".join(
                f"{item.content_index}:{item.book_position}" for item in records
            ).encode("utf-8")
        ).hexdigest()
        current = self.database.row(
            """
            SELECT * FROM source_fragment_sets
            WHERE book_id = ? AND status = 'current'
            ORDER BY version DESC LIMIT 1
            """,
            (book_id,),
        )
        if current and current["content_fingerprint"] == fingerprint:
            self._mark_traceability_status(book_id, current["id"])
            return current
        version_row = self.database.row(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM source_fragment_sets WHERE book_id = ?
            """,
            (book_id,),
        )
        version = int(version_row["version"]) + 1 if version_row else 1
        set_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE source_fragment_sets SET status = 'historical'
                WHERE book_id = ? AND status = 'current'
                """,
                (book_id,),
            )
            connection.execute(
                """
                INSERT INTO source_fragment_sets
                  (id, book_id, version, content_fingerprint, status, created_at)
                VALUES (?, ?, ?, ?, 'current', ?)
                """,
                (set_id, book_id, version, fingerprint, now_iso()),
            )
            for item in records:
                text_fingerprint = hashlib.sha256(
                    normalized_text(item.text).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO source_fragments
                      (content_index, book_id, source_section_id, text,
                       text_fingerprint, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.content_index,
                        book_id,
                        item.source_section_id,
                        item.text,
                        text_fingerprint,
                        now_iso(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_fragment_set_members
                      (fragment_set_id, content_index, root_section_id,
                       section_path_json, book_position, section_position)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        set_id,
                        item.content_index,
                        item.root_section_id,
                        json.dumps(item.section_path, ensure_ascii=False),
                        item.book_position,
                        item.section_position,
                    ),
                )
        created = self.database.row(
            "SELECT * FROM source_fragment_sets WHERE id = ?", (set_id,)
        ) or {}
        self._mark_traceability_status(book_id, set_id)
        return created

    def _mark_traceability_status(
        self, book_id: str, fragment_set_id: str
    ) -> None:
        book = self.database.row(
            "SELECT book_type, status FROM books WHERE id = ?", (book_id,)
        )
        if (
            not book
            or book["book_type"] != "non_narrative"
            or book["status"] not in {"analyzed", "analysis_partial"}
        ):
            return
        root_count_row = self.database.row(
            """
            SELECT COUNT(*) AS count FROM sections
            WHERE book_id = ? AND parent_id IS NULL
              AND status = 'confirmed' AND analysis_enabled = 1
            """,
            (book_id,),
        )
        precise_count_row = self.database.row(
            """
            SELECT COUNT(DISTINCT root_section_id) AS count
            FROM chapter_analyses
            WHERE book_id = ? AND status = 'succeeded'
              AND fragment_set_id = ?
            """,
            (book_id, fragment_set_id),
        )
        root_count = int(root_count_row["count"]) if root_count_row else 0
        precise_count = int(precise_count_row["count"]) if precise_count_row else 0
        if root_count and precise_count < root_count:
            next_status = "analysis_partial" if precise_count else "ready_to_analyze"
            self.database.execute(
                "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, now_iso(), book_id),
            )

    def ensure_all_books(self) -> None:
        for book in self.database.rows("SELECT id FROM books ORDER BY created_at"):
            self.ensure_current_fragment_set(book["id"])

    def chapter_fragments(
        self, fragment_set_id: str, root_section_id: str
    ) -> list[dict[str, Any]]:
        return self.database.rows(
            """
            SELECT f.*, m.fragment_set_id, m.root_section_id,
                   m.section_path_json, m.book_position, m.section_position
            FROM source_fragment_set_members m
            JOIN source_fragments f ON f.content_index = m.content_index
            WHERE m.fragment_set_id = ? AND m.root_section_id = ?
            ORDER BY m.book_position
            """,
            (fragment_set_id, root_section_id),
        )

    def fragment_detail(self, content_index: str) -> dict[str, Any]:
        fragment = self.database.row(
            "SELECT * FROM source_fragments WHERE content_index = ?",
            (content_index,),
        )
        if not fragment:
            raise KeyError(content_index)
        memberships = self.database.rows(
            """
            SELECT m.*, s.status AS fragment_set_status, s.version
            FROM source_fragment_set_members m
            JOIN source_fragment_sets s ON s.id = m.fragment_set_id
            WHERE m.content_index = ?
            ORDER BY s.version DESC
            """,
            (content_index,),
        )
        fragment["memberships"] = memberships
        current = next(
            (
                membership
                for membership in memberships
                if membership["fragment_set_status"] == "current"
            ),
            memberships[0] if memberships else None,
        )
        if current:
            fragment.update(
                {
                    "section_path_json": current["section_path_json"],
                    "book_position": current["book_position"],
                    "section_position": current["section_position"],
                    "fragment_set_id": current["fragment_set_id"],
                }
            )
        return fragment
