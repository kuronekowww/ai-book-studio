import json
from pathlib import Path

import pytest

from app.config import Settings
from app.model_catalog import MODEL_PRESETS, ModelManager


def settings(data_dir: Path, *, model: str = "demo-model") -> Settings:
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "studio.sqlite3",
        provider="demo" if model == "demo-model" else "anthropic",
        api_base="http://environment.local/api",
        api_key="test-key",
        model=model,
    )


def test_catalog_has_seven_unique_models() -> None:
    assert len(MODEL_PRESETS) == 7
    assert len({preset.id for preset in MODEL_PRESETS}) == 7
    assert {preset.id for preset in MODEL_PRESETS} >= {
        "kimi-k3",
        "claude-sonnet-5",
        "glm-5.2",
        "kimi-k2.6",
        "deepseek-v4-pro",
        "hy3",
    }


def test_manager_persists_selection_without_credentials(tmp_path) -> None:
    manager = ModelManager(settings(tmp_path))

    snapshot = manager.switch("kimi-k3")
    saved = manager.selection_path.read_text(encoding="utf-8")

    assert snapshot.model_id == "kimi-k3"
    assert snapshot.provider.model == "kimi-k3"
    assert "test-key" not in saved
    restarted = ModelManager(settings(tmp_path))
    assert restarted.status()["current_model_id"] == "kimi-k3"
    assert restarted.status()["selection_source"] == "local"


def test_manager_rejects_unknown_model_without_changing_selection(tmp_path) -> None:
    manager = ModelManager(settings(tmp_path))
    manager.switch("glm-5.2")

    with pytest.raises(ValueError, match="未知模型"):
        manager.switch("not-a-model")

    assert manager.status()["current_model_id"] == "glm-5.2"


def test_manager_ignores_damaged_selection_file(tmp_path) -> None:
    path = tmp_path / "model-settings.json"
    path.write_text("{broken", encoding="utf-8")

    manager = ModelManager(settings(tmp_path))

    assert manager.status()["current_model_id"] is None
    assert manager.status()["selection_source"] == "environment"
    assert manager.status()["model"] == "deterministic-editor-v1"


def test_environment_model_is_selected_before_local_choice_exists(tmp_path) -> None:
    manager = ModelManager(settings(tmp_path, model="claude-sonnet-5"))

    status = manager.status()

    assert status["current_model_id"] == "claude-sonnet-5"
    assert status["selection_source"] == "environment"
    assert json.dumps(status, ensure_ascii=False).find("test-key") == -1


def test_environment_snapshot_can_be_restored_after_global_switch(tmp_path) -> None:
    manager = ModelManager(settings(tmp_path))
    run_model_id = manager.snapshot().run_model_id
    manager.switch("kimi-k3")

    restored = manager.snapshot_for_run(run_model_id)

    assert restored.model_id is None
    assert restored.provider.model == "deterministic-editor-v1"
