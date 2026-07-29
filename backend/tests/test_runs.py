from app.db import Database
import asyncio

from app.runs import RunService, TaskRegistry


def test_run_service_reuses_active_task_and_tracks_stage_progress(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    runs = RunService(database)

    created, reused = runs.create(
        scope_type="project_generation",
        scope_id="project-1",
        stage="full",
        current_stage="prepare_analysis",
        progress_total=4,
        metadata={"model_ids": {"mind_map": "kimi-k3"}},
    )
    duplicate, duplicate_reused = runs.create(
        scope_type="project_generation",
        scope_id="project-1",
        stage="full",
        current_stage="prepare_analysis",
    )

    assert reused is False
    assert duplicate_reused is True
    assert duplicate["id"] == created["id"]

    runs.mark_running(
        created["id"],
        current_stage="generate_mind_map",
        message="正在生成思维导图",
    )
    runs.set_stage(
        created["id"],
        "generate_mind_map",
        "succeeded",
        message="思维导图已生成",
        output={"artifact_type": "mind_map", "version": 1},
    )
    running = runs.set_progress(
        created["id"],
        current=2,
        current_stage="generate_album_outline",
    )

    assert running["status"] == "running"
    assert running["progress_current"] == 2
    assert running["progress_total"] == 4
    assert running["metadata_json"]["stages"]["generate_mind_map"]["output"] == {
        "artifact_type": "mind_map",
        "version": 1,
    }
    assert running["started_at"]
    assert running["heartbeat_at"]
    merged = runs.merge_metadata(created["id"], {"input_signature": "locked"})
    assert merged["metadata_json"]["model_ids"] == {"mind_map": "kimi-k3"}
    assert merged["metadata_json"]["input_signature"] == "locked"

    finished = runs.finish(created["id"], message="专辑大纲已生成")
    assert finished["status"] == "succeeded"
    assert finished["progress_current"] == 4
    assert finished["finished_at"]


def test_run_service_filters_active_and_resets_for_resume(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    runs = RunService(database)
    active, _ = runs.create(
        scope_type="book_analysis_batch",
        scope_id="book-1",
        stage="book_analysis",
        current_stage="prepare_chapters",
    )
    runs.mark_running(active["id"], increment_attempt=True)
    completed, _ = runs.create(
        scope_type="episode",
        scope_id="episode-1",
        stage="outline",
        current_stage="outline",
    )
    runs.finish(completed["id"])

    assert [item["id"] for item in runs.list(active_only=True)] == [active["id"]]
    resumed = runs.reset_for_resume(active["id"])
    assert resumed["status"] == "pending"
    assert resumed["attempt"] == 1
    assert "恢复任务" in resumed["message"]


def test_task_registry_deduplicates_and_persists_unhandled_failure(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "studio.sqlite3")
        database.init()
        runs = RunService(database)
        registry = TaskRegistry(runs)
        run, _ = runs.create(
            scope_type="project_generation",
            scope_id="project-1",
            stage="full",
            current_stage="prepare_analysis",
        )
        calls = 0

        async def explode() -> None:
            nonlocal calls
            calls += 1
            runs.mark_running(run["id"])
            await asyncio.sleep(0)
            raise RuntimeError("模拟后台异常")

        first = registry.spawn(run["id"], explode)
        duplicate = registry.spawn(run["id"], explode)
        assert duplicate is first
        await first
        assert calls == 1
        failed = runs.get(run["id"])
        assert failed["status"] == "failed"
        assert failed["error_stage"] == "prepare_analysis"
        assert "模拟后台异常" in failed["message"]

    asyncio.run(scenario())
