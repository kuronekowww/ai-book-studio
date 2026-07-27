from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from typing import Any

from .contexts import EpisodeContextBuilder
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
        self.contexts = EpisodeContextBuilder(database)

    async def analyze_book(self, book_id: str) -> dict[str, Any]:
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
        self.database.execute(
            "DELETE FROM knowledge_items WHERE book_id = ? AND kind != '人物关系'",
            (book_id,),
        )
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
        relationship_result = {"failed_section_ids": [], "relationship_count": 0}
        if book["book_type"] == "narrative":
            relationship_result = await self._extract_character_relationships(
                book_id, sections
            )
        else:
            self.database.execute(
                "DELETE FROM knowledge_items WHERE book_id = ? AND kind = '人物关系'",
                (book_id,),
            )
        status = (
            "analysis_partial_failed"
            if relationship_result["failed_section_ids"]
            else "analyzed"
        )
        self.database.execute(
            "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), book_id),
        )
        counts = Counter(item[2] for item in items)
        return {
            "knowledge_count": len(items)
            + int(relationship_result["relationship_count"]),
            "counts": counts,
            "mind_map": mind_map,
            **relationship_result,
        }

    async def _extract_character_relationships(
        self, book_id: str, sections: list[dict[str, Any]]
    ) -> dict[str, Any]:
        succeeded = {
            row["scope_id"]
            for row in self.database.rows(
                """
                SELECT scope_id FROM workflow_runs
                WHERE scope_type = 'book_section_analysis'
                  AND stage = 'character_relationships'
                  AND status = 'succeeded'
                  AND json_extract(metadata_json, '$.book_id') = ?
                """,
                (book_id,),
            )
        }
        targets = [section for section in sections if section["id"] not in succeeded]
        semaphore = asyncio.Semaphore(5)

        async def extract(section: dict[str, Any]) -> tuple[dict[str, Any], list[str], str]:
            run_id = uuid.uuid4().hex
            now = now_iso()
            self.database.execute(
                """
                INSERT INTO workflow_runs
                  (id, scope_type, scope_id, stage, status, message,
                   metadata_json, created_at, updated_at)
                VALUES (?, 'book_section_analysis', ?, 'character_relationships',
                        'pending', '', ?, ?, ?)
                """,
                (
                    run_id,
                    section["id"],
                    json.dumps(
                        {"book_id": book_id, "section_title": section["title"]},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            try:
                async with semaphore:
                    self.database.execute(
                        """
                        UPDATE workflow_runs
                        SET status = 'running', updated_at = ? WHERE id = ?
                        """,
                        (now_iso(), run_id),
                    )
                    source = (
                        f"原文块 ID：{section['id']}\n"
                        f"标题：{section['title']}\n\n{section['content']}"
                    )
                    raw = await self.provider.generate(
                        PROMPTS["character_relationships"], source
                    )
                relationships = self._parse_character_relationships(raw)
                self.database.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'succeeded', message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        f"提取 {len(relationships)} 条人物关系",
                        now_iso(),
                        run_id,
                    ),
                )
                return section, relationships, ""
            except Exception as error:
                self.database.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'failed', message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(error)[:500], now_iso(), run_id),
                )
                return section, [], str(error)

        results = await asyncio.gather(*(extract(section) for section in targets))
        failed_section_ids: list[str] = []
        for section, relationships, error in results:
            if error:
                failed_section_ids.append(section["id"])
                continue
            existing = self.database.rows(
                """
                SELECT * FROM knowledge_items
                WHERE book_id = ? AND kind = '人物关系'
                """,
                (book_id,),
            )
            for item in existing:
                if section["id"] in item["source_section_ids"]:
                    self.database.execute(
                        "DELETE FROM knowledge_items WHERE id = ?", (item["id"],)
                    )
            if relationships:
                self.database.executemany(
                    """
                    INSERT INTO knowledge_items
                      (id, book_id, kind, title, body, source_section_ids, created_at)
                    VALUES (?, ?, '人物关系', ?, ?, ?, ?)
                    """,
                    [
                        (
                            uuid.uuid4().hex,
                            book_id,
                            f"{section['title']} · 人物关系 {index}",
                            relationship,
                            json.dumps([section["id"]], ensure_ascii=False),
                            now_iso(),
                        )
                        for index, relationship in enumerate(relationships, start=1)
                    ],
                )
        relationship_count = self.database.row(
            """
            SELECT COUNT(*) AS count FROM knowledge_items
            WHERE book_id = ? AND kind = '人物关系'
            """,
            (book_id,),
        )
        return {
            "failed_section_ids": failed_section_ids,
            "relationship_count": int(relationship_count["count"])
            if relationship_count
            else 0,
        }

    @staticmethod
    def _parse_character_relationships(raw: str) -> list[str]:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
        data = json.loads(cleaned)
        relationships = data.get("relationships")
        if not isinstance(relationships, list):
            raise ValueError("人物关系模型输出缺少 relationships 数组")
        parsed: list[str] = []
        for item in relationships:
            if not isinstance(item, dict):
                raise ValueError("人物关系模型输出结构无效")
            characters = item.get("characters")
            relationship = item.get("relationship")
            if not isinstance(characters, list) or not all(
                isinstance(name, str) and name.strip() for name in characters
            ):
                raise ValueError("人物关系模型输出缺少有效人物")
            if not isinstance(relationship, str) or not relationship.strip():
                raise ValueError("人物关系模型输出缺少关系描述")
            evidence = item.get("evidence")
            body = f"{'、'.join(name.strip() for name in characters)}：{relationship.strip()}"
            if isinstance(evidence, str) and evidence.strip():
                body += f"（依据：{evidence.strip()}）"
            parsed.append(body)
        return parsed

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
                    (
                        f"围绕“{section['title']}”展开：先说明本节讨论的问题，"
                        "再梳理原文中的核心观点、事件或案例，最后总结其意义。"
                    ),
                    "outline_review",
                    json.dumps([section["id"]], ensure_ascii=False),
                )
            )
        self.database.executemany(
            """
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style,
               content_framework, status, source_section_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            episodes,
        )
        return self.project_detail(project_id)

    def confirm_project(self, project_id: str) -> dict[str, Any]:
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        if not project:
            raise KeyError(project_id)
        episodes = self.database.rows(
            "SELECT * FROM episodes WHERE project_id = ? ORDER BY position",
            (project_id,),
        )
        if not episodes:
            raise ValueError("专辑大纲至少需要一条声音")
        valid_book_ids = set(project["book_ids"])
        for episode in episodes:
            if not episode["content_framework"].strip():
                raise ValueError(f"第 {episode['position']} 条声音缺少内容框架")
            if not episode["source_section_ids"]:
                raise ValueError(f"第 {episode['position']} 条声音没有关联原文块")
            for section_id in episode["source_section_ids"]:
                section = self.database.row(
                    "SELECT book_id FROM sections WHERE id = ?", (section_id,)
                )
                if not section or section["book_id"] not in valid_book_ids:
                    raise ValueError(
                        f"第 {episode['position']} 条声音包含无效原文块"
                    )
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
        stages = ["outline", "draft", "final"]
        start = stages.index(from_stage)
        for stage in stages[start:]:
            self.database.execute(
                "UPDATE episodes SET status = ? WHERE id = ?",
                (f"generating_{stage}", episode_id),
            )
            try:
                context = self.contexts.build(episode_id, stage)
                prompt = PROMPTS[context.prompt_id]
                content = await self.provider.generate(prompt, context.source)
            except Exception as error:
                self.database.execute(
                    "UPDATE episodes SET status = 'failed' WHERE id = ?",
                    (episode_id,),
                )
                raise StageGenerationError(stage, error) from error
            self._save_artifact(
                episode_id,
                stage,
                content,
                prompt.version,
                input_snapshot=context.source,
            )
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
        input_snapshot: str = "",
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
               provider, model, author_type, input_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                input_snapshot,
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
            SELECT id, episode_id, stage, version, content, prompt_version,
                   provider, model, author_type, created_at
            FROM artifact_versions
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
