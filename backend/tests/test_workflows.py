import asyncio
import uuid

from app.db import Database, now_iso
from app.providers import DemoProvider
from app.workflows import WorkflowService


def seed_book(database: Database) -> str:
    book_id = uuid.uuid4().hex
    now = now_iso()
    database.execute(
        """
        INSERT INTO books
          (id, title, author, filename, status, source_type, parse_version, created_at, updated_at)
        VALUES (?, '测试书', '测试作者', '测试.md', 'ready_to_analyze', 'markdown', 1, ?, ?)
        """,
        (book_id, now, now),
    )
    theme_id = uuid.uuid4().hex
    article_id = uuid.uuid4().hex
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed')
        """,
        [
            (theme_id, book_id, None, 3, 0, "主题", "", "theme"),
            (
                article_id,
                book_id,
                theme_id,
                4,
                1,
                "第一篇",
                "这是一个用于验证工作流的观点。它包含足够长的论证和一个清晰结论。",
                "article",
            ),
        ],
    )
    return book_id


def test_workflow_keeps_versions(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = WorkflowService(database, DemoProvider())
    book_id = seed_book(database)

    analysis = service.analyze_book(book_id)
    assert analysis["knowledge_count"] >= 1

    project = service.create_project("测试专辑", book_id)
    service.confirm_project(project["id"])
    episode_id = project["episodes"][0]["id"]

    asyncio.run(service.generate_episode(episode_id, "outline"))
    asyncio.run(service.generate_episode(episode_id, "draft"))
    versions = service.episode_detail(episode_id)["versions"]
    assert sum(item["stage"] == "outline" for item in versions) == 1
    assert sum(item["stage"] == "draft" for item in versions) == 2
    assert sum(item["stage"] == "final" for item in versions) == 2

    model_final = next(item for item in versions if item["stage"] == "final")
    service.save_manual_final(episode_id, f"{model_final['content']}\n人工修订")
    updated_versions = service.episode_detail(episode_id)["versions"]
    final_versions = [item for item in updated_versions if item["stage"] == "final"]
    assert len(final_versions) == 3
    assert final_versions[0]["author_type"] == "human"
    assert final_versions[-1]["author_type"] == "model"
