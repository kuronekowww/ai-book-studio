import asyncio
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


def seed_project(main) -> tuple[str, str, str]:
    now = now_iso()
    book_id = "run-api-book"
    project_id = "run-api-project"
    episode_id = "run-api-episode"
    main.database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type, parse_version,
           created_at, updated_at)
        VALUES (?, '任务测试书', '', 'test.md', 'analyzed', 'markdown', 1, ?, ?)
        """,
        (book_id, now, now),
    )
    main.database.execute(
        """
        INSERT INTO projects
          (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, '任务测试项目', ?, 'ready', ?, ?)
        """,
        (project_id, f'["{book_id}"]', now, now),
    )
    main.database.execute(
        """
        INSERT INTO episodes
          (id, project_id, position, title, content_type, style,
           content_framework, status, source_section_ids)
        VALUES (?, ?, 1, '测试声音', '解读', '观点', '测试框架', 'ready', '[]')
        """,
        (episode_id, project_id),
    )
    return book_id, project_id, episode_id


def test_run_api_filters_active_materializes_outputs_and_cancels_children(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    book_id, project_id, episode_id = seed_project(main)
    with TestClient(main.app) as client:
        parent, _ = main.runs.create(
            scope_type="project_batch",
            scope_id=project_id,
            stage="full",
            current_stage="episode_generation",
            progress_total=1,
        )
        child, _ = main.runs.create(
            scope_type="episode",
            scope_id=episode_id,
            stage="outline",
            current_stage="outline",
            progress_total=3,
            parent_run_id=parent["id"],
            reuse_active=False,
        )
        artifact_id = uuid.uuid4().hex
        main.database.execute(
            """
            INSERT INTO artifact_versions
              (id, episode_id, stage, version, content, prompt_version,
               provider, model, author_type, created_at)
            VALUES (?, ?, 'outline', 1, '阶段输出正文', 'test-v1',
                    'demo', 'demo-model', 'model', ?)
            """,
            (artifact_id, episode_id, now_iso()),
        )
        main.runs.set_stage(
            child["id"],
            "outline",
            "succeeded",
            output={
                "artifact_type": "episode_artifact",
                "artifact_id": artifact_id,
                "version": 1,
            },
        )
        active = client.get("/api/runs", params={"active": True})
        assert active.status_code == 200
        assert {item["id"] for item in active.json()} == {
            parent["id"],
            child["id"],
        }
        assert next(
            item for item in active.json() if item["id"] == parent["id"]
        )["scope_label"] == "任务测试项目"

        outputs = client.get(f"/api/runs/{parent['id']}/outputs")
        assert outputs.status_code == 200
        assert outputs.json()["outputs"][0]["content"] == "阶段输出正文"
        assert outputs.json()["outputs"][0]["label"] == "声音细纲"

        knowledge_id = uuid.uuid4().hex
        main.database.execute(
            """
            INSERT INTO knowledge_items
              (id, book_id, kind, title, body, source_section_ids, created_at)
            VALUES (?, ?, '观点', '测试观点', '测试知识正文', '[]', ?)
            """,
            (knowledge_id, book_id, now_iso()),
        )
        book_run, _ = main.runs.create(
            scope_type="book_analysis_batch",
            scope_id=book_id,
            stage="book_analysis",
            current_stage="analyze_chapters",
            progress_total=1,
            reuse_active=False,
        )
        main.runs.set_stage(
            book_run["id"],
            "analyze_chapters",
            "succeeded",
            output={"artifact_type": "book_knowledge", "knowledge_count": 1},
        )
        book_outputs = client.get(f"/api/runs/{book_run['id']}/outputs")
        assert book_outputs.status_code == 200
        assert book_outputs.json()["outputs"][0]["label"] == "书籍知识资产"
        assert "测试知识正文" in book_outputs.json()["outputs"][0]["content"]

        cancelled = client.post(f"/api/runs/{parent['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert main.runs.get(child["id"])["status"] == "cancelled"


def test_project_generation_returns_202_before_background_work_finishes(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    _, project_id, _ = seed_project(main)
    async def slow_project_run(run_id: str, **_kwargs) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(main, "execute_project_generation_run", slow_project_run)
    with TestClient(main.app) as client:
        response = client.post(
            f"/api/projects/{project_id}/generate-outline",
            json={
                "album_special_requirements": "",
                "desired_episode_count": None,
                "episode_word_count_min": 1800,
                "episode_word_count_max": 2300,
            },
        )
        assert response.status_code == 202
        run = response.json()
        assert run["scope_type"] == "project_generation"
        assert run["scope_id"] == project_id
        assert run["status"] in {"pending", "running"}
        assert run["metadata_json"]["episode_word_count_min"] == 1800
        assert run["metadata_json"]["episode_word_count_max"] == 2300
        saved_project = main.database.row(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        assert saved_project["episode_word_count_min"] == 1800
        assert saved_project["episode_word_count_max"] == 2300
        duplicate = client.post(
            f"/api/projects/{project_id}/generate-outline",
            json={
                "album_special_requirements": "不会覆盖当前任务",
                "desired_episode_count": 12,
            },
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == run["id"]
        assert duplicate.json()["reused"] is True

        invalid = client.post(
            f"/api/projects/{project_id}/generate-outline",
            json={
                "album_special_requirements": "",
                "desired_episode_count": None,
                "episode_word_count_min": 2600,
                "episode_word_count_max": 2500,
            },
        )
        assert invalid.status_code == 400


def test_album_module_output_is_visible_and_failed_module_can_retry(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    _, project_id, _ = seed_project(main)

    async def paused_project_run(run_id: str, **_kwargs) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(main, "execute_project_generation_run", paused_project_run)
    run, _ = main.runs.create(
        scope_type="project_generation",
        scope_id=project_id,
        stage="full",
        current_stage="expand_album_modules",
        progress_total=6,
        reuse_active=False,
    )
    main.runs.finish(
        run["id"], status="partial_failed", message="一个模块失败"
    )
    artifact = main.workflows.album_planning.artifacts.upsert(
        run_id=run["id"],
        project_id=project_id,
        artifact_type="module_outline",
        module_key="MODULE_002",
        position=2,
        content="",
        status="failed",
        error_message="网关超时",
    )

    with TestClient(main.app) as client:
        outputs = client.get(f"/api/runs/{run['id']}/outputs")
        assert outputs.status_code == 200
        module = outputs.json()["outputs"][0]
        assert module["module_key"] == "MODULE_002"
        assert module["status"] == "failed"
        assert module["error_message"] == "网关超时"

        retried = client.post(
            f"/api/runs/{run['id']}/modules/MODULE_002/retry"
        )
        assert retried.status_code == 202
        assert retried.json()["status"] in {"pending", "running"}
        assert (
            main.database.row(
                "SELECT status FROM album_planning_artifacts WHERE id = ?",
                (artifact["id"],),
            )["status"]
            == "pending"
        )


def test_failed_album_structure_can_start_local_artifact_recovery(
    tmp_path, monkeypatch
) -> None:
    main = load_main(tmp_path, monkeypatch)
    _, project_id, _ = seed_project(main)

    async def paused_project_run(run_id: str, **_kwargs) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(main, "execute_project_generation_run", paused_project_run)
    run, _ = main.runs.create(
        scope_type="project_generation",
        scope_id=project_id,
        stage="full",
        current_stage="structure_album_outline",
        progress_total=6,
        reuse_active=False,
    )
    main.runs.finish(
        run["id"], status="partial_failed", message="结构化失败"
    )
    main.workflows.album_planning.artifacts.upsert(
        run_id=run["id"],
        project_id=project_id,
        artifact_type="module_plan",
        content=(
            "## 模块1：测试模块\n"
            "听众问题：为什么？\n"
            "来源章节：[CHAPTER_001]\n"
            "建议声音数：1"
        ),
    )
    main.workflows.album_planning.artifacts.upsert(
        run_id=run["id"],
        project_id=project_id,
        artifact_type="module_outline",
        module_key="MODULE_001",
        position=1,
        content=(
            "## 第1集：测试声音\n"
            "听众钩子：为什么值得听？\n"
            "核心主题：解释问题。\n"
            "核心要点：\n1. 现象；\n2. 原因。\n"
            "内容类型：深度解读\n"
            "来源章节：[CHAPTER_001]"
        ),
    )
    main.workflows.album_planning.artifacts.upsert(
        run_id=run["id"],
        project_id=project_id,
        artifact_type="structured_outline",
        content="",
        status="failed",
        error_message="字段不完整",
    )

    with TestClient(main.app) as client:
        response = client.post(
            f"/api/runs/{run['id']}/structure/recover"
        )
        assert response.status_code == 202
        recovered = response.json()
        assert recovered["status"] in {"pending", "running"}
        assert recovered["metadata_json"]["structure_only_recovery"] is True
        structured = main.workflows.album_planning.artifacts.get(
            run["id"], "structured_outline"
        )
        assert structured["status"] == "pending"

        succeeded, _ = main.runs.create(
            scope_type="project_generation",
            scope_id=project_id,
            stage="full",
            current_stage="save_project_outline",
            progress_total=6,
            metadata={
                "stages": {
                    "structure_album_outline": {"status": "succeeded"},
                    "save_project_outline": {"status": "succeeded"},
                }
            },
            reuse_active=False,
        )
        main.runs.finish(succeeded["id"], message="已完成")
        repeated = client.post(
            f"/api/runs/{succeeded['id']}/structure/recover"
        )
        assert repeated.status_code == 202
        assert repeated.json()["reused"] is True
