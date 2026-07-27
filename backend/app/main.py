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
from .ingestion import parse_book
from .obsidian import ObsidianSyncService
from .providers import build_provider
from .workflows import StageGenerationError, WorkflowService


settings = get_settings()
database = Database(settings.database_path)
database.init()
provider = build_provider(settings)
workflows = WorkflowService(database, provider)
batches = BatchService(database, workflows, concurrency=5)
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


class SectionsPayload(BaseModel):
    sections: list[SectionUpdate]


class ProjectCreate(BaseModel):
    title: str
    book_id: str


class EpisodeUpdate(BaseModel):
    id: str
    position: int
    title: str
    content_type: str
    style: str
    content_framework: str
    source_section_ids: list[str]


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


def not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label}不存在")


def create_task(coroutine: Any) -> None:
    task = asyncio.create_task(coroutine)
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)


async def execute_episode_run(
    run_id: str, episode_id: str, from_stage: str
) -> None:
    current = database.row("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if not current or current["status"] == "cancelled":
        return
    database.execute(
        """
        UPDATE workflow_runs
        SET status = 'running', message = '', updated_at = ?
        WHERE id = ?
        """,
        (now_iso(), run_id),
    )
    try:
        await workflows.generate_episode(episode_id, from_stage)
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
    return {"status": "ok", "provider": provider.name, "model": provider.model}


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
            "SELECT COUNT(*) AS knowledge_count FROM knowledge_items WHERE book_id = ?",
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
        )
        for section in parsed.sections
    ]
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "SELECT * FROM knowledge_items WHERE book_id = ? ORDER BY kind, title",
        (book_id,),
    )
    book["mind_map"] = database.row(
        "SELECT * FROM mind_maps WHERE book_id = ? ORDER BY version DESC LIMIT 1",
        (book_id,),
    )
    return book


@app.put("/api/books/{book_id}/sections")
def update_sections(book_id: str, payload: SectionsPayload) -> dict[str, Any]:
    if not database.row("SELECT id FROM books WHERE id = ?", (book_id,)):
        raise not_found("书籍")
    database.execute("DELETE FROM sections WHERE book_id = ?", (book_id,))
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')
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
            )
            for section in payload.sections
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
    try:
        return await workflows.analyze_book(book_id)
    except KeyError as error:
        raise not_found("书籍") from error


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
    try:
        batch = batches.create_batch(project_id)
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
    database.execute(
        """
        INSERT INTO workflow_runs
          (id, scope_type, scope_id, stage, status, message, created_at, updated_at)
        VALUES (?, 'episode', ?, ?, 'pending', '', ?, ?)
        """,
        (run_id, episode_id, payload.from_stage, now, now),
    )
    create_task(execute_episode_run(run_id, episode_id, payload.from_stage))
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
    return {
        "provider": provider.name,
        "model": provider.model,
        "api_key_configured": bool(settings.api_key),
        "data_dir": str(settings.data_dir),
    }
