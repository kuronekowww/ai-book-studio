from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .db import Database, now_iso


ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("succeeded", "partial_failed", "failed", "cancelled")


class RunService:
    """Small persistent state machine shared by every long-running workflow."""

    def __init__(self, database: Database):
        self.database = database
        self._metadata_lock = threading.RLock()

    def create(
        self,
        *,
        scope_type: str,
        scope_id: str,
        stage: str,
        current_stage: str,
        progress_total: int = 0,
        metadata: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        position: int = 0,
        reuse_active: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        if reuse_active:
            active = self.database.row(
                """
                SELECT * FROM workflow_runs
                WHERE scope_type = ? AND scope_id = ?
                  AND parent_run_id IS ?
                  AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (scope_type, scope_id, parent_run_id),
            )
            if active:
                return active, True
        run_id = uuid.uuid4().hex
        now = now_iso()
        self.database.execute(
            """
            INSERT INTO workflow_runs
              (id, scope_type, scope_id, stage, current_stage, status, message,
               parent_run_id, error_stage, position, progress_current,
               progress_total, started_at, finished_at, heartbeat_at, attempt,
               metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', '', ?, '', ?, 0, ?, NULL, NULL,
                    ?, 0, ?, ?, ?)
            """,
            (
                run_id,
                scope_type,
                scope_id,
                stage,
                current_stage,
                parent_run_id,
                position,
                max(0, progress_total),
                now,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return self.get(run_id), False

    def get(self, run_id: str) -> dict[str, Any]:
        run = self.database.row(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        )
        if not run:
            raise KeyError(run_id)
        return run

    def list(
        self,
        *,
        active_only: bool = False,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if active_only:
            conditions.append("status IN ('pending', 'running')")
        if scope_type:
            conditions.append("scope_type = ?")
            params.append(scope_type)
        if scope_id:
            conditions.append("scope_id = ?")
            params.append(scope_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.database.rows(
            f"""
            SELECT * FROM workflow_runs
            {where}
            ORDER BY created_at DESC LIMIT ?
            """,
            (*params, max(1, min(limit, 500))),
        )

    def mark_running(
        self,
        run_id: str,
        *,
        current_stage: str | None = None,
        message: str = "",
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        now = now_iso()
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'running',
                current_stage = COALESCE(?, current_stage),
                message = ?, error_stage = '',
                started_at = COALESCE(started_at, ?),
                heartbeat_at = ?, finished_at = NULL,
                attempt = attempt + ?,
                updated_at = ?
            WHERE id = ? AND status != 'cancelled'
            """,
            (
                current_stage,
                message,
                now,
                now,
                int(increment_attempt),
                now,
                run_id,
            ),
        )
        return self.get(run_id)

    def set_progress(
        self,
        run_id: str,
        *,
        current: int | None = None,
        total: int | None = None,
        current_stage: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        run = self.get(run_id)
        now = now_iso()
        next_current = (
            max(0, current) if current is not None else run["progress_current"]
        )
        next_total = max(0, total) if total is not None else run["progress_total"]
        if next_total:
            next_current = min(next_current, next_total)
        self.database.execute(
            """
            UPDATE workflow_runs
            SET progress_current = ?, progress_total = ?,
                current_stage = COALESCE(?, current_stage),
                message = COALESCE(?, message),
                heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_current,
                next_total,
                current_stage,
                message,
                now,
                now,
                run_id,
            ),
        )
        return self.get(run_id)

    def set_stage(
        self,
        run_id: str,
        stage: str,
        status: str,
        *,
        message: str | None = None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._metadata_lock:
            run = self.get(run_id)
            metadata = dict(run.get("metadata_json") or {})
            stages = dict(metadata.get("stages") or {})
            stage_data = dict(stages.get(stage) or {})
            stage_data.update({"status": status, "updated_at": now_iso()})
            if message is not None:
                stage_data["message"] = message
            if output is not None:
                stage_data["output"] = output
            stages[stage] = stage_data
            metadata["stages"] = stages
            now = now_iso()
            self.database.execute(
                """
                UPDATE workflow_runs
                SET current_stage = ?, message = COALESCE(?, message),
                    metadata_json = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    stage,
                    message,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                    run_id,
                ),
            )
        return self.get(run_id)

    def merge_metadata(
        self, run_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        with self._metadata_lock:
            run = self.get(run_id)
            metadata = dict(run.get("metadata_json") or {})
            metadata.update(values)
            now = now_iso()
            self.database.execute(
                """
                UPDATE workflow_runs
                SET metadata_json = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                    run_id,
                ),
            )
        return self.get(run_id)

    def stage_status(self, run_id: str, stage: str) -> str:
        run = self.get(run_id)
        stages = (run.get("metadata_json") or {}).get("stages") or {}
        data = stages.get(stage) or {}
        return str(data.get("status") or "")

    def finish(
        self,
        run_id: str,
        *,
        status: str = "succeeded",
        message: str = "",
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"无效终态：{status}")
        run = self.get(run_id)
        now = now_iso()
        current = run["progress_current"]
        if status == "succeeded" and run["progress_total"]:
            current = run["progress_total"]
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = ?, message = ?, progress_current = ?,
                finished_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, message, current, now, now, now, run_id),
        )
        return self.get(run_id)

    def fail(
        self,
        run_id: str,
        error: Exception | str,
        *,
        error_stage: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'failed', message = ?, error_stage = ?,
                finished_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (str(error)[:500], error_stage, now, now, now, run_id),
        )
        return self.get(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return run
        return self.finish(run_id, status="cancelled", message="用户已取消")

    def reset_for_resume(self, run_id: str) -> dict[str, Any]:
        now = now_iso()
        self.database.execute(
            """
            UPDATE workflow_runs
            SET status = 'pending', message = '应用重启后恢复任务',
                finished_at = NULL, heartbeat_at = ?, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (now, now, run_id),
        )
        return self.get(run_id)


class TaskRegistry:
    """Own background coroutine references and convert escaped errors to run failures."""

    def __init__(self, runs: RunService):
        self.runs = runs
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    def spawn(
        self,
        run_id: str,
        coroutine_factory: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task[Any]:
        existing = self.tasks.get(run_id)
        if existing and not existing.done():
            return existing
        task = asyncio.create_task(self._guard(run_id, coroutine_factory))
        self.tasks[run_id] = task

        def discard(completed: asyncio.Task[Any]) -> None:
            if self.tasks.get(run_id) is completed:
                self.tasks.pop(run_id, None)

        task.add_done_callback(discard)
        return task

    async def _guard(
        self,
        run_id: str,
        coroutine_factory: Callable[[], Awaitable[Any]],
    ) -> None:
        try:
            await coroutine_factory()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            try:
                current = self.runs.get(run_id)
            except KeyError:
                return
            if current["status"] in ACTIVE_STATUSES:
                self.runs.fail(
                    run_id,
                    error,
                    error_stage=current.get("current_stage") or current.get("stage") or "",
                )

    async def shutdown(self) -> None:
        pending = [task for task in self.tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.tasks.clear()
