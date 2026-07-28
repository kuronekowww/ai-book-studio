import asyncio
import sqlite3
import uuid

from app.batches import BatchService
from app.db import Database, now_iso
from app.providers import DemoProvider
from app.workflows import StageGenerationError


def seed_project(database: Database, count: int = 7) -> tuple[str, list[str]]:
    project_id = uuid.uuid4().hex
    now = now_iso()
    database.execute(
        """
        INSERT INTO projects (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, '并发测试专辑', '[]', 'ready', ?, ?)
        """,
        (project_id, now, now),
    )
    episode_ids = [uuid.uuid4().hex for _ in range(count)]
    database.executemany(
        """
        INSERT INTO episodes
          (id, project_id, position, title, content_type, style, status, source_section_ids)
        VALUES (?, ?, ?, ?, '解读', '观点', 'ready', '[]')
        """,
        [
            (episode_id, project_id, position, f"声音 {position}")
            for position, episode_id in enumerate(episode_ids, start=1)
        ],
    )
    return project_id, episode_ids


class TrackingWorkflows:
    def __init__(self, database: Database, failing_id: str):
        self.database = database
        self.failing_id = failing_id
        self.active = 0
        self.max_active = 0
        self.stage_provider_models: list[dict[str, str]] = []

    def latest_artifact(self, episode_id: str, stage: str):
        return self.database.row(
            """
            SELECT * FROM artifact_versions
            WHERE episode_id = ? AND stage = ?
            ORDER BY version DESC LIMIT 1
            """,
            (episode_id, stage),
        )

    async def generate_episode(
        self,
        episode_id: str,
        from_stage: str,
        provider=None,
        *,
        stage_providers=None,
    ):
        if stage_providers:
            self.stage_provider_models.append(
                {
                    stage: stage_provider.model
                    for stage, stage_provider in stage_providers.items()
                }
            )
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            if episode_id == self.failing_id:
                raise StageGenerationError("draft", RuntimeError("模拟模型失败"))
            self.database.execute(
                "UPDATE episodes SET status = 'review' WHERE id = ?",
                (episode_id,),
            )
        finally:
            self.active -= 1


def test_batch_limits_concurrency_and_isolates_failure(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id, episode_ids = seed_project(database)
    workflows = TrackingWorkflows(database, failing_id=episode_ids[2])
    batches = BatchService(database, workflows, concurrency=5)  # type: ignore[arg-type]

    batch = batches.create_batch(project_id)
    duplicate = batches.create_batch(project_id)
    assert duplicate["id"] == batch["id"]

    asyncio.run(batches.run_batch(batch["id"]))
    result = batches.batch_detail(batch["id"])
    assert workflows.max_active == 5
    assert result["status"] == "partial_failed"
    assert result["summary"]["completed"] == 6
    assert result["summary"]["failed"] == 1
    failed = next(child for child in result["children"] if child["status"] == "failed")
    assert failed["error_stage"] == "draft"
    assert database.row(
        "SELECT status FROM projects WHERE id = ?", (project_id,)
    )["status"] == "partial_failed"

    batches.reconcile_episode_success(episode_ids[2])
    reconciled = batches.batch_detail(batch["id"])
    assert reconciled["status"] == "succeeded"
    assert reconciled["summary"]["failed"] == 0


def test_batch_skips_existing_final(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id, episode_ids = seed_project(database, count=2)
    now = now_iso()
    database.execute(
        """
        INSERT INTO artifact_versions
          (id, episode_id, stage, version, content, prompt_version,
           provider, model, author_type, created_at)
        VALUES (?, ?, 'final', 1, '已有终稿', 'v1', 'demo', 'demo', 'model', ?)
        """,
        (uuid.uuid4().hex, episode_ids[0], now),
    )
    workflows = TrackingWorkflows(database, failing_id="")
    batches = BatchService(database, workflows, concurrency=5)  # type: ignore[arg-type]
    batch = batches.create_batch(project_id)
    assert batch["summary"]["total"] == 1
    assert batch["children"][0]["scope_id"] == episode_ids[1]


def test_batch_restores_the_model_snapshot_from_run_metadata(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id, _ = seed_project(database, count=1)
    workflows = TrackingWorkflows(database, failing_id="")
    captured_model_ids: list[str | None] = []
    locked_provider = DemoProvider(name="anthropic", model="locked-model")

    def resolve(model_id: str | None):
        captured_model_ids.append(model_id)
        return locked_provider

    batches = BatchService(
        database,
        workflows,  # type: ignore[arg-type]
        concurrency=5,
        provider_resolver=resolve,
    )
    batch = batches.create_batch(project_id, "kimi-k3")

    asyncio.run(batches.run_batch(batch["id"]))
    finished = batches.batch_detail(batch["id"])

    assert captured_model_ids == ["kimi-k3", "kimi-k3", "kimi-k3"]
    assert finished["metadata_json"]["model_id"] == "kimi-k3"
    assert finished["metadata_json"]["stage_model_ids"] == {
        "outline": "kimi-k3",
        "draft": "kimi-k3",
        "final": "kimi-k3",
    }
    assert workflows.stage_provider_models == [
        {
            "outline": "locked-model",
            "draft": "locked-model",
            "final": "locked-model",
        }
    ]


def test_batch_restores_different_model_for_each_episode_stage(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id, _ = seed_project(database, count=1)
    workflows = TrackingWorkflows(database, failing_id="")

    def resolve(model_id: str | None):
        return DemoProvider(name="anthropic", model=str(model_id))

    batches = BatchService(
        database,
        workflows,  # type: ignore[arg-type]
        concurrency=5,
        provider_resolver=resolve,
    )
    batch = batches.create_batch(
        project_id,
        stage_model_ids={
            "outline": "kimi-k3",
            "draft": "claude-sonnet-5",
            "final": "glm-5.2",
        },
    )

    asyncio.run(batches.run_batch(batch["id"]))

    assert workflows.stage_provider_models == [
        {
            "outline": "kimi-k3",
            "draft": "claude-sonnet-5",
            "final": "glm-5.2",
        }
    ]


def test_database_adds_context_columns_to_existing_tables(tmp_path) -> None:
    path = tmp_path / "studio.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE books (
              id TEXT PRIMARY KEY, title TEXT, author TEXT, filename TEXT,
              status TEXT, source_type TEXT, parse_version INTEGER,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE projects (
              id TEXT PRIMARY KEY, title TEXT, book_ids TEXT, status TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE episodes (
              id TEXT PRIMARY KEY, project_id TEXT, position INTEGER, title TEXT,
              content_type TEXT, style TEXT, status TEXT, source_section_ids TEXT
            );
            INSERT INTO episodes
              (id, project_id, position, title, content_type, style, status,
               source_section_ids)
            VALUES
              ('legacy-episode', 'legacy-project', 1, '旧声音', '解读', '观点',
               'review', '[]');
            CREATE TABLE artifact_versions (
              id TEXT PRIMARY KEY, episode_id TEXT, stage TEXT, version INTEGER,
              content TEXT, prompt_version TEXT, provider TEXT, model TEXT,
              created_at TEXT
            );
            CREATE TABLE workflow_runs (
              id TEXT PRIMARY KEY, scope_type TEXT, scope_id TEXT, stage TEXT,
              status TEXT, message TEXT, created_at TEXT, updated_at TEXT
            );
            """
        )
    database = Database(path)
    database.init()
    with sqlite3.connect(path) as connection:
        book_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(books)")
        }
        episode_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(episodes)")
        }
        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifact_versions)")
        }
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)")
        }
        mind_map_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(mind_maps)")
        }
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        legacy_framework = connection.execute(
            """
            SELECT content_framework FROM episodes
            WHERE id = 'legacy-episode'
            """
        ).fetchone()[0]
    assert "book_type" in book_columns
    assert "content_framework" in episode_columns
    assert "旧声音" in legacy_framework
    assert {"author_type", "input_snapshot"} <= artifact_columns
    assert {"parent_run_id", "error_stage", "position", "metadata_json"} <= run_columns
    assert {"provider", "model"} <= mind_map_columns
    assert "analysis_model_id" in book_columns
    assert "model_overrides_json" in project_columns
