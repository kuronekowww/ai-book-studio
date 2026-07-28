import importlib
import json

from fastapi.testclient import TestClient

from app.db import now_iso


def test_settings_api_lists_and_switches_models_without_exposing_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_BOOK_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_BOOK_STUDIO_PROVIDER", "demo")
    monkeypatch.setenv("AI_BOOK_STUDIO_API_KEY", "secret-test-key")
    monkeypatch.setenv("AI_BOOK_STUDIO_MODEL", "demo-model")

    main = importlib.import_module("app.main")
    main = importlib.reload(main)
    client = TestClient(main.app)

    initial = client.get("/api/settings/status")
    assert initial.status_code == 200
    initial_data = initial.json()
    assert len(initial_data["available_models"]) == 8
    assert any(
        item["id"] == "doubao-seed-2.0-pro"
        and item["provider"] == "openai-compatible"
        for item in initial_data["available_models"]
    )
    assert "secret-test-key" not in initial.text

    now = now_iso()
    main.database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES ('book-model-api', '模型测试书', '', 'test.md', 'analyzed',
                'markdown', 1, ?, ?)
        """,
        (now, now),
    )
    created = client.post(
        "/api/projects",
        json={"title": "模型测试专辑", "book_id": "book-model-api"},
    )
    assert created.status_code == 200
    project = created.json()
    assert project["model_overrides"]["album_outline"] == "kimi-k3"
    assert project["effective_models"]["mind_map"]["follows_global"] is True
    assert project["effective_models"]["album_outline"]["model_id"] == "kimi-k3"

    project_model = client.put(
        f"/api/projects/{project['id']}/models/episode_draft",
        json={"model_id": "glm-5.2"},
    )
    assert project_model.status_code == 200
    assert (
        project_model.json()["model_overrides"]["episode_draft"]
        == "glm-5.2"
    )
    follow_global = client.put(
        f"/api/projects/{project['id']}/models/episode_draft",
        json={"model_id": None},
    )
    assert follow_global.status_code == 200
    assert follow_global.json()["model_overrides"]["episode_draft"] is None

    book_model = client.put(
        "/api/books/book-model-api/model",
        json={"model_id": "claude-sonnet-5"},
    )
    assert book_model.status_code == 200
    assert book_model.json()["analysis_model_id"] == "claude-sonnet-5"
    assert "secret-test-key" not in json.dumps(book_model.json())

    switched = client.put(
        "/api/settings/model", json={"model_id": "kimi-k3"}
    )
    assert switched.status_code == 200
    assert switched.json()["current_model_id"] == "kimi-k3"
    assert switched.json()["model"] == "kimi-k3"
    assert "secret-test-key" not in switched.text

    rejected = client.put(
        "/api/settings/model", json={"model_id": "unknown-model"}
    )
    assert rejected.status_code == 400
    assert client.get("/api/settings/status").json()["current_model_id"] == "kimi-k3"
