from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from typing import Any

from .db import Database, now_iso
from .prompts import PROMPTS
from .providers import ModelProvider


def clean_excerpt(text: str, limit: int = 360) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def extract_sentence(text: str) -> str:
    parts = re.split(r"(?<=[。！？!?])", re.sub(r"\s+", " ", text))
    candidates = [part.strip() for part in parts if 24 <= len(part.strip()) <= 180]
    return candidates[0] if candidates else clean_excerpt(text, 160)


class StageGenerationError(RuntimeError):
    def __init__(self, stage: str, error: Exception):
        self.stage = stage
        self.original = error
        super().__init__(str(error))


class WorkflowService:
    def __init__(self, database: Database, provider: ModelProvider):
        self.database = database
        self.provider = provider

    def analyze_book(self, book_id: str) -> dict[str, Any]:
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        sections = self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND kind IN ('article', 'section')
            ORDER BY position
            """,
            (book_id,),
        )
        self.database.execute("DELETE FROM knowledge_items WHERE book_id = ?", (book_id,))
        items: list[tuple[Any, ...]] = []
        for section in sections:
            body = section["content"].strip()
            if not body:
                continue
            source_ids = json.dumps([section["id"]], ensure_ascii=False)
            created_at = now_iso()
            point_id = uuid.uuid4().hex
            items.append(
                (
                    point_id,
                    book_id,
                    "观点",
                    section["title"],
                    extract_sentence(body),
                    source_ids,
                    created_at,
                )
            )
            quote = next(
                (
                    sentence.strip()
                    for sentence in re.split(r"(?<=[。！？])", body)
                    if 36 <= len(sentence.strip()) <= 120
                ),
                "",
            )
            if quote:
                items.append(
                    (
                        uuid.uuid4().hex,
                        book_id,
                        "金句",
                        f"{section['title']} · 摘录",
                        quote,
                        source_ids,
                        created_at,
                    )
                )
            if any(word in section["title"] for word in ("案", "故事", "事件", "纪念")):
                items.append(
                    (
                        uuid.uuid4().hex,
                        book_id,
                        "案例",
                        section["title"],
                        clean_excerpt(body, 420),
                        source_ids,
                        created_at,
                    )
                )
        self.database.executemany(
            """
            INSERT INTO knowledge_items
              (id, book_id, kind, title, body, source_section_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            items,
        )
        mind_map = self._mind_map(book_id, book["title"])
        current = self.database.row(
            "SELECT COALESCE(MAX(version), 0) AS version FROM mind_maps WHERE book_id = ?",
            (book_id,),
        )
        version = int(current["version"]) + 1 if current else 1
        self.database.execute(
            """
            INSERT INTO mind_maps (id, book_id, version, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, book_id, version, mind_map, now_iso()),
        )
        self.database.execute(
            "UPDATE books SET status = 'analyzed', updated_at = ? WHERE id = ?",
            (now_iso(), book_id),
        )
        counts = Counter(item[2] for item in items)
        return {"knowledge_count": len(items), "counts": counts, "mind_map": mind_map}

    def _mind_map(self, book_id: str, title: str) -> str:
        themes = self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND level = 3
            ORDER BY position
            """,
            (book_id,),
        )
        articles = self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND level = 4
            ORDER BY position
            """,
            (book_id,),
        )
        by_parent: dict[str, list[dict[str, Any]]] = {}
        for article in articles:
            by_parent.setdefault(article["parent_id"], []).append(article)
        lines = [f"# {title}"]
        for theme in themes:
            lines.append(f"- {theme['title']}")
            for article in by_parent.get(theme["id"], [])[:8]:
                lines.append(f"  - {article['title']}")
        return "\n".join(lines)

    def create_project(self, title: str, book_id: str) -> dict[str, Any]:
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        project_id = uuid.uuid4().hex
        now = now_iso()
        self.database.execute(
            """
            INSERT INTO projects (id, title, book_ids, status, created_at, updated_at)
            VALUES (?, ?, ?, 'outline_review', ?, ?)
            """,
            (project_id, title, json.dumps([book_id]), now, now),
        )
        source_sections = self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND level = 4
            ORDER BY position
            LIMIT 12
            """,
            (book_id,),
        )
        episodes: list[tuple[Any, ...]] = []
        for index, section in enumerate(source_sections, start=1):
            content_type = "过渡" if index > 1 and index % 4 == 0 else "解读"
            style = "观点"
            episodes.append(
                (
                    uuid.uuid4().hex,
                    project_id,
                    index,
                    section["title"],
                    content_type,
                    style,
                    "outline_review",
                    json.dumps([section["id"]], ensure_ascii=False),
                )
            )
        self.database.executemany(
            """
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style, status, source_section_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            episodes,
        )
        return self.project_detail(project_id)

    def confirm_project(self, project_id: str) -> dict[str, Any]:
        if not self.database.row("SELECT id FROM projects WHERE id = ?", (project_id,)):
            raise KeyError(project_id)
        self.database.execute(
            "UPDATE projects SET status = 'ready', updated_at = ? WHERE id = ?",
            (now_iso(), project_id),
        )
        self.database.execute(
            "UPDATE episodes SET status = 'ready' WHERE project_id = ?",
            (project_id,),
        )
        return self.project_detail(project_id)

    async def generate_episode(
        self, episode_id: str, from_stage: str = "outline"
    ) -> dict[str, Any]:
        episode = self.database.row("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        if not episode:
            raise KeyError(episode_id)
        sections = []
        for section_id in episode["source_section_ids"]:
            section = self.database.row("SELECT * FROM sections WHERE id = ?", (section_id,))
            if section:
                sections.append(section)
        source = "\n\n".join(
            f"[{section['id']}] {section['title']}\n{section['content']}" for section in sections
        )
        stages = ["outline", "draft", "final"]
        start = stages.index(from_stage)
        previous = source
        for stage in stages[start:]:
            self.database.execute(
                "UPDATE episodes SET status = ? WHERE id = ?",
                (f"generating_{stage}", episode_id),
            )
            prompt_id = {
                "outline": "episode_outline",
                "draft": "episode_draft",
                "final": "episode_final",
            }[stage]
            prompt = PROMPTS[prompt_id]
            if stage != "outline":
                latest = self.latest_artifact(episode_id, stages[stages.index(stage) - 1])
                previous = latest["content"] if latest else previous
            try:
                content = await self.provider.generate(prompt, previous)
            except Exception as error:
                self.database.execute(
                    "UPDATE episodes SET status = 'failed' WHERE id = ?",
                    (episode_id,),
                )
                raise StageGenerationError(stage, error) from error
            self._save_artifact(episode_id, stage, content, prompt.version)
        self.database.execute(
            "UPDATE episodes SET status = 'review' WHERE id = ?",
            (episode_id,),
        )
        return self.episode_detail(episode_id)

    def _save_artifact(
        self,
        episode_id: str,
        stage: str,
        content: str,
        prompt_version: str,
        *,
        author_type: str = "model",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        current = self.database.row(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM artifact_versions
            WHERE episode_id = ? AND stage = ?
            """,
            (episode_id, stage),
        )
        version = int(current["version"]) + 1 if current else 1
        self.database.execute(
            """
            INSERT INTO artifact_versions
              (id, episode_id, stage, version, content, prompt_version,
               provider, model, author_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                episode_id,
                stage,
                version,
                content,
                prompt_version,
                provider or self.provider.name,
                model or self.provider.model,
                author_type,
                now_iso(),
            ),
        )

    def save_manual_final(self, episode_id: str, content: str) -> dict[str, Any]:
        if not self.database.row("SELECT id FROM episodes WHERE id = ?", (episode_id,)):
            raise KeyError(episode_id)
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("终稿内容不能为空")
        self._save_artifact(
            episode_id,
            "final",
            clean_content,
            "human-edit-v1",
            author_type="human",
            provider="human",
            model="manual-edit",
        )
        self.database.execute(
            "UPDATE episodes SET status = 'review' WHERE id = ?",
            (episode_id,),
        )
        return self.episode_detail(episode_id)

    def latest_artifact(self, episode_id: str, stage: str) -> dict[str, Any] | None:
        return self.database.row(
            """
            SELECT * FROM artifact_versions
            WHERE episode_id = ? AND stage = ?
            ORDER BY version DESC LIMIT 1
            """,
            (episode_id, stage),
        )

    def project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.database.row("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            raise KeyError(project_id)
        project["episodes"] = self.database.rows(
            "SELECT * FROM episodes WHERE project_id = ? ORDER BY position",
            (project_id,),
        )
        return project

    def episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self.database.row("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        if not episode:
            raise KeyError(episode_id)
        episode["versions"] = self.database.rows(
            """
            SELECT * FROM artifact_versions
            WHERE episode_id = ?
            ORDER BY stage, version DESC
            """,
            (episode_id,),
        )
        episode["sources"] = [
            self.database.row("SELECT * FROM sections WHERE id = ?", (section_id,))
            for section_id in episode["source_section_ids"]
        ]
        episode["sources"] = [source for source in episode["sources"] if source]
        return episode
