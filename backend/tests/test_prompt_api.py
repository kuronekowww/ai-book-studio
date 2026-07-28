import importlib

from fastapi.testclient import TestClient

from app.db import now_iso


def test_prompt_api_versions_inheritance_validation_and_preview(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_BOOK_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_BOOK_STUDIO_PROVIDER", "demo")
    monkeypatch.setenv("AI_BOOK_STUDIO_MODEL", "demo-model")

    main = importlib.import_module("app.main")
    main = importlib.reload(main)
    client = TestClient(main.app)
    now = now_iso()
    main.database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES ('prompt-book', '提示词接口测试书', '作者', 'test.md',
                'analyzed', 'markdown', 1, ?, ?)
        """,
        (now, now),
    )
    created = client.post(
        "/api/projects",
        json={"title": "提示词接口测试项目", "book_id": "prompt-book"},
    )
    assert created.status_code == 200
    project_id = created.json()["id"]

    templates = client.get("/api/prompts/templates")
    assert templates.status_code == 200
    assert len(templates.json()) == 4
    assert all(item["source_scope"] == "system" for item in templates.json())

    invalid = client.post(
        "/api/prompts/versions",
        json={
            "stage_key": "episode_draft",
            "scope": "global",
            "user_template": "缺少原文 {{episode_outline}}",
        },
    )
    assert invalid.status_code == 400
    assert "source_text" in invalid.json()["detail"]

    global_version = client.post(
        "/api/prompts/versions",
        json={
            "stage_key": "episode_draft",
            "scope": "global",
            "user_template": "全局模板\n{{episode_outline}}\n{{source_text}}",
        },
    )
    assert global_version.status_code == 200
    assert global_version.json()["source_label"] == "全局 v1"

    inherited = client.get(
        "/api/prompts/templates", params={"project_id": project_id}
    )
    draft = next(
        item
        for item in inherited.json()
        if item["stage_key"] == "episode_draft"
    )
    assert draft["source_label"] == "全局 v1"

    project_version = client.post(
        "/api/prompts/versions",
        json={
            "stage_key": "episode_draft",
            "scope": "project",
            "project_id": project_id,
            "user_template": "项目模板\n{{episode_outline}}\n{{source_text}}",
        },
    )
    assert project_version.status_code == 200
    assert project_version.json()["source_label"] == "项目 v1"

    history = client.get(
        "/api/prompts/history",
        params={
            "stage_key": "episode_draft",
            "scope": "project",
            "project_id": project_id,
        },
    )
    assert history.status_code == 200
    assert len(history.json()) == 1

    preview = client.post(
        "/api/prompts/preview",
        json={
            "stage_key": "episode_draft",
            "project_id": project_id,
            "user_template": "预览\n{{episode_outline}}\n{{source_text}}",
        },
    )
    assert preview.status_code == 200
    assert "{{episode_outline}}" not in preview.json()["rendered_user_template"]
    assert preview.json()["protected_suffix"]

    cleared = client.delete(
        f"/api/projects/{project_id}/prompts/episode_draft"
    )
    assert cleared.status_code == 200
    assert cleared.json()["source_label"] == "全局 v1"
    reset = client.delete("/api/prompts/global/episode_draft")
    assert reset.status_code == 200
    assert reset.json()["source_scope"] == "system"
