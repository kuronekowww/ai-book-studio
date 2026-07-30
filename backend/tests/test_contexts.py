import json
import uuid

import pytest

from app.contexts import EpisodeContextBuilder
from app.db import Database, now_iso


def seed_context(
    database: Database, *, book_type: str = "narrative"
) -> tuple[str, str, str]:
    now = now_iso()
    book_id = uuid.uuid4().hex
    project_id = uuid.uuid4().hex
    episode_id = uuid.uuid4().hex
    source_id = uuid.uuid4().hex
    other_source_id = uuid.uuid4().hex
    database.execute(
        """
        INSERT INTO books
          (id, title, author, book_type, filename, status, source_type,
           parse_version, created_at, updated_at)
        VALUES (?, '叙事测试书', '测试作者', ?, 'test.md', 'analyzed',
                'markdown', 1, ?, ?)
        """,
        (book_id, book_type, now, now),
    )
    database.executemany(
        """
        INSERT INTO sections
          (id, book_id, parent_id, level, position, title, content, kind, status)
        VALUES (?, ?, NULL, 4, ?, ?, ?, 'article', 'confirmed')
        """,
        [
            (source_id, book_id, 1, "当前章节", "当前章节的完整原文。人物甲遇见人物乙。"),
            (other_source_id, book_id, 2, "其他章节", "不应进入当前声音的其他原文。"),
        ],
    )
    database.execute(
        """
        INSERT INTO projects (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, '测试专辑', ?, 'ready', ?, ?)
        """,
        (project_id, json.dumps([book_id]), now, now),
    )
    database.execute(
        """
        INSERT INTO episodes
          (id, project_id, position, title, content_type, style,
           content_framework, status, source_section_ids)
        VALUES (?, ?, 1, '相遇', '故事', '观点', '先介绍人物，再讲相遇事件。',
                'ready', ?)
        """,
        (episode_id, project_id, json.dumps([source_id])),
    )
    database.executemany(
        """
        INSERT INTO knowledge_items
          (id, book_id, kind, title, body, source_section_ids, created_at)
        VALUES (?, ?, '人物关系', ?, ?, ?, ?)
        """,
        [
            (
                uuid.uuid4().hex,
                book_id,
                "当前人物",
                "人物甲与人物乙是同行者。",
                json.dumps([source_id]),
                now,
            ),
            (
                uuid.uuid4().hex,
                book_id,
                "其他人物",
                "人物丙与人物丁是对手。",
                json.dumps([other_source_id]),
                now,
            ),
        ],
    )
    return episode_id, source_id, other_source_id


def test_narrative_outline_uses_framework_matching_relationships_and_source(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    episode_id, source_id, other_source_id = seed_context(database)

    context = EpisodeContextBuilder(database).build(episode_id, "outline")

    assert context.prompt_id == "episode_outline_narrative"
    assert "先介绍人物，再讲相遇事件。" in context.source
    assert "人物甲与人物乙是同行者。" in context.source
    assert "人物丙与人物丁是对手。" not in context.source
    assert source_id in context.source
    assert other_source_id not in context.source
    assert "当前章节的完整原文" in context.source
    assert context.variables["episode_framework"] == "先介绍人物，再讲相遇事件。"
    assert "人物甲与人物乙是同行者。" in context.variables["character_relationships"]
    assert "当前章节的完整原文" in context.variables["source_text"]
    assert "2000–2500" in context.variables["episode_word_count_range"]


def test_non_narrative_outline_omits_relationship_section(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    episode_id, _, _ = seed_context(database, book_type="non_narrative")

    context = EpisodeContextBuilder(database).build(episode_id, "outline")

    assert context.prompt_id == "episode_outline_non_narrative"
    assert "# 人物关系" not in context.source
    assert "人物甲与人物乙是同行者。" not in context.source
    assert context.variables["character_relationships"] == "非故事类书籍无须提供人物关系。"


def test_draft_and_final_include_latest_previous_artifact_and_same_source(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    episode_id, source_id, _ = seed_context(database)
    now = now_iso()
    database.executemany(
        """
        INSERT INTO artifact_versions
          (id, episode_id, stage, version, content, prompt_version,
           provider, model, author_type, input_snapshot, created_at)
        VALUES (?, ?, ?, 1, ?, 'test-v1', 'demo', 'demo', 'model', '', ?)
        """,
        [
            (uuid.uuid4().hex, episode_id, "outline", "最新细纲", now),
            (uuid.uuid4().hex, episode_id, "draft", "最新初稿", now),
        ],
    )

    draft = EpisodeContextBuilder(database).build(episode_id, "draft")
    final = EpisodeContextBuilder(database).build(episode_id, "final")

    assert draft.prompt_id == "episode_draft"
    assert "最新细纲" in draft.source
    assert "最新初稿" in final.source
    assert source_id in draft.source and source_id in final.source
    assert "当前章节的完整原文" in draft.source
    assert "当前章节的完整原文" in final.source
    assert draft.variables["episode_outline"] == "最新细纲"
    assert final.variables["episode_draft"] == "最新初稿"
    assert draft.variables["episode_word_count_range"] == (
        final.variables["episode_word_count_range"]
    )


def test_missing_previous_artifact_fails_before_generation(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    episode_id, _, _ = seed_context(database)

    with pytest.raises(ValueError, match="缺少上一步产物"):
        EpisodeContextBuilder(database).build(episode_id, "draft")
