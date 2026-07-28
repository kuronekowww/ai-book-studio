from __future__ import annotations

import json
from typing import Any

from .db import Database, now_iso
from .model_catalog import (
    ENVIRONMENT_MODEL_ID,
    MODEL_PRESETS_BY_ID,
    ModelManager,
    ModelSnapshot,
)


PROJECT_MODEL_STAGES = (
    "mind_map",
    "album_outline",
    "episode_outline",
    "episode_draft",
    "episode_final",
)
EPISODE_STAGE_MODEL_KEYS = {
    "outline": "episode_outline",
    "draft": "episode_draft",
    "final": "episode_final",
}
DEFAULT_PROJECT_MODEL_OVERRIDES: dict[str, str | None] = {
    "mind_map": None,
    "album_outline": "kimi-k3",
    "episode_outline": None,
    "episode_draft": None,
    "episode_final": None,
}


class ModelRoutingService:
    def __init__(self, database: Database, manager: ModelManager):
        self.database = database
        self.manager = manager

    @staticmethod
    def normalize_project_overrides(
        raw: Any,
    ) -> dict[str, str | None]:
        result = dict(DEFAULT_PROJECT_MODEL_OVERRIDES)
        if isinstance(raw, dict):
            for stage in PROJECT_MODEL_STAGES:
                value = raw.get(stage)
                if value is None or isinstance(value, str):
                    result[stage] = value
        return result

    def _validate_model_id(self, model_id: str | None) -> None:
        if model_id is None:
            return
        if model_id not in MODEL_PRESETS_BY_ID:
            raise ValueError("未知模型 ID")

    def project_snapshot(self, project_id: str, stage: str) -> ModelSnapshot:
        if stage not in PROJECT_MODEL_STAGES:
            raise ValueError("未知项目模型环节")
        project = self.database.row(
            "SELECT model_overrides_json FROM projects WHERE id = ?",
            (project_id,),
        )
        if not project:
            raise KeyError(project_id)
        overrides = self.normalize_project_overrides(
            project.get("model_overrides_json")
        )
        model_id = overrides[stage]
        self._validate_model_id(model_id)
        return self.manager.snapshot(model_id)

    def project_stage_snapshots(
        self, project_id: str, stages: tuple[str, ...] = PROJECT_MODEL_STAGES
    ) -> dict[str, ModelSnapshot]:
        return {
            stage: self.project_snapshot(project_id, stage)
            for stage in stages
        }

    def episode_stage_snapshots(
        self, project_id: str
    ) -> dict[str, ModelSnapshot]:
        return {
            stage: self.project_snapshot(project_id, model_key)
            for stage, model_key in EPISODE_STAGE_MODEL_KEYS.items()
        }

    def book_snapshot(self, book_id: str) -> ModelSnapshot:
        book = self.database.row(
            "SELECT analysis_model_id FROM books WHERE id = ?", (book_id,)
        )
        if not book:
            raise KeyError(book_id)
        model_id = book.get("analysis_model_id")
        self._validate_model_id(model_id)
        return self.manager.snapshot(model_id)

    def update_project(
        self, project_id: str, stage: str, model_id: str | None
    ) -> dict[str, Any]:
        if stage not in PROJECT_MODEL_STAGES:
            raise ValueError("未知项目模型环节")
        self._validate_model_id(model_id)
        project = self.database.row(
            "SELECT model_overrides_json FROM projects WHERE id = ?",
            (project_id,),
        )
        if not project:
            raise KeyError(project_id)
        overrides = self.normalize_project_overrides(
            project.get("model_overrides_json")
        )
        overrides[stage] = model_id
        self.database.execute(
            """
            UPDATE projects
            SET model_overrides_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(overrides, ensure_ascii=False), now_iso(), project_id),
        )
        return self.project_config(project_id)

    def update_book(
        self, book_id: str, model_id: str | None
    ) -> dict[str, Any]:
        self._validate_model_id(model_id)
        if not self.database.row("SELECT id FROM books WHERE id = ?", (book_id,)):
            raise KeyError(book_id)
        self.database.execute(
            "UPDATE books SET analysis_model_id = ?, updated_at = ? WHERE id = ?",
            (model_id, now_iso(), book_id),
        )
        return self.book_config(book_id)

    def _snapshot_info(self, snapshot: ModelSnapshot) -> dict[str, Any]:
        model_id = snapshot.run_model_id
        preset = MODEL_PRESETS_BY_ID.get(model_id)
        return {
            "model_id": model_id,
            "label": preset.label if preset else snapshot.provider.model,
            "model": snapshot.provider.model,
            "provider": snapshot.provider.name,
            "follows_global": False,
        }

    def project_config(self, project_id: str) -> dict[str, Any]:
        project = self.database.row(
            "SELECT model_overrides_json FROM projects WHERE id = ?",
            (project_id,),
        )
        if not project:
            raise KeyError(project_id)
        overrides = self.normalize_project_overrides(
            project.get("model_overrides_json")
        )
        effective: dict[str, dict[str, Any]] = {}
        global_snapshot = self.manager.snapshot()
        for stage in PROJECT_MODEL_STAGES:
            model_id = overrides[stage]
            snapshot = (
                global_snapshot
                if model_id is None
                else self.manager.snapshot(model_id)
            )
            effective[stage] = {
                **self._snapshot_info(snapshot),
                "follows_global": model_id is None,
            }
        return {
            "model_overrides": overrides,
            "effective_models": effective,
            "global_model": {
                **self._snapshot_info(global_snapshot),
                "follows_global": True,
            },
        }

    def book_config(self, book_id: str) -> dict[str, Any]:
        book = self.database.row(
            "SELECT analysis_model_id FROM books WHERE id = ?", (book_id,)
        )
        if not book:
            raise KeyError(book_id)
        model_id = book.get("analysis_model_id")
        snapshot = (
            self.manager.snapshot()
            if model_id is None
            else self.manager.snapshot(model_id)
        )
        return {
            "analysis_model_id": model_id,
            "effective_analysis_model": {
                **self._snapshot_info(snapshot),
                "follows_global": model_id is None,
            },
        }

    def restore_stage_snapshots(
        self, model_ids: dict[str, Any]
    ) -> dict[str, ModelSnapshot]:
        snapshots: dict[str, ModelSnapshot] = {}
        for stage in ("outline", "draft", "final"):
            model_id = model_ids.get(stage)
            if not isinstance(model_id, str):
                raise ValueError(f"运行记录缺少 {stage} 模型快照")
            if (
                model_id != ENVIRONMENT_MODEL_ID
                and model_id not in MODEL_PRESETS_BY_ID
            ):
                raise ValueError(f"运行记录包含未知模型：{model_id}")
            snapshots[stage] = self.manager.snapshot_for_run(model_id)
        return snapshots
