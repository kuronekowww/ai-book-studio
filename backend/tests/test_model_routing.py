import json

import pytest

from app.config import Settings
from app.db import Database, now_iso
from app.model_catalog import ModelManager
from app.model_routing import ModelRoutingService


def build_routing(tmp_path) -> tuple[Database, ModelManager, ModelRoutingService]:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    manager = ModelManager(
        Settings(
            data_dir=tmp_path,
            database_path=tmp_path / "studio.sqlite3",
            provider="demo",
            api_base="",
            api_key="",
            model="demo-model",
        )
    )
    routing = ModelRoutingService(database, manager)
    now = now_iso()
    database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES ('book-1', '测试书', '', 'book.md', 'analyzed', 'markdown',
                1, ?, ?)
        """,
        (now, now),
    )
    database.execute(
        """
        INSERT INTO projects
          (id, title, book_ids, status, created_at, updated_at)
        VALUES ('project-1', '测试专辑', ?, 'outline_review', ?, ?)
        """,
        (json.dumps(["book-1"]), now, now),
    )
    return database, manager, routing


def test_project_defaults_album_to_kimi_and_other_stages_follow_global(
    tmp_path,
) -> None:
    _, manager, routing = build_routing(tmp_path)

    initial = routing.project_config("project-1")

    assert initial["model_overrides"]["album_outline"] == "kimi-k3"
    assert initial["effective_models"]["album_outline"]["model_id"] == "kimi-k3"
    assert initial["effective_models"]["mind_map"]["follows_global"] is True
    manager.switch("glm-5.2")
    changed = routing.project_config("project-1")
    assert changed["effective_models"]["mind_map"]["model_id"] == "glm-5.2"
    assert changed["effective_models"]["album_outline"]["model_id"] == "kimi-k3"


def test_project_and_book_model_overrides_persist_independently(tmp_path) -> None:
    _, _, routing = build_routing(tmp_path)

    routing.update_book("book-1", "claude-sonnet-5")
    routing.update_project("project-1", "episode_draft", "deepseek-v4-pro")

    assert routing.book_config("book-1")["analysis_model_id"] == "claude-sonnet-5"
    project = routing.project_config("project-1")
    assert project["model_overrides"]["episode_draft"] == "deepseek-v4-pro"
    assert project["model_overrides"]["episode_final"] is None


def test_model_overrides_reject_unknown_stage_and_model(tmp_path) -> None:
    _, _, routing = build_routing(tmp_path)

    with pytest.raises(ValueError, match="未知项目模型环节"):
        routing.update_project("project-1", "json_repair", None)
    with pytest.raises(ValueError, match="未知模型"):
        routing.update_project("project-1", "mind_map", "missing-model")
