from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from typing import Any

from .chapter_analysis import (
    build_chapter_source,
    content_index,
    derive_knowledge_cards,
    parse_json_object,
    render_chapter_markdown,
    validate_chapter_analysis,
)
from .contexts import EpisodeContextBuilder
from .db import Database, now_iso
from .evidence import EvidenceService
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
        self.evidence = EvidenceService(database)
        self.contexts = EpisodeContextBuilder(database)

    async def analyze_book(
        self, book_id: str, provider: ModelProvider | None = None
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        if book["book_type"] == "non_narrative":
            return await self._analyze_non_narrative(
                book, provider=task_provider
            )
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
                book_id, sections, task_provider
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

    def _chapter_roots(self, book_id: str) -> list[dict[str, Any]]:
        return self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND parent_id IS NULL
              AND status = 'confirmed' AND analysis_enabled = 1
            ORDER BY position
            """,
            (book_id,),
        )

    async def _analyze_non_narrative(
        self,
        book: dict[str, Any],
        only_root_id: str | None = None,
        provider: ModelProvider | None = None,
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
        book_id = book["id"]
        fragment_set = self.evidence.ensure_current_fragment_set(book_id)
        roots = self._chapter_roots(book_id)
        if only_root_id:
            roots = [root for root in roots if root["id"] == only_root_id]
        else:
            roots = [
                root
                for root in roots
                if not self.database.row(
                    """
                    SELECT id FROM chapter_analyses
                    WHERE root_section_id = ? AND status = 'succeeded'
                      AND fragment_set_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (root["id"], fragment_set["id"]),
                )
            ]
        if not roots:
            if not only_root_id and self._all_chapters_ready(book_id):
                self.database.execute(
                    "UPDATE books SET status = 'analyzed', updated_at = ? WHERE id = ?",
                    (now_iso(), book_id),
                )
                count = self.database.row(
                    "SELECT COUNT(*) AS count FROM knowledge_items WHERE book_id = ?",
                    (book_id,),
                )
                return {
                    "knowledge_count": int(count["count"]) if count else 0,
                    "chapter_count": 0,
                    "succeeded_count": 0,
                    "failed_chapters": [],
                    "parent_run_id": None,
                }
            raise ValueError("没有可拆书的已确认一级章节，请先检查章节范围")
        all_sections = self.database.rows(
            "SELECT * FROM sections WHERE book_id = ? ORDER BY position", (book_id,)
        )
        parent_run_id = uuid.uuid4().hex
        started = now_iso()
        self.database.execute(
            """
            INSERT INTO workflow_runs
              (id, scope_type, scope_id, stage, status, message,
               metadata_json, created_at, updated_at)
            VALUES (?, 'book_analysis_batch', ?, 'book_analysis', 'running', '',
                    ?, ?, ?)
            """,
            (
                parent_run_id,
                book_id,
                json.dumps({"chapter_count": len(roots)}, ensure_ascii=False),
                started,
                started,
            ),
        )
        semaphore = asyncio.Semaphore(5)

        async def analyze_root(
            root: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
            child_run_id = uuid.uuid4().hex
            now = now_iso()
            self.database.execute(
                """
                INSERT INTO workflow_runs
                  (id, scope_type, scope_id, stage, status, message,
                   parent_run_id, position, metadata_json, created_at, updated_at)
                VALUES (?, 'chapter_analysis', ?, 'book_analysis', 'pending', '',
                        ?, ?, ?, ?, ?)
                """,
                (
                    child_run_id,
                    root["id"],
                    parent_run_id,
                    root["position"],
                    json.dumps(
                        {"book_id": book_id, "chapter_title": root["title"]},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            try:
                fragments = self.evidence.chapter_fragments(
                    fragment_set["id"], root["id"]
                )
                chapter_source = build_chapter_source(
                    root,
                    all_sections,
                    fragments=fragments,
                    fragment_set_id=fragment_set["id"],
                )
                async with semaphore:
                    self.database.execute(
                        "UPDATE workflow_runs SET status = 'running', updated_at = ? WHERE id = ?",
                        (now_iso(), child_run_id),
                    )
                    raw = await task_provider.generate(
                        PROMPTS["book_analysis"], chapter_source.source
                    )
                try:
                    parsed = parse_json_object(raw)
                except json.JSONDecodeError:
                    async with semaphore:
                        repaired = await task_provider.generate(
                            PROMPTS["json_repair"], raw
                        )
                    parsed = parse_json_object(repaired)
                data = validate_chapter_analysis(
                    parsed, chapter_source.fragments_by_index
                )
                cards = derive_knowledge_cards(
                    data, chapter_source.index_to_section_id, book_id
                )
                rendered = render_chapter_markdown(data, cards)
                analysis_id = self._save_chapter_analysis(
                    book_id,
                    root,
                    data,
                    rendered,
                    chapter_source.source,
                    cards,
                    task_provider,
                    fragment_set["id"],
                )
                self.database.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'succeeded', message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (f"已生成 {len(cards)} 条知识资产", now_iso(), child_run_id),
                )
                return root, {"analysis_id": analysis_id, "card_count": len(cards)}, ""
            except Exception as error:
                self.database.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'failed', message = ?, error_stage = 'book_analysis',
                        updated_at = ? WHERE id = ?
                    """,
                    (str(error)[:500], now_iso(), child_run_id),
                )
                return root, None, str(error)

        results = await asyncio.gather(*(analyze_root(root) for root in roots))
        failed = [
            {"section_id": root["id"], "title": root["title"], "error": error}
            for root, result, error in results
            if result is None
        ]
        succeeded = [result for _, result, _ in results if result is not None]
        if only_root_id:
            all_ready = self._all_chapters_ready(book_id)
        else:
            all_ready = not failed and len(succeeded) == len(roots)
        status = (
            "analyzed"
            if all_ready
            else "analysis_partial_failed"
            if failed
            else "analysis_partial"
        )
        if not all_ready:
            self.database.execute(
                "DELETE FROM mind_maps WHERE book_id = ?", (book_id,)
            )
        self.database.execute(
            "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), book_id),
        )
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = ?, message = ?, updated_at = ? WHERE id = ?
            """,
            (
                "succeeded" if not failed else "failed",
                f"成功 {len(succeeded)} 章，失败 {len(failed)} 章",
                now_iso(),
                parent_run_id,
            ),
        )
        count = self.database.row(
            "SELECT COUNT(*) AS count FROM knowledge_items WHERE book_id = ?",
            (book_id,),
        )
        return {
            "knowledge_count": int(count["count"]) if count else 0,
            "chapter_count": len(roots),
            "succeeded_count": len(succeeded),
            "failed_chapters": failed,
            "parent_run_id": parent_run_id,
        }

    def _save_chapter_analysis(
        self,
        book_id: str,
        root: dict[str, Any],
        data: dict[str, Any],
        rendered: str,
        input_snapshot: str,
        cards: list[dict[str, Any]],
        provider: ModelProvider,
        fragment_set_id: str,
    ) -> str:
        current = self.database.row(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM chapter_analyses WHERE root_section_id = ?
            """,
            (root["id"],),
        )
        version = int(current["version"]) + 1 if current else 1
        analysis_id = uuid.uuid4().hex
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO chapter_analyses
                  (id, book_id, root_section_id, version, status,
                   structured_json, rendered_markdown, prompt_version,
                   provider, model, input_snapshot, fragment_set_id, created_at)
                VALUES (?, ?, ?, ?, 'succeeded', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    book_id,
                    root["id"],
                    version,
                    json.dumps(data, ensure_ascii=False),
                    rendered,
                    PROMPTS["book_analysis"].version,
                    provider.name,
                    provider.model,
                    input_snapshot,
                    fragment_set_id,
                    now_iso(),
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_items SET status = 'superseded'
                WHERE id IN (
                    SELECT link.knowledge_item_id
                    FROM chapter_analysis_knowledge_items link
                    JOIN chapter_analyses analysis
                      ON analysis.id = link.chapter_analysis_id
                    WHERE analysis.root_section_id = ?
                  )
                """,
                (root["id"],),
            )
            for card in cards:
                connection.execute(
                    """
                    INSERT INTO knowledge_items
                      (id, book_id, kind, title, body, source_section_ids,
                       chapter_analysis_id, origin, source_scheme, status,
                       stable_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'chapter_model',
                            'paragraph_evidence_v1', 'active', ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      kind = excluded.kind,
                      title = excluded.title,
                      body = excluded.body,
                      source_section_ids = excluded.source_section_ids,
                      chapter_analysis_id = excluded.chapter_analysis_id,
                      origin = excluded.origin,
                      source_scheme = excluded.source_scheme,
                      status = 'active',
                      stable_key = excluded.stable_key
                    """,
                    (
                        card["id"],
                        book_id,
                        card["kind"],
                        card["title"],
                        card["body"],
                        json.dumps(card["source_section_ids"], ensure_ascii=False),
                        analysis_id,
                        card["stable_key"],
                        now_iso(),
                    ),
                )
                connection.execute(
                    "DELETE FROM knowledge_item_sources WHERE knowledge_item_id = ?",
                    (card["id"],),
                )
                connection.executemany(
                    """
                    INSERT INTO knowledge_item_sources
                      (knowledge_item_id, content_index, source_order)
                    VALUES (?, ?, ?)
                    """,
                    [
                        (card["id"], index, position)
                        for position, index in enumerate(
                            card["source_content_indexes"], start=1
                        )
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO chapter_analysis_knowledge_items
                      (chapter_analysis_id, knowledge_item_id)
                    VALUES (?, ?)
                    """,
                    (analysis_id, card["id"]),
                )
        return analysis_id

    def _all_chapters_ready(self, book_id: str) -> bool:
        roots = self._chapter_roots(book_id)
        if not roots:
            return False
        fragment_set = self.evidence.ensure_current_fragment_set(book_id)
        return all(
            self.database.row(
                """
                SELECT id FROM chapter_analyses
                WHERE root_section_id = ? AND status = 'succeeded'
                  AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            for root in roots
        )

    async def retry_chapter(
        self,
        book_id: str,
        root_section_id: str,
        provider: ModelProvider | None = None,
    ) -> dict[str, Any]:
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        if book["book_type"] != "non_narrative":
            raise ValueError("单章模型拆书仅适用于非叙事类书籍")
        root = self.database.row(
            """
            SELECT * FROM sections
            WHERE id = ? AND book_id = ? AND parent_id IS NULL
              AND analysis_enabled = 1 AND status = 'confirmed'
            """,
            (root_section_id, book_id),
        )
        if not root:
            raise ValueError("章节不存在、未确认或未纳入拆书")
        return await self._analyze_non_narrative(
            book, root_section_id, provider or self.provider
        )

    async def _extract_character_relationships(
        self,
        book_id: str,
        sections: list[dict[str, Any]],
        provider: ModelProvider,
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
                    raw = await provider.generate(
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

    def _latest_chapter_analyses(self, book_id: str) -> list[dict[str, Any]]:
        roots = self._chapter_roots(book_id)
        if not roots:
            raise ValueError("没有纳入拆书的一级章节")
        fragment_set = self.evidence.ensure_current_fragment_set(book_id)
        latest: list[dict[str, Any]] = []
        for root in roots:
            analysis = self.database.row(
                """
                SELECT * FROM chapter_analyses
                WHERE root_section_id = ? AND status = 'succeeded'
                  AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            if not analysis:
                raise ValueError(
                    f"章节“{root['title']}”尚未生成段落级溯源拆书稿，请先重跑该章"
                )
            analysis["chapter_title"] = root["title"]
            analysis["position"] = root["position"]
            latest.append(analysis)
        return latest

    async def _book_analysis_input(
        self, book_id: str, provider: ModelProvider
    ) -> tuple[str, bool]:
        analyses = self._latest_chapter_analyses(book_id)
        complete = "\n\n".join(
            item["rendered_markdown"] for item in analyses
        )
        if len(complete) <= 90_000:
            return complete, False
        compressed: list[str] = []
        for item in analyses:
            content = item["compressed_markdown"].strip()
            if not content:
                source = item["rendered_markdown"]
                expected = set(re.findall(r"content_[0-9a-f]{8,40}", source))
                expected_assets = set(
                    re.findall(r"knowledge_[0-9a-f]{24}", source)
                )
                content = await provider.generate(
                    PROMPTS["chapter_compression"], source
                )
                actual = set(re.findall(r"content_[0-9a-f]{8,40}", content))
                actual_assets = set(
                    re.findall(r"knowledge_[0-9a-f]{24}", content)
                )
                if actual != expected or actual_assets != expected_assets:
                    raise ValueError(
                        f"章节“{item['chapter_title']}”压缩后来源标识不完整"
                    )
                self.database.execute(
                    "UPDATE chapter_analyses SET compressed_markdown = ? WHERE id = ?",
                    (content, item["id"]),
                )
            compressed.append(content)
        return "\n\n".join(compressed), True

    async def generate_project_knowledge_outputs(
        self,
        project_id: str,
        special_requirements: str = "",
        desired_episode_count: int | None = None,
        provider: ModelProvider | None = None,
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        if not project:
            raise KeyError(project_id)
        if not project["book_ids"]:
            raise ValueError("项目没有关联书籍")
        book = self.database.row(
            "SELECT * FROM books WHERE id = ?", (project["book_ids"][0],)
        )
        if not book:
            raise ValueError("项目关联书籍不存在")
        facts, compressed = await self._book_analysis_input(
            book["id"], task_provider
        )
        requirements = special_requirements.strip()
        count_text = (
            str(desired_episode_count)
            if desired_episode_count is not None
            else "未指定，由模型根据内容自行决定"
        )
        album_source = (
            f"# 书籍信息\n书名：{book['title']}\n作者：{book['author'] or '未填写'}\n"
            f"书籍类型：{'叙事类' if book['book_type'] == 'narrative' else '非叙事类'}\n\n"
            f"# 专辑特殊要求\n{requirements or '无'}\n\n"
            f"# 期望集数\n{count_text}\n\n# 拆书稿\n{facts}"
        )
        mind_source = (
            f"# 书籍信息\n书名：{book['title']}\n作者：{book['author'] or '未填写'}"
            f"\n\n# 拆书稿\n{facts}"
        )
        mind_result, album_result = await asyncio.gather(
            task_provider.generate(PROMPTS["mind_map"], mind_source),
            task_provider.generate(PROMPTS["album_outline"], album_source),
            return_exceptions=True,
        )
        response: dict[str, Any] = {
            "compressed": compressed,
            "mind_map": {"status": "failed"},
            "album_outline": {"status": "failed"},
        }
        if isinstance(mind_result, Exception):
            response["mind_map"]["error"] = str(mind_result)
        else:
            current = self.database.row(
                "SELECT COALESCE(MAX(version), 0) AS version FROM mind_maps WHERE book_id = ?",
                (book["id"],),
            )
            version = int(current["version"]) + 1 if current else 1
            self.database.execute(
                """
                INSERT INTO mind_maps
                  (id, book_id, version, content, provider, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    book["id"],
                    version,
                    mind_result.strip(),
                    task_provider.name,
                    task_provider.model,
                    now_iso(),
                ),
            )
            response["mind_map"] = {"status": "succeeded", "version": version}
        if isinstance(album_result, Exception):
            response["album_outline"]["error"] = str(album_result)
        else:
            try:
                try:
                    album_data = parse_json_object(album_result)
                except json.JSONDecodeError:
                    repaired_album = await task_provider.generate(
                        PROMPTS["json_repair"], album_result
                    )
                    album_data = parse_json_object(repaired_album)
                episodes, notice = self._validate_album_outline(
                    album_data, book, desired_episode_count
                )
                self._save_generated_album(project_id, episodes)
                self.database.execute(
                    """
                    UPDATE projects
                    SET album_special_requirements = ?, desired_episode_count = ?,
                        episode_count_notice = ?, status = 'outline_review',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        requirements,
                        desired_episode_count,
                        notice,
                        now_iso(),
                        project_id,
                    ),
                )
                response["album_outline"] = {
                    "status": "succeeded",
                    "episode_count": len(episodes),
                    "notice": notice,
                }
            except Exception as error:
                response["album_outline"]["error"] = str(error)
        response["project"] = self.project_detail(project_id)
        return response

    def _validate_album_outline(
        self,
        data: dict[str, Any],
        book: dict[str, Any],
        desired_episode_count: int | None,
    ) -> tuple[list[dict[str, Any]], str]:
        raw_episodes = data.get("album_outline")
        if not isinstance(raw_episodes, list) or not raw_episodes:
            raise ValueError("专辑大纲输出缺少 album_outline")
        sections = self.database.rows(
            "SELECT id FROM sections WHERE book_id = ?", (book["id"],)
        )
        index_map = {content_index(section["id"]): section["id"] for section in sections}
        active_assets = self.database.rows(
            """
            SELECT * FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
              AND source_scheme = 'paragraph_evidence_v1'
            """,
            (book["id"],),
        )
        asset_map = {asset["id"]: asset for asset in active_assets}
        asset_sources: dict[str, list[str]] = {}
        for asset in active_assets:
            rows = self.database.rows(
                """
                SELECT f.source_section_id
                FROM knowledge_item_sources source
                JOIN source_fragments f
                  ON f.content_index = source.content_index
                WHERE source.knowledge_item_id = ?
                ORDER BY source.source_order
                """,
                (asset["id"],),
            )
            asset_sources[asset["id"]] = list(
                dict.fromkeys(row["source_section_id"] for row in rows)
            )
        seen_regular: set[str] = set()
        episodes: list[dict[str, Any]] = []
        for position, item in enumerate(raw_episodes, start=1):
            if not isinstance(item, dict):
                raise ValueError("专辑大纲条目结构无效")
            title = item.get("title")
            main_points = item.get("main_points")
            content_type = item.get("content_type")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (title, main_points, content_type)
            ):
                raise ValueError(f"专辑第 {position} 条字段不完整")
            normalized_type = content_type.strip().replace("类", "")
            if normalized_type not in {"解读", "过渡"}:
                raise ValueError(f"专辑第 {position} 条内容类型无效")
            if book["book_type"] == "narrative" and normalized_type != "解读":
                raise ValueError("叙事类书籍不能生成过渡声音")
            knowledge_item_ids = item.get("knowledge_item_ids")
            source_section_ids: list[str]
            if isinstance(knowledge_item_ids, list) and knowledge_item_ids:
                if not all(
                    isinstance(asset_id, str) and asset_id.strip()
                    for asset_id in knowledge_item_ids
                ):
                    raise ValueError(f"专辑第 {position} 条知识资产 ID 无效")
                knowledge_item_ids = list(
                    dict.fromkeys(asset_id.strip() for asset_id in knowledge_item_ids)
                )
                unknown = [
                    asset_id
                    for asset_id in knowledge_item_ids
                    if asset_id not in asset_map
                ]
                if unknown:
                    raise ValueError(f"专辑第 {position} 条引用了不存在的知识资产")
                if normalized_type == "过渡" and len(knowledge_item_ids) < 2:
                    raise ValueError(f"专辑第 {position} 条过渡声音至少需要两个知识资产")
                if normalized_type == "解读":
                    duplicated = seen_regular.intersection(knowledge_item_ids)
                    if duplicated:
                        raise ValueError("同一知识资产被拆分到多条普通声音")
                    seen_regular.update(knowledge_item_ids)
                source_section_ids = list(
                    dict.fromkeys(
                        section_id
                        for asset_id in knowledge_item_ids
                        for section_id in asset_sources[asset_id]
                    )
                )
            else:
                identifier = item.get("section_identifier")
                if not isinstance(identifier, str) or not identifier.strip():
                    raise ValueError(f"专辑第 {position} 条缺少知识资产来源")
                indexes = [
                    part.strip()
                    for part in re.split(r"[,，]", identifier)
                    if part.strip()
                ]
                expected_count = 2 if normalized_type == "过渡" else 1
                if len(indexes) != expected_count:
                    raise ValueError(f"专辑第 {position} 条来源索引数量无效")
                unknown = [index for index in indexes if index not in index_map]
                if unknown:
                    raise ValueError(
                        f"专辑第 {position} 条引用了不存在的 content_index"
                    )
                if normalized_type == "解读":
                    if indexes[0] in seen_regular:
                        raise ValueError("同一 content_index 被拆分到多条普通声音")
                    seen_regular.add(indexes[0])
                knowledge_item_ids = []
                source_section_ids = [index_map[index] for index in indexes]
            episodes.append(
                {
                    "id": uuid.uuid4().hex,
                    "position": position,
                    "title": title.strip(),
                    "content_type": normalized_type,
                    "style": "观点",
                    "content_framework": main_points.strip(),
                    "source_section_ids": source_section_ids,
                    "knowledge_item_ids": knowledge_item_ids,
                }
            )
        notice = ""
        if desired_episode_count is not None and desired_episode_count != len(episodes):
            notice = (
                f"期望 {desired_episode_count} 集，模型在保持来源完整的前提下生成"
                f" {len(episodes)} 集。"
            )
        return episodes, notice

    def _save_generated_album(
        self, project_id: str, episodes: list[dict[str, Any]]
    ) -> None:
        self.database.execute("DELETE FROM episodes WHERE project_id = ?", (project_id,))
        self.database.executemany(
            """
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style,
               content_framework, status, source_section_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'outline_review', ?)
            """,
            [
                (
                    item["id"],
                    project_id,
                    item["position"],
                    item["title"],
                    item["content_type"],
                    item["style"],
                    item["content_framework"],
                    json.dumps(item["source_section_ids"], ensure_ascii=False),
                )
                for item in episodes
            ],
        )
        self.database.executemany(
            """
            INSERT INTO episode_knowledge_items
              (episode_id, knowledge_item_id, position, role)
            VALUES (?, ?, ?, 'primary')
            """,
            [
                (item["id"], knowledge_item_id, position)
                for item in episodes
                for position, knowledge_item_id in enumerate(
                    item.get("knowledge_item_ids", []), start=1
                )
            ],
        )

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
        has_chapter_analyses = self.database.row(
            "SELECT id FROM chapter_analyses WHERE book_id = ? LIMIT 1",
            (book_id,),
        )
        source_sections = (
            []
            if has_chapter_analyses
            else self.database.rows(
                """
                SELECT * FROM sections
                WHERE book_id = ? AND level = 4
                ORDER BY position
                LIMIT 12
                """,
                (book_id,),
            )
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
            asset_links = self.database.rows(
                """
                SELECT item.*
                FROM episode_knowledge_items link
                JOIN knowledge_items item ON item.id = link.knowledge_item_id
                WHERE link.episode_id = ?
                ORDER BY link.position
                """,
                (episode["id"],),
            )
            for asset in asset_links:
                if (
                    asset["book_id"] not in valid_book_ids
                    or asset["status"] != "active"
                    or asset["source_scheme"] != "paragraph_evidence_v1"
                ):
                    raise ValueError(
                        f"第 {episode['position']} 条声音包含无效或已过期知识资产"
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
        self,
        episode_id: str,
        from_stage: str = "outline",
        provider: ModelProvider | None = None,
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
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
                content = await task_provider.generate(prompt, context.source)
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
                provider=task_provider.name,
                model=task_provider.model,
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
        for episode in project["episodes"]:
            episode["knowledge_item_ids"] = [
                row["knowledge_item_id"]
                for row in self.database.rows(
                    """
                    SELECT knowledge_item_id
                    FROM episode_knowledge_items
                    WHERE episode_id = ?
                    ORDER BY position
                    """,
                    (episode["id"],),
                )
            ]
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
        episode["knowledge_item_ids"] = [
            row["knowledge_item_id"]
            for row in self.database.rows(
                """
                SELECT knowledge_item_id
                FROM episode_knowledge_items
                WHERE episode_id = ?
                ORDER BY position
                """,
                (episode_id,),
            )
        ]
        episode["evidence"] = self.contexts.evidence_bundle(episode_id)
        return episode
