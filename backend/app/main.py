from __future__ import annotations

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
from .model_routing import ModelRoutingService
from .obsidian import ObsidianSyncService
from .prompt_config import PromptConfigurationService
from .providers import ModelGenerationError, ModelProvider
from .runs import RunService, TaskRegistry
from .text_metrics import (
    DEFAULT_EPISODE_WORD_COUNT_MAX,
    DEFAULT_EPISODE_WORD_COUNT_MIN,
    format_episode_word_count_range,
    validate_episode_word_count_range,
)
from .workflows import StageGenerationError, WorkflowService


settings = get_settings()
database = Database(settings.database_path)
database.init()
evidence = EvidenceService(database)
evidence.ensure_all_books()
model_manager = ModelManager(settings)
model_routing = ModelRoutingService(database, model_manager)
prompt_configuration = PromptConfigurationService(database)
initial_provider = model_manager.snapshot().provider
workflows = WorkflowService(
    database, initial_provider, prompt_configuration=prompt_configuration
)
batches = BatchService(
    database,
    workflows,
    concurrency=5,
    provider_resolver=lambda model_id: (
        model_manager.snapshot_for_run(model_id).provider
    ),
)
obsidian = ObsidianSyncService(database)
runs = RunService(database)
task_registry = TaskRegistry(runs)

app = FastAPI(title="AI Book Studio API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
    episode_word_count_min: int = DEFAULT_EPISODE_WORD_COUNT_MIN
    episode_word_count_max: int = DEFAULT_EPISODE_WORD_COUNT_MAX


class EpisodeUpdate(BaseModel):
    id: str
    position: int
    title: str
    content_type: str
    style: str
    content_framework: str
    section_identifier: str = ""
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


class ProjectModelSelectionPayload(BaseModel):
    model_id: str | None = None


class PromptVersionPayload(BaseModel):
    stage_key: str
    scope: Literal["global", "project"]
    project_id: str | None = None
    user_template: str = Field(min_length=1, max_length=50_000)


class PromptRestorePayload(BaseModel):
    stage_key: str
    scope: Literal["global", "project"]
    version_id: str
    project_id: str | None = None


class PromptPreviewPayload(BaseModel):
    stage_key: str
    user_template: str = Field(min_length=1, max_length=50_000)
    project_id: str | None = None
    episode_id: str | None = None
    module_key: str | None = None


def not_found(label: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label}不存在")


def _latest_prompt_modules(project_id: str) -> list[dict[str, Any]]:
    latest = database.row(
        """
        SELECT run_id, artifact_type
        FROM album_planning_artifacts
        WHERE project_id = ?
          AND artifact_type IN ('module_source', 'module_outline')
          AND status = 'succeeded'
        ORDER BY created_at DESC,
          CASE artifact_type WHEN 'module_source' THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (project_id,),
    )
    if not latest:
        return []
    artifact_type = latest["artifact_type"]
    artifacts = database.rows(
        """
        SELECT *
        FROM album_planning_artifacts
        WHERE run_id = ? AND artifact_type = ?
          AND status = 'succeeded'
        ORDER BY position, created_at
        """,
        (latest["run_id"], artifact_type),
    )
    child_runs = database.rows(
        """
        SELECT *
        FROM workflow_runs
        WHERE parent_run_id = ? AND scope_type = 'project_album_module'
        ORDER BY position, created_at
        """,
        (latest["run_id"],),
    )
    metadata_by_key = {
        str(run["metadata_json"].get("module_key") or ""): run["metadata_json"]
        for run in child_runs
    }
    recovered_content: dict[str, str] = {}
    chapter_keys_by_module: dict[str, list[str]] = {}
    project = database.row("SELECT * FROM projects WHERE id = ?", (project_id,))
    book_id = project["book_ids"][0] if project and project["book_ids"] else ""
    if book_id:
        try:
            entries, key_map = workflows.album_planning.build_chapter_catalog(
                book_id
            )
            key_by_section = {
                section_id: chapter_key
                for chapter_key, section_id in key_map.items()
            }
            for artifact in artifacts:
                chapter_keys = [
                    key_by_section[section_id]
                    for section_id in artifact["source_chapter_ids_json"]
                    if section_id in key_by_section
                ]
                chapter_keys_by_module[artifact["module_key"]] = chapter_keys
                if artifact_type == "module_outline":
                    recovered_content[artifact["module_key"]] = (
                        workflows.album_planning.render_module_source(
                            entries, chapter_keys
                        )
                    )
        except ValueError:
            recovered_content = {}
            chapter_keys_by_module = {}
    return [
        {
            "run_id": artifact["run_id"],
            "module_key": artifact["module_key"],
            "position": artifact["position"],
            "title": (
                metadata_by_key.get(artifact["module_key"], {}).get("module_title")
                or f"知识模块 {artifact['position']}"
            ),
            "chapter_ids": (
                chapter_keys_by_module.get(artifact["module_key"])
                or artifact["source_chapter_ids_json"]
            ),
            "content": (
                recovered_content.get(artifact["module_key"])
                or artifact["content"]
            ),
            "character_count": len(
                recovered_content.get(artifact["module_key"])
                or artifact["content"]
            ),
        }
        for artifact in artifacts
    ]


def _prompt_material(
    key: str,
    label: str,
    source: str,
    content: str,
    *,
    compressed: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "source": source,
        "character_count": len(content),
        "compressed": compressed,
        "content": content,
    }


def prompt_preview_values(
    stage_key: str,
    project_id: str | None = None,
    episode_id: str | None = None,
    module_key: str | None = None,
) -> tuple[dict[str, str], str, list[dict[str, Any]]]:
    values = {
        "full_book_analysis": (
            "# 第一章 示例拆书稿\n\n## 子主题\n示例观点与原文索引。"
        ),
        "planning_book_analysis": (
            "[CHAPTER_001] 第一章 示例章节\n章节主题：示例主题\n"
            "子主题：示例子主题\n主要观点：示例观点"
        ),
        "module_book_analysis": (
            "[CHAPTER_001] 第一章 示例章节\n章节主题：示例主题\n"
            "子主题：示例子主题\n主要观点：示例观点"
        ),
        "book_analysis": "# 第一章 示例拆书稿\n\n## 子主题\n示例观点与原文索引。",
        "chapter_catalog": "[CHAPTER_001] 第一章 示例章节\n章节主题：示例主题",
        "module_brief": (
            "模块标题：示例知识模块\n听众问题：这个模块解决什么问题？\n"
            "来源章节：[CHAPTER_001]\n本模块分配集数：3"
        ),
        "module_source": (
            "[CHAPTER_001] 第一章 示例章节\n章节主题：示例主题\n"
            "子主题：示例子主题\n主要观点：示例观点"
        ),
        "book_title": "示例书名",
        "book_author": "示例作者",
        "book_type": "非叙事类",
        "album_special_requirements": "无",
        "desired_episode_count": "3",
        "episode_title": "示例声音标题",
        "episode_framework": "听众钩子、核心主题与递进核心要点。",
        "source_text": "示例原文块与知识资产证据。",
        "character_relationships": "非故事类书籍无须提供人物关系。",
        "episode_outline": "示例声音细纲。",
        "episode_draft": "示例声音初稿。",
        "previous_episode_final": "当前没有可用的上一集终稿。",
        "episode_word_count_range": format_episode_word_count_range(
            DEFAULT_EPISODE_WORD_COUNT_MIN,
            DEFAULT_EPISODE_WORD_COUNT_MAX,
        ),
    }
    book_type = "non_narrative"
    if not project_id:
        material_keys = {
            "mind_map": ("full_book_analysis",),
            "album_module_plan": ("planning_book_analysis", "chapter_catalog"),
            "album_outline": ("module_brief", "module_book_analysis"),
            "episode_outline": ("episode_framework", "module_book_analysis"),
            "episode_draft": ("episode_outline", "source_text"),
            "episode_final": ("episode_draft", "source_text"),
        }.get(stage_key, ())
        return values, book_type, [
            _prompt_material(key, key, "示例材料", values[key])
            for key in material_keys
        ]
    project = database.row("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise KeyError(project_id)
    book = (
        database.row("SELECT * FROM books WHERE id = ?", (project["book_ids"][0],))
        if project["book_ids"]
        else None
    )
    if book:
        book_type = book["book_type"]
        values.update(
            {
                "book_title": book["title"],
                "book_author": book["author"] or "未填写",
                "book_type": "叙事类" if book_type == "narrative" else "非叙事类",
                "album_special_requirements": (
                    project["album_special_requirements"] or "无"
                ),
                "desired_episode_count": (
                    str(project["desired_episode_count"])
                    if project["desired_episode_count"]
                    else "未指定，由模型根据内容自行决定"
                ),
                "episode_word_count_range": format_episode_word_count_range(
                    int(
                        project.get("episode_word_count_min")
                        or DEFAULT_EPISODE_WORD_COUNT_MIN
                    ),
                    int(
                        project.get("episode_word_count_max")
                        or DEFAULT_EPISODE_WORD_COUNT_MAX
                    ),
                ),
            }
        )
        analyses = database.rows(
            """
            SELECT current.rendered_markdown
            FROM chapter_analyses current
            WHERE current.book_id = ? AND current.status = 'succeeded'
              AND current.version = (
                SELECT MAX(latest.version)
                FROM chapter_analyses latest
                WHERE latest.root_section_id = current.root_section_id
              )
            ORDER BY current.created_at
            """,
            (book["id"],),
        )
        if analyses:
            complete_analysis = "\n\n".join(
                item["rendered_markdown"] for item in analyses
            )
            values["full_book_analysis"] = complete_analysis
            values["book_analysis"] = complete_analysis
            try:
                entries, _ = workflows.album_planning.build_chapter_catalog(
                    book["id"]
                )
                values["chapter_catalog"] = (
                    workflows.album_planning.render_catalog(entries)
                )
                values["planning_book_analysis"] = (
                    workflows.album_planning.render_planning_analysis(entries)
                )
                preview_entries = entries[: min(3, len(entries))]
                fallback_module = (
                    workflows.album_planning.render_module_source(
                        entries,
                        [entry.chapter_key for entry in preview_entries],
                    )
                )
                values["module_source"] = fallback_module
                values["module_book_analysis"] = fallback_module
                values["module_brief"] = (
                    "模块标题：预览知识模块\n"
                    "听众问题：这些章节共同解决什么问题？\n"
                    "来源章节："
                    + "、".join(
                        f"[{entry.chapter_key}]" for entry in preview_entries
                    )
                    + "\n本模块分配集数：3"
                )
            except ValueError:
                pass
    modules = _latest_prompt_modules(project_id)
    selected_module = next(
        (item for item in modules if item["module_key"] == module_key),
        modules[0] if modules else None,
    )
    if selected_module:
        module_content = selected_module["content"]
        values["module_book_analysis"] = module_content
        values["module_source"] = module_content
        if stage_key == "episode_outline":
            values["source_text"] = module_content
        values["module_brief"] = (
            f"模块标识：{selected_module['module_key']}\n"
            f"模块标题：{selected_module['title']}\n"
            "来源章节："
            + "、".join(selected_module["chapter_ids"])
        )

    episode: dict[str, Any] | None = None
    if stage_key in {"episode_outline", "episode_draft", "episode_final"}:
        episode = (
            database.row(
                "SELECT * FROM episodes WHERE id = ? AND project_id = ?",
                (episode_id, project_id),
            )
            if episode_id
            else database.row(
                """
                SELECT * FROM episodes
                WHERE project_id = ? ORDER BY position LIMIT 1
                """,
                (project_id,),
            )
        )
        if episode:
            stage_name = {
                "episode_outline": "outline",
                "episode_draft": "draft",
                "episode_final": "final",
            }[stage_key]
            try:
                context = workflows.contexts.build(episode["id"], stage_name)
                values.update(context.variables)
            except ValueError:
                values.update(
                    {
                        "episode_title": episode["title"],
                        "episode_framework": episode["content_framework"],
                    }
                )
                if stage_key != "episode_outline":
                    bundle = workflows.contexts.evidence_bundle(episode["id"])
                    values["source_text"] = workflows.contexts._format_bundle(bundle)
                else:
                    try:
                        outline_context = workflows.contexts.build(
                            episode["id"], "outline"
                        )
                        values.update(outline_context.variables)
                    except ValueError:
                        pass
            for artifact_stage, variable in (
                ("outline", "episode_outline"),
                ("draft", "episode_draft"),
            ):
                artifact = workflows.latest_artifact(episode["id"], artifact_stage)
                if artifact:
                    values[variable] = artifact["content"]
            if episode.get("module_key"):
                episode_module = next(
                    (
                        item
                        for item in modules
                        if item["module_key"] == episode["module_key"]
                    ),
                    None,
                )
                if episode_module and stage_key == "episode_outline":
                    values["module_book_analysis"] = episode_module["content"]
                    values["source_text"] = episode_module["content"]

    missing_episode = (
        stage_key in {"episode_outline", "episode_draft", "episode_final"}
        and episode is None
    )
    if missing_episode:
        values.update(
            {
                "episode_title": "当前项目尚无声音",
                "episode_framework": "当前项目尚无已确认的声音框架。",
                "episode_outline": "当前声音尚未生成声音细纲。",
                "episode_draft": "当前声音尚未生成声音初稿。",
            }
        )
    material_specs = {
        "mind_map": (
            ("full_book_analysis", "全书拆书稿", "最新成功章节拆书稿合并"),
        ),
        "album_module_plan": (
            ("planning_book_analysis", "策划版全书拆书稿", "最新章节策划摘要"),
            ("chapter_catalog", "全书轻量章节目录", "最新拆书章节目录"),
        ),
        "album_outline": (
            ("module_brief", "当前模块任务", "最新专辑规划运行"),
            ("module_book_analysis", "当前模块详细拆书稿", "模块所含一级章节"),
        ),
        "episode_outline": (
            ("episode_framework", "当前声音框架", "已确认专辑大纲"),
            ("module_book_analysis", "所属模块详细拆书稿", "声音关联模块"),
        ),
        "episode_draft": (
            ("episode_outline", "上一步声音细纲", "最新声音细纲版本"),
            ("source_text", "当前声音关联原文", "段落级原文匹配"),
        ),
        "episode_final": (
            ("episode_draft", "上一步声音初稿", "最新声音初稿版本"),
            ("source_text", "当前声音关联原文", "段落级原文匹配"),
        ),
    }.get(stage_key, ())
    materials = [
        _prompt_material(
            key,
            label,
            (
                "当前项目尚无声音，以下为缺失状态"
                if missing_episode
                and key
                in {
                    "episode_framework",
                    "episode_outline",
                    "episode_draft",
                    "source_text",
                }
                else source
            ),
            values.get(key, ""),
        )
        for key, label, source in material_specs
    ]
    return values, book_type, materials


async def execute_episode_run(
    run_id: str,
    episode_id: str,
    from_stage: str,
    provider: ModelProvider | None = None,
    stage_providers: dict[str, ModelProvider] | None = None,
    stage_prompt_locks: dict[str, dict[str, str]] | None = None,
) -> None:
    current = runs.get(run_id)
    if not current or current["status"] == "cancelled":
        return
    task_provider = provider
    locked_stage_providers = stage_providers
    locked_prompt_versions = stage_prompt_locks
    if locked_prompt_versions is None:
        metadata_locks = current["metadata_json"].get("stage_prompt_locks")
        if isinstance(metadata_locks, dict):
            locked_prompt_versions = metadata_locks
    if locked_stage_providers is None:
        stage_model_ids = current["metadata_json"].get("stage_model_ids")
        if isinstance(stage_model_ids, dict) and stage_model_ids:
            locked_stage_providers = {
                stage: snapshot.provider
                for stage, snapshot in model_routing.restore_stage_snapshots(
                    stage_model_ids
                ).items()
            }
        else:
            task_provider = task_provider or model_manager.snapshot_for_run(
                current["metadata_json"].get("model_id")
            ).provider
    stage_order = ["outline", "draft", "final"]
    target_stages = stage_order[stage_order.index(from_stage) :]
    source_links = database.row(
        """
        SELECT COUNT(*) AS count FROM episode_knowledge_items
        WHERE episode_id = ?
        """,
        (episode_id,),
    )
    if not source_links or not int(source_links["count"]):
        target_stages = ["match_episode_sources", *target_stages]
    completed_stages = {
        stage
        for stage in target_stages
        if runs.stage_status(run_id, stage) == "succeeded"
    }
    runs.mark_running(
        run_id,
        current_stage=next(
            (stage for stage in target_stages if stage not in completed_stages),
            target_stages[-1],
        ),
        message="正在生成声音文稿",
        increment_attempt=True,
    )
    runs.set_progress(
        run_id,
        current=len(completed_stages),
        total=len(target_stages),
    )

    def report_stage(
        stage: str,
        status: str,
        output: dict[str, Any] | None,
        message: str | None,
    ) -> None:
        runs.set_stage(
            run_id,
            stage,
            status,
            message=message,
            output=output,
        )
        completed = sum(
            runs.stage_status(run_id, item) == "succeeded"
            for item in target_stages
        )
        runs.set_progress(
            run_id,
            current=completed,
            total=len(target_stages),
            current_stage=stage,
            message=message,
        )

    def cancelled() -> bool:
        return runs.get(run_id)["status"] == "cancelled"

    try:
        word_count_range = (
            int(
                current["metadata_json"].get("episode_word_count_min")
                or DEFAULT_EPISODE_WORD_COUNT_MIN
            ),
            int(
                current["metadata_json"].get("episode_word_count_max")
                or DEFAULT_EPISODE_WORD_COUNT_MAX
            ),
        )
        await workflows.generate_episode(
            episode_id,
            from_stage,
            provider=task_provider,
            stage_providers=locked_stage_providers,
            stage_prompt_locks=locked_prompt_versions,
            word_count_range=word_count_range,
            progress_callback=report_stage,
            completed_stages=completed_stages,
            cancelled=cancelled,
        )
        if cancelled():
            return
        batches.reconcile_episode_success(episode_id)
        runs.finish(run_id, message="声音版本已生成")
    except StageGenerationError as error:
        runs.fail(run_id, error, error_stage=error.stage)
    except Exception as error:
        runs.fail(run_id, error, error_stage=runs.get(run_id)["current_stage"])


async def execute_project_generation_run(
    run_id: str,
    *,
    mind_map_provider: ModelProvider | None = None,
    album_outline_provider: ModelProvider | None = None,
) -> None:
    current = runs.get(run_id)
    if current["status"] == "cancelled":
        return
    metadata = current.get("metadata_json") or {}
    project_id = current["scope_id"]
    model_ids = metadata.get("stage_model_ids") or {}
    if mind_map_provider is None:
        mind_map_provider = model_manager.snapshot_for_run(
            model_ids.get("mind_map")
        ).provider
    if album_outline_provider is None:
        album_outline_provider = model_manager.snapshot_for_run(
            model_ids.get("album_outline")
        ).provider
    stage_order = [
        "prepare_chapter_catalog",
        "generate_mind_map",
        "design_album_modules",
        "expand_album_modules",
        "structure_album_outline",
        "save_project_outline",
    ]
    completed_stages = {
        stage
        for stage in stage_order
        if runs.stage_status(run_id, stage) == "succeeded"
    }
    if completed_stages == set(stage_order):
        runs.finish(run_id, message="思维导图与专辑大纲已生成")
        return
    runs.mark_running(
        run_id,
        current_stage=next(
            (stage for stage in stage_order if stage not in completed_stages),
            stage_order[-1],
        ),
        message="正在生成思维导图与专辑大纲",
        increment_attempt=True,
    )
    runs.set_progress(
        run_id,
        current=len(completed_stages),
        total=len(stage_order),
    )

    def report_stage(
        stage: str,
        status: str,
        output: dict[str, Any] | None,
        message: str | None,
    ) -> None:
        runs.set_stage(
            run_id,
            stage,
            status,
            message=message,
            output=output,
        )
        completed = sum(
            runs.stage_status(run_id, item) == "succeeded"
            for item in stage_order
        )
        runs.set_progress(
            run_id,
            current=completed,
            total=len(stage_order),
            current_stage=stage,
            message=message,
        )

    def cancelled() -> bool:
        return runs.get(run_id)["status"] == "cancelled"

    try:
        result = await workflows.generate_project_knowledge_outputs(
            project_id,
            str(metadata.get("album_special_requirements") or ""),
            metadata.get("desired_episode_count"),
            episode_word_count_min=int(
                metadata.get("episode_word_count_min")
                or DEFAULT_EPISODE_WORD_COUNT_MIN
            ),
            episode_word_count_max=int(
                metadata.get("episode_word_count_max")
                or DEFAULT_EPISODE_WORD_COUNT_MAX
            ),
            mind_map_provider=mind_map_provider,
            album_outline_provider=album_outline_provider,
            mind_map_prompt_lock=(
                (metadata.get("project_prompt_locks") or {}).get("mind_map")
            ),
            module_plan_prompt_lock=(
                (metadata.get("project_prompt_locks") or {}).get(
                    "album_module_plan"
                )
            ),
            album_prompt_lock=(
                (metadata.get("project_prompt_locks") or {}).get(
                    "album_outline"
                )
                or metadata.get("album_prompt_lock")
            ),
            planning_run_id=run_id,
            retry_module_key=metadata.get("retry_module_key"),
            progress_callback=report_stage,
            cancelled=cancelled,
        )
        if runs.get(run_id)["status"] == "cancelled":
            return
        if (
            result["mind_map"]["status"] == "succeeded"
            and result["album_outline"]["status"] == "succeeded"
        ):
            runs.finish(run_id, message="思维导图与专辑大纲已生成")
        else:
            runs.finish(
                run_id,
                status="partial_failed",
                message=(
                    result["album_outline"].get("error")
                    or result["mind_map"].get("error")
                    or "部分内容生成失败"
                )[:500],
            )
    except Exception as error:
        runs.fail(
            run_id,
            error,
            error_stage=runs.get(run_id)["current_stage"],
        )


async def execute_book_analysis_run(
    run_id: str,
    *,
    provider: ModelProvider | None = None,
) -> None:
    current = runs.get(run_id)
    if current["status"] == "cancelled":
        return
    metadata = current.get("metadata_json") or {}
    book_id = current["scope_id"]
    task_provider = provider or model_manager.snapshot_for_run(
        metadata.get("model_id")
    ).provider
    root_section_id = metadata.get("root_section_id")
    book = database.row("SELECT * FROM books WHERE id = ?", (book_id,))
    if not book:
        runs.fail(run_id, "书籍不存在", error_stage="prepare_chapters")
        return
    runs.mark_running(
        run_id,
        current_stage="prepare_chapters",
        message="正在准备拆书任务",
        increment_attempt=True,
    )

    def report_stage(
        stage: str,
        status: str,
        output: dict[str, Any] | None,
        message: str | None,
    ) -> None:
        runs.set_stage(
            run_id,
            stage,
            status,
            message=message,
            output=output,
        )
        runs.set_progress(
            run_id,
            current_stage=stage,
            message=message,
        )

    def cancelled() -> bool:
        return runs.get(run_id)["status"] == "cancelled"

    try:
        if book["book_type"] == "narrative":
            if runs.stage_status(run_id, "analyze_chapters") != "succeeded":
                report_stage(
                    "prepare_chapters",
                    "succeeded",
                    {"book_type": "narrative"},
                    "叙事类书籍输入已准备",
                )
                report_stage(
                    "analyze_chapters",
                    "running",
                    None,
                    "正在整理知识与人物关系",
                )
                result = await workflows.analyze_book(
                    book_id,
                    task_provider,
                    parent_run_id=run_id,
                    cancelled=cancelled,
                )
                if cancelled():
                    return
                report_stage(
                    "analyze_chapters",
                    "succeeded",
                    {
                        "artifact_type": "book_knowledge",
                        "knowledge_count": result.get("knowledge_count", 0),
                    },
                    "书籍知识已生成",
                )
            report_stage(
                "finalize_book",
                "succeeded",
                {"book_status": "analyzed"},
                "书籍知识状态已更新",
            )
            runs.set_progress(run_id, current=1, total=1)
            runs.finish(run_id, message="拆书与知识入库已完成")
            return

        if isinstance(root_section_id, str) and root_section_id:
            result = await workflows.retry_chapter(
                book_id,
                root_section_id,
                task_provider,
                parent_run_id=run_id,
                progress_callback=report_stage,
                cancelled=cancelled,
            )
        else:
            result = await workflows.analyze_book(
                book_id,
                task_provider,
                parent_run_id=run_id,
                progress_callback=report_stage,
                cancelled=cancelled,
            )
        if runs.get(run_id)["status"] == "cancelled":
            return
        partial_count = len(result.get("partial_chapters") or [])
        failed_count = len(result.get("failed_chapters") or [])
        status = "partial_failed" if partial_count or failed_count else "succeeded"
        runs.finish(
            run_id,
            status=status,
            message=(
                f"成功 {result.get('succeeded_count', 0)} 章，"
                f"部分成功 {partial_count} 章，失败 {failed_count} 章"
            ),
        )
    except Exception as error:
        runs.fail(
            run_id,
            error,
            error_stage=runs.get(run_id)["current_stage"],
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
        runs.reset_for_resume(run["id"])
        task_registry.spawn(run["id"], lambda run_id=run["id"]: batches.run_batch(run_id))
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
        runs.reset_for_resume(run["id"])
        task_registry.spawn(
            run["id"],
            lambda run=run: execute_episode_run(
                run["id"], run["scope_id"], run["stage"]
            ),
        )
    incomplete_projects = database.rows(
        """
        SELECT * FROM workflow_runs
        WHERE scope_type = 'project_generation'
          AND status IN ('pending', 'running')
        ORDER BY created_at
        """
    )
    for run in incomplete_projects:
        runs.reset_for_resume(run["id"])
        task_registry.spawn(
            run["id"],
            lambda run_id=run["id"]: execute_project_generation_run(run_id),
        )
    incomplete_books = database.rows(
        """
        SELECT * FROM workflow_runs
        WHERE scope_type = 'book_analysis_batch'
          AND status IN ('pending', 'running')
        ORDER BY created_at
        """
    )
    for run in incomplete_books:
        runs.reset_for_resume(run["id"])
        task_registry.spawn(
            run["id"],
            lambda run_id=run["id"]: execute_book_analysis_run(run_id),
        )


@app.on_event("shutdown")
async def stop_background_tasks() -> None:
    await task_registry.shutdown()


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
        book.update(model_routing.book_config(book["id"]))
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
    book.update(model_routing.book_config(book_id))
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


@app.post("/api/books/{book_id}/analyze", status_code=202)
async def analyze_book(book_id: str) -> dict[str, Any]:
    try:
        snapshot = model_routing.book_snapshot(book_id)
        chapter_count = database.row(
            """
            SELECT COUNT(*) AS count FROM sections
            WHERE book_id = ? AND parent_id IS NULL
              AND status = 'confirmed' AND analysis_enabled = 1
            """,
            (book_id,),
        )
        run, reused = runs.create(
            scope_type="book_analysis_batch",
            scope_id=book_id,
            stage="book_analysis",
            current_stage="prepare_chapters",
            progress_total=int((chapter_count or {}).get("count") or 1),
            metadata={"model_id": snapshot.run_model_id},
        )
        task_registry.spawn(
            run["id"],
            lambda: execute_book_analysis_run(
                run["id"], provider=snapshot.provider
            ),
        )
        run = runs.get(run["id"])
        run["reused"] = reused
        return run
    except KeyError as error:
        raise not_found("书籍") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post(
    "/api/books/{book_id}/chapters/{section_id}/analyze",
    status_code=202,
)
async def retry_chapter(book_id: str, section_id: str) -> dict[str, Any]:
    try:
        snapshot = model_routing.book_snapshot(book_id)
        root = database.row(
            """
            SELECT id FROM sections
            WHERE id = ? AND book_id = ? AND parent_id IS NULL
              AND status = 'confirmed' AND analysis_enabled = 1
            """,
            (section_id, book_id),
        )
        if not root:
            raise ValueError("章节不存在、未确认或未纳入拆书")
        run, reused = runs.create(
            scope_type="book_analysis_batch",
            scope_id=book_id,
            stage="book_analysis",
            current_stage="prepare_chapters",
            progress_total=1,
            metadata={
                "model_id": snapshot.run_model_id,
                "root_section_id": section_id,
            },
        )
        task_registry.spawn(
            run["id"],
            lambda: execute_book_analysis_run(
                run["id"], provider=snapshot.provider
            ),
        )
        run = runs.get(run["id"])
        run["reused"] = reused
        return run
    except KeyError as error:
        raise not_found("书籍") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    projects = database.rows("SELECT * FROM projects ORDER BY created_at DESC")
    for project in projects:
        project.pop("album_outline_draft_json", None)
        project.pop("album_outline_draft_signature", None)
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
        project.update(model_routing.project_config(project["id"]))
    return projects


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    try:
        project = workflows.create_project(payload.title, payload.book_id)
        project.update(model_routing.project_config(project["id"]))
        return project
    except KeyError as error:
        raise not_found("书籍") from error


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str) -> dict[str, Any]:
    try:
        project = workflows.project_detail(project_id)
        project.update(model_routing.project_config(project_id))
        return project
    except KeyError as error:
        raise not_found("项目") from error


@app.get("/api/projects/{project_id}/prompt-modules")
def project_prompt_modules(project_id: str) -> list[dict[str, Any]]:
    if not database.row("SELECT id FROM projects WHERE id = ?", (project_id,)):
        raise not_found("项目")
    return [
        {
            "run_id": module["run_id"],
            "module_key": module["module_key"],
            "position": module["position"],
            "title": module["title"],
            "chapter_ids": module["chapter_ids"],
            "character_count": module["character_count"],
        }
        for module in _latest_prompt_modules(project_id)
    ]


@app.post("/api/projects/{project_id}/generate-outline", status_code=202)
async def generate_project_outline(
    project_id: str, payload: ProjectGenerationPayload
) -> dict[str, Any]:
    try:
        validate_episode_word_count_range(
            payload.episode_word_count_min,
            payload.episode_word_count_max,
        )
        snapshots = model_routing.project_stage_snapshots(
            project_id, ("mind_map", "album_outline")
        )
        project_prompt_locks = {
            stage: prompt_configuration.lock_stage(stage, project_id)
            for stage in (
                "mind_map",
                "album_module_plan",
                "album_outline",
            )
        }
        run, reused = runs.create(
            scope_type="project_generation",
            scope_id=project_id,
            stage="full",
            current_stage="prepare_chapter_catalog",
            progress_total=6,
            metadata={
                "album_special_requirements": payload.album_special_requirements,
                "desired_episode_count": payload.desired_episode_count,
                "episode_word_count_min": payload.episode_word_count_min,
                "episode_word_count_max": payload.episode_word_count_max,
                "stage_model_ids": {
                    stage: snapshot.run_model_id
                    for stage, snapshot in snapshots.items()
                },
                "project_prompt_locks": project_prompt_locks,
            },
        )
        if not reused:
            database.execute(
                """
                UPDATE projects
                SET album_special_requirements = ?,
                    desired_episode_count = ?,
                    episode_word_count_min = ?,
                    episode_word_count_max = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.album_special_requirements.strip(),
                    payload.desired_episode_count,
                    payload.episode_word_count_min,
                    payload.episode_word_count_max,
                    now_iso(),
                    project_id,
                ),
            )
        task_registry.spawn(
            run["id"],
            lambda: execute_project_generation_run(
                run["id"],
                mind_map_provider=snapshots["mind_map"].provider,
                album_outline_provider=snapshots["album_outline"].provider,
            ),
        )
        run = runs.get(run["id"])
        run["reused"] = reused
        return run
    except KeyError as error:
        raise not_found("项目") from error
    except (ValueError, ModelGenerationError) as error:
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
           content_framework, section_identifier, status, source_section_ids)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'outline_review', ?)
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
                episode.section_identifier.strip(),
                json.dumps(episode.source_section_ids, ensure_ascii=False),
            )
            for index, episode in enumerate(payload.episodes, start=1)
        ],
    )
    database.execute(
        "UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), project_id)
    )
    project = workflows.project_detail(project_id)
    project.update(model_routing.project_config(project_id))
    return project


@app.post("/api/projects/{project_id}/confirm")
def confirm_project(project_id: str) -> dict[str, Any]:
    try:
        project = workflows.confirm_project(project_id)
        project.update(model_routing.project_config(project_id))
        return project
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/projects/{project_id}/generate-all")
async def generate_all(project_id: str) -> dict[str, Any]:
    try:
        snapshots = model_routing.episode_stage_snapshots(project_id)
        batch = batches.create_batch(
            project_id,
            stage_model_ids={
                stage: snapshot.run_model_id
                for stage, snapshot in snapshots.items()
            },
            stage_prompt_locks=prompt_configuration.lock_episode_stages(
                project_id
            ),
        )
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if batch["status"] == "pending":
        task_registry.spawn(
            batch["id"],
            lambda: batches.run_batch(batch["id"]),
        )
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


@app.post("/api/episodes/{episode_id}/generate", status_code=202)
async def generate_episode(
    episode_id: str, payload: GeneratePayload
) -> dict[str, Any]:
    if payload.from_stage not in {"outline", "draft", "final"}:
        raise HTTPException(status_code=400, detail="from_stage 参数无效")
    episode = database.row(
        """
        SELECT episode.id, episode.project_id,
               project.episode_word_count_min,
               project.episode_word_count_max
        FROM episodes episode
        JOIN projects project ON project.id = episode.project_id
        WHERE episode.id = ?
        """,
        (episode_id,),
    )
    if not episode:
        raise not_found("声音")
    snapshots = model_routing.episode_stage_snapshots(episode["project_id"])
    stage_model_ids = {
        stage: snapshot.run_model_id for stage, snapshot in snapshots.items()
    }
    stage_prompt_locks = prompt_configuration.lock_episode_stages(
        episode["project_id"]
    )
    stage_order = ["outline", "draft", "final"]
    target_stages = stage_order[stage_order.index(payload.from_stage) :]
    source_links = database.row(
        """
        SELECT COUNT(*) AS count FROM episode_knowledge_items
        WHERE episode_id = ?
        """,
        (episode_id,),
    )
    if not source_links or not int(source_links["count"]):
        target_stages = ["match_episode_sources", *target_stages]
    run, reused = runs.create(
        scope_type="episode",
        scope_id=episode_id,
        stage=payload.from_stage,
        current_stage=payload.from_stage,
        progress_total=len(target_stages),
        metadata={
            "stage_model_ids": stage_model_ids,
            "stage_prompt_locks": stage_prompt_locks,
            "episode_word_count_min": episode["episode_word_count_min"],
            "episode_word_count_max": episode["episode_word_count_max"],
        },
    )
    task_registry.spawn(
        run["id"],
        lambda: execute_episode_run(
            run["id"],
            episode_id,
            payload.from_stage,
            stage_providers={
                stage: snapshot.provider
                for stage, snapshot in snapshots.items()
            },
            stage_prompt_locks=stage_prompt_locks,
        ),
    )
    run = runs.get(run["id"])
    run["reused"] = reused
    return run


def _decorate_run(run: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(run)
    if run["scope_type"] == "book_analysis_batch":
        book = database.row(
            "SELECT title FROM books WHERE id = ?", (run["scope_id"],)
        )
        decorated["scope_label"] = (book or {}).get("title") or "书籍拆解"
    elif run["scope_type"] in {"project_generation", "project_batch"}:
        project = database.row(
            "SELECT title FROM projects WHERE id = ?", (run["scope_id"],)
        )
        decorated["scope_label"] = (project or {}).get("title") or "内容项目"
    elif run["scope_type"] == "episode":
        episode = database.row(
            "SELECT title, project_id FROM episodes WHERE id = ?",
            (run["scope_id"],),
        )
        decorated["scope_label"] = (episode or {}).get("title") or "声音文稿"
        decorated["project_id"] = (episode or {}).get("project_id")
    elif run["scope_type"] == "chapter_analysis":
        section = database.row(
            "SELECT title, book_id FROM sections WHERE id = ?",
            (run["scope_id"],),
        )
        decorated["scope_label"] = (section or {}).get("title") or "章节拆书"
        decorated["book_id"] = (section or {}).get("book_id")
    return decorated


@app.get("/api/runs")
def list_runs(
    active: bool = False,
    scope_type: str | None = None,
    scope_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [
        _decorate_run(run)
        for run in runs.list(
            active_only=active,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
    ]


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    try:
        run = runs.get(run_id)
    except KeyError:
        raise not_found("运行记录")
    detail = _decorate_run(run)
    children = database.rows(
        """
        SELECT * FROM workflow_runs
        WHERE parent_run_id = ? ORDER BY position, created_at
        """,
        (run_id,),
    )
    if children:
        detail["children"] = [_decorate_run(child) for child in children]
    return detail


def _stage_output_content(
    run: dict[str, Any],
    stage: str,
    reference: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_type = reference.get("artifact_type")
    if artifact_type == "episode_artifact":
        artifact = database.row(
            """
            SELECT id, episode_id, stage, version, content, provider, model,
                   author_type, created_at
            FROM artifact_versions WHERE id = ?
            """,
            (reference.get("artifact_id"),),
        )
        return (
            {
                "stage": stage,
                "artifact_type": artifact_type,
                "label": {
                    "outline": "声音细纲",
                    "draft": "声音初稿",
                    "final": "声音终稿",
                }.get(artifact["stage"], artifact["stage"]),
                **artifact,
            }
            if artifact
            else None
        )
    if artifact_type == "chapter_analysis":
        analysis = database.row(
            """
            SELECT analysis.id, analysis.root_section_id, analysis.version,
                   analysis.status, analysis.rendered_markdown AS content,
                   analysis.provider, analysis.model, analysis.created_at,
                   section.title
            FROM chapter_analyses analysis
            JOIN sections section ON section.id = analysis.root_section_id
            WHERE analysis.id = ?
            """,
            (reference.get("analysis_id"),),
        )
        return (
            {
                "stage": stage,
                "artifact_type": artifact_type,
                "label": f"章节拆书 · {analysis['title']}",
                **analysis,
            }
            if analysis
            else None
        )
    if artifact_type == "mind_map":
        project = database.row(
            "SELECT book_ids FROM projects WHERE id = ?", (run["scope_id"],)
        )
        book_id = project["book_ids"][0] if project and project["book_ids"] else ""
        mind_map = database.row(
            """
            SELECT id, version, content, provider, model, created_at
            FROM mind_maps WHERE book_id = ? AND version = ?
            """,
            (book_id, reference.get("version")),
        )
        return (
            {
                "stage": stage,
                "artifact_type": artifact_type,
                "label": "思维导图",
                **mind_map,
            }
            if mind_map
            else None
        )
    if artifact_type == "album_planning_artifact":
        planning_type = reference.get("planning_artifact_type")
        module_key = str(reference.get("module_key") or "")
        artifact = database.row(
            """
            SELECT * FROM album_planning_artifacts
            WHERE run_id = ? AND artifact_type = ? AND module_key = ?
            """,
            (run["id"], planning_type, module_key),
        )
        if not artifact:
            return None
        labels = {
            "chapter_catalog": "轻量章节目录",
            "module_plan": "全书知识模块",
            "module_outline": "模块专辑大纲",
            "combined_outline": "合并 Markdown 大纲",
            "structured_outline": "结构化专辑大纲",
        }
        content = artifact["content"]
        if planning_type == "chapter_catalog":
            try:
                content = json.loads(content).get("catalog_markdown", content)
            except (json.JSONDecodeError, AttributeError):
                pass
        return {
            "stage": stage,
            "artifact_type": artifact_type,
            "planning_artifact_type": planning_type,
            "module_key": artifact["module_key"],
            "label": labels.get(planning_type, "专辑规划产物"),
            "content": content,
            "status": artifact["status"],
            "error_message": artifact["error_message"],
            "position": artifact["position"],
            "created_at": artifact["created_at"],
        }
    if artifact_type == "project_outline":
        episodes = database.rows(
            """
            SELECT position, title, content_type, content_framework,
                   section_identifier
            FROM episodes WHERE project_id = ? ORDER BY position
            """,
            (run["scope_id"],),
        )
        content = "\n\n".join(
            (
                f"第{episode['position']}集：{episode['title']}\n"
                f"{episode['content_framework']}\n"
                f"内容索引：{episode['section_identifier']}"
            )
            for episode in episodes
        )
        return {
            "stage": stage,
            "artifact_type": artifact_type,
            "label": "专辑大纲",
            "content": content,
            "episode_count": len(episodes),
        }
    if artifact_type == "book_knowledge":
        items = database.rows(
            """
            SELECT id, kind, title, body
            FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
            ORDER BY created_at, id
            """,
            (run["scope_id"],),
        )
        for item in items:
            item["source_content_indexes"] = [
                source["content_index"]
                for source in database.rows(
                    """
                    SELECT content_index FROM knowledge_item_sources
                    WHERE knowledge_item_id = ? ORDER BY source_order
                    """,
                    (item["id"],),
                )
            ]
        content = "\n\n".join(
            (
                f"## {item['kind']} · {item['title']}\n"
                f"{item['body']}\n"
                f"原文索引：{', '.join(item['source_content_indexes'])}"
            )
            for item in items
        )
        return {
            "stage": stage,
            "artifact_type": artifact_type,
            "label": "书籍知识资产",
            "content": content,
            "knowledge_count": len(items),
        }
    return None


@app.get("/api/runs/{run_id}/outputs")
def run_outputs(run_id: str) -> dict[str, Any]:
    try:
        parent = runs.get(run_id)
    except KeyError:
        raise not_found("运行记录")
    related = [parent, *database.rows(
        "SELECT * FROM workflow_runs WHERE parent_run_id = ? ORDER BY position",
        (run_id,),
    )]
    outputs: list[dict[str, Any]] = []
    for related_run in related:
        stages = (related_run.get("metadata_json") or {}).get("stages") or {}
        for stage, stage_data in stages.items():
            reference = (
                stage_data.get("output")
                if isinstance(stage_data, dict)
                else None
            )
            if not isinstance(reference, dict):
                continue
            materialized = _stage_output_content(
                related_run, str(stage), reference
            )
            if materialized:
                outputs.append(materialized)
    if parent["scope_type"] == "project_generation":
        existing_planning = {
            (
                output.get("planning_artifact_type"),
                output.get("module_key", ""),
            )
            for output in outputs
        }
        labels = {
            "chapter_catalog": "轻量章节目录",
            "module_plan": "全书知识模块",
            "module_outline": "模块专辑大纲",
            "combined_outline": "合并 Markdown 大纲",
            "structured_outline": "结构化专辑大纲",
        }
        for artifact in database.rows(
            """
            SELECT * FROM album_planning_artifacts
            WHERE run_id = ? ORDER BY position, created_at
            """,
            (run_id,),
        ):
            key = (artifact["artifact_type"], artifact["module_key"])
            if key in existing_planning:
                continue
            content = artifact["content"]
            if artifact["artifact_type"] == "chapter_catalog":
                try:
                    content = json.loads(content).get("catalog_markdown", content)
                except (json.JSONDecodeError, AttributeError):
                    pass
            outputs.append(
                {
                    "stage": artifact["artifact_type"],
                    "artifact_type": "album_planning_artifact",
                    "planning_artifact_type": artifact["artifact_type"],
                    "module_key": artifact["module_key"],
                    "label": labels.get(
                        artifact["artifact_type"], "专辑规划产物"
                    ),
                    "content": content,
                    "status": artifact["status"],
                    "error_message": artifact["error_message"],
                    "position": artifact["position"],
                    "created_at": artifact["created_at"],
                }
            )
    return {"run_id": run_id, "outputs": outputs}


@app.post("/api/runs/{run_id}/modules/{module_key}/retry", status_code=202)
async def retry_album_module(run_id: str, module_key: str) -> dict[str, Any]:
    try:
        run = runs.get(run_id)
    except KeyError:
        raise not_found("运行记录")
    if run["scope_type"] != "project_generation":
        raise HTTPException(status_code=400, detail="该任务不是专辑规划任务")
    artifact = database.row(
        """
        SELECT * FROM album_planning_artifacts
        WHERE run_id = ? AND artifact_type = 'module_outline'
          AND module_key = ?
        """,
        (run_id, module_key),
    )
    if not artifact:
        raise not_found("专辑模块")
    if artifact["status"] != "failed":
        raise HTTPException(status_code=400, detail="只有失败模块可以单独重跑")
    metadata = dict(run.get("metadata_json") or {})
    metadata["retry_module_key"] = module_key
    stages = dict(metadata.get("stages") or {})
    for stage in (
        "expand_album_modules",
        "structure_album_outline",
        "save_project_outline",
    ):
        stages.pop(stage, None)
    metadata["stages"] = stages
    now = now_iso()
    database.execute(
        """
        UPDATE album_planning_artifacts
        SET status = 'pending', error_message = '', updated_at = ?
        WHERE id = ?
        """,
        (now, artifact["id"]),
    )
    database.execute(
        """
        UPDATE workflow_runs
        SET status = 'pending', current_stage = 'expand_album_modules',
            message = '正在重跑失败模块', error_stage = '',
            finished_at = NULL, metadata_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (json.dumps(metadata, ensure_ascii=False), now, run_id),
    )
    task_registry.spawn(
        run_id, lambda: execute_project_generation_run(run_id)
    )
    return runs.get(run_id)


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        run = runs.get(run_id)
    except KeyError:
        raise not_found("运行记录")
    if run["scope_type"] == "project_batch":
        return batches.cancel_batch(run_id)
    if run["status"] in {"succeeded", "partial_failed", "failed", "cancelled"}:
        return run
    cancelled = runs.cancel(run_id)
    now = now_iso()
    database.execute(
        """
        UPDATE workflow_runs
        SET status = 'cancelled', message = '父任务已取消',
            finished_at = ?, heartbeat_at = ?, updated_at = ?
        WHERE parent_run_id = ? AND status IN ('pending', 'running')
        """,
        (now, now, now, run_id),
    )
    return cancelled


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


@app.get("/api/prompts/templates")
def list_prompt_templates(
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return prompt_configuration.list_templates(project_id)
    except KeyError as error:
        raise not_found("项目") from error


@app.get("/api/prompts/effective")
def effective_prompt(
    stage_key: str,
    project_id: str | None = None,
) -> dict[str, Any]:
    try:
        return prompt_configuration.effective(stage_key, project_id)
    except KeyError as error:
        raise not_found("项目或提示词") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/prompts/history")
def prompt_history(
    stage_key: str,
    scope: Literal["global", "project"],
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return prompt_configuration.history(stage_key, scope, project_id)
    except KeyError as error:
        raise not_found("项目或提示词") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/prompts/versions")
def create_prompt_version(payload: PromptVersionPayload) -> dict[str, Any]:
    try:
        return prompt_configuration.create_version(
            payload.stage_key,
            payload.scope,
            payload.user_template,
            payload.project_id,
        )
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/prompts/restore")
def restore_prompt_version(payload: PromptRestorePayload) -> dict[str, Any]:
    try:
        return prompt_configuration.restore(
            payload.stage_key,
            payload.scope,
            payload.version_id,
            payload.project_id,
        )
    except KeyError as error:
        raise not_found("项目或提示词") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/api/prompts/global/{stage_key}")
def reset_global_prompt(stage_key: str) -> dict[str, Any]:
    try:
        return prompt_configuration.reset_global(stage_key)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/api/projects/{project_id}/prompts/{stage_key}")
def clear_project_prompt(project_id: str, stage_key: str) -> dict[str, Any]:
    try:
        return prompt_configuration.clear_project(stage_key, project_id)
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/prompts/preview")
def preview_prompt(payload: PromptPreviewPayload) -> dict[str, Any]:
    try:
        values, book_type, input_materials = prompt_preview_values(
            payload.stage_key,
            payload.project_id,
            payload.episode_id,
            payload.module_key,
        )
        preview = prompt_configuration.preview(
            payload.stage_key,
            payload.user_template,
            values,
            project_id=payload.project_id,
            book_type=book_type,
        )
        preview["input_materials"] = input_materials
        return preview
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put("/api/settings/model")
def select_model(payload: ModelSelectionPayload) -> dict[str, Any]:
    try:
        model_manager.switch(payload.model_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail="无法保存本地模型设置") from error
    return model_manager.status()


@app.put("/api/books/{book_id}/model")
def select_book_analysis_model(
    book_id: str, payload: ProjectModelSelectionPayload
) -> dict[str, Any]:
    try:
        config = model_routing.update_book(book_id, payload.model_id)
    except KeyError as error:
        raise not_found("书籍") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return config


@app.put("/api/projects/{project_id}/models/{stage}")
def select_project_stage_model(
    project_id: str,
    stage: str,
    payload: ProjectModelSelectionPayload,
) -> dict[str, Any]:
    try:
        return model_routing.update_project(
            project_id, stage, payload.model_id
        )
    except KeyError as error:
        raise not_found("项目") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
