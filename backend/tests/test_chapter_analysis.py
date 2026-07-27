import asyncio
import json
import re
import uuid

from app.chapter_analysis import (
    build_chapter_source,
    content_index,
    derive_knowledge_cards,
    render_chapter_markdown,
    validate_chapter_analysis,
    validate_chapter_analysis_partial,
)
from app.db import Database, now_iso
from app.prompts import PromptDefinition
from app.providers import DemoProvider
from app.workflows import WorkflowService


def seed_chapter_book(database: Database, chapter_count: int = 1) -> str:
    book_id = uuid.uuid4().hex
    now = now_iso()
    database.execute(
        """
        INSERT INTO books
          (id, title, author, book_type, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES (?, '章节测试书', '作者', 'non_narrative', 'test.epub',
                'ready_to_analyze', 'epub', 1, ?, ?)
        """,
        (book_id, now, now),
    )
    rows = []
    for position in range(chapter_count):
        root_id = uuid.uuid4().hex
        child_id = uuid.uuid4().hex
        rows.extend(
            [
                (
                    root_id,
                    book_id,
                    None,
                    1,
                    position * 10,
                    f"第{position + 1}章",
                    "章节开篇内容。" * 80,
                    "theme",
                ),
                (
                    child_id,
                    book_id,
                    root_id,
                    2,
                    position * 10 + 1,
                    f"子主题{position + 1}",
                    "作者提出观点并给出论据。" * 80,
                    "theme",
                ),
            ]
        )
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind,
           status, analysis_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 1)
        """,
        rows,
    )
    return book_id


def test_chapter_source_and_renderer_keep_stable_indexes(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    book_id = seed_chapter_book(database)
    sections = database.rows(
        "SELECT * FROM sections WHERE book_id = ? ORDER BY position", (book_id,)
    )
    source = build_chapter_source(sections[0], sections)
    child_index, child_fragment = next(
        (index, fragment)
        for index, fragment in source.fragments_by_index.items()
        if fragment["source_section_id"] == sections[1]["id"]
    )
    exact_text = "作者提出观点并给出论据。"
    data = {
        "chapter_title": "第一章",
        "chapter_theme": "解释核心问题。",
        "subtopics": [
                {
                    "title": "核心问题",
                    "definitions": [
                        {
                            "name": "概念",
                            "definition": exact_text,
                            "source_content_indexes": [child_index],
                        }
                    ],
                    "quotes": [
                        {
                            "text": exact_text,
                            "source_content_indexes": [child_index],
                        }
                    ],
                    "viewpoints": [
                        {
                            "text": exact_text,
                            "source_content_indexes": [child_index],
                            "arguments": [
                                {
                                    "text": exact_text,
                                    "source_content_indexes": [child_index],
                                }
                            ],
                            "case": {
                                "summary": "案例概述。",
                                "relation": "支持观点。",
                                "source_content_indexes": [child_index],
                                "evidence_quotes": [
                                    {
                                        "text": exact_text,
                                        "source_content_indexes": [child_index],
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    validated = validate_chapter_analysis(data, source.fragments_by_index)
    cards = derive_knowledge_cards(validated, source.index_to_section_id, book_id)
    markdown = render_chapter_markdown(validated, cards)

    assert source.source.index("# 第1章") < source.source.index("## 子主题1")
    assert child_index in source.source
    assert f"**原文索引：** {child_index}" in markdown
    assert all(card["source_content_indexes"] == [child_index] for card in cards)
    assert {card["kind"] for card in cards} == {"概念", "金句", "观点", "论据", "案例"}


class ConcurrentChapterProvider:
    name = "test"
    model = "concurrency"

    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.fail_title = ""
        self.book_analysis_calls = 0

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        if prompt.id != "book_analysis":
            return await DemoProvider().generate(prompt, source)
        self.book_analysis_calls += 1
        if self.fail_title and self.fail_title in source:
            raise RuntimeError("模拟章节失败")
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        matches = re.findall(
            r"\[content_index: (content_[0-9a-f]+)\]\n"
            r"\[章节路径: [^\]]*\]\n"
            r"(.*?)(?=\n\[content_index: |\n#{1,6} |\Z)",
            source,
            flags=re.S,
        )
        index, fragment = matches[-1]
        exact_text = next(
            item.strip()
            for item in re.split(r"(?<=[。！？!?])", fragment)
            if item.strip()
        )
        return json.dumps(
            {
                "chapter_title": "章节",
                "chapter_theme": "主题",
                "subtopics": [
                    {
                        "title": "子主题",
                        "definitions": [],
                        "quotes": [],
                        "viewpoints": [
                            {
                                "text": exact_text,
                                "source_content_indexes": [index],
                                "arguments": [
                                    {
                                        "text": exact_text,
                                        "source_content_indexes": [index],
                                    }
                                ],
                                "case": None,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )


class PartiallyInvalidChapterProvider(ConcurrentChapterProvider):
    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        if prompt.id != "book_analysis":
            return await DemoProvider().generate(prompt, source)
        matches = re.findall(
            r"\[content_index: (content_[0-9a-f]+)\]\n"
            r"\[章节路径: [^\]]*\]\n"
            r"(.*?)(?=\n\[content_index: |\n#{1,6} |\Z)",
            source,
            flags=re.S,
        )
        index, fragment = matches[-1]
        exact_text = next(
            item.strip()
            for item in re.split(r"(?<=[。！？!?])", fragment)
            if item.strip()
        )
        return json.dumps(
            {
                "chapter_title": "章节",
                "chapter_theme": "主题",
                "subtopics": [
                    {
                        "title": "子主题",
                        "definitions": [],
                        "quotes": [],
                        "viewpoints": [
                            {
                                "text": "引用不存在来源的观点",
                                "source_content_indexes": ["content_missing"],
                                "arguments": [
                                    {
                                        "text": exact_text,
                                        "source_content_indexes": [index],
                                    }
                                ],
                                "case": None,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )


def test_chapter_batch_limits_concurrency_and_generates_album(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = ConcurrentChapterProvider()
    service = WorkflowService(database, provider)
    book_id = seed_chapter_book(database, chapter_count=7)

    result = asyncio.run(service.analyze_book(book_id))

    assert result["succeeded_count"] == 7
    assert provider.maximum == 5
    assert database.row("SELECT status FROM books WHERE id = ?", (book_id,))[
        "status"
    ] == "analyzed"
    assert database.row("SELECT COUNT(*) AS count FROM chapter_analyses")["count"] == 7

    project = service.create_project("测试专辑", book_id)
    generated = asyncio.run(
        service.generate_project_knowledge_outputs(
            project["id"], "突出社会结构", desired_episode_count=3
        )
    )
    assert generated["mind_map"]["status"] == "succeeded"
    assert generated["album_outline"]["status"] == "succeeded"
    assert generated["project"]["album_special_requirements"] == "突出社会结构"
    assert "期望 3 集" in generated["project"]["episode_count_notice"]
    episode = generated["project"]["episodes"][0]
    assert episode["knowledge_item_ids"]
    bundle = service.contexts.evidence_bundle(episode["id"])
    assert bundle["knowledge_items"]
    assert bundle["direct_fragments"]
    context = service.contexts.build(episode["id"], "outline")
    assert "# 直接原文证据" in context.source
    assert bundle["direct_fragments"][0]["content_index"] in context.source


def test_partial_chapter_saves_valid_assets_and_blocks_album(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = WorkflowService(database, PartiallyInvalidChapterProvider())
    book_id = seed_chapter_book(database)

    result = asyncio.run(service.analyze_book(book_id))

    assert result["succeeded_count"] == 0
    assert len(result["partial_chapters"]) == 1
    analysis = database.row(
        "SELECT * FROM chapter_analyses WHERE book_id = ?",
        (book_id,),
    )
    assert analysis["status"] == "partial"
    assert analysis["valid_item_count"] == 1
    assert analysis["invalid_item_count"] == 1
    assert analysis["validation_issues_json"][0]["asset_type"] == "观点"
    assert database.row(
        "SELECT COUNT(*) AS count FROM knowledge_items WHERE book_id = ? AND status = 'active'",
        (book_id,),
    )["count"] == 1
    try:
        service._latest_chapter_analyses(book_id)
        raise AssertionError("部分成功章节不应进入专辑生成")
    except ValueError as error:
        assert "尚未完整通过" in str(error)


def test_invalid_content_index_is_rejected() -> None:
    data = {
        "chapter_title": "章节",
        "chapter_theme": "主题",
        "subtopics": [
                {
                    "title": "子主题",
                    "definitions": [],
                    "quotes": [],
                    "viewpoints": [
                        {
                            "text": "原文",
                            "source_content_indexes": ["content_missing"],
                            "arguments": [],
                            "case": None,
                        }
                    ],
                }
            ],
        }
    try:
        validate_chapter_analysis(
            data,
            {
                "content_valid": {
                    "text": "原文",
                    "book_position": 1,
                }
            },
        )
        raise AssertionError("未知 content_index 应被拒绝")
    except ValueError as error:
        assert "无效 content_index" in str(error)


def test_non_verbatim_asset_text_is_rejected() -> None:
    data = {
        "chapter_title": "章节",
        "chapter_theme": "主题",
        "subtopics": [
            {
                "title": "子主题",
                "definitions": [],
                "quotes": [
                    {
                        "text": "模型改写后的金句",
                        "source_content_indexes": ["content_valid"],
                    }
                ],
                "viewpoints": [],
            }
        ],
    }
    try:
        validate_chapter_analysis(
            data,
            {
                "content_valid": {
                    "text": "这是原文中的观点。",
                    "book_position": 1,
                }
            },
        )
        raise AssertionError("非逐字原文金句应被拒绝")
    except ValueError as error:
        assert "连续原文" in str(error)


def test_partial_validation_keeps_valid_sibling_assets() -> None:
    fragments = {
        "content_valid": {
            "text": "这是原文观点。这是原文论据。",
            "book_position": 1,
        }
    }
    data = {
        "chapter_title": "章节",
        "chapter_theme": "主题",
        "subtopics": [
            {
                "title": "子主题",
                "definitions": [],
                "quotes": [],
                "viewpoints": [
                    {
                        "text": "引用了不存在来源的观点",
                        "source_content_indexes": ["content_missing"],
                        "arguments": [
                            {
                                "text": "这是原文论据。",
                                "source_content_indexes": ["content_valid"],
                            }
                        ],
                        "case": None,
                    }
                ],
            }
        ],
    }

    result = validate_chapter_analysis_partial(data, fragments)

    assert result.invalid_item_count == 1
    assert result.issues[0]["asset_type"] == "观点"
    assert result.data["subtopics"][0]["viewpoints"] == []
    assert result.data["subtopics"][0]["orphan_arguments"][0]["text"] == "这是原文论据。"
    cards = derive_knowledge_cards(
        result.data,
        {"content_valid": "section_valid"},
        "book_valid",
    )
    assert len(cards) == 1
    assert cards[0]["kind"] == "论据"


def test_partial_retry_skips_already_successful_chapters(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    provider = ConcurrentChapterProvider()
    provider.fail_title = "第2章"
    service = WorkflowService(database, provider)
    book_id = seed_chapter_book(database, chapter_count=2)

    first = asyncio.run(service.analyze_book(book_id))
    assert first["succeeded_count"] == 1
    assert len(first["failed_chapters"]) == 1
    assert provider.book_analysis_calls == 2

    provider.fail_title = ""
    second = asyncio.run(service.analyze_book(book_id))
    assert second["succeeded_count"] == 1
    assert second["failed_chapters"] == []
    assert provider.book_analysis_calls == 3
    assert database.row("SELECT status FROM books WHERE id = ?", (book_id,))[
        "status"
    ] == "analyzed"


def test_single_chapter_success_marks_book_as_partial_not_failed(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = WorkflowService(database, DemoProvider())
    book_id = seed_chapter_book(database, chapter_count=2)
    root = database.row(
        "SELECT id FROM sections WHERE book_id = ? AND parent_id IS NULL ORDER BY position LIMIT 1",
        (book_id,),
    )

    result = asyncio.run(service.retry_chapter(book_id, root["id"]))

    assert result["failed_chapters"] == []
    assert database.row("SELECT status FROM books WHERE id = ?", (book_id,))[
        "status"
    ] == "analysis_partial"
