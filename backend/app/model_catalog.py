from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import Settings
from .providers import ModelProvider, build_provider


@dataclass(frozen=True)
class ModelPreset:
    id: str
    label: str
    model: str
    api_base: str
    provider: str = "anthropic"

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "model": self.model,
            "provider": self.provider,
        }


MODEL_PRESETS = (
    ModelPreset(
        id="claude-sonnet-4-6-wangsu-anthropic",
        label="Claude Sonnet 4.6",
        model="claude-sonnet-4-6-wangsu-anthropic",
        api_base=(
            "http://deepgate.ximalaya.local/"
            "claude-sonnet-4-6-wangsu-anthropic/api"
        ),
    ),
    ModelPreset(
        id="kimi-k3",
        label="Kimi K3",
        model="kimi-k3",
        api_base="http://deepgate.ximalaya.local/kimi-k3-universal/api/v1",
    ),
    ModelPreset(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        model="claude-sonnet-5",
        api_base=(
            "http://deepgate.ximalaya.local/claude-5-sonnet-universal/api"
        ),
    ),
    ModelPreset(
        id="glm-5.2",
        label="GLM 5.2",
        model="glm-5.2",
        api_base="http://deepgate.ximalaya.local/glm-5.2-universal/api/v1",
    ),
    ModelPreset(
        id="kimi-k2.6",
        label="Kimi K2.6",
        model="kimi-k2.6",
        api_base=(
            "http://deepgate.ximalaya.local/kimi-k2.6-anthropic/api"
        ),
    ),
    ModelPreset(
        id="deepseek-v4-pro",
        label="DeepSeek V4 Pro",
        model="deepseek-v4-pro",
        api_base=(
            "http://deepgate.ximalaya.local/deepseek-v4-pro-anthropic/api"
        ),
    ),
    ModelPreset(
        id="hy3",
        label="HY3",
        model="hy3",
        api_base="http://deepgate.ximalaya.local/hy3-universal/api/v1",
    ),
    ModelPreset(
        id="doubao-seed-2.0-pro",
        label="Doubao Seed 2.0 Pro",
        model="doubao-seed-2.0-pro",
        api_base=(
            "http://deepgate.ximalaya.local/"
            "doubao-seed-2.0-pro/api/v1"
        ),
        provider="openai-compatible",
    ),
)
MODEL_PRESETS_BY_ID = {preset.id: preset for preset in MODEL_PRESETS}
ENVIRONMENT_MODEL_ID = "__environment__"


@dataclass(frozen=True)
class ModelSnapshot:
    model_id: str | None
    provider: ModelProvider

    @property
    def run_model_id(self) -> str:
        return self.model_id or ENVIRONMENT_MODEL_ID


class ModelManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.selection_path = settings.data_dir / "model-settings.json"
        self._lock = threading.RLock()
        self._selected_model_id, self._selection_source = self._load_selection()

    def _load_selection(self) -> tuple[str | None, str]:
        try:
            data = json.loads(self.selection_path.read_text(encoding="utf-8"))
            model_id = data.get("model_id")
            if isinstance(model_id, str) and model_id in MODEL_PRESETS_BY_ID:
                return model_id, "local"
        except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
            pass
        environment_match = next(
            (
                preset.id
                for preset in MODEL_PRESETS
                if self.settings.provider == preset.provider
                and self.settings.model == preset.model
            ),
            None,
        )
        return environment_match, "environment"

    def _provider_for(self, model_id: str | None) -> ModelProvider:
        if model_id is None:
            return build_provider(self.settings)
        preset = MODEL_PRESETS_BY_ID.get(model_id)
        if not preset:
            raise ValueError("未知模型 ID")
        selected_settings = replace(
            self.settings,
            provider=preset.provider,
            api_base=preset.api_base,
            model=preset.model,
        )
        return build_provider(selected_settings)

    def snapshot(self, model_id: str | None = None) -> ModelSnapshot:
        with self._lock:
            selected_id = self._selected_model_id if model_id is None else model_id
            return ModelSnapshot(selected_id, self._provider_for(selected_id))

    def snapshot_for_run(self, model_id: str | None) -> ModelSnapshot:
        if model_id == ENVIRONMENT_MODEL_ID:
            return ModelSnapshot(None, build_provider(self.settings))
        return self.snapshot(model_id)

    def switch(self, model_id: str) -> ModelSnapshot:
        if model_id not in MODEL_PRESETS_BY_ID:
            raise ValueError("未知模型 ID")
        snapshot = ModelSnapshot(model_id, self._provider_for(model_id))
        payload = json.dumps({"model_id": model_id}, ensure_ascii=False, indent=2)
        temporary_path = self.selection_path.with_suffix(".json.tmp")
        with self._lock:
            self.selection_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                temporary_path.write_text(f"{payload}\n", encoding="utf-8")
                os.replace(temporary_path, self.selection_path)
            finally:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
            self._selected_model_id = model_id
            self._selection_source = "local"
        return snapshot

    def status(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "provider": snapshot.provider.name,
            "model": snapshot.provider.model,
            "current_model_id": snapshot.model_id,
            "selection_source": self._selection_source,
            "api_key_configured": bool(self.settings.api_key),
            "data_dir": str(self.settings.data_dir),
            "available_models": [
                preset.public_dict() for preset in MODEL_PRESETS
            ],
        }
