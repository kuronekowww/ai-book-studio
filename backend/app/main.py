from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .batches import BatchService
from .config import get_settings
from .db import Database, now_iso
from .evidence import EvidenceService
from .ingestion import analysis_candidate_map, parse_book
from .model_catalog import ModelManager
from .obsidian import ObsidianSyncService
from .providers import ModelProvider
from .workflows import StageGenerationError, WorkflowService


settings = get_settings()
database = Database(settings.database_path)
database.init()
evidence = EvidenceService(database)
evidence.ensure_all_books()
model_manager = ModelManager(settings)
initial_provider = model_manager.snapshot().provider
workflows = WorkflowService(database, initial_provider)
batches = BatchService(
    database,
    workflows,
    concurrency=5,
    provider_resolver=lambda model_id: (
        model_manager.snapshot_for_run(model_id).provider
    ),
)
obsidian = ObsidianSyncService(database)

app = FastAPI(title="AI Book Studio API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
active_tasks: set[asyncio.Task[Any]] = set()


class SectionUpdate(BaseModel):
    id: str
    parent_id: str | None = None
    level: int = Field(ge=1, le=6)
    position: int
    title: str
    content: str
    kind: str = "section"
    analysis_enabled: bool = True
    analysis_exclusion_reason: str = ""


class SectionsPayload(BaseModel):
    sections: list[SectionUpdate]


class ProjectCreate(BaseModel):
    title: str
    book_id: str


class ProjectGenerationPayload(BaseModel):
    album_special_requirements: str = ""
    desired_episode_count: int | None = Field(default=None, ge=1, le=500)


class EpisodeUpdate(BaseModel):
    id: str
    position: int
    title: str
    content_type: str
    style: str
    content_framework: str
    source_section_ids: list[str]
    knowledge_item_ids: list[str] = Field(default_factory=list)


class EpisodesPayload(BaseModel):
    episodes: list[EpisodeUpdate]


class BookTypePayload(BaseModel):
    book_type: Literal["narrative", "non_narrative"]


class GeneratePayload(BaseModel):
    from_stage: str = "outline"


class FinalVersionPayload(BaseModel):
    content: str = Field(min_length=1)


class SyncPayload(BaseModel):
    vault_path: str
    book_id: str | None = None
    project_id: str | None = None


class ModelSelectionPayload(BaseModel):
    model_id: str


def not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label}不存在")


def create_task(coroutine: Any) -> None:
    task = asyncio.create_task(coroutine)
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)


async def execute_episode_run(
    run_id: str,
    episode_id: str,
    from_stage: str,
    provider: ModelProvider | None = None,
) -> None:
    current = database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if not current or current["status"] == "cancelled":
        return
    task_provider = provider or model_manager.snapshot_for_run(
        current["metadata_json"].get("model_id")
    ).provider
    database.execute(
        """
        UPDATE workflow_runs
        SET status = 'running', message = '', updated_at = ?
        WHERE id = ?
        """,
        (now_iso(), run_id),
    )
    try:
        await workflows.generate_episode(
            episode_id, from_stage, provider=task_provider
        )
        batches.reconcile_episode_success(episode_id)
        current = database.row("SELECT status FROM workflow_runs WHERE id = ?", (run_id,))
        status = "cancelled" if current and current["status"] == "cancelled" else "succeeded"
        database.execute(
            """
            UPDATE workflow_runs
            SET status = ?, message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, "声音版本已生成" if status == "succeeded" else "用户已取消", now_iso(), run_id),
        )
    except StageGenerationError as error:
        database.execute(
            """
            UPDATE workflow_runs
            SET status = 'failed', message = ?, error_stage = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(error)[:500], error.stage, now_iso(), run_id),
        )
    except Exception as error:
        database.execute(
            """
            UPDATE workflow_runs
            SET status = 'failed', message = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(error)[:500], now_iso(), run_id),
        )


@app.on_event("startup")
async def resume_incomplete_runs() -> None:
    incomplete_batches = database.rows(
        """
        SELECT * FROM workflow_runs
        WHERE scope_type = 'project_batch'
          AND status IN ('pending', 'running')
        ORDER BY created_at
        """
    )
    for run in incomplete_batches:
        create_task(batches.run_batch(run["id"]))
    incomplete_episodes = database.rows(
        """
        SELECT * FROM workflow_runs
        WHERE scope_type = 'episode'
          AND parent_run_id IS NULL
          AND status IN ('pending', 'running')
        ORDER BY created_at
        """
    )
    for run in incomplete_episodes:
        create_task(execute_episode_run(run["id"], run["scope_id"], run["stage"]))


@app.get("/health")
def health() -> dict[str, str]:
    snapshot = model_manager.snapshot()
    return {
        "status": "ok",
        "provider": snapshot.provider.name,
        "model": snapshot.provider.model,
    }


@app.get("/api/books")
def list_books() -> list[dict[str, Any]]:
    books = database.rows("SELECT * FROM books ORDER BY created_at DESC")
    for book in books:
        counts = database.row(
            """
            SELECT
              COUNT(*) AS section_count,
              SUM(CASE WHEN level = 3 THEN 1 ELSE 0 END) AS theme_count,
              SUM(CASE WHEN level = 4 THEN 1 ELSE 0 END) AS article_count
            FROM sections WHERE book_id = ?
            """,
            (book["id"],),
        )
        knowledge = database.row(
            """
            SELECT COUNT(*) AS knowledge_count FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
            """,
            (book["id"],),
        )
        book.update(counts or {})
        book.update(knowledge or {})
    return books


@app.post("/api/books/import")
async def import_book(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    book_type: Literal["narrative", "non_narrative"] = Form("non_narrative"),
) -> dict[str, Any]:
    filename = file.filename or "未命名.md"
    content = await file.read()
    try:
        parsed = parse_book(content, filename)
    except (ValueError, KeyError, StopIteration) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    book_id = uuid.uuid4().hex
    now = now_iso()
    final_title = title.strip() or parsed.title
    database.execute(
        """
        INSERT INTO books
          (id, title, author, book_type, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'segment_review', ?, 1, ?, ?)
        """,
        (
            book_id,
            final_title,
            author.strip(),
            book_type,
            filename,
            parsed.source_type,
            now,
            now,
        ),
    )
    candidate_flags = analysis_candidate_map(parsed.sections)
    rows = [
        (
            section.id,
            book_id,
            section.parent_id,
            section.level,
            section.position,
            section.title,
            section.content,
            section.kind,
            "draft",
            int(candidate_flags.get(section.id, (False, ""))[0]),
            candidate_flags.get(section.id, (False, ""))[1],
        )
        for section in parsed.sections
    ]
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status,
           analysis_enabled, analysis_exclusion_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    imported_dir = settings.data_dir / "imports"
    imported_dir.mkdir(parents=True, exist_ok=True)
    (imported_dir / f"{book_id}{Path(filename).suffix.lower()}").write_bytes(content)
    result = book_detail(book_id)
    result["diagnostics"] = parsed.diagnostics
    return result


@app.get("/api/books/{book_id}")
def book_detail(book_id: str) -> dict[str, Any]:
    book = database.row("SELECT * FROM books WHERE id = ?", (book_id,))
    if not book:
        raise not_found("书籍")
    book["sections"] = database.rows(
        "SELECT * FROM sections WHERE book_id = ? ORDER BY position",
        (book_id,),
    )
    book["knowledge"] = database.rows(
        """
        SELECT * FROM knowledge_items
        WHERE book_id = ? AND status = 'active'
        ORDER BY kind, title
        """,
        (book_id,),
    )
    for item in book["knowledge"]:
        item["source_content_indexes"] = [
            row["content_index"]
            for row in database.rows(
                """
                SELECT content_index FROM knowledge_item_sources
                WHERE knowledge_item_id = ? ORDER BY source_order
                """,
                (item["id"],),
            )
        ]
    book["mind_map"] = database.row(
        "SELECT * FROM mind_maps WHERE book_id = ? ORDER BY version DESC LIMIT 1",
        (book_id,),
    )
    book["chapter_analyses"] = database.rows(
        """
        SELECT ca.id, ca.book_id, ca.root_section_id, ca.version, ca.status,
               ca.rendered_markdown, ca.compressed_markdown, ca.prompt_version,
               ca.provider, ca.model, ca.fragment_set_id, ca.created_at,
               ca.validation_issues_json, ca.valid_item_count,
               ca.invalid_item_count,
               s.title AS chapter_title
        FROM chapter_analyses ca
        JOIN sections s ON s.id = ca.root_section_id
        WHERE ca.book_id = ?
        ORDER BY s.position, ca.version DESC
        """,
        (book_id,),
    )
    current_set = database.row(
        """
        SELECT * FROM source_fragment_sets
        WHERE book_id = ? AND status = 'current'
        ORDER BY version DESC LIMIT 1
        """,
        (book_id,),
    )
    book["fragment_set"] = current_set
    book["fragment_count"] = (
        database.row(
            """
            SELECT COUNT(*) AS count
            FROM source_fragment_set_members
            WHERE fragment_set_id = ?
            """,
            (current_set["id"],),
        )["count"]
        if current_set
        else 0
    )
    return book


@app.get("/api/source-fragments/{content_index}")
def source_fragment_detail(content_index: str) -> dict[str, Any]:
    try:
        return evidence.fragment_detail(content_index)
    except KeyError as error:
        raise not_found("原文片段") from error


@app.get("/api/knowledge-items/{knowledge_item_id}/sources")
def knowledge_item_sources(knowledge_item_id: str) -> dict[str, Any]:
    item = database.row(
        "SELECT * FROM knowledge_items WHERE id = ?", (knowledge_item_id,)
    )
    if not item:
        raise not_found("知识资产")
    sources = database.rows(
        """
        SELECT f.*, source.source_order, member.section_path_json,
               member.book_position, member.fragment_set_id
        FROM knowledge_item_sources source
        JOIN source_fragments f ON f.content_index = source.content_index
        LEFT JOIN source_fragment_set_members member
          ON member.content_index = source.content_index
        LEFT JOIN source_fragment_sets fragment_set
          ON fragment_set.id = member.fragment_set_id
        WHERE source.knowledge_item_id = ?
          AND (fragment_set.status = 'current' OR fragment_set.status IS NULL)
        ORDER BY source.source_order
        """,
        (knowledge_item_id,),
    )
    return {"knowledge_item": item, "sources": sources}


@app.put("/api/books/{book_id}/sections")
def update_sections(book_id: str, payload: SectionsPayload) -> dict[str, Any]:
    if not database.row("SELECT id FROM books WHERE id = ?", (book_id,)):
        raise not_found("书籍")
    database.execute("DELETE FROM sections WHERE book_id = ?", (book_id,))
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status,
           analysis_enabled, analysis_exclusion_reason, analysis_selection_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, 'manual')
        """,
        [
            (
                section.id,
                book_id,
                section.parent_id,
                section.level,
                section.position,
                section.title,
                section.content,
                section.kind,
                int(section.analysis_enabled),
                section.analysis_exclusion_reason,
            )
            for section in payload.sections
        ],
    )
    database.executemany(
        """
        INSERT INTO episode_knowledge_items
          (episode_id, knowledge_item_id, position, role)
        VALUES (?, ?, ?, 'primary')
        """,
        [
            (episode.id, knowledge_item_id, source_position)
            for episode in payload.episodes
            for source_position, knowledge_item_id in enumerate(
                episode.knowledge_item_ids, start=1
            )
        ],
    )
    return book_detail(book_id)


@app.put("/api/books/{book_id}/type")
def update_book_type(book_id: str, payload: BookTypePayload) -> dict[str, Any]:
    book = database.row("SELECT * FROM books WHERE id = ?", (book_id,))
    if not book:
        raise not_found("书籍")
    if book["book_type"] == payload.book_type:
        return book_detail(book_id)
    database.execute(
        """
        DELETE FROM workflow_runs
        WHERE scope_type = 'book_section_analysis'
          AND scope_id IN (SELECT id FROM sections WHERE book_id = ?)
        """,
        (book_id,),
    )
    next_status = (
        "segment_review"
        if book["status"] == "segment_review"
        else "ready_to_analyze"
    )
    database.execute(
        """
        UPDATE books
        SET book_type = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (payload.book_type, next_status, now_iso(), book_id),
    )
    return book_detail(book_id)


@app.post("/api/books/{book_id}/confirm")
def confirm_sections(book_id: str) -> dict[str, Any]:
    if not database.row("SELECT id FROM books WHERE id = ?", (book_id,)):
        raise not_found("书籍")
    database.execute(
        "UPDATE sections SET status = 'confirmed' WHERE book_id = ?", (book_id,)
    )
    database.execute(
        "UPDATE books SET status = 'ready_to_analyze', updated_at = ? WHERE id = ?",
        (now_iso(), book_id),
    )
    return book_detail(book_id)


@app.post("/api/books/{book_id}/analyze")
async def analyze_book(book_id: str) -> dict[str, Any]:
    snapshot = model_manager.snapshot()
    try:
        return await workflows.analyze_book(book_id, snapshot.provider)
    except KeyError as error:
        raise not_found("书籍") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/books/{book_id}/chapters/{section_id}/analyze")
async def retry_chapter(book_id: str, section_id: str) -> dict[str, Any]:
    snapshot = model_manager.snapshot()
    try:
        return await workflows.retry_chapter(
            book_id, section_id, snapshot.provider
        )
    except KeyError as error:
        raise not_found("书籍") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    projects = database.rows("SELECT * FROM projects ORDER BY created_at DESC")
    for project in projects:
        count = database.row(
            """
            SELECT COUNT(*) AS episode_count,
              SUM(
                CASE WHEN status IN ('completed', 'review', 'approved')
                THEN 1 ELSE 0 END
              ) AS completed_count
            FROM episodes WHERE project_id = ?
            """,
            (project["id"],),
        )
        project.update(count or {})
    return projects


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    try:
        return workflows.create_project(payload.title, payload.book_id)
    except KeyError as error:
        raise not_found("书籍") from error


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str) -> dict[str, Any]:
    try:
        return workflows.project_detail(project_id)
    except KeyError as error:
        raise not_found("项目") from error


@app.post("/api/projects/{project_id}/generate-outline")
async def generate_project_outline(
    project_id: str, payload: ProjectGenerationPayload
) -> dict[str, Any]:
    snapshot = model_manager.snapshot()
    try:
        return await workflows.generate_project_knowledge_outputs(
            project_id,
            payload.album_special_requirements,
            payload.desired_episode_count,
            snapshot.provider,
        )
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put("/api/projects/{project_id}/episodes")
def update_project_episodes(
    project_id: str, payload: EpisodesPayload
) -> dict[str, Any]:
    project = database.row("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise not_found("项目")
    database.execute("DELETE FROM episodes WHERE project_id = ?", (project_id,))
    database.executemany(
        """
        INSERT INTO episodes
          (id, project_id, position, title, content_type, style,
           content_framework, status, source_section_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'outline_review', ?)
        """,
        [
            (
                episode.id,
                project_id,
                index,
                episode.title,
                episode.content_type,
                episode.style,
                episode.content_framework.strip(),
                json.dumps(episode.source_section_ids, ensure_ascii=False),
            )
            for index, episode in enumerate(payload.episodes, start=1)
        ],
    )
    database.execute(
        "UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), project_id)
    )
    return workflows.project_detail(project_id)


@app.post("/api/projects/{project_id}/confirm")
def confirm_project(project_id: str) -> dict[str, Any]:
    try:
        return workflows.confirm_project(project_id)
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/projects/{project_id}/generate-all")
async def generate_all(project_id: str) -> dict[str, Any]:
    snapshot = model_manager.snapshot()
    try:
        batch = batches.create_batch(project_id, snapshot.run_model_id)
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if batch["status"] == "pending":
        create_task(batches.run_batch(batch["id"]))
    return batch


@app.get("/api/projects/{project_id}/batch")
def project_batch(project_id: str) -> dict[str, Any] | None:
    if not database.row("SELECT id FROM projects WHERE id = ?", (project_id,)):
        raise not_found("项目")
    return batches.latest_batch(project_id)


@app.get("/api/episodes/{episode_id}")
def episode_detail(episode_id: str) -> dict[str, Any]:
    try:
        return workflows.episode_detail(episode_id)
    except KeyError as error:
        raise not_found("声音") from error


@app.get("/api/episodes/{episode_id}/evidence")
def episode_evidence(episode_id: str) -> dict[str, Any]:
    try:
        return workflows.contexts.evidence_bundle(episode_id)
    except KeyError as error:
        raise not_found("声音") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/episodes/{episode_id}/final-versions")
def save_manual_final(
    episode_id: str, payload: FinalVersionPayload
) -> dict[str, Any]:
    try:
        return workflows.save_manual_final(episode_id, payload.content)
    except KeyError as error:
        raise not_found("声音") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/episodes/{episode_id}/generate")
async def generate_episode(
    episode_id: str, payload: GeneratePayload
) -> dict[str, Any]:
    if payload.from_stage not in {"outline", "draft", "final"}:
        raise HTTPException(status_code=400, detail="from_stage 参数无效")
    if not database.row("SELECT id FROM episodes WHERE id = ?", (episode_id,)):
        raise not_found("声音")
    run_id = uuid.uuid4().hex
    now = now_iso()
    snapshot = model_manager.snapshot()
    database.execute(
        """
        INSERT INTO workflow_runs
          (id, scope_type, scope_id, stage, status, message, metadata_json,
           created_at, updated_at)
        VALUES (?, 'episode', ?, ?, 'pending', '', ?, ?, ?)
        """,
        (
            run_id,
            episode_id,
            payload.from_stage,
            json.dumps({"model_id": snapshot.run_model_id}, ensure_ascii=False),
            now,
            now,
        ),
    )
    create_task(
        execute_episode_run(
            run_id,
            episode_id,
            payload.from_stage,
            snapshot.provider,
        )
    )
    return database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)) or {}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return database.rows(
        "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT 100"
    )


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    run = database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if not run:
        raise not_found("运行记录")
    return run


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    run = database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if not run:
        raise not_found("运行记录")
    if run["scope_type"] == "project_batch":
        return batches.cancel_batch(run_id)
    if run["status"] in {"succeeded", "failed", "cancelled"}:
        return run
    database.execute(
        """
        UPDATE workflow_runs
        SET status = 'cancelled', message = '用户已取消', updated_at = ?
        WHERE id = ?
        """,
        (now_iso(), run_id),
    )
    return database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)) or {}


@app.post("/api/obsidian/sync")
def sync_obsidian(payload: SyncPayload) -> dict[str, Any]:
    if not payload.book_id and not payload.project_id:
        raise HTTPException(status_code=400, detail="至少选择一本书或一个项目")
    try:
        return obsidian.sync(
            payload.vault_path,
            book_id=payload.book_id,
            project_id=payload.project_id,
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/settings/status")
def settings_status() -> dict[str, Any]:
    return model_manager.status()


@app.put("/api/settings/model")
def select_model(payload: ModelSelectionPayload) -> dict[str, Any]:
    try:
        model_manager.switch(payload.model_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法保存本地模型设置") from error
    return model_manager.status()
