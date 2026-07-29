from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable

from .db import Database, now_iso
from .providers import ModelProvider
from .runs import RunService
from .workflows import StageGenerationError, WorkflowService


ACTIVE_STATUSES = ("pending", "running")


class BatchService:
    def __init__(
        self,
        database: Database,
        workflows: WorkflowService,
        concurrency: int = 5,
        provider_resolver: Callable[[str | None], ModelProvider] | None = None,
    ):
        self.database = database
        self.workflows = workflows
        self.concurrency = concurrency
        self.provider_resolver = provider_resolver
        self.runs = RunService(database)

    def create_batch(
        self,
        project_id: str,
        model_id: str | None = None,
        *,
        stage_model_ids: dict[str, str] | None = None,
        stage_prompt_locks: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        if not project:
            raise KeyError(project_id)
        if project["status"] == "outline_review":
            raise ValueError("请先确认专辑大纲")

        active = self.database.row(
            """
            SELECT * FROM workflow_runs
            WHERE scope_type = 'project_batch'
              AND scope_id = ?
              AND status IN ('pending', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id,),
        )
        if active:
            return self.batch_detail(active["id"])

        episodes = self.database.rows(
            """
            SELECT e.*
            FROM episodes e
            WHERE e.project_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM artifact_versions a
                WHERE a.episode_id = e.id AND a.stage = 'final'
              )
              AND NOT EXISTS (
                SELECT 1 FROM workflow_runs r
                WHERE r.scope_type = 'episode'
                  AND r.scope_id = e.id
                  AND r.status IN ('pending', 'running')
              )
            ORDER BY e.position
            """,
            (project_id,),
        )
        if not episodes:
            raise ValueError("所有声音都已有终稿，无需重复生成")

        batch_id = uuid.uuid4().hex
        created_at = now_iso()
        locked_stage_model_ids = stage_model_ids or (
            {
                "outline": model_id,
                "draft": model_id,
                "final": model_id,
            }
            if model_id
            else {}
        )
        metadata = {
            "total": len(episodes),
            "completed": 0,
            "failed": 0,
            "concurrency": self.concurrency,
            "model_id": model_id,
            "stage_model_ids": locked_stage_model_ids,
            "stage_prompt_locks": stage_prompt_locks or {},
        }
        self.database.execute(
            """
            INSERT INTO workflow_runs
              (id, scope_type, scope_id, stage, current_stage, status, message,
               parent_run_id, error_stage, position, metadata_json,
               progress_current, progress_total, heartbeat_at,
               created_at, updated_at)
            VALUES (?, 'project_batch', ?, 'full', 'episode_generation',
                    'pending', '', NULL, '', 0, ?, 0, ?, ?, ?, ?)
            """,
            (
                batch_id,
                project_id,
                json.dumps(metadata, ensure_ascii=False),
                len(episodes),
                created_at,
                created_at,
                created_at,
            ),
        )

        child_rows: list[tuple[Any, ...]] = []
        for episode in episodes:
            from_stage = self._first_missing_stage(episode["id"])
            child_rows.append(
                (
                    uuid.uuid4().hex,
                    episode["id"],
                    from_stage,
                    batch_id,
                    episode["position"],
                    len(("outline", "draft", "final")[
                        ("outline", "draft", "final").index(from_stage) :
                    ]),
                    created_at,
                    created_at,
                )
            )
        self.database.executemany(
            """
            INSERT INTO workflow_runs
              (id, scope_type, scope_id, stage, current_stage, status, message,
               parent_run_id, error_stage, position, metadata_json,
               progress_current, progress_total, heartbeat_at,
               created_at, updated_at)
            VALUES (?, 'episode', ?, ?, ?, 'pending', '',
                    ?, '', ?, '{}', 0, ?, ?, ?, ?)
            """,
            [
                (
                    child_id,
                    episode_id,
                    from_stage,
                    from_stage,
                    parent_id,
                    position,
                    total,
                    created_at,
                    created_at,
                    updated_at,
                )
                for (
                    child_id,
                    episode_id,
                    from_stage,
                    parent_id,
                    position,
                    total,
                    created_at,
                    updated_at,
                ) in child_rows
            ],
        )
        self.database.execute(
            "UPDATE projects SET status = 'producing', updated_at = ? WHERE id = ?",
            (now_iso(), project_id),
        )
        return self.batch_detail(batch_id)

    def _first_missing_stage(self, episode_id: str) -> str:
        for stage in ("outline", "draft", "final"):
            if not self.workflows.latest_artifact(episode_id, stage):
                return stage
        return "final"

    async def run_batch(
        self,
        batch_id: str,
        provider: ModelProvider | None = None,
        *,
        stage_providers: dict[str, ModelProvider] | None = None,
        stage_prompt_locks: dict[str, dict[str, str]] | None = None,
    ) -> None:
        batch = self.database.row(
            "SELECT * FROM workflow_runs WHERE id = ?", (batch_id,)
        )
        if not batch or batch["scope_type"] != "project_batch":
            return
        if batch["status"] == "cancelled":
            return
        task_provider = provider
        locked_stage_providers = stage_providers
        locked_prompt_versions = stage_prompt_locks
        if locked_prompt_versions is None:
            metadata_locks = batch["metadata_json"].get("stage_prompt_locks")
            if isinstance(metadata_locks, dict):
                locked_prompt_versions = metadata_locks
        if locked_stage_providers is None and self.provider_resolver:
            stage_model_ids = batch["metadata_json"].get("stage_model_ids")
            if isinstance(stage_model_ids, dict) and stage_model_ids:
                locked_stage_providers = {
                    stage: self.provider_resolver(model_id)
                    for stage, model_id in stage_model_ids.items()
                    if stage in {"outline", "draft", "final"}
                    and isinstance(model_id, str)
                }
            elif batch["metadata_json"].get("model_id"):
                task_provider = self.provider_resolver(
                    batch["metadata_json"].get("model_id")
                )

        self.runs.mark_running(
            batch_id,
            current_stage="episode_generation",
            message="正在生产声音终稿",
            increment_attempt=True,
        )
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'pending', message = '应用重启后恢复任务', updated_at = ?
            WHERE parent_run_id = ? AND status = 'running'
            """,
            (now_iso(), batch_id),
        )
        self.database.execute(
            "UPDATE projects SET status = 'producing', updated_at = ? WHERE id = ?",
            (now_iso(), batch["scope_id"]),
        )

        children = self.database.rows(
            """
            SELECT * FROM workflow_runs
            WHERE parent_run_id = ? AND status = 'pending'
            ORDER BY position
            """,
            (batch_id,),
        )
        semaphore = asyncio.Semaphore(self.concurrency)
        prompt_kwargs = (
            {"stage_prompt_locks": locked_prompt_versions}
            if locked_prompt_versions
            else {}
        )

        async def worker(child: dict[str, Any]) -> None:
            async with semaphore:
                current_batch = self.database.row(
                    "SELECT status FROM workflow_runs WHERE id = ?", (batch_id,)
                )
                if not current_batch or current_batch["status"] == "cancelled":
                    self._mark_child_cancelled(child["id"])
                    return
                self.database.execute(
                    """
                    UPDATE workflow_runs
                    SET status = 'running', message = '正在生成', error_stage = '',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now_iso(), child["id"]),
                )
                try:
                    stage_order = ["outline", "draft", "final"]
                    target_stages = stage_order[stage_order.index(child["stage"]) :]
                    completed_stages = {
                        stage
                        for stage in target_stages
                        if self.runs.stage_status(child["id"], stage)
                        == "succeeded"
                    }
                    self.runs.mark_running(
                        child["id"],
                        current_stage=next(
                            (
                                stage
                                for stage in target_stages
                                if stage not in completed_stages
                            ),
                            target_stages[-1],
                        ),
                        message="正在生成",
                        increment_attempt=True,
                    )
                    self.runs.set_progress(
                        child["id"],
                        current=len(completed_stages),
                        total=len(target_stages),
                    )

                    def report_stage(
                        stage: str,
                        status: str,
                        output: dict[str, Any] | None,
                        message: str | None,
                    ) -> None:
                        self.runs.set_stage(
                            child["id"],
                            stage,
                            status,
                            message=message,
                            output=output,
                        )
                        completed = sum(
                            self.runs.stage_status(child["id"], item)
                            == "succeeded"
                            for item in target_stages
                        )
                        self.runs.set_progress(
                            child["id"],
                            current=completed,
                            total=len(target_stages),
                            current_stage=stage,
                            message=message,
                        )

                    def cancelled() -> bool:
                        current_parent = self.database.row(
                            "SELECT status FROM workflow_runs WHERE id = ?",
                            (batch_id,),
                        )
                        return (
                            not current_parent
                            or current_parent["status"] == "cancelled"
                        )

                    workflow_progress_kwargs = {
                        "progress_callback": report_stage,
                        "completed_stages": completed_stages,
                        "cancelled": cancelled,
                    }
                    if locked_stage_providers:
                        await self.workflows.generate_episode(
                            child["scope_id"],
                            child["stage"],
                            stage_providers=locked_stage_providers,
                            **prompt_kwargs,
                            **workflow_progress_kwargs,
                        )
                    elif task_provider is None:
                        await self.workflows.generate_episode(
                            child["scope_id"],
                            child["stage"],
                            **prompt_kwargs,
                            **workflow_progress_kwargs,
                        )
                    else:
                        await self.workflows.generate_episode(
                            child["scope_id"],
                            child["stage"],
                            provider=task_provider,
                            **prompt_kwargs,
                            **workflow_progress_kwargs,
                        )
                    if cancelled():
                        self._mark_child_cancelled(child["id"])
                    else:
                        self.runs.finish(
                            child["id"], message="声音终稿已生成"
                        )
                except StageGenerationError as error:
                    self._mark_child_failed(child["id"], error.stage, str(error))
                except Exception as error:
                    self.database.execute(
                        "UPDATE episodes SET status = 'failed' WHERE id = ?",
                        (child["scope_id"],),
                    )
                    self._mark_child_failed(child["id"], child["stage"], str(error))
                finally:
                    self._update_parent_progress(batch_id)

        await asyncio.gather(*(worker(child) for child in children))
        self._finish_batch(batch_id, batch["scope_id"])

    def _mark_child_failed(
        self, run_id: str, stage: str, message: str
    ) -> None:
        self.runs.fail(run_id, message, error_stage=stage)

    def _mark_child_cancelled(self, run_id: str) -> None:
        self.runs.cancel(run_id)

    def _update_parent_progress(self, batch_id: str) -> None:
        counts = self.database.row(
            """
            SELECT COUNT(*) AS total,
              SUM(
                CASE WHEN status IN
                  ('succeeded', 'failed', 'partial_failed', 'cancelled')
                THEN 1 ELSE 0 END
              ) AS completed
            FROM workflow_runs WHERE parent_run_id = ?
            """,
            (batch_id,),
        ) or {"total": 0, "completed": 0}
        self.runs.set_progress(
            batch_id,
            current=int(counts["completed"] or 0),
            total=int(counts["total"] or 0),
            current_stage="episode_generation",
        )

    def _finish_batch(self, batch_id: str, project_id: str) -> None:
        batch = self.database.row(
            "SELECT metadata_json FROM workflow_runs WHERE id = ?", (batch_id,)
        )
        counts = self.database.row(
            """
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS completed,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled
            FROM workflow_runs WHERE parent_run_id = ?
            """,
            (batch_id,),
        ) or {"total": 0, "completed": 0, "failed": 0, "cancelled": 0}
        metadata = {
            **counts,
            "concurrency": self.concurrency,
            "model_id": (batch or {}).get("metadata_json", {}).get("model_id"),
            "stage_model_ids": (
                (batch or {}).get("metadata_json", {}).get("stage_model_ids", {})
            ),
            "stage_prompt_locks": (
                (batch or {}).get("metadata_json", {}).get(
                    "stage_prompt_locks", {}
                )
            ),
        }
        if counts["failed"]:
            batch_status = "partial_failed"
            project_status = "partial_failed"
            message = f"{counts['completed']} 条完成，{counts['failed']} 条失败"
        elif counts["cancelled"]:
            batch_status = "cancelled"
            project_status = "ready"
            message = "批次已取消"
        else:
            batch_status = "succeeded"
            project_status = "review"
            message = f"{counts['completed']} 条声音终稿已生成"
        self.database.execute(
            "UPDATE workflow_runs SET metadata_json = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), batch_id),
        )
        self.runs.finish(batch_id, status=batch_status, message=message)
        self.database.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
            (project_status, now_iso(), project_id),
        )

    def batch_detail(self, batch_id: str) -> dict[str, Any]:
        batch = self.database.row(
            "SELECT * FROM workflow_runs WHERE id = ?", (batch_id,)
        )
        if not batch or batch["scope_type"] != "project_batch":
            raise KeyError(batch_id)
        children = self.database.rows(
            """
            SELECT r.*, e.title AS episode_title, e.status AS episode_status
            FROM workflow_runs r
            JOIN episodes e ON e.id = r.scope_id
            WHERE r.parent_run_id = ?
            ORDER BY r.position
            """,
            (batch_id,),
        )
        counts = {
            "total": len(children),
            "completed": sum(child["status"] == "succeeded" for child in children),
            "failed": sum(child["status"] == "failed" for child in children),
            "running": sum(child["status"] == "running" for child in children),
            "pending": sum(child["status"] == "pending" for child in children),
        }
        batch["children"] = children
        batch["summary"] = {**counts, "concurrency": self.concurrency}
        return batch

    def latest_batch(self, project_id: str) -> dict[str, Any] | None:
        batch = self.database.row(
            """
            SELECT * FROM workflow_runs
            WHERE scope_type = 'project_batch' AND scope_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id,),
        )
        return self.batch_detail(batch["id"]) if batch else None

    def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.database.row(
            "SELECT * FROM workflow_runs WHERE id = ?", (batch_id,)
        )
        if not batch or batch["scope_type"] != "project_batch":
            raise KeyError(batch_id)
        if batch["status"] not in ACTIVE_STATUSES:
            return self.batch_detail(batch_id)
        self.runs.cancel(batch_id)
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'cancelled', message = '批次已取消',
                finished_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE parent_run_id = ? AND status = 'pending'
            """,
            (now_iso(), now_iso(), now_iso(), batch_id),
        )
        return self.batch_detail(batch_id)

    def reconcile_episode_success(self, episode_id: str) -> None:
        child = self.database.row(
            """
            SELECT * FROM workflow_runs
            WHERE scope_type = 'episode'
              AND scope_id = ?
              AND parent_run_id IS NOT NULL
              AND status = 'failed'
            ORDER BY created_at DESC LIMIT 1
            """,
            (episode_id,),
        )
        if not child:
            return
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'succeeded', message = '单条重跑已完成',
                error_stage = '', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), child["id"]),
        )
        parent = self.database.row(
            "SELECT * FROM workflow_runs WHERE id = ?", (child["parent_run_id"],)
        )
        if parent:
            self._finish_batch(parent["id"], parent["scope_id"])
