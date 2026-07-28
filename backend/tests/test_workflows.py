import asyncio
import re
import uuid

from app.db import Database, now_iso
from app.prompts import PromptDefinition
from app.providers import DemoProvider, ModelOutputTruncatedError
from app.workflows import WorkflowService


def seed_book(database: Database, book_type: str = "non_narrative") -> str:
    book_id = uuid.uuid4().hex
    now = now_iso()
    database.execute(
        """
        INSERT INTO books
          (id, title, author, book_type, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES (?, '测试书', '测试作者', ?, '测试.md', 'ready_to_analyze',
                'markdown', 1, ?, ?)
        """,
        (book_id, book_type, now, now),
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

    analysis = asyncio.run(service.analyze_book(book_id))
    assert analysis["knowledge_count"] >= 1

    project = service.create_project("测试专辑", book_id)
    album_prompt = service.prompts.create_version(
        "album_outline",
        "project",
        "项目专辑提示词\n{{book_analysis}}",
        project["id"],
    )
    project = asyncio.run(
        service.generate_project_knowledge_outputs(project["id"])
    )["project"]
    assert project["album_prompt_version_id"] == album_prompt["prompt_version_id"]
    service.confirm_project(project["id"])
    episode_id = project["episodes"][0]["id"]
    draft_prompt = service.prompts.create_version(
        "episode_draft",
        "project",
        "项目声音初稿提示词\n{{episode_outline}}\n{{source_text}}",
        project["id"],
    )

    locked_provider = DemoProvider(name="anthropic", model="locked-model")
    asyncio.run(
        service.generate_episode(
            episode_id, "outline", provider=locked_provider
        )
    )
    first_versions = service.episode_detail(episode_id)["versions"]
    assert {item["model"] for item in first_versions} == {"locked-model"}
    stage_providers = {
        "outline": DemoProvider(name="anthropic", model="outline-model"),
        "draft": DemoProvider(name="anthropic", model="draft-model"),
        "final": DemoProvider(name="anthropic", model="final-model"),
    }
    asyncio.run(
        service.generate_episode(
            episode_id, "outline", stage_providers=stage_providers
        )
    )
    staged_versions = service.episode_detail(episode_id)["versions"]
    latest_by_stage = {
        stage: next(
            item for item in staged_versions if item["stage"] == stage
        )
        for stage in ("outline", "draft", "final")
    }
    assert {
        stage: artifact["model"]
        for stage, artifact in latest_by_stage.items()
    } == {
        "outline": "outline-model",
        "draft": "draft-model",
        "final": "final-model",
    }
    asyncio.run(service.generate_episode(episode_id, "draft"))
    versions = service.episode_detail(episode_id)["versions"]
    assert sum(item["stage"] == "outline" for item in versions) == 2
    assert sum(item["stage"] == "draft" for item in versions) == 3
    assert sum(item["stage"] == "final" for item in versions) == 3
    snapshots = database.rows(
        """
        SELECT stage, input_snapshot, prompt_version_id
        FROM artifact_versions
        WHERE episode_id = ? AND author_type = 'model'
        """,
        (episode_id,),
    )
    assert all(item["input_snapshot"] for item in snapshots)
    draft_snapshots = [item for item in snapshots if item["stage"] == "draft"]
    assert all(
        item["prompt_version_id"] == draft_prompt["prompt_version_id"]
        for item in draft_snapshots
    )
    assert all(
        "项目声音初稿提示词" in item["input_snapshot"]
        for item in draft_snapshots
    )

    model_final = next(item for item in versions if item["stage"] == "final")
    service.save_manual_final(episode_id, f"{model_final['content']}\n人工修订")
    updated_versions = service.episode_detail(episode_id)["versions"]
    final_versions = [item for item in updated_versions if item["stage"] == "final"]
    assert len(final_versions) == 4
    assert final_versions[0]["author_type"] == "human"
    assert final_versions[-1]["author_type"] == "model"


class RelationshipProvider:
    name = "test"
    model = "relationship-test"

    def __init__(self, failing_title: str = ""):
        self.failing_title = failing_title
        self.calls: list[str] = []
        self.demo = DemoProvider()

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        self.calls.append(prompt.id)
        if prompt.id != "character_relationships":
            return await self.demo.generate(prompt, source)
        if self.failing_title and self.failing_title in source:
            raise RuntimeError("模拟人物关系提取失败")
        return (
            '{"relationships":[{"characters":["甲","乙"],'
            '"relationship":"同行者","evidence":"两人一同出发"}]}'
        )


def test_narrative_analysis_saves_relationships_with_server_source_id(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = RelationshipProvider()
    service = WorkflowService(database, provider)
    book_id = seed_book(database, "narrative")
    section = database.row(
        "SELECT * FROM sections WHERE book_id = ? AND level = 4", (book_id,)
    )

    result = asyncio.run(service.analyze_book(book_id))

    relationships = database.rows(
        """
        SELECT * FROM knowledge_items
        WHERE book_id = ? AND kind = '人物关系'
        """,
        (book_id,),
    )
    assert result["failed_section_ids"] == []
    assert result["relationship_count"] == 1
    assert relationships[0]["source_section_ids"] == [section["id"]]
    assert "甲、乙：同行者" in relationships[0]["body"]
    assert provider.calls.count("character_relationships") == 1


def test_non_narrative_analysis_does_not_call_relationship_model(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = RelationshipProvider()
    service = WorkflowService(database, provider)
    book_id = seed_book(database, "non_narrative")

    asyncio.run(service.analyze_book(book_id))

    assert "character_relationships" not in provider.calls


def test_narrative_analysis_keeps_successes_and_retries_failed_sections(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = RelationshipProvider(failing_title="失败章节")
    service = WorkflowService(database, provider)
    book_id = seed_book(database, "narrative")
    database.execute(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status)
        VALUES (?, ?, NULL, 4, 2, '失败章节', '甲和乙再次相遇。',
                'article', 'confirmed')
        """,
        (uuid.uuid4().hex, book_id),
    )

    first = asyncio.run(service.analyze_book(book_id))
    first_count = database.row(
        """
        SELECT COUNT(*) AS count FROM knowledge_items
        WHERE book_id = ? AND kind = '人物关系'
        """,
        (book_id,),
    )["count"]

    assert len(first["failed_section_ids"]) == 1
    assert first_count == 1
    assert database.row("SELECT status FROM books WHERE id = ?", (book_id,))[
        "status"
    ] == "analysis_partial_failed"

    provider.failing_title = ""
    second = asyncio.run(service.analyze_book(book_id))

    assert second["failed_section_ids"] == []
    assert second["relationship_count"] == 2
    assert provider.calls.count("character_relationships") == 3
    assert database.row("SELECT status FROM books WHERE id = ?", (book_id,))[
        "status"
    ] == "analyzed"


def test_project_confirmation_requires_framework_and_valid_source(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = WorkflowService(database, DemoProvider())
    book_id = seed_book(database)
    asyncio.run(service.analyze_book(book_id))
    project = service.create_project("测试专辑", book_id)
    project = asyncio.run(
        service.generate_project_knowledge_outputs(project["id"])
    )["project"]
    episode_id = project["episodes"][0]["id"]
    database.execute(
        "UPDATE episodes SET content_framework = '' WHERE id = ?", (episode_id,)
    )

    try:
        service.confirm_project(project["id"])
        raise AssertionError("空声音框架应阻止确认")
    except ValueError as error:
        assert "缺少内容框架" in str(error)

    database.execute(
        """
        UPDATE episodes
        SET content_framework = '有效框架', source_section_ids = '["missing"]'
        WHERE id = ?
        """,
        (episode_id,),
    )
    try:
        service.confirm_project(project["id"])
        raise AssertionError("无效原文块应阻止确认")
    except ValueError as error:
        assert "无效原文块" in str(error)


class TruncatingCompressionProvider:
    name = "test"
    model = "compression-test"

    def __init__(self, max_source_chars: int = 1800):
        self.max_source_chars = max_source_chars
        self.calls: list[str] = []

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        assert prompt.id == "chapter_compression"
        self.calls.append(source)
        if len(source) > self.max_source_chars:
            raise ModelOutputTruncatedError(6300, "length")
        return source


def long_compression_source(group_count: int = 8) -> str:
    groups = ["# 第一章 长章节"]
    for index in range(group_count):
        content_id = f"content_{index:040x}"
        knowledge_id = f"knowledge_{index:024x}"
        groups.append(
            "\n".join(
                [
                    f"## 子主题 {index + 1}",
                    f"### 主要观点：{'观点内容。' * 180}",
                    f"**知识资产 ID：** {knowledge_id}",
                    f"**原文索引：** {content_id}",
                ]
            )
        )
    return "\n\n".join(groups)


def test_long_chapter_compression_splits_and_retries_truncated_chunks(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = TruncatingCompressionProvider()
    service = WorkflowService(database, provider)
    source = long_compression_source()

    compressed = asyncio.run(
        service._compress_chapter_markdown("第一章 长章节", source, provider)
    )

    assert set(re.findall(r"content_[0-9a-f]{8,40}", compressed)) == set(
        re.findall(r"content_[0-9a-f]{8,40}", source)
    )
    assert set(re.findall(r"knowledge_[0-9a-f]{24}", compressed)) == set(
        re.findall(r"knowledge_[0-9a-f]{24}", source)
    )
    assert compressed.index("content_" + f"{0:040x}") < compressed.index(
        "content_" + f"{7:040x}"
    )
    assert any(len(call) > provider.max_source_chars for call in provider.calls)
    assert all(
        len(call) <= 5000
        for call in provider.calls
        if len(call) <= provider.max_source_chars
    )


def test_chapter_compression_rejects_missing_source_identifier(tmp_path) -> None:
    class MissingIdentifierProvider(TruncatingCompressionProvider):
        async def generate(self, prompt: PromptDefinition, source: str) -> str:
            self.calls.append(source)
            return re.sub(
                r"knowledge_[0-9a-f]{24}", "knowledge_missing", source, count=1
            )

    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = MissingIdentifierProvider(max_source_chars=100_000)
    service = WorkflowService(database, provider)

    try:
        asyncio.run(
            service._compress_chapter_markdown(
                "第一章 长章节",
                long_compression_source(group_count=2),
                provider,
            )
        )
        raise AssertionError("来源标识缺失时不应接受压缩结果")
    except ValueError as error:
        assert "压缩后来源标识不完整" in str(error)


def test_chapter_compression_splits_when_large_output_drops_identifier(
    tmp_path,
) -> None:
    class OmittingLargeChunkProvider(TruncatingCompressionProvider):
        async def generate(self, prompt: PromptDefinition, source: str) -> str:
            self.calls.append(source)
            if len(source) > self.max_source_chars:
                return re.sub(
                    r"content_[0-9a-f]{8,40}",
                    "content_missing",
                    source,
                    count=1,
                )
            return source

    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = OmittingLargeChunkProvider(max_source_chars=1800)
    service = WorkflowService(database, provider)
    source = long_compression_source()

    compressed = asyncio.run(
        service._compress_chapter_markdown("第一章 长章节", source, provider)
    )

    assert set(re.findall(r"content_[0-9a-f]{8,40}", compressed)) == set(
        re.findall(r"content_[0-9a-f]{8,40}", source)
    )
    assert any(len(call) > provider.max_source_chars for call in provider.calls)
