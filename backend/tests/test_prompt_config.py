import uuid

from app.db import Database, now_iso
from app.prompt_config import (
    PROMPT_TEMPLATE_SPECS,
    PromptConfigurationService,
    render_user_template,
    validate_user_template,
)


def seed_project(database: Database) -> str:
    book_id = uuid.uuid4().hex
    project_id = uuid.uuid4().hex
    now = now_iso()
    database.execute(
        """
        INSERT INTO books
          (id, title, author, book_type, filename, status, source_type,
           created_at, updated_at)
        VALUES (?, '提示词测试书', '作者', 'non_narrative', 'test.md',
                'analyzed', 'markdown', ?, ?)
        """,
        (book_id, now, now),
    )
    database.execute(
        """
        INSERT INTO projects
          (id, title, book_ids, status, created_at, updated_at)
        VALUES (?, '提示词测试项目', ?, 'ready', ?, ?)
        """,
        (project_id, f'["{book_id}"]', now, now),
    )
    return project_id


def test_prompt_defaults_are_initialized_idempotently(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = PromptConfigurationService(database)
    service.ensure_defaults()

    templates = service.list_templates()

    assert len(templates) == 4
    assert {item["stage_key"] for item in templates} == set(
        PROMPT_TEMPLATE_SPECS
    )
    assert all(item["source_scope"] == "system" for item in templates)
    assert database.row(
        "SELECT COUNT(*) AS count FROM prompt_versions WHERE scope = 'system'"
    )["count"] == 4
    album = service.effective("album_outline")
    assert album["system_version"] == "2026-07-29.3"
    assert "module_source" in album["allowed_placeholders"]
    assert "只输出以下 Markdown 结构" in album["protected_suffix"]
    assert "knowledge_item_id" in album["protected_suffix"]
    assert "必须严格输出该数量" in album["protected_suffix"]
    assert "当前调用只处理一个知识模块" in album["protected_suffix"]


def test_prompt_template_validation_and_single_pass_rendering() -> None:
    spec = PROMPT_TEMPLATE_SPECS["episode_draft"]
    valid = "{{episode_outline}}\n{{source_text}}"
    validate_user_template(spec, valid)
    rendered = render_user_template(
        spec,
        valid,
        {
            "episode_outline": "细纲中保留 {{source_text}} 字样",
            "source_text": "原文",
        },
    )
    assert rendered == "细纲中保留 {{source_text}} 字样\n原文"

    for invalid, expected in (
        ("{{episode_outline}}", "缺少必要占位符"),
        ("{{episode_outline}}\n{{source_text}}\n{{unknown}}", "未知占位符"),
        ("{{episode_outline}}\n{{source_text", "花括号不完整"),
    ):
        try:
            validate_user_template(spec, invalid)
        except ValueError as error:
            assert expected in str(error)
        else:
            raise AssertionError(f"无效模板应被拒绝：{invalid}")

    album = PROMPT_TEMPLATE_SPECS["album_outline"]
    validate_user_template(album, "旧模板仍可用\n{{book_analysis}}")
    validate_user_template(album, "新模板\n{{chapter_catalog}}\n{{module_source}}")
    try:
        validate_user_template(album, "没有材料占位符")
    except ValueError as error:
        assert "章节目录或模块材料" in str(error)
    else:
        raise AssertionError("专辑模板必须保留至少一个材料占位符")


def test_global_and_project_prompt_versions_inherit_and_restore(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id = seed_project(database)
    service = PromptConfigurationService(database)
    system = service.effective("episode_draft", project_id)
    assert system["source_scope"] == "system"

    global_v1 = service.create_version(
        "episode_draft",
        "global",
        "全局一版\n{{episode_outline}}\n{{source_text}}",
    )
    assert global_v1["source_label"] == "全局 v1"
    assert (
        service.effective("episode_draft", project_id)["user_template"]
        == global_v1["user_template"]
    )

    project_v1 = service.create_version(
        "episode_draft",
        "project",
        "项目一版\n{{episode_outline}}\n{{source_text}}",
        project_id,
    )
    assert project_v1["source_label"] == "项目 v1"

    service.create_version(
        "episode_draft",
        "global",
        "全局二版\n{{episode_outline}}\n{{source_text}}",
    )
    assert (
        service.effective("episode_draft", project_id)["user_template"]
        == project_v1["user_template"]
    )

    restored = service.restore(
        "episode_draft",
        "project",
        project_v1["prompt_version_id"],
        project_id,
    )
    assert restored["source_label"] == "项目 v2"
    assert restored["user_template"] == project_v1["user_template"]
    assert len(service.history("episode_draft", "project", project_id)) == 2

    inherited = service.clear_project("episode_draft", project_id)
    assert inherited["source_label"] == "全局 v2"
    reset = service.reset_global("episode_draft")
    assert reset["source_scope"] == "system"
    assert service.effective("episode_draft", project_id)["source_scope"] == "system"


def test_locked_prompt_snapshot_does_not_follow_newer_global_version(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    project_id = seed_project(database)
    service = PromptConfigurationService(database)
    first = service.create_version(
        "episode_final",
        "global",
        "第一版\n{{episode_draft}}\n{{source_text}}",
    )
    locked = service.lock_stage("episode_final", project_id)
    service.create_version(
        "episode_final",
        "global",
        "第二版\n{{episode_draft}}\n{{source_text}}",
    )

    snapshot = service.snapshot(
        "episode_final",
        {
            "episode_draft": "初稿",
            "source_text": "原文",
        },
        project_id=project_id,
        locked=locked,
    )

    assert snapshot.prompt_version_id == first["prompt_version_id"]
    assert snapshot.source.startswith("第一版")
    assert "第二版" not in snapshot.source

    try:
        service.snapshot(
            "episode_draft",
            {
                "episode_outline": "细纲",
                "source_text": "原文",
            },
            project_id=project_id,
            locked=locked,
        )
    except ValueError as error:
        assert "锁定的提示词版本不存在" in str(error)
    else:
        raise AssertionError("其他环节的提示词版本不能混入声音初稿")
