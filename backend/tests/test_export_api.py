import importlib
import uuid

from fastapi.testclient import TestClient

from app.db import now_iso


def load_main(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BOOK_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AI_BOOK_STUDIO_PROVIDER", "demo")
    monkeypatch.setenv("AI_BOOK_STUDIO_MODEL", "demo-model")
    main = importlib.import_module("app.main")
    return importlib.reload(main)


def insert_project(main, project_id: str, title: str) -> None:
    now = now_iso()
    book_id = f"{project_id}-book"
    main.database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type, parse_version,
           created_at, updated_at)
        VALUES (?, '导出测试书', '', 'test.md', 'analyzed', 'markdown', 1, ?, ?)
        """,
        (book_id, now, now),
    )
    main.database.execute(
        """
        INSERT INTO projects
          (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, ?, ?, 'review', ?, ?)
        """,
        (project_id, title, f'["{book_id}"]', now, now),
    )


def insert_episode(
    main, project_id: str, episode_id: str, position: int, title: str
) -> None:
    main.database.execute(
        """
        INSERT INTO episodes
          (id, project_id, position, title, content_type, style,
           content_framework, status, source_section_ids)
        VALUES (?, ?, ?, ?, '解读', '观点', '测试框架', 'review', '[]')
        """,
        (episode_id, project_id, position, title),
    )


def insert_artifact(
    main,
    episode_id: str,
    stage: str,
    version: int,
    content: str,
    author_type: str = "model",
) -> None:
    main.database.execute(
        """
        INSERT INTO artifact_versions
          (id, episode_id, stage, version, content, prompt_version,
           provider, model, author_type, created_at)
        VALUES (?, ?, ?, ?, ?, 'test-v1', 'demo', 'demo-model', ?, ?)
        """,
        (
            uuid.uuid4().hex,
            episode_id,
            stage,
            version,
            content,
            author_type,
            now_iso(),
        ),
    )


def test_project_finals_export_uses_latest_versions_and_keeps_gaps(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    project_id = "markdown-export-project"
    insert_project(main, project_id, "专辑\n测试")
    insert_episode(main, project_id, "episode-second", 2, "第二\n集")
    insert_episode(main, project_id, "episode-first", 1, "第一集")
    insert_episode(main, project_id, "episode-missing", 3, "缺稿集")
    insert_artifact(main, "episode-first", "final", 1, "旧模型稿")
    insert_artifact(
        main,
        "episode-first",
        "final",
        2,
        "人工终稿第一段\n\n人工终稿第二段",
        author_type="human",
    )
    insert_artifact(main, "episode-first", "draft", 3, "不能导出的初稿")
    insert_artifact(main, "episode-second", "final", 1, "第二集终稿")

    with TestClient(main.app) as client:
        response = client.get(f"/api/projects/{project_id}/finals/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/markdown; charset=utf-8"
        )
        assert response.headers["content-disposition"] == "attachment"
        markdown = response.text
        assert markdown.startswith("# 专辑 测试\n")
        assert markdown.endswith("\n")
        assert markdown.index("## 第 01 集：第一集") < markdown.index(
            "## 第 02 集：第二 集"
        )
        assert markdown.index("## 第 02 集：第二 集") < markdown.index(
            "## 第 03 集：缺稿集"
        )
        assert "人工终稿第一段\n\n人工终稿第二段" in markdown
        assert "旧模型稿" not in markdown
        assert "不能导出的初稿" not in markdown
        assert markdown.count("> 本集终稿尚未生成") == 1

        missing = client.get("/api/projects/missing/finals/export")
        assert missing.status_code == 404


def test_project_finals_export_handles_an_empty_album(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    project_id = "empty-export-project"
    insert_project(main, project_id, "空专辑")

    with TestClient(main.app) as client:
        response = client.get(f"/api/projects/{project_id}/finals/export")

        assert response.status_code == 200
        assert response.text == "# 空专辑\n\n> 当前专辑尚未创建声音。\n"
