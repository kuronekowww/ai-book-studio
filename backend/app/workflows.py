from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter
from collections.abc import Callable
from typing import Any

from .album_planning import AlbumModule, AlbumPlanningService
from .chapter_analysis import (
    build_chapter_source,
    content_index,
    derive_knowledge_cards,
    parse_json_object,
    render_chapter_markdown,
    validate_chapter_analysis_partial,
)
from .contexts import EpisodeContextBuilder
from .db import Database, now_iso
from .evidence import EvidenceService
from .prompt_config import PromptConfigurationService
from .prompts import PROMPTS
from .providers import (
    ModelGenerationError,
    ModelOutputTruncatedError,
    ModelProvider,
)
from .runs import RunService
from .text_metrics import (
    DEFAULT_EPISODE_WORD_COUNT_MAX,
    DEFAULT_EPISODE_WORD_COUNT_MIN,
    format_episode_word_count_range,
    inspect_word_count,
    validate_episode_word_count_range,
)


CHAPTER_COMPRESSION_CHUNK_CHARS = 5_000
CHAPTER_COMPRESSION_MIN_CHARS = 600
BOOK_ANALYSIS_INPUT_MAX_CHARS = 100_000
StageProgressCallback = Callable[
    [str, str, dict[str, Any] | None, str | None], None
]


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


class ChapterAnalysisOutputError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        diagnostics: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.diagnostics = diagnostics or {}


class LocalRevalidationProvider:
    name = "local-revalidation"
    model = "stored-structured-json"

    async def generate(self, prompt: Any, source: str) -> str:
        raise RuntimeError("本地历史重新校验不得调用模型")


class WorkflowService:
    def __init__(
        self,
        database: Database,
        provider: ModelProvider,
        prompt_configuration: PromptConfigurationService | None = None,
    ):
        self.database = database
        self.provider = provider
        self.evidence = EvidenceService(database)
        self.contexts = EpisodeContextBuilder(database)
        self.prompts = prompt_configuration or PromptConfigurationService(database)
        self.album_planning = AlbumPlanningService(database)

    async def analyze_book(
        self,
        book_id: str,
        provider: ModelProvider | None = None,
        *,
        parent_run_id: str | None = None,
        progress_callback: StageProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        if book["book_type"] == "non_narrative":
            return await self._analyze_non_narrative(
                book,
                provider=task_provider,
                parent_run_id=parent_run_id,
                progress_callback=progress_callback,
                cancelled=cancelled,
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
        *,
        parent_run_id: str | None = None,
        progress_callback: StageProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        task_provider = provider or self.provider
        run_service = RunService(self.database)
        book_id = book["id"]
        fragment_set = self.evidence.ensure_current_fragment_set(book_id)
        roots = self._chapter_roots(book_id)
        existing_parent = (
            run_service.get(parent_run_id) if parent_run_id else None
        )
        locked_root_ids = (
            (existing_parent.get("metadata_json") or {}).get("root_section_ids")
            if existing_parent
            else None
        )
        if isinstance(locked_root_ids, list) and locked_root_ids:
            locked_set = {
                item for item in locked_root_ids if isinstance(item, str)
            }
            roots = [root for root in roots if root["id"] in locked_set]
        elif only_root_id:
            roots = [root for root in roots if root["id"] == only_root_id]
        else:
            pending_roots: list[dict[str, Any]] = []
            for root in roots:
                latest = self.database.row(
                    """
                    SELECT status FROM chapter_analyses
                    WHERE root_section_id = ? AND fragment_set_id = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (root["id"], fragment_set["id"]),
                )
                if not latest or latest["status"] != "succeeded":
                    pending_roots.append(root)
            roots = pending_roots
        if not roots:
            if not only_root_id and self._all_chapters_ready(book_id):
                self.database.execute(
                    "UPDATE books SET status = 'analyzed', updated_at = ? WHERE id = ?",
                    (now_iso(), book_id),
                )
                count = self.database.row(
                    """
                    SELECT COUNT(*) AS count FROM knowledge_items
                    WHERE book_id = ? AND status = 'active'
                    """,
                    (book_id,),
                )
                return {
                    "knowledge_count": int(count["count"]) if count else 0,
                    "chapter_count": 0,
                    "succeeded_count": 0,
                    "failed_chapters": [],
                    "parent_run_id": parent_run_id,
                }
            raise ValueError("没有可拆书的已确认一级章节，请先检查章节范围")
        all_sections = self.database.rows(
            "SELECT * FROM sections WHERE book_id = ? ORDER BY position", (book_id,)
        )
        external_parent = parent_run_id is not None
        if parent_run_id is None:
            parent, _ = run_service.create(
                scope_type="book_analysis_batch",
                scope_id=book_id,
                stage="book_analysis",
                current_stage="analyze_chapters",
                progress_total=len(roots),
                metadata={
                    "chapter_count": len(roots),
                    "provider": task_provider.name,
                    "model": task_provider.model,
                    "root_section_ids": [root["id"] for root in roots],
                    "fragment_set_id": fragment_set["id"],
                },
                reuse_active=False,
            )
            parent_run_id = parent["id"]
            run_service.mark_running(
                parent_run_id,
                current_stage="analyze_chapters",
                message="正在逐章拆书",
            )
        else:
            run_service.merge_metadata(
                parent_run_id,
                {
                    "chapter_count": len(roots),
                    "provider": task_provider.name,
                    "model": task_provider.model,
                    "root_section_ids": [root["id"] for root in roots],
                    "fragment_set_id": fragment_set["id"],
                },
            )
            run_service.set_progress(
                parent_run_id,
                total=len(roots),
                current_stage="analyze_chapters",
                message="正在逐章拆书",
            )
        if progress_callback:
            progress_callback(
                "prepare_chapters",
                "succeeded",
                {
                    "chapter_count": len(roots),
                    "fragment_set_id": fragment_set["id"],
                },
                f"已锁定 {len(roots)} 个一级章节",
            )
            progress_callback(
                "analyze_chapters",
                "running",
                None,
                "正在逐章拆书",
            )
        semaphore = asyncio.Semaphore(5)

        async def analyze_root(
            root: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
            existing_child = self.database.row(
                """
                SELECT * FROM workflow_runs
                WHERE parent_run_id = ? AND scope_type = 'chapter_analysis'
                  AND scope_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (parent_run_id, root["id"]),
            )
            if existing_child:
                child_run_id = existing_child["id"]
            else:
                child, _ = run_service.create(
                    scope_type="chapter_analysis",
                    scope_id=root["id"],
                    stage="book_analysis",
                    current_stage="book_analysis",
                    progress_total=1,
                    metadata={
                        "book_id": book_id,
                        "chapter_title": root["title"],
                        "provider": task_provider.name,
                        "model": task_provider.model,
                    },
                    parent_run_id=parent_run_id,
                    position=root["position"],
                    reuse_active=False,
                )
                child_run_id = child["id"]

            latest_analysis = self.database.row(
                """
                SELECT * FROM chapter_analyses
                WHERE root_section_id = ? AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            child_state = run_service.get(child_run_id)
            if (
                latest_analysis
                and latest_analysis["status"] in {"succeeded", "partial"}
                and child_state["status"] in {
                    "pending",
                    "running",
                    "succeeded",
                    "partial_failed",
                }
            ):
                status = latest_analysis["status"]
                if child_state["status"] in {"pending", "running"}:
                    run_service.set_stage(
                        child_run_id,
                        "book_analysis",
                        "succeeded" if status == "succeeded" else "partial_failed",
                        output={
                            "artifact_type": "chapter_analysis",
                            "analysis_id": latest_analysis["id"],
                            "version": latest_analysis["version"],
                        },
                    )
                    run_service.set_progress(child_run_id, current=1, total=1)
                    run_service.finish(
                        child_run_id,
                        status=(
                            "succeeded"
                            if status == "succeeded"
                            else "partial_failed"
                        ),
                        message=(
                            "已恢复章节拆书结果"
                            if status == "succeeded"
                            else "已恢复部分成功章节"
                        ),
                    )
                return root, {
                    "analysis_id": latest_analysis["id"],
                    "card_count": int(latest_analysis["valid_item_count"]),
                    "status": status,
                    "invalid_item_count": int(
                        latest_analysis["invalid_item_count"]
                    ),
                }, ""
            if child_state["status"] in {"failed", "cancelled"}:
                return root, None, child_state["message"] or child_state["status"]
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
                    run_service.mark_running(
                        child_run_id,
                        current_stage="book_analysis",
                        message=f"正在拆解《{root['title']}》",
                        increment_attempt=True,
                    )
                    raw = await task_provider.generate(
                        PROMPTS["book_analysis"], chapter_source.source
                    )
                if cancelled and cancelled():
                    raise asyncio.CancelledError
                try:
                    parsed = parse_json_object(raw)
                except json.JSONDecodeError as original_error:
                    async with semaphore:
                        try:
                            repaired = await task_provider.generate(
                                PROMPTS["json_repair"], raw
                            )
                        except Exception as repair_error:
                            raise ChapterAnalysisOutputError(
                                "模型输出 JSON 无法解析，且自动修复失败",
                                category="json_repair_failed",
                                diagnostics={
                                    "response_chars": len(raw),
                                    "original_json_error": str(original_error),
                                    "repair_error": str(repair_error),
                                },
                            ) from repair_error
                    try:
                        parsed = parse_json_object(repaired)
                    except (json.JSONDecodeError, ValueError) as repair_parse_error:
                        raise ChapterAnalysisOutputError(
                            "模型输出 JSON 无法解析，自动修复后仍然无效",
                            category="json_repair_failed",
                            diagnostics={
                                "response_chars": len(raw),
                                "repair_response_chars": len(repaired),
                                "original_json_error": str(original_error),
                                "repair_json_error": str(repair_parse_error),
                            },
                        ) from repair_parse_error
                validation = validate_chapter_analysis_partial(
                    parsed, chapter_source.fragments_by_index
                )
                data = validation.data
                cards = derive_knowledge_cards(
                    data, chapter_source.index_to_section_id, book_id
                )
                if not cards:
                    raise ChapterAnalysisOutputError(
                        "章节没有任何通过来源校验的知识资产",
                        category="no_valid_assets",
                        diagnostics={
                            "response_chars": len(raw),
                            "invalid_item_count": validation.invalid_item_count,
                        },
                    )
                rendered = render_chapter_markdown(data, cards)
                analysis_status = (
                    "partial" if validation.invalid_item_count else "succeeded"
                )
                analysis_id = self._save_chapter_analysis(
                    book_id,
                    root,
                    data,
                    rendered,
                    chapter_source.source,
                    cards,
                    task_provider,
                    fragment_set["id"],
                    status=analysis_status,
                    validation_issues=validation.issues,
                    valid_item_count=len(cards),
                    invalid_item_count=validation.invalid_item_count,
                )
                run_status = (
                    "partial_failed"
                    if analysis_status == "partial"
                    else "succeeded"
                )
                message = (
                    f"已保存 {len(cards)} 条知识资产，"
                    f"{validation.invalid_item_count} 条未通过校验"
                    if analysis_status == "partial"
                    else f"已生成 {len(cards)} 条知识资产"
                )
                run_service.merge_metadata(
                    child_run_id,
                    {
                        "book_id": book_id,
                        "chapter_title": root["title"],
                        "analysis_status": analysis_status,
                        "valid_item_count": len(cards),
                        "invalid_item_count": validation.invalid_item_count,
                        "validation_issues": validation.issues,
                        "response_chars": len(raw),
                    },
                )
                run_service.set_stage(
                    child_run_id,
                    "book_analysis",
                    run_status,
                    message=message,
                    output={
                        "artifact_type": "chapter_analysis",
                        "analysis_id": analysis_id,
                        "version": self.database.row(
                            "SELECT version FROM chapter_analyses WHERE id = ?",
                            (analysis_id,),
                        )["version"],
                    },
                )
                run_service.set_progress(child_run_id, current=1, total=1)
                run_service.finish(
                    child_run_id,
                    status=run_status,
                    message=message,
                )
                return root, {
                    "analysis_id": analysis_id,
                    "card_count": len(cards),
                    "status": analysis_status,
                    "invalid_item_count": validation.invalid_item_count,
                }, ""
            except Exception as error:
                diagnostics: dict[str, Any] = {
                    "book_id": book_id,
                    "chapter_title": root["title"],
                }
                category = "book_analysis_error"
                if isinstance(error, ModelGenerationError):
                    category = error.category
                    diagnostics.update(error.diagnostics)
                elif isinstance(error, ChapterAnalysisOutputError):
                    category = error.category
                    diagnostics.update(error.diagnostics)
                diagnostics["error_category"] = category
                run_service.merge_metadata(child_run_id, diagnostics)
                run_service.fail(
                    child_run_id,
                    error,
                    error_stage="book_analysis",
                )
                return root, None, str(error)

        async def analyze_and_report(
            root: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
            result = await analyze_root(root)
            counts = self.database.row(
                """
                SELECT COUNT(*) AS total,
                  SUM(
                    CASE WHEN status IN
                      ('succeeded', 'partial_failed', 'failed', 'cancelled')
                    THEN 1 ELSE 0 END
                  ) AS completed
                FROM workflow_runs WHERE parent_run_id = ?
                """,
                (parent_run_id,),
            ) or {"total": len(roots), "completed": 0}
            run_service.set_progress(
                parent_run_id,
                current=int(counts["completed"] or 0),
                total=int(counts["total"] or len(roots)),
                current_stage="analyze_chapters",
                message=(
                    f"已完成 {int(counts['completed'] or 0)} / "
                    f"{int(counts['total'] or len(roots))} 章"
                ),
            )
            return result

        results = await asyncio.gather(
            *(analyze_and_report(root) for root in roots)
        )
        failed = [
            {"section_id": root["id"], "title": root["title"], "error": error}
            for root, result, error in results
            if result is None
        ]
        succeeded = [
            result
            for _, result, _ in results
            if result is not None and result["status"] == "succeeded"
        ]
        partial = [
            {
                "section_id": root["id"],
                "title": root["title"],
                "analysis_id": result["analysis_id"],
                "valid_item_count": result["card_count"],
                "invalid_item_count": result["invalid_item_count"],
            }
            for root, result, _ in results
            if result is not None and result["status"] == "partial"
        ]
        if only_root_id:
            all_ready = self._all_chapters_ready(book_id)
        else:
            all_ready = not failed and not partial and len(succeeded) == len(roots)
        status = (
            "analyzed"
            if all_ready
            else "analysis_partial_failed"
            if failed or partial
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
        summary_message = (
            f"成功 {len(succeeded)} 章，部分成功 {len(partial)} 章，"
            f"失败 {len(failed)} 章"
        )
        analysis_stage_status = (
            "partial_failed" if failed or partial else "succeeded"
        )
        run_service.set_stage(
            parent_run_id,
            "analyze_chapters",
            analysis_stage_status,
            message=summary_message,
            output={
                "artifact_type": "chapter_analysis_batch",
                "succeeded_count": len(succeeded),
                "partial_count": len(partial),
                "failed_count": len(failed),
            },
        )
        if progress_callback:
            progress_callback(
                "analyze_chapters",
                analysis_stage_status,
                {
                    "succeeded_count": len(succeeded),
                    "partial_count": len(partial),
                    "failed_count": len(failed),
                },
                summary_message,
            )
            progress_callback(
                "finalize_book",
                "running",
                None,
                "正在汇总书籍知识状态",
            )
            progress_callback(
                "finalize_book",
                "succeeded",
                {"book_status": status},
                "书籍知识状态已更新",
            )
        if not external_parent:
            run_service.finish(
                parent_run_id,
                status=analysis_stage_status,
                message=summary_message,
            )
        count = self.database.row(
            """
            SELECT COUNT(*) AS count FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
            """,
            (book_id,),
        )
        return {
            "knowledge_count": int(count["count"]) if count else 0,
            "chapter_count": len(roots),
            "succeeded_count": len(succeeded),
            "partial_chapters": partial,
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
        *,
        status: str = "succeeded",
        validation_issues: list[dict[str, Any]] | None = None,
        valid_item_count: int = 0,
        invalid_item_count: int = 0,
        prompt_version: str | None = None,
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
                   provider, model, input_snapshot, fragment_set_id,
                   validation_issues_json, valid_item_count, invalid_item_count,
                   created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    book_id,
                    root["id"],
                    version,
                    status,
                    json.dumps(data, ensure_ascii=False),
                    rendered,
                    prompt_version or PROMPTS["book_analysis"].version,
                    provider.name,
                    provider.model,
                    input_snapshot,
                    fragment_set_id,
                    json.dumps(validation_issues or [], ensure_ascii=False),
                    valid_item_count,
                    invalid_item_count,
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
        for root in roots:
            latest = self.database.row(
                """
                SELECT status FROM chapter_analyses
                WHERE root_section_id = ? AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            if not latest or latest["status"] != "succeeded":
                return False
        return True

    def revalidate_partial_chapters(self, book_id: str) -> dict[str, Any]:
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        if book["book_type"] != "non_narrative":
            raise ValueError("历史逐章重新校验仅适用于非叙事类书籍")
        unfinished = self.database.row(
            """
            SELECT id FROM workflow_runs
            WHERE status IN ('pending', 'running')
              AND (
                scope_id = ?
                OR json_extract(metadata_json, '$.book_id') = ?
              )
            LIMIT 1
            """,
            (book_id, book_id),
        )
        if unfinished:
            raise ValueError("该书仍有运行中的任务，请等待完成后再重新校验")

        fragment_set = self.evidence.ensure_current_fragment_set(book_id)
        roots = self._chapter_roots(book_id)
        all_sections = self.database.rows(
            "SELECT * FROM sections WHERE book_id = ? ORDER BY position",
            (book_id,),
        )
        upgraded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        local_provider = LocalRevalidationProvider()

        for root in roots:
            latest = self.database.row(
                """
                SELECT * FROM chapter_analyses
                WHERE root_section_id = ? AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            if not latest or latest["status"] != "partial":
                skipped.append(
                    {
                        "section_id": root["id"],
                        "title": root["title"],
                        "status": latest["status"] if latest else "missing",
                    }
                )
                continue
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
                validation = validate_chapter_analysis_partial(
                    latest["structured_json"],
                    chapter_source.fragments_by_index,
                )
                if validation.issues:
                    raise ValueError(validation.issues[0]["error"])
                cards = derive_knowledge_cards(
                    validation.data,
                    chapter_source.index_to_section_id,
                    book_id,
                )
                existing_ids = {
                    row["knowledge_item_id"]
                    for row in self.database.rows(
                        """
                        SELECT knowledge_item_id
                        FROM chapter_analysis_knowledge_items
                        WHERE chapter_analysis_id = ?
                        """,
                        (latest["id"],),
                    )
                }
                new_ids = {card["id"] for card in cards}
                if not cards or new_ids != existing_ids:
                    raise ValueError(
                        "重新校验后的知识资产集合与当前部分版本不一致，"
                        "为保护历史数据已停止升级"
                    )
                rendered = render_chapter_markdown(validation.data, cards)
                analysis_id = self._save_chapter_analysis(
                    book_id,
                    root,
                    validation.data,
                    rendered,
                    latest["input_snapshot"],
                    cards,
                    local_provider,
                    fragment_set["id"],
                    status="succeeded",
                    validation_issues=[],
                    valid_item_count=len(cards),
                    invalid_item_count=0,
                    prompt_version="local-revalidation-2026-07-28",
                )
                upgraded.append(
                    {
                        "section_id": root["id"],
                        "title": root["title"],
                        "previous_analysis_id": latest["id"],
                        "analysis_id": analysis_id,
                        "version": int(latest["version"]) + 1,
                        "knowledge_count": len(cards),
                    }
                )
            except Exception as error:
                failed.append(
                    {
                        "section_id": root["id"],
                        "title": root["title"],
                        "error": str(error),
                    }
                )

        all_ready = self._all_chapters_ready(book_id)
        self.database.execute(
            "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
            (
                "analyzed" if all_ready else "analysis_partial_failed",
                now_iso(),
                book_id,
            ),
        )
        active = self.database.row(
            """
            SELECT COUNT(*) AS count FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
            """,
            (book_id,),
        )
        return {
            "book_id": book_id,
            "upgraded": upgraded,
            "skipped": skipped,
            "failed": failed,
            "all_chapters_ready": all_ready,
            "book_status": "analyzed" if all_ready else "analysis_partial_failed",
            "active_knowledge_count": int(active["count"]) if active else 0,
            "model_calls": 0,
        }

    async def retry_chapter(
        self,
        book_id: str,
        root_section_id: str,
        provider: ModelProvider | None = None,
        *,
        parent_run_id: str | None = None,
        progress_callback: StageProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
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
            book,
            root_section_id,
            provider or self.provider,
            parent_run_id=parent_run_id,
            progress_callback=progress_callback,
            cancelled=cancelled,
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
                        {
                            "book_id": book_id,
                            "section_title": section["title"],
                            "provider": provider.name,
                            "model": provider.model,
                        },
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
            WHERE book_id = ? AND kind = '人物关系' AND status = 'active'
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
                WHERE root_section_id = ? AND fragment_set_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            if not analysis or analysis["status"] != "succeeded":
                detail = ""
                if analysis and analysis["status"] == "partial":
                    detail = (
                        f"（最新版本有 {analysis['invalid_item_count']} 条"
                        "知识资产未通过来源或金句原文校验）"
                    )
                raise ValueError(
                    f"章节“{root['title']}”尚未完整通过段落级溯源校验"
                    f"{detail}，请先重跑该章"
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
        if len(complete) <= BOOK_ANALYSIS_INPUT_MAX_CHARS:
            return complete, False
        compact_chapters = [
            self._compact_chapter_analysis(book_id, item)
            for item in analyses
        ]
        compact_complete = "\n\n".join(compact_chapters)
        if len(compact_complete) <= BOOK_ANALYSIS_INPUT_MAX_CHARS:
            return compact_complete, True
        limiter = asyncio.Semaphore(5)

        async def compressed_chapter(
            item: dict[str, Any], source: str
        ) -> str:
            return await self._compress_chapter_markdown(
                item["chapter_title"],
                source,
                provider,
                limiter=limiter,
            )

        results = await asyncio.gather(
            *(
                compressed_chapter(item, source)
                for item, source in zip(
                    analyses, compact_chapters, strict=True
                )
            ),
            return_exceptions=True,
        )
        error = next(
            (result for result in results if isinstance(result, Exception)),
            None,
        )
        if error:
            raise error
        compressed = [str(result) for result in results]
        return "\n\n".join(compressed), True

    def _compact_chapter_analysis(
        self, book_id: str, analysis: dict[str, Any]
    ) -> str:
        data = analysis["structured_json"]
        fragments = self.database.rows(
            """
            SELECT content_index, source_section_id
            FROM source_fragments WHERE book_id = ?
            """,
            (book_id,),
        )
        index_to_section_id = {
            fragment["content_index"]: fragment["source_section_id"]
            for fragment in fragments
        }
        cards = derive_knowledge_cards(data, index_to_section_id, book_id)
        active_ids = {
            row["id"]
            for row in self.database.rows(
                """
                SELECT id FROM knowledge_items
                WHERE book_id = ? AND status = 'active'
                  AND source_scheme = 'paragraph_evidence_v1'
                """,
                (book_id,),
            )
        }
        card_lookup = {
            (
                card["kind"],
                card["body"],
                tuple(card["source_content_indexes"]),
            ): card
            for card in cards
            if card["id"] in active_ids and card["kind"] != "论据"
        }

        lines = [
            f"# {data['chapter_title']}",
            f"**章节主题：** {data['chapter_theme']}",
        ]

        def append_asset(
            kind: str,
            label: str,
            body: str,
            indexes: list[str],
        ) -> None:
            card = card_lookup.get((kind, body, tuple(indexes)))
            if not card:
                return
            lines.extend(
                [
                    f"- [{kind}] {label}：{re.sub(r'\\s+', ' ', body).strip()}",
                    f"  - 知识资产 ID：{card['id']}",
                    f"  - 原文索引：{'、'.join(indexes)}",
                ]
            )

        for subtopic in data["subtopics"]:
            lines.append(f"\n## 子主题：{subtopic['title']}")
            for definition in subtopic["definitions"]:
                append_asset(
                    "概念",
                    definition["name"],
                    definition["definition"],
                    definition["source_content_indexes"],
                )
            for quote in subtopic["quotes"]:
                append_asset(
                    "金句",
                    "金句",
                    quote["text"],
                    quote["source_content_indexes"],
                )
            for viewpoint in subtopic["viewpoints"]:
                append_asset(
                    "观点",
                    "主要观点",
                    viewpoint["text"],
                    viewpoint["source_content_indexes"],
                )
                case = viewpoint["case"]
                if case:
                    body = f"{case['summary']}\n\n关联：{case['relation']}"
                    append_asset(
                        "案例",
                        "案例",
                        body,
                        case["source_content_indexes"],
                    )
            for case in subtopic.get("orphan_cases", []):
                body = f"{case['summary']}\n\n关联：{case['relation']}"
                append_asset(
                    "案例",
                    "案例",
                    body,
                    case["source_content_indexes"],
                )
        return "\n".join(lines)

    async def _compress_chapter_markdown(
        self,
        chapter_title: str,
        source: str,
        provider: ModelProvider,
        *,
        limiter: asyncio.Semaphore | None = None,
    ) -> str:
        limiter = limiter or asyncio.Semaphore(5)
        chunks = self._split_markdown_chunks(
            source, CHAPTER_COMPRESSION_CHUNK_CHARS
        )
        compressed = await asyncio.gather(
            *(
                self._compress_chapter_chunk(
                    chapter_title, chunk, provider, limiter
                )
                for chunk in chunks
            )
        )
        result = "\n\n".join(item.strip() for item in compressed if item.strip())
        self._validate_compression_identifiers(chapter_title, source, result)
        return result

    async def _compress_chapter_chunk(
        self,
        chapter_title: str,
        source: str,
        provider: ModelProvider,
        limiter: asyncio.Semaphore,
    ) -> str:
        try:
            async with limiter:
                content = await provider.generate(
                    PROMPTS["chapter_compression"], source
                )
        except ModelOutputTruncatedError as error:
            return await self._compress_smaller_chapter_chunks(
                chapter_title,
                source,
                provider,
                limiter,
                error,
            )
        try:
            self._validate_compression_identifiers(
                chapter_title, source, content
            )
        except ValueError as error:
            return await self._compress_smaller_chapter_chunks(
                chapter_title,
                source,
                provider,
                limiter,
                error,
            )
        return content

    async def _compress_smaller_chapter_chunks(
        self,
        chapter_title: str,
        source: str,
        provider: ModelProvider,
        limiter: asyncio.Semaphore,
        error: Exception,
    ) -> str:
        if len(source) <= CHAPTER_COMPRESSION_MIN_CHARS:
            if isinstance(error, ModelOutputTruncatedError):
                raise ValueError(
                    f"章节“{chapter_title}”压缩在最小分段后仍达到模型输出"
                    f"长度上限（当前分段 {len(source)} 字符）"
                ) from error
            raise error
        target_chars = max(
            CHAPTER_COMPRESSION_MIN_CHARS,
            min(len(source) - 1, len(source) // 2),
        )
        smaller_chunks = self._split_markdown_chunks(source, target_chars)
        if len(smaller_chunks) < 2:
            midpoint = max(1, len(source) // 2)
            smaller_chunks = [source[:midpoint], source[midpoint:]]
        compressed = await asyncio.gather(
            *(
                self._compress_chapter_chunk(
                    chapter_title, chunk, provider, limiter
                )
                for chunk in smaller_chunks
                if chunk
            )
        )
        content = "\n\n".join(
            item.strip() for item in compressed if item.strip()
        )
        self._validate_compression_identifiers(chapter_title, source, content)
        return content

    @staticmethod
    def _split_markdown_chunks(source: str, max_chars: int) -> list[str]:
        text = source.strip()
        if not text or len(text) <= max_chars:
            return [text] if text else []

        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n{2,}", text)
            if paragraph.strip()
        ]
        atoms: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph) <= max_chars:
                atoms.append(paragraph)
                continue
            lines = [line for line in paragraph.splitlines() if line.strip()]
            for line in lines:
                if len(line) <= max_chars:
                    atoms.append(line)
                    continue
                atoms.extend(
                    line[index : index + max_chars]
                    for index in range(0, len(line), max_chars)
                )

        chunks: list[str] = []
        current = ""
        for atom in atoms:
            candidate = f"{current}\n\n{atom}" if current else atom
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = atom
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _validate_compression_identifiers(
        chapter_title: str, source: str, compressed: str
    ) -> None:
        patterns = {
            "原文索引": r"content_[0-9a-f]{8,40}",
            "知识资产 ID": r"knowledge_[0-9a-f]{24}",
        }
        missing: list[str] = []
        unexpected: list[str] = []
        for label, pattern in patterns.items():
            expected = set(re.findall(pattern, source))
            actual = set(re.findall(pattern, compressed))
            if expected - actual:
                missing.append(f"{label} 缺少 {len(expected - actual)} 个")
            if actual - expected:
                unexpected.append(f"{label} 新增 {len(actual - expected)} 个")
        if missing or unexpected:
            detail = "；".join([*missing, *unexpected])
            raise ValueError(
                f"章节“{chapter_title}”压缩后来源标识不完整：{detail}"
            )

    async def generate_project_knowledge_outputs(
        self,
        project_id: str,
        special_requirements: str = "",
        desired_episode_count: int | None = None,
        provider: ModelProvider | None = None,
        *,
        episode_word_count_min: int = DEFAULT_EPISODE_WORD_COUNT_MIN,
        episode_word_count_max: int = DEFAULT_EPISODE_WORD_COUNT_MAX,
        mind_map_provider: ModelProvider | None = None,
        album_outline_provider: ModelProvider | None = None,
        mind_map_prompt_lock: dict[str, str] | None = None,
        module_plan_prompt_lock: dict[str, str] | None = None,
        album_prompt_lock: dict[str, str] | None = None,
        planning_run_id: str | None = None,
        retry_module_key: str | None = None,
        allow_structure_model_fallback: bool = True,
        progress_callback: StageProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        fallback_provider = provider or self.provider
        validate_episode_word_count_range(
            episode_word_count_min, episode_word_count_max
        )
        episode_word_count_range = format_episode_word_count_range(
            episode_word_count_min, episode_word_count_max
        )
        mind_provider = mind_map_provider or fallback_provider
        album_provider = album_outline_provider or fallback_provider
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

        def report(
            stage: str,
            status: str,
            output: dict[str, Any] | None,
            message: str,
        ) -> None:
            if progress_callback:
                progress_callback(stage, status, output, message)

        def check_cancelled() -> None:
            if cancelled and cancelled():
                raise asyncio.CancelledError

        entries: list[Any]
        key_map: dict[str, str]
        report(
            "prepare_chapter_catalog",
            "running",
            None,
            "正在准备轻量章节目录",
        )
        entries, key_map = self.album_planning.build_chapter_catalog(book["id"])
        catalog = self.album_planning.render_catalog(entries)
        planning_analysis = self.album_planning.render_planning_analysis(entries)
        if planning_run_id:
            self.album_planning.artifacts.upsert(
                run_id=planning_run_id,
                project_id=project_id,
                artifact_type="chapter_catalog",
                source_chapter_ids=list(key_map.values()),
                content=json.dumps(
                    {
                        "catalog_markdown": catalog,
                        "chapter_key_map": key_map,
                    },
                    ensure_ascii=False,
                ),
            )
        check_cancelled()
        report(
            "prepare_chapter_catalog",
            "succeeded",
            {
                "artifact_type": "album_planning_artifact",
                "planning_artifact_type": "chapter_catalog",
                "chapter_count": len(entries),
            },
            f"已准备 {len(entries)} 个一级章节",
        )

        response: dict[str, Any] = {
            "compressed": False,
            "mind_map": {"status": "failed"},
            "album_outline": {"status": "failed"},
        }
        existing_episode = self.database.row(
            "SELECT id FROM episodes WHERE project_id = ? LIMIT 1",
            (project_id,),
        )
        reusable_mind_map = (
            self.database.row(
                """
                SELECT * FROM mind_maps
                WHERE book_id = ? ORDER BY version DESC LIMIT 1
                """,
                (book["id"],),
            )
            if not existing_episode
            else None
        )
        if reusable_mind_map:
            response["mind_map"] = {
                "status": "succeeded",
                "version": reusable_mind_map["version"],
                "reused": True,
            }
            report(
                "generate_mind_map",
                "succeeded",
                {
                    "artifact_type": "mind_map",
                    "version": reusable_mind_map["version"],
                    "reused": True,
                },
                "已复用思维导图",
            )
        else:
            report("generate_mind_map", "running", None, "正在生成思维导图")
            try:
                mind_facts, compressed = await self._book_analysis_input(
                    book["id"], mind_provider
                )
                check_cancelled()
                mind_snapshot = self.prompts.snapshot(
                    "mind_map",
                    {
                        "full_book_analysis": mind_facts,
                        "book_analysis": mind_facts,
                        "book_title": book["title"],
                        "book_author": book["author"] or "未填写",
                        "book_type": (
                            "叙事类"
                            if book["book_type"] == "narrative"
                            else "非叙事类"
                        ),
                    },
                    project_id=project_id,
                    locked=mind_map_prompt_lock,
                    prompt_id="mind_map",
                    book_type=book["book_type"],
                )
                mind_result = await mind_provider.generate(
                    mind_snapshot.prompt, mind_snapshot.source
                )
                check_cancelled()
                current = self.database.row(
                    """
                    SELECT COALESCE(MAX(version), 0) AS version
                    FROM mind_maps WHERE book_id = ?
                    """,
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
                        mind_provider.name,
                        mind_provider.model,
                        now_iso(),
                    ),
                )
                response["compressed"] = compressed
                response["mind_map"] = {"status": "succeeded", "version": version}
                report(
                    "generate_mind_map",
                    "succeeded",
                    {"artifact_type": "mind_map", "version": version},
                    "思维导图已生成",
                )
            except Exception as error:
                response["mind_map"] = {
                    "status": "failed",
                    "error": str(error),
                }
                report(
                    "generate_mind_map",
                    "failed",
                    None,
                    str(error),
                )

        requirements = special_requirements.strip()
        count_text = (
            str(desired_episode_count)
            if desired_episode_count is not None
            else "未指定，由模型根据内容自行决定"
        )
        count_range_text = (
            (
                f"{max(1, desired_episode_count - 2)}"
                f"至{desired_episode_count + 2}集"
            )
            if desired_episode_count is not None
            else "未指定"
        )
        report(
            "design_album_modules",
            "running",
            None,
            "正在设计全书知识模块",
        )
        module_plan_artifact = (
            self.album_planning.artifacts.get(
                planning_run_id, "module_plan"
            )
            if planning_run_id
            else None
        )
        try:
            if module_plan_artifact and module_plan_artifact["status"] == "succeeded":
                module_plan_markdown = module_plan_artifact["content"]
            else:
                module_plan_snapshot = self.prompts.snapshot(
                    "album_module_plan",
                    {
                        "planning_book_analysis": planning_analysis,
                        "book_analysis": planning_analysis,
                        "chapter_catalog": catalog,
                        "book_title": book["title"],
                        "book_author": book["author"] or "未填写",
                        "book_type": (
                            "叙事类"
                            if book["book_type"] == "narrative"
                            else "非叙事类"
                        ),
                        "album_special_requirements": requirements or "无",
                        "desired_episode_count": (
                            f"{count_text}；允许范围：{count_range_text}"
                        ),
                        "episode_word_count_range": episode_word_count_range,
                    },
                    project_id=project_id,
                    locked=module_plan_prompt_lock,
                    prompt_id="album_module_plan",
                    book_type=book["book_type"],
                )
                module_plan_markdown = await album_provider.generate(
                    module_plan_snapshot.prompt, module_plan_snapshot.source
                )
                check_cancelled()
            modules = self.album_planning.parse_module_plan(
                module_plan_markdown, set(key_map)
            )
            modules = self.album_planning.split_oversized_modules(
                modules, entries
            )
            episode_budget = None
            if desired_episode_count is not None:
                modules, episode_budget = (
                    self.album_planning.apply_episode_budget(
                        modules,
                        entries,
                        desired_episode_count=desired_episode_count,
                    )
                )
                module_plan_markdown = self.album_planning.render_module_plan(
                    modules, episode_budget
                )
            if planning_run_id:
                self.album_planning.artifacts.upsert(
                    run_id=planning_run_id,
                    project_id=project_id,
                    artifact_type="module_plan",
                    source_chapter_ids=list(key_map.values()),
                    content=module_plan_markdown,
                )
            report(
                "design_album_modules",
                "succeeded",
                {
                    "artifact_type": "album_planning_artifact",
                    "planning_artifact_type": "module_plan",
                    "module_count": len(modules),
                    "selected_episode_count": (
                        episode_budget.selected_count
                        if episode_budget
                        else None
                    ),
                },
                (
                    f"已设计 {len(modules)} 个知识模块，"
                    f"本次规划 {episode_budget.selected_count} 集"
                    if episode_budget
                    else f"已设计 {len(modules)} 个知识模块"
                ),
            )
        except Exception as error:
            if planning_run_id:
                self.album_planning.artifacts.mark_failed(
                    run_id=planning_run_id,
                    project_id=project_id,
                    artifact_type="module_plan",
                    module_key="",
                    position=0,
                    source_chapter_ids=list(key_map.values()),
                    error=error,
                )
            report("design_album_modules", "failed", None, str(error))
            response["album_outline"]["error"] = str(error)
            response["project"] = self.project_detail(project_id)
            return response

        report(
            "expand_album_modules",
            "running",
            {"module_count": len(modules), "completed": 0, "failed": 0},
            f"正在展开 {len(modules)} 个专辑模块",
        )
        limiter = asyncio.Semaphore(2)
        run_service = RunService(self.database)
        completed_modules = 0
        failed_modules = 0

        async def expand(module: AlbumModule) -> tuple[AlbumModule, str | Exception]:
            nonlocal completed_modules, failed_modules
            module_source = self.album_planning.render_module_source(
                entries, module.chapter_keys
            )
            if planning_run_id:
                self.album_planning.artifacts.upsert(
                    run_id=planning_run_id,
                    project_id=project_id,
                    artifact_type="module_source",
                    module_key=module.key,
                    position=module.position,
                    source_chapter_ids=[
                        key_map[key] for key in module.chapter_keys
                    ],
                    content=module_source,
                )
            existing = (
                self.album_planning.artifacts.get(
                    planning_run_id, "module_outline", module.key
                )
                if planning_run_id
                else None
            )
            if existing and existing["status"] == "succeeded":
                try:
                    existing_content = (
                        self.album_planning.validate_module_outline(
                            existing["content"],
                            set(module.chapter_keys),
                            expected_episode_count=(
                                module.suggested_episode_count
                                if episode_budget
                                else None
                            ),
                        )
                    )
                except ValueError:
                    existing = None
                else:
                    completed_modules += 1
                    return module, existing_content
            if (
                existing
                and existing["status"] == "failed"
                and retry_module_key
                and module.key != retry_module_key
            ):
                failed_modules += 1
                return module, RuntimeError(
                    existing["error_message"] or "模块尚未成功"
                )
            child_run: dict[str, Any] | None = None
            if planning_run_id:
                child_run, _ = run_service.create(
                    scope_type="project_album_module",
                    scope_id=f"{project_id}:{module.key}",
                    stage="album_outline",
                    current_stage="expand_album_module",
                    progress_total=1,
                    metadata={
                        "module_key": module.key,
                        "module_title": module.title,
                        "chapter_keys": list(module.chapter_keys),
                    },
                    parent_run_id=planning_run_id,
                    position=module.position,
                    reuse_active=False,
                )
                run_service.mark_running(
                    child_run["id"],
                    current_stage="expand_album_module",
                    message=f"正在生成{module.title}",
                    increment_attempt=True,
                )
            try:
                async with limiter:
                    check_cancelled()
                    module_brief = (
                        f"模块标题：{module.title}\n"
                        f"听众问题：{module.listener_question}\n"
                        + (
                            f"本模块分配集数：{module.suggested_episode_count}"
                            "（必须严格生成这个数量）\n"
                            if episode_budget
                            else f"建议声音数：{module.suggested_episode_count}\n"
                        )
                        + "来源章节："
                        + "、".join(f"[{key}]" for key in module.chapter_keys)
                    )
                    module_count_text = (
                        str(module.suggested_episode_count)
                        if episode_budget
                        else "未指定，由模型根据当前模块内容自行决定"
                    )
                    snapshot = self.prompts.snapshot(
                        "album_outline",
                        {
                            "book_analysis": module_source,
                            "chapter_catalog": catalog,
                            "module_brief": module_brief,
                            "module_book_analysis": module_source,
                            "module_source": module_source,
                            "book_title": book["title"],
                            "book_author": book["author"] or "未填写",
                            "book_type": (
                                "叙事类"
                                if book["book_type"] == "narrative"
                                else "非叙事类"
                            ),
                            "album_special_requirements": requirements or "无",
                            "desired_episode_count": module_count_text,
                            "episode_word_count_range": (
                                episode_word_count_range
                            ),
                        },
                        project_id=project_id,
                        locked=album_prompt_lock,
                        prompt_id="album_outline",
                        book_type=book["book_type"],
                    )
                    markdown = await album_provider.generate(
                        snapshot.prompt, snapshot.source
                    )
                    check_cancelled()
                    expected_episode_count = (
                        module.suggested_episode_count
                        if episode_budget
                        else None
                    )
                    try:
                        markdown = (
                            self.album_planning.validate_module_outline(
                                markdown,
                                set(module.chapter_keys),
                                expected_episode_count=expected_episode_count,
                            )
                        )
                    except ValueError as validation_error:
                        if (
                            expected_episode_count is None
                            or "本模块分配" not in str(validation_error)
                        ):
                            raise
                        correction_source = (
                            "# 必须生成集数\n"
                            f"必须生成集数：{expected_episode_count}\n\n"
                            "# 合法章节标识\n"
                            + "、".join(
                                f"[{key}]" for key in module.chapter_keys
                            )
                            + "\n\n# 当前模块精简材料\n"
                            + module_source
                            + "\n\n# 首次生成结果\n"
                            + markdown
                        )
                        markdown = await album_provider.generate(
                            PROMPTS["album_outline_count_repair"],
                            correction_source,
                        )
                        check_cancelled()
                        markdown = (
                            self.album_planning.validate_module_outline(
                                markdown,
                                set(module.chapter_keys),
                                expected_episode_count=expected_episode_count,
                            )
                        )
                if planning_run_id:
                    self.album_planning.artifacts.upsert(
                        run_id=planning_run_id,
                        project_id=project_id,
                        artifact_type="module_outline",
                        module_key=module.key,
                        position=module.position,
                        source_chapter_ids=[
                            key_map[key] for key in module.chapter_keys
                        ],
                        content=markdown,
                    )
                if child_run:
                    run_service.set_stage(
                        child_run["id"],
                        "expand_album_module",
                        "succeeded",
                        output={
                            "artifact_type": "album_planning_artifact",
                            "planning_artifact_type": "module_outline",
                            "module_key": module.key,
                        },
                    )
                    run_service.finish(
                        child_run["id"], message=f"{module.title}已生成"
                    )
                completed_modules += 1
                report(
                    "expand_album_modules",
                    "running",
                    {
                        "module_count": len(modules),
                        "completed": completed_modules,
                        "failed": failed_modules,
                    },
                    f"已完成 {completed_modules}/{len(modules)} 个模块",
                )
                return module, markdown
            except Exception as error:
                failed_modules += 1
                if planning_run_id:
                    self.album_planning.artifacts.mark_failed(
                        run_id=planning_run_id,
                        project_id=project_id,
                        artifact_type="module_outline",
                        module_key=module.key,
                        position=module.position,
                        source_chapter_ids=[
                            key_map[key] for key in module.chapter_keys
                        ],
                        error=error,
                    )
                if child_run:
                    run_service.fail(
                        child_run["id"],
                        error,
                        error_stage="expand_album_module",
                    )
                report(
                    "expand_album_modules",
                    "running",
                    {
                        "module_count": len(modules),
                        "completed": completed_modules,
                        "failed": failed_modules,
                    },
                    f"{module.title}生成失败，正在继续后续模块",
                )
                return module, error

        expanded = await asyncio.gather(*(expand(module) for module in modules))
        errors = [
            (module, result)
            for module, result in expanded
            if isinstance(result, Exception)
        ]
        if errors:
            message = (
                f"成功 {len(modules) - len(errors)} 个模块，"
                f"失败 {len(errors)} 个模块；可单独重跑失败模块"
            )
            report(
                "expand_album_modules",
                "failed",
                {
                    "module_count": len(modules),
                    "completed": len(modules) - len(errors),
                    "failed": len(errors),
                    "failed_modules": [module.key for module, _ in errors],
                },
                message,
            )
            response["album_outline"]["error"] = message
            response["album_outline"]["failed_modules"] = [
                module.key for module, _ in errors
            ]
            response["project"] = self.project_detail(project_id)
            return response
        report(
            "expand_album_modules",
            "succeeded",
            {
                "module_count": len(modules),
                "completed": len(modules),
                "failed": 0,
            },
            f"{len(modules)} 个模块已全部生成",
        )

        combined_parts: list[str] = []
        for module, result in expanded:
            annotated = re.sub(
                r"(^##\s+第?\d+\s*集[：:].*$\n?)",
                lambda match: (
                    f"{match.group(1)}模块标识：{module.key}\n"
                ),
                str(result),
                flags=re.M,
            )
            combined_parts.append(annotated)
        combined = "\n\n".join(combined_parts)
        if planning_run_id:
            self.album_planning.artifacts.upsert(
                run_id=planning_run_id,
                project_id=project_id,
                artifact_type="combined_outline",
                source_chapter_ids=list(key_map.values()),
                content=combined,
            )
        report(
            "structure_album_outline",
            "running",
            None,
            "正在整理专辑大纲页面数据",
        )
        try:
            structured_artifact = (
                self.album_planning.artifacts.get(
                    planning_run_id, "structured_outline"
                )
                if planning_run_id
                else None
            )
            if structured_artifact and structured_artifact["status"] == "succeeded":
                structured_raw = structured_artifact["content"]
                structured = parse_json_object(structured_raw)
                episodes, notice = self.album_planning.validate_structured_outline(
                    structured,
                    entries,
                    book_type=book["book_type"],
                    desired_episode_count=desired_episode_count,
                    modules=modules,
                    expected_episode_count=(
                        episode_budget.selected_count
                        if episode_budget
                        else None
                    ),
                    allowed_episode_range=(
                        (
                            episode_budget.minimum_count,
                            episode_budget.maximum_count,
                        )
                        if episode_budget
                        else None
                    ),
                )
            else:
                markdown_by_module = {
                    module.key: str(result)
                    for module, result in expanded
                }
                try:
                    structured = self.album_planning.parse_module_outlines(
                        modules, markdown_by_module, entries
                    )
                    structured_raw = json.dumps(
                        structured, ensure_ascii=False
                    )
                    episodes, notice = (
                        self.album_planning.validate_structured_outline(
                            structured,
                            entries,
                            book_type=book["book_type"],
                            desired_episode_count=desired_episode_count,
                            modules=modules,
                            expected_episode_count=(
                                episode_budget.selected_count
                                if episode_budget
                                else None
                            ),
                            allowed_episode_range=(
                                (
                                    episode_budget.minimum_count,
                                    episode_budget.maximum_count,
                                )
                                if episode_budget
                                else None
                            ),
                        )
                    )
                except Exception as deterministic_error:
                    if not allow_structure_model_fallback:
                        raise ValueError(
                            "本地结构化恢复失败："
                            f"{deterministic_error}"
                        ) from deterministic_error
                    structure_source = (
                        "# 合法章节标识\n"
                        + "、".join(f"[{key}]" for key in key_map)
                        + "\n\n# 已完成 Markdown 专辑大纲\n"
                        + combined
                    )
                    structured_raw = await album_provider.generate(
                        PROMPTS["album_outline_structure"], structure_source
                    )
                    check_cancelled()
                    try:
                        structured = parse_json_object(structured_raw)
                    except json.JSONDecodeError:
                        repaired = await album_provider.generate(
                            PROMPTS["json_repair"], structured_raw
                        )
                        structured = parse_json_object(repaired)
                        structured_raw = repaired
                    episodes, notice = (
                        self.album_planning.validate_structured_outline(
                            structured,
                            entries,
                            book_type=book["book_type"],
                            desired_episode_count=desired_episode_count,
                            modules=modules,
                            expected_episode_count=(
                                episode_budget.selected_count
                                if episode_budget
                                else None
                            ),
                            allowed_episode_range=(
                                (
                                    episode_budget.minimum_count,
                                    episode_budget.maximum_count,
                                )
                                if episode_budget
                                else None
                            ),
                        )
                    )
            if planning_run_id:
                self.album_planning.artifacts.upsert(
                    run_id=planning_run_id,
                    project_id=project_id,
                    artifact_type="structured_outline",
                    source_chapter_ids=list(key_map.values()),
                    content=structured_raw,
                )
            report(
                "structure_album_outline",
                "succeeded",
                {
                    "artifact_type": "album_planning_artifact",
                    "planning_artifact_type": "structured_outline",
                    "episode_count": len(episodes),
                },
                f"已整理 {len(episodes)} 集页面数据",
            )
        except Exception as error:
            if planning_run_id:
                self.album_planning.artifacts.mark_failed(
                    run_id=planning_run_id,
                    project_id=project_id,
                    artifact_type="structured_outline",
                    module_key="",
                    position=0,
                    source_chapter_ids=list(key_map.values()),
                    error=error,
                )
            report("structure_album_outline", "failed", None, str(error))
            response["album_outline"]["error"] = str(error)
            response["project"] = self.project_detail(project_id)
            return response

        report(
            "save_project_outline",
            "running",
            None,
            "正在保存章节级专辑大纲",
        )
        active_prompt = self.prompts.effective("album_outline", project_id)
        self._save_generated_album_and_project(
            project_id,
            episodes,
            planning_run_id=planning_run_id or "",
            requirements=requirements,
            desired_episode_count=desired_episode_count,
            episode_word_count_min=episode_word_count_min,
            episode_word_count_max=episode_word_count_max,
            notice=notice,
            prompt_version_id=active_prompt["prompt_version_id"],
            prompt_system_version_id=active_prompt["system_version_id"],
        )
        response["album_outline"] = {
            "status": "succeeded",
            "episode_count": len(episodes),
            "notice": notice,
        }
        report(
            "save_project_outline",
            "succeeded",
            {
                "artifact_type": "project_outline",
                "project_id": project_id,
                "episode_count": len(episodes),
            },
            f"已生成 {len(episodes)} 集专辑大纲",
        )
        response["project"] = self.project_detail(project_id)
        return response

    async def _generate_project_knowledge_outputs_legacy(
        self,
        project_id: str,
        special_requirements: str = "",
        desired_episode_count: int | None = None,
        provider: ModelProvider | None = None,
        *,
        episode_word_count_min: int = DEFAULT_EPISODE_WORD_COUNT_MIN,
        episode_word_count_max: int = DEFAULT_EPISODE_WORD_COUNT_MAX,
        mind_map_provider: ModelProvider | None = None,
        album_outline_provider: ModelProvider | None = None,
        album_prompt_lock: dict[str, str] | None = None,
        progress_callback: StageProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        fallback_provider = provider or self.provider
        validate_episode_word_count_range(
            episode_word_count_min, episode_word_count_max
        )
        episode_word_count_range = format_episode_word_count_range(
            episode_word_count_min, episode_word_count_max
        )
        mind_provider = mind_map_provider or fallback_provider
        album_provider = album_outline_provider or fallback_provider
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
        if progress_callback:
            progress_callback(
                "prepare_analysis",
                "running",
                None,
                "正在准备完整拆书稿",
            )
        mind_facts, mind_compressed = await self._book_analysis_input(
            book["id"], mind_provider
        )
        if (
            mind_provider.name == album_provider.name
            and mind_provider.model == album_provider.model
        ):
            album_facts = mind_facts
            album_compressed = mind_compressed
        else:
            album_facts, album_compressed = await self._book_analysis_input(
                book["id"], album_provider
            )
        if cancelled and cancelled():
            raise asyncio.CancelledError
        if progress_callback:
            progress_callback(
                "prepare_analysis",
                "succeeded",
                {
                    "mind_map_compressed": mind_compressed,
                    "album_outline_compressed": album_compressed,
                },
                "拆书稿已准备",
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
            f"# 期望集数\n{count_text}\n\n"
            f"# 每集目标字数\n{episode_word_count_range}\n\n"
            f"# 拆书稿\n{album_facts}"
        )
        album_prompt = self.prompts.snapshot(
            "album_outline",
            {
                "book_analysis": album_facts,
                "book_title": book["title"],
                "book_author": book["author"] or "未填写",
                "book_type": (
                    "叙事类"
                    if book["book_type"] == "narrative"
                    else "非叙事类"
                ),
                "album_special_requirements": requirements or "无",
                "desired_episode_count": count_text,
                "episode_word_count_range": episode_word_count_range,
            },
            project_id=project_id,
            locked=album_prompt_lock,
            prompt_id="album_outline",
            book_type=book["book_type"],
        )
        mind_source = (
            f"# 书籍信息\n书名：{book['title']}\n作者：{book['author'] or '未填写'}"
            f"\n\n# 拆书稿\n{mind_facts}"
        )
        album_draft_signature = uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps(
                {
                    "album_source": album_source,
                    "prompt_version_id": album_prompt.prompt_version_id,
                    "system_version_id": album_prompt.system_version_id,
                    "provider": album_provider.name,
                    "model": album_provider.model,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        ).hex
        existing_episode = self.database.row(
            "SELECT id FROM episodes WHERE project_id = ? LIMIT 1",
            (project_id,),
        )
        reusable_mind_map = (
            self.database.row(
                """
                SELECT * FROM mind_maps
                WHERE book_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (book["id"],),
            )
            if not existing_episode
            else None
        )
        reusable_album_data: dict[str, Any] | None = None
        if (
            not existing_episode
            and project.get("album_outline_draft_json")
            and project.get("album_outline_draft_signature")
            == album_draft_signature
        ):
            try:
                candidate = json.loads(project["album_outline_draft_json"])
                if isinstance(candidate, dict):
                    reusable_album_data = candidate
            except json.JSONDecodeError:
                reusable_album_data = None
        if reusable_album_data is not None:
            if reusable_mind_map:
                mind_result: str | Exception | None = None
            else:
                if progress_callback:
                    progress_callback(
                        "generate_mind_map",
                        "running",
                        None,
                        "正在生成思维导图",
                    )
                (mind_result,) = await asyncio.gather(
                    mind_provider.generate(PROMPTS["mind_map"], mind_source),
                    return_exceptions=True,
                )
            album_result: str | Exception | None = None
        elif reusable_mind_map:
            mind_result: str | Exception | None = None
            if progress_callback:
                progress_callback(
                    "generate_album_outline",
                    "running",
                    None,
                    "正在生成专辑大纲",
                )
            (album_result,) = await asyncio.gather(
                album_provider.generate(album_prompt.prompt, album_prompt.source),
                return_exceptions=True,
            )
        else:
            if progress_callback:
                progress_callback(
                    "generate_mind_map",
                    "running",
                    None,
                    "正在生成思维导图",
                )
                progress_callback(
                    "generate_album_outline",
                    "running",
                    None,
                    "正在生成专辑大纲",
                )
            mind_result, album_result = await asyncio.gather(
                mind_provider.generate(PROMPTS["mind_map"], mind_source),
                album_provider.generate(album_prompt.prompt, album_prompt.source),
                return_exceptions=True,
            )
        if cancelled and cancelled():
            raise asyncio.CancelledError
        response: dict[str, Any] = {
            "compressed": mind_compressed or album_compressed,
            "mind_map": {"status": "failed"},
            "album_outline": {"status": "failed"},
        }
        if reusable_mind_map:
            response["mind_map"] = {
                "status": "succeeded",
                "version": reusable_mind_map["version"],
                "reused": True,
            }
            if progress_callback:
                progress_callback(
                    "generate_mind_map",
                    "succeeded",
                    {
                        "artifact_type": "mind_map",
                        "version": reusable_mind_map["version"],
                        "reused": True,
                    },
                    "已复用思维导图",
                )
        elif isinstance(mind_result, Exception):
            response["mind_map"]["error"] = str(mind_result)
            if progress_callback:
                progress_callback(
                    "generate_mind_map",
                    "failed",
                    None,
                    str(mind_result),
                )
        else:
            if cancelled and cancelled():
                raise asyncio.CancelledError
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
                    str(mind_result).strip(),
                    mind_provider.name,
                    mind_provider.model,
                    now_iso(),
                ),
            )
            response["mind_map"] = {"status": "succeeded", "version": version}
            if progress_callback:
                progress_callback(
                    "generate_mind_map",
                    "succeeded",
                    {"artifact_type": "mind_map", "version": version},
                    "思维导图已生成",
                )
        if isinstance(album_result, Exception):
            response["album_outline"]["error"] = str(album_result)
            if progress_callback:
                progress_callback(
                    "generate_album_outline",
                    "failed",
                    None,
                    str(album_result),
                )
        else:
            try:
                if cancelled and cancelled():
                    raise asyncio.CancelledError
                if reusable_album_data is not None:
                    album_data = reusable_album_data
                    response["album_outline"]["reused_draft"] = True
                else:
                    try:
                        album_data = parse_json_object(str(album_result))
                    except json.JSONDecodeError:
                        repaired_album = await album_provider.generate(
                            PROMPTS["json_repair"], str(album_result)
                        )
                        album_data = parse_json_object(repaired_album)
                    self.database.execute(
                        """
                        UPDATE projects
                        SET album_outline_draft_json = ?,
                            album_outline_draft_signature = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(album_data, ensure_ascii=False),
                            album_draft_signature,
                            project_id,
                        ),
                    )
                if progress_callback:
                    progress_callback(
                        "generate_album_outline",
                        "succeeded",
                        {
                            "artifact_type": "album_outline_draft",
                            "reused": reusable_album_data is not None,
                        },
                        "专辑大纲模型输出已生成",
                    )
                    progress_callback(
                        "save_project_outline",
                        "running",
                        None,
                        "正在校验并保存专辑大纲",
                    )
                episodes, notice = self._validate_album_outline(
                    album_data, book, desired_episode_count
                )
                self._save_generated_album(project_id, episodes)
                self.database.execute(
                    """
                    UPDATE projects
                    SET album_special_requirements = ?, desired_episode_count = ?,
                        episode_word_count_min = ?, episode_word_count_max = ?,
                        episode_count_notice = ?, status = 'outline_review',
                        album_prompt_version_id = ?,
                        album_prompt_system_version_id = ?,
                        album_outline_draft_json = '',
                        album_outline_draft_signature = '',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        requirements,
                        desired_episode_count,
                        episode_word_count_min,
                        episode_word_count_max,
                        notice,
                        album_prompt.prompt_version_id,
                        album_prompt.system_version_id,
                        now_iso(),
                        project_id,
                    ),
                )
                response["album_outline"] = {
                    "status": "succeeded",
                    "episode_count": len(episodes),
                    "notice": notice,
                }
                if progress_callback:
                    progress_callback(
                        "save_project_outline",
                        "succeeded",
                        {
                            "artifact_type": "project_outline",
                            "project_id": project_id,
                            "episode_count": len(episodes),
                        },
                        f"已生成 {len(episodes)} 集专辑大纲",
                    )
            except Exception as error:
                response["album_outline"]["error"] = str(error)
                if progress_callback:
                    failed_stage = (
                        "save_project_outline"
                        if self.database.row(
                            """
                            SELECT album_outline_draft_json FROM projects
                            WHERE id = ?
                            """,
                            (project_id,),
                        ).get("album_outline_draft_json")
                        else "generate_album_outline"
                    )
                    progress_callback(
                        failed_stage,
                        "failed",
                        None,
                        str(error),
                    )
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
        asset_content_indexes: dict[str, list[str]] = {}
        for asset in active_assets:
            rows = self.database.rows(
                """
                SELECT source.content_index, f.source_section_id
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
            asset_content_indexes[asset["id"]] = list(
                dict.fromkeys(row["content_index"] for row in rows)
            )
        seen_regular: set[str] = set()
        episodes: list[dict[str, Any]] = []
        for position, item in enumerate(raw_episodes, start=1):
            if not isinstance(item, dict):
                raise ValueError("专辑大纲条目结构无效")
            title = item.get("title")
            main_points = item.get("main_points")
            section_identifier = item.get("section_identifier")
            content_type = item.get("content_type")
            if not all(
                isinstance(value, str) and value.strip()
                for value in (
                    title,
                    main_points,
                    section_identifier,
                    content_type,
                )
            ):
                raise ValueError(f"专辑第 {position} 条字段不完整")
            required_markers = ("听众钩子：", "核心主题：", "核心要点：")
            if any(marker not in main_points for marker in required_markers):
                raise ValueError(
                    f"专辑第 {position} 条主要内容缺少听众钩子、核心主题或核心要点"
                )
            marker_positions = [main_points.index(marker) for marker in required_markers]
            if marker_positions != sorted(marker_positions):
                raise ValueError(
                    f"专辑第 {position} 条主要内容结构顺序无效"
                )
            key_points_text = main_points.split("核心要点：", 1)[1].strip()
            key_point_count = len(
                re.findall(
                    r"(?:^|[\n；;])\s*(?:\d+\s*[.．、)）]|[-*])\s*\S+",
                    key_points_text,
                    flags=re.M,
                )
            )
            if key_point_count < 2 or key_point_count > 4:
                raise ValueError(
                    f"专辑第 {position} 条核心要点必须为 2 至 4 条"
                )
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
                source_content_indexes = list(
                    dict.fromkeys(
                        index
                        for asset_id in knowledge_item_ids
                        for index in asset_content_indexes[asset_id]
                    )
                )
                identifier_label = re.sub(
                    r"\s*原文索引\s*[:：].*$",
                    "",
                    section_identifier,
                    flags=re.S,
                ).strip() or "内容来源"
                section_identifier = (
                    f"{identifier_label} 原文索引："
                    f"{'、'.join(source_content_indexes)}"
                )
                supplied_indexes = item.get("source_content_indexes")
                if supplied_indexes is not None:
                    if not isinstance(supplied_indexes, list) or not all(
                        isinstance(index, str) and index.strip()
                        for index in supplied_indexes
                    ):
                        raise ValueError(
                            f"专辑第 {position} 条原文索引结构无效"
                        )
                    supplied_indexes = list(
                        dict.fromkeys(index.strip() for index in supplied_indexes)
                    )
                    if set(supplied_indexes) != set(source_content_indexes):
                        raise ValueError(
                            f"专辑第 {position} 条原文索引与知识资产来源不一致"
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
                source_content_indexes = indexes
                source_section_ids = [index_map[index] for index in indexes]
            episodes.append(
                {
                    "id": uuid.uuid4().hex,
                    "position": position,
                    "title": title.strip(),
                    "content_type": normalized_type,
                    "style": "观点",
                    "content_framework": main_points.strip(),
                    "section_identifier": section_identifier.strip(),
                    "source_section_ids": source_section_ids,
                    "knowledge_item_ids": knowledge_item_ids,
                    "source_content_indexes": source_content_indexes,
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
        self,
        project_id: str,
        episodes: list[dict[str, Any]],
        *,
        planning_run_id: str = "",
    ) -> None:
        with self.database.connect() as connection:
            self._replace_generated_album(
                connection,
                project_id,
                episodes,
                planning_run_id=planning_run_id,
            )

    @staticmethod
    def _replace_generated_album(
        connection: Any,
        project_id: str,
        episodes: list[dict[str, Any]],
        *,
        planning_run_id: str = "",
    ) -> None:
        connection.execute(
            "DELETE FROM episodes WHERE project_id = ?", (project_id,)
        )
        connection.executemany(
            """
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style,
               content_framework, section_identifier, planning_run_id,
               module_key, status, source_section_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outline_review', ?)
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
                    item["section_identifier"],
                    planning_run_id,
                    item.get("module_key", ""),
                    json.dumps(item["source_section_ids"], ensure_ascii=False),
                )
                for item in episodes
            ],
        )
        connection.executemany(
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

    def _save_generated_album_and_project(
        self,
        project_id: str,
        episodes: list[dict[str, Any]],
        *,
        planning_run_id: str,
        requirements: str,
        desired_episode_count: int | None,
        episode_word_count_min: int,
        episode_word_count_max: int,
        notice: str,
        prompt_version_id: str,
        prompt_system_version_id: str,
    ) -> None:
        with self.database.connect() as connection:
            self._replace_generated_album(
                connection,
                project_id,
                episodes,
                planning_run_id=planning_run_id,
            )
            connection.execute(
                """
                UPDATE projects
                SET album_special_requirements = ?, desired_episode_count = ?,
                    episode_word_count_min = ?, episode_word_count_max = ?,
                    episode_count_notice = ?, status = 'outline_review',
                    album_prompt_version_id = ?,
                    album_prompt_system_version_id = ?,
                    album_outline_draft_json = '',
                    album_outline_draft_signature = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    requirements,
                    desired_episode_count,
                    episode_word_count_min,
                    episode_word_count_max,
                    notice,
                    prompt_version_id,
                    prompt_system_version_id,
                    now_iso(),
                    project_id,
                ),
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
                    f"章节：{section['title']}",
                    "outline_review",
                    json.dumps([section["id"]], ensure_ascii=False),
                )
            )
        self.database.executemany(
            """
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style,
               content_framework, section_identifier, status, source_section_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        *,
        stage_providers: dict[str, ModelProvider] | None = None,
        stage_prompt_locks: dict[str, dict[str, str]] | None = None,
        word_count_range: tuple[int, int] | None = None,
        progress_callback: StageProgressCallback | None = None,
        completed_stages: set[str] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        fallback_provider = provider or self.provider
        episode = self.database.row("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        if not episode:
            raise KeyError(episode_id)
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (episode["project_id"],)
        )
        if not project:
            raise ValueError("声音所属项目不存在")
        episode_word_count_min, episode_word_count_max = (
            word_count_range
            if word_count_range is not None
            else (
                int(
                    project.get("episode_word_count_min")
                    or DEFAULT_EPISODE_WORD_COUNT_MIN
                ),
                int(
                    project.get("episode_word_count_max")
                    or DEFAULT_EPISODE_WORD_COUNT_MAX
                ),
            )
        )
        validate_episode_word_count_range(
            episode_word_count_min, episode_word_count_max
        )
        creative_stages = ["outline", "draft", "final"]
        start = creative_stages.index(from_stage)
        stages = creative_stages[start:]
        existing_links = self.database.row(
            """
            SELECT COUNT(*) AS count FROM episode_knowledge_items
            WHERE episode_id = ?
            """,
            (episode_id,),
        )
        if not existing_links or not int(existing_links["count"]):
            stages = ["match_episode_sources", *stages]
        for stage in stages:
            if stage in (completed_stages or set()):
                continue
            if cancelled and cancelled():
                return self.episode_detail(episode_id)
            task_provider = (
                stage_providers.get(
                    "outline" if stage == "match_episode_sources" else stage,
                    fallback_provider,
                )
                if stage_providers
                else fallback_provider
            )
            if progress_callback:
                progress_callback(
                    stage,
                    "running",
                    None,
                    (
                        "正在匹配本集原文"
                        if stage == "match_episode_sources"
                        else f"正在生成{stage}"
                    ),
                )
            if stage == "match_episode_sources":
                try:
                    selected = await self._match_episode_sources(
                        episode_id, task_provider
                    )
                except Exception as error:
                    if progress_callback:
                        progress_callback(stage, "failed", None, str(error))
                    self.database.execute(
                        "UPDATE episodes SET status = 'failed' WHERE id = ?",
                        (episode_id,),
                    )
                    raise StageGenerationError(stage, error) from error
                if progress_callback:
                    progress_callback(
                        stage,
                        "succeeded",
                        {
                            "artifact_type": "episode_source_match",
                            "episode_id": episode_id,
                            "knowledge_count": len(selected),
                        },
                        f"已匹配 {len(selected)} 条知识资产",
                    )
                continue
            self.database.execute(
                "UPDATE episodes SET status = ? WHERE id = ?",
                (f"generating_{stage}", episode_id),
            )
            try:
                context = self.contexts.build(episode_id, stage)
                prompt_snapshot = self.prompts.snapshot(
                    f"episode_{stage}",
                    context.variables,
                    project_id=context.project_id,
                    locked=(
                        stage_prompt_locks.get(stage)
                        if stage_prompt_locks
                        else None
                    ),
                    prompt_id=context.prompt_id,
                    book_type=context.book_type,
                )
                content = await task_provider.generate(
                    prompt_snapshot.prompt, prompt_snapshot.source
                )
                if stage in {"draft", "final"}:
                    content = await self._fit_episode_word_count(
                        content,
                        stage,
                        task_provider,
                        episode_word_count_min,
                        episode_word_count_max,
                        progress_callback=progress_callback,
                    )
            except Exception as error:
                if progress_callback:
                    progress_callback(stage, "failed", None, str(error))
                self.database.execute(
                    "UPDATE episodes SET status = 'failed' WHERE id = ?",
                    (episode_id,),
                )
                raise StageGenerationError(stage, error) from error
            if cancelled and cancelled():
                return self.episode_detail(episode_id)
            artifact = self._save_artifact(
                episode_id,
                stage,
                content,
                prompt_snapshot.version_label,
                provider=task_provider.name,
                model=task_provider.model,
                input_snapshot=prompt_snapshot.source,
                prompt_version_id=prompt_snapshot.prompt_version_id,
                prompt_system_version_id=prompt_snapshot.system_version_id,
            )
            if progress_callback:
                progress_callback(
                    stage,
                    "succeeded",
                    {
                        "artifact_type": "episode_artifact",
                        "artifact_id": artifact["id"],
                        "version": artifact["version"],
                    },
                    f"{stage}已生成",
                )
        self.database.execute(
            "UPDATE episodes SET status = 'review' WHERE id = ?",
            (episode_id,),
        )
        return self.episode_detail(episode_id)

    async def _fit_episode_word_count(
        self,
        content: str,
        stage: str,
        provider: ModelProvider,
        minimum: int,
        maximum: int,
        *,
        progress_callback: StageProgressCallback | None = None,
    ) -> str:
        label = "初稿" if stage == "draft" else "终稿"
        current = content.strip()
        result = inspect_word_count(current, minimum, maximum)
        for attempt in range(1, 3):
            if result.within_range:
                return current
            if progress_callback:
                progress_callback(
                    stage,
                    "running",
                    {
                        "word_count": result.actual,
                        "word_count_min": minimum,
                        "word_count_max": maximum,
                        "repair_attempt": attempt,
                    },
                    f"正在调整{label}字数（{result.actual} 字）",
                )
            repair_source = (
                f"# 当前阶段\n声音{label}\n\n"
                f"# 目标范围\n{minimum}–{maximum} 字\n\n"
                f"# 当前实际字数\n{result.actual} 字\n\n"
                f"# 调整要求\n{result.instruction}；"
                "以程序计数结果为准，不要在输出中报告字数。\n\n"
                f"# 当前文稿\n{current}"
            )
            current = (
                await provider.generate(
                    PROMPTS["episode_word_count_repair"],
                    repair_source,
                )
            ).strip()
            result = inspect_word_count(current, minimum, maximum)
        if result.within_range:
            return current
        direction = (
            f"少 {minimum - result.actual} 字"
            if result.actual < minimum
            else f"多 {result.actual - maximum} 字"
        )
        raise ValueError(
            f"声音{label}字数不符合要求：实际 {result.actual} 字，"
            f"目标 {minimum}–{maximum} 字（{direction}），"
            "自动调整 2 次后仍未达标，可单条重跑"
        )

    async def _match_episode_sources(
        self, episode_id: str, provider: ModelProvider
    ) -> list[str]:
        existing = self.database.rows(
            """
            SELECT knowledge_item_id FROM episode_knowledge_items
            WHERE episode_id = ? ORDER BY position
            """,
            (episode_id,),
        )
        if existing:
            return [item["knowledge_item_id"] for item in existing]
        episode = self.database.row(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        if not episode:
            raise KeyError(episode_id)
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (episode["project_id"],)
        )
        if not project or not project["book_ids"]:
            raise ValueError("声音所属项目没有有效来源书籍")
        chapter_ids = list(dict.fromkeys(episode["source_section_ids"]))
        if not chapter_ids:
            raise ValueError("当前声音没有来源章节")
        placeholders = ",".join("?" for _ in chapter_ids)
        candidates = self.database.rows(
            f"""
            SELECT DISTINCT item.id, item.kind, item.title, item.body
            FROM knowledge_items item
            JOIN knowledge_item_sources source
              ON source.knowledge_item_id = item.id
            JOIN source_fragment_set_members member
              ON member.content_index = source.content_index
            JOIN source_fragment_sets fragment_set
              ON fragment_set.id = member.fragment_set_id
            WHERE item.book_id IN ({",".join("?" for _ in project["book_ids"])})
              AND item.status = 'active'
              AND item.source_scheme = 'paragraph_evidence_v1'
              AND fragment_set.status = 'current'
              AND member.root_section_id IN ({placeholders})
            ORDER BY member.book_position, item.created_at, item.id
            """,
            (*project["book_ids"], *chapter_ids),
        )
        if not candidates:
            raise ValueError("所选来源章节下没有可用知识资产，请先检查拆书结果")
        catalog = "\n\n".join(
            (
                f"[{item['id']}] {item['kind']} · {item['title']}\n"
                f"{clean_excerpt(item['body'], 320)}"
            )
            for item in candidates
        )
        source = (
            f"# 当前声音\n标题：{episode['title']}\n"
            f"内容框架：\n{episode['content_framework']}\n\n"
            f"# 候选知识资产\n{catalog}"
        )
        raw = await provider.generate(PROMPTS["episode_source_match"], source)
        try:
            data = parse_json_object(raw)
        except json.JSONDecodeError:
            repaired = await provider.generate(PROMPTS["json_repair"], raw)
            data = parse_json_object(repaired)
        selected = data.get("knowledge_item_ids")
        if not isinstance(selected, list) or not selected:
            raise ValueError("没有匹配到能够支撑当前声音的知识资产")
        selected_ids = list(
            dict.fromkeys(
                str(item).strip() for item in selected if str(item).strip()
            )
        )
        candidate_ids = {item["id"] for item in candidates}
        unknown = [item for item in selected_ids if item not in candidate_ids]
        if unknown:
            raise ValueError("来源匹配返回了所选章节范围之外的知识资产")
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM episode_knowledge_items WHERE episode_id = ?",
                (episode_id,),
            )
            connection.executemany(
                """
                INSERT INTO episode_knowledge_items
                  (episode_id, knowledge_item_id, position, role)
                VALUES (?, ?, ?, 'primary')
                """,
                [
                    (episode_id, knowledge_id, position)
                    for position, knowledge_id in enumerate(selected_ids, start=1)
                ],
            )
        return selected_ids

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
        prompt_version_id: str | None = None,
        prompt_system_version_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.database.row(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM artifact_versions
            WHERE episode_id = ? AND stage = ?
            """,
            (episode_id, stage),
        )
        version = int(current["version"]) + 1 if current else 1
        artifact_id = uuid.uuid4().hex
        created_at = now_iso()
        self.database.execute(
            """
            INSERT INTO artifact_versions
              (id, episode_id, stage, version, content, prompt_version,
               prompt_version_id, prompt_system_version_id, provider, model,
               author_type, input_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                episode_id,
                stage,
                version,
                content,
                prompt_version,
                prompt_version_id,
                prompt_system_version_id,
                provider or self.provider.name,
                model or self.provider.model,
                author_type,
                input_snapshot,
                created_at,
            ),
        )
        return {
            "id": artifact_id,
            "episode_id": episode_id,
            "stage": stage,
            "version": version,
            "content": content,
            "provider": provider or self.provider.name,
            "model": model or self.provider.model,
            "author_type": author_type,
            "created_at": created_at,
        }

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
        project.pop("album_outline_draft_json", None)
        project.pop("album_outline_draft_signature", None)
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
            episode["source_content_indexes"] = self._episode_content_indexes(
                episode
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
        episode["source_content_indexes"] = self._episode_content_indexes(episode)
        try:
            episode["evidence"] = self.contexts.evidence_bundle(episode_id)
            episode["source_match_pending"] = False
        except ValueError as error:
            if "尚未匹配具体知识资产" not in str(error):
                raise
            episode["evidence"] = {
                "knowledge_items": [],
                "direct_fragments": [],
                "auxiliary_fragments": [],
                "legacy_sections": [],
            }
            episode["source_match_pending"] = True
        return episode

    def _episode_content_indexes(
        self, episode: dict[str, Any]
    ) -> list[str]:
        rows = self.database.rows(
            """
            SELECT source.content_index
            FROM episode_knowledge_items link
            JOIN knowledge_item_sources source
              ON source.knowledge_item_id = link.knowledge_item_id
            WHERE link.episode_id = ?
            ORDER BY link.position, source.source_order
            """,
            (episode["id"],),
        )
        if rows:
            return list(
                dict.fromkeys(row["content_index"] for row in rows)
            )
        source_ids = episode.get("source_section_ids", [])
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            root = self.database.row(
                f"""
                SELECT section.book_id
                FROM sections section
                WHERE section.id IN ({placeholders})
                  AND section.parent_id IS NULL
                LIMIT 1
                """,
                tuple(source_ids),
            )
            if root and self.database.row(
                "SELECT id FROM chapter_analyses WHERE book_id = ? LIMIT 1",
                (root["book_id"],),
            ):
                return []
        return [
            content_index(section_id)
            for section_id in source_ids
        ]
