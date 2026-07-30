import json
import uuid

import pytest

from app.album_planning import (
    AlbumModule,
    AlbumPlanningArtifactRepository,
    AlbumPlanningService,
    ChapterPlanningEntry,
)
from app.db import Database, now_iso
from test_chapter_analysis import seed_chapter_book


def _seed_successful_analyses(database: Database, book_id: str) -> None:
    now = now_iso()
    fragment_set_id = uuid.uuid4().hex
    database.execute(
        """
        INSERT INTO source_fragment_sets
          (id, book_id, version, content_fingerprint, status, created_at)
        VALUES (?, ?, 1, 'fingerprint', 'active', ?)
        """,
        (fragment_set_id, book_id, now),
    )
    roots = database.rows(
        """
        SELECT * FROM sections
        WHERE book_id = ? AND parent_id IS NULL
        ORDER BY position
        """,
        (book_id,),
    )
    for position, root in enumerate(roots, start=1):
        data = {
            "chapter_title": root["title"],
            "chapter_theme": f"主题 {position}",
            "subtopics": [
                {
                    "title": f"子主题 {position}",
                    "definitions": [],
                    "quotes": [],
                    "viewpoints": [
                        {
                            "text": f"观点 {position}",
                            "source_content_indexes": [f"content_{position:04d}"],
                            "arguments": [],
                        }
                    ],
                }
            ],
        }
        database.execute(
            """
            INSERT INTO chapter_analyses
              (id, book_id, root_section_id, version, status, structured_json,
               rendered_markdown, prompt_version, provider, model,
               fragment_set_id, created_at)
            VALUES (?, ?, ?, 1, 'succeeded', ?, ?, 'v1', 'demo', 'demo', ?, ?)
            """,
            (
                uuid.uuid4().hex,
                book_id,
                root["id"],
                json.dumps(data, ensure_ascii=False),
                f"# {root['title']}\nknowledge_deadbeef content_deadbeef",
                fragment_set_id,
                now,
            ),
        )


def test_artifact_repository_upserts_within_run_only(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    book_id = seed_chapter_book(database)
    now = now_iso()
    project_id = uuid.uuid4().hex
    database.execute(
        """
        INSERT INTO projects
          (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, '项目', ?, 'draft', ?, ?)
        """,
        (project_id, json.dumps([book_id]), now, now),
    )
    run_ids = []
    for position in range(2):
        run_id = uuid.uuid4().hex
        run_ids.append(run_id)
        database.execute(
            """
            INSERT INTO workflow_runs
              (id, scope_type, scope_id, stage, status, position,
               metadata_json, created_at, updated_at)
            VALUES (?, 'project_generation', ?, 'full', 'running', ?, '{}', ?, ?)
            """,
            (run_id, project_id, position, now, now),
        )
    repository = AlbumPlanningArtifactRepository(database)
    first = repository.upsert(
        run_id=run_ids[0],
        project_id=project_id,
        artifact_type="module_outline",
        module_key="MODULE_001",
        source_chapter_ids=["section-1"],
        content="old",
    )
    updated = repository.upsert(
        run_id=run_ids[0],
        project_id=project_id,
        artifact_type="module_outline",
        module_key="MODULE_001",
        source_chapter_ids=["section-1", "section-2"],
        content="new",
    )
    other = repository.upsert(
        run_id=run_ids[1],
        project_id=project_id,
        artifact_type="module_outline",
        module_key="MODULE_001",
        content="other",
    )

    assert updated["id"] == first["id"]
    assert updated["content"] == "new"
    assert updated["source_chapter_ids_json"] == ["section-1", "section-2"]
    assert other["id"] != first["id"]


def test_catalog_is_stable_lightweight_and_complete(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    book_id = seed_chapter_book(database, chapter_count=25)
    _seed_successful_analyses(database, book_id)
    service = AlbumPlanningService(database)

    entries, key_map = service.build_chapter_catalog(book_id)
    markdown = service.render_catalog(entries)
    planning_analysis = service.render_planning_analysis(entries)

    assert len(entries) == 25
    assert entries[0].chapter_key == "CHAPTER_001"
    assert entries[-1].chapter_key == "CHAPTER_025"
    assert list(key_map) == [f"CHAPTER_{position:03d}" for position in range(1, 26)]
    assert "主题 1" in markdown
    assert "子主题 25" in markdown
    assert "knowledge_" not in markdown
    assert "content_" not in markdown
    assert "观点 1" in planning_analysis
    assert "观点 25" in planning_analysis
    assert planning_analysis.index("[CHAPTER_001]") < planning_analysis.index(
        "[CHAPTER_025]"
    )
    assert "knowledge_" not in planning_analysis
    assert "content_" not in planning_analysis


def test_module_plan_allows_multiple_chapters_and_rejects_omissions() -> None:
    markdown = """
## 模块一：理解问题
听众问题：为什么会这样？
来源章节：[CHAPTER_001]、[CHAPTER_002]
建议声音数：4

## 模块二：寻找出路
来源章节：[CHAPTER_003]
建议声音数：3
"""
    modules = AlbumPlanningService.parse_module_plan(
        markdown, {"CHAPTER_001", "CHAPTER_002", "CHAPTER_003"}
    )
    assert modules[0].chapter_keys == ("CHAPTER_001", "CHAPTER_002")
    assert modules[0].suggested_episode_count == 4

    with pytest.raises(ValueError, match="遗漏章节"):
        AlbumPlanningService.parse_module_plan(
            markdown, {"CHAPTER_001", "CHAPTER_002", "CHAPTER_003", "CHAPTER_004"}
        )


def test_structured_outline_keeps_chapter_level_sources_and_allows_reuse() -> None:
    entries = [
        ChapterPlanningEntry(
            chapter_key=f"CHAPTER_{position:03d}",
            section_id=f"section-{position}",
            title=f"第{position}章",
            theme="主题",
            subtopic_titles=("子主题",),
            concise_points=("观点",),
            position=position,
        )
        for position in range(1, 4)
    ]
    main_points = (
        "听众钩子：为什么值得听？\n"
        "核心主题：解释一个问题。\n"
        "核心要点：\n1. 现象；\n2. 机制。"
    )
    episodes, _ = AlbumPlanningService.validate_structured_outline(
        {
            "album_outline": [
                {
                    "title": "跨章理解",
                    "main_points": main_points,
                    "chapter_keys": ["CHAPTER_001", "CHAPTER_002"],
                    "content_type": "解读",
                },
                {
                    "title": "换个角度再看第一章",
                    "main_points": main_points,
                    "chapter_keys": ["CHAPTER_001"],
                    "content_type": "解读",
                },
            ]
        },
        entries,
        book_type="non_narrative",
        desired_episode_count=None,
    )

    assert episodes[0]["source_section_ids"] == ["section-1", "section-2"]
    assert episodes[1]["source_section_ids"] == ["section-1"]
    assert episodes[0]["knowledge_item_ids"] == []
    assert episodes[0]["source_content_indexes"] == []


def test_structured_outline_keeps_and_validates_module_key() -> None:
    entries = [
        ChapterPlanningEntry(
            chapter_key=f"CHAPTER_{position:03d}",
            section_id=f"section-{position}",
            title=f"第{position}章",
            theme="主题",
            subtopic_titles=("子主题",),
            concise_points=("观点",),
            position=position,
        )
        for position in range(1, 3)
    ]
    modules = [
        AlbumModule(
            key="MODULE_001",
            title="第一个模块",
            listener_question="为什么？",
            chapter_keys=("CHAPTER_001",),
            suggested_episode_count=1,
            position=1,
        ),
        AlbumModule(
            key="MODULE_002",
            title="第二个模块",
            listener_question="怎么办？",
            chapter_keys=("CHAPTER_002",),
            suggested_episode_count=1,
            position=2,
        ),
    ]
    main_points = (
        "听众钩子：为什么值得听？\n"
        "核心主题：解释一个问题。\n"
        "核心要点：\n1. 现象；\n2. 机制。"
    )
    episodes, _ = AlbumPlanningService.validate_structured_outline(
        {
            "album_outline": [
                {
                    "title": "第一集",
                    "main_points": main_points,
                    "module_key": "MODULE_001",
                    "chapter_keys": ["CHAPTER_001"],
                    "content_type": "解读",
                }
            ]
        },
        entries,
        modules=modules,
        book_type="non_narrative",
        desired_episode_count=None,
    )
    assert episodes[0]["module_key"] == "MODULE_001"

    with pytest.raises(ValueError, match="所属模块之外"):
        AlbumPlanningService.validate_structured_outline(
            {
                "album_outline": [
                    {
                        "title": "越界声音",
                        "main_points": main_points,
                        "module_key": "MODULE_001",
                        "chapter_keys": ["CHAPTER_002"],
                        "content_type": "解读",
                    }
                ]
            },
            entries,
            modules=modules,
            book_type="non_narrative",
            desired_episode_count=None,
        )


def test_oversized_module_splits_without_losing_or_reordering_chapters() -> None:
    entries = [
        ChapterPlanningEntry(
            chapter_key=f"CHAPTER_{position:03d}",
            section_id=f"section-{position}",
            title=f"第{position}章",
            theme="主" * 120,
            subtopic_titles=("子主题",),
            concise_points=("观点" * 80,),
            position=position,
        )
        for position in range(1, 7)
    ]
    modules = AlbumPlanningService.split_oversized_modules(
        [
            AlbumModule(
                key="MODULE_001",
                title="长模块",
                listener_question="为什么？",
                chapter_keys=tuple(entry.chapter_key for entry in entries),
                suggested_episode_count=6,
                position=1,
            )
        ],
        entries,
        max_chars=600,
    )

    assert len(modules) > 1
    assert [key for module in modules for key in module.chapter_keys] == [
        entry.chapter_key for entry in entries
    ]
    assert all(
        len(AlbumPlanningService.render_module_source(entries, module.chapter_keys))
        <= 600
        for module in modules
    )


def _planning_entries(count: int) -> list[ChapterPlanningEntry]:
    return [
        ChapterPlanningEntry(
            chapter_key=f"CHAPTER_{position:03d}",
            section_id=f"section-{position}",
            title=f"第{position}章",
            theme=f"主题 {position}",
            subtopic_titles=(f"子主题 {position}",),
            concise_points=(f"观点 {position}",),
            position=position,
        )
        for position in range(1, count + 1)
    ]


def _planning_modules(count: int) -> list[AlbumModule]:
    return [
        AlbumModule(
            key=f"MODULE_{position:03d}",
            title=f"模块 {position}",
            listener_question=f"问题 {position}",
            chapter_keys=(f"CHAPTER_{position:03d}",),
            suggested_episode_count=(position % 5) + 1,
            position=position,
        )
        for position in range(1, count + 1)
    ]


def test_episode_budget_selects_total_within_range_and_merges_in_order() -> None:
    fifteen, budget = AlbumPlanningService.apply_episode_budget(
        _planning_modules(15),
        _planning_entries(15),
        desired_episode_count=15,
    )
    assert (budget.minimum_count, budget.maximum_count) == (13, 17)
    assert budget.selected_count == 17
    assert len(fifteen) == 15
    assert sum(module.suggested_episode_count for module in fifteen) == 17

    within_range, budget = AlbumPlanningService.apply_episode_budget(
        _planning_modules(4),
        _planning_entries(4),
        desired_episode_count=15,
    )
    assert budget.selected_count == 14
    assert sum(module.suggested_episode_count for module in within_range) == 14

    merged, budget = AlbumPlanningService.apply_episode_budget(
        _planning_modules(20),
        _planning_entries(20),
        desired_episode_count=15,
    )
    assert len(merged) == 17
    assert [key for module in merged for key in module.chapter_keys] == [
        f"CHAPTER_{position:03d}" for position in range(1, 21)
    ]
    assert budget.selected_count == 17
    assert sum(module.suggested_episode_count for module in merged) == 17


def test_module_and_final_outline_enforce_selected_episode_count() -> None:
    markdown = """## 第1集：第一集
听众钩子：为什么？
核心主题：解释问题。
核心要点：
1. 现象
内容类型：解读
来源章节：[CHAPTER_001]

## 第2集：第二集
听众钩子：然后呢？
核心主题：继续解释。
核心要点：
1. 原因
内容类型：解读
来源章节：[CHAPTER_001]
"""
    with pytest.raises(ValueError, match="本模块分配 1 集"):
        AlbumPlanningService.validate_module_outline(
            markdown,
            {"CHAPTER_001"},
            expected_episode_count=1,
        )

    main_points = (
        "听众钩子：为什么值得听？\n"
        "核心主题：解释一个问题。\n"
        "核心要点：\n1. 现象；\n2. 机制。"
    )
    data = {
        "album_outline": [
            {
                "title": f"声音 {position}",
                "main_points": main_points,
                "chapter_keys": ["CHAPTER_001"],
                "content_type": "解读",
            }
            for position in range(1, 3)
        ]
    }
    with pytest.raises(ValueError, match="本次规划总数为 1"):
        AlbumPlanningService.validate_structured_outline(
            data,
            _planning_entries(1),
            book_type="non_narrative",
            desired_episode_count=2,
            expected_episode_count=1,
            allowed_episode_range=(1, 4),
        )


def test_deterministic_module_outline_parser_normalizes_supported_markdown() -> None:
    entries = _planning_entries(2)
    modules = [
        AlbumModule(
            key="MODULE_001",
            title="完整模块",
            listener_question="为什么？",
            chapter_keys=("CHAPTER_001", "CHAPTER_002"),
            suggested_episode_count=2,
            position=1,
        )
    ]
    markdown = """## 第1集：普通标签
听众钩子：为什么值得听？
核心主题：解释第一个问题。
核心要点：
1. 先看现象；
2. 再看原因。
内容类型：解读类
来源章节：[CHAPTER_001]

## 第2集：加粗标签
**听众钩子**：另一个问题为什么重要？
**核心主题**：解释第二个问题。
**核心要点**：
1. 看变化；
2. 看结果。
**内容类型**：深度解读
**来源章节**：[CHAPTER_002]、[CHAPTER_002]
"""

    structured = AlbumPlanningService.parse_module_outlines(
        modules,
        {"MODULE_001": markdown},
        entries,
    )

    assert [item["title"] for item in structured["album_outline"]] == [
        "普通标签",
        "加粗标签",
    ]
    assert [item["module_key"] for item in structured["album_outline"]] == [
        "MODULE_001",
        "MODULE_001",
    ]
    assert [item["content_type"] for item in structured["album_outline"]] == [
        "解读",
        "解读",
    ]
    assert structured["album_outline"][1]["chapter_keys"] == ["CHAPTER_002"]
    assert structured["album_outline"][1]["main_points"] == (
        "听众钩子：另一个问题为什么重要？\n"
        "核心主题：解释第二个问题。\n"
        "核心要点：\n"
        "1. 看变化；\n"
        "2. 看结果。"
    )

    episodes, _ = AlbumPlanningService.validate_structured_outline(
        structured,
        entries,
        modules=modules,
        book_type="non_narrative",
        desired_episode_count=2,
        expected_episode_count=2,
        allowed_episode_range=(1, 4),
    )
    assert len(episodes) == 2


def test_deterministic_module_outline_parser_rejects_unsafe_variations() -> None:
    entries = _planning_entries(2)
    modules = [
        AlbumModule(
            key="MODULE_001",
            title="第一个模块",
            listener_question="为什么？",
            chapter_keys=("CHAPTER_001",),
            suggested_episode_count=1,
            position=1,
        )
    ]
    base = """## 第1集：测试声音
听众钩子：为什么值得听？
核心主题：解释问题。
核心要点：
1. 现象；
2. 原因。
内容类型：{content_type}
来源章节：[{chapter_key}]
"""

    with pytest.raises(ValueError, match="未知内容类型.*热点"):
        AlbumPlanningService.parse_module_outlines(
            modules,
            {
                "MODULE_001": base.format(
                    content_type="热点", chapter_key="CHAPTER_001"
                )
            },
            entries,
        )

    with pytest.raises(ValueError, match="缺少字段.*听众钩子"):
        AlbumPlanningService.parse_module_outlines(
            modules,
            {
                "MODULE_001": base.format(
                    content_type="解读", chapter_key="CHAPTER_001"
                ).replace("听众钩子：为什么值得听？\n", "")
            },
            entries,
        )

    with pytest.raises(ValueError, match="所属模块之外.*CHAPTER_002"):
        AlbumPlanningService.parse_module_outlines(
            modules,
            {
                "MODULE_001": base.format(
                    content_type="解读", chapter_key="CHAPTER_002"
                )
            },
            entries,
        )


def test_structured_outline_normalizes_supported_content_type_aliases() -> None:
    entries = _planning_entries(1)
    main_points = (
        "听众钩子：为什么值得听？\n"
        "核心主题：解释问题。\n"
        "核心要点：\n1. 现象；\n2. 机制。"
    )

    episodes, _ = AlbumPlanningService.validate_structured_outline(
        {
            "album_outline": [
                {
                    "title": "测试声音",
                    "main_points": main_points,
                    "chapter_keys": ["CHAPTER_001"],
                    "content_type": "深度解读",
                }
            ]
        },
        entries,
        book_type="non_narrative",
        desired_episode_count=None,
    )

    assert episodes[0]["content_type"] == "解读"
