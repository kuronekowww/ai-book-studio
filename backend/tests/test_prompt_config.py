import uuid

from app.db import Database, now_iso
from app.prompt_config import (
    PROMPT_TEMPLATE_SPECS,
    PromptConfigurationService,
    protected_suffix_for_runtime,
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

    assert len(templates) == 6
    assert {item["stage_key"] for item in templates} == set(
        PROMPT_TEMPLATE_SPECS
    )
    assert all(item["source_scope"] == "system" for item in templates)
    assert database.row(
        "SELECT COUNT(*) AS count FROM prompt_versions WHERE scope = 'system'"
    )["count"] == 6
    album = service.effective("album_outline")
    assert album["system_version"] == "2026-07-30.3"
    assert album["label"] == "分模块专辑大纲"
    assert "module_book_analysis" in album["allowed_placeholders"]
    assert "module_source" in album["allowed_placeholders"]
    assert "只输出以下 Markdown 结构" in album["protected_suffix"]
    assert "knowledge_item_id" in album["protected_suffix"]
    assert "必须严格输出该数量" in album["protected_suffix"]
    assert "当前调用只处理一个知识模块" in album["protected_suffix"]
    assert "episode_word_count_range" in album["allowed_placeholders"]
    assert service.effective("mind_map")["required_placeholder_groups"] == [
        ["full_book_analysis", "book_analysis"]
    ]
    assert service.effective("album_module_plan")["required_placeholders"] == [
        "chapter_catalog"
    ]


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
    validate_user_template(
        album, "新模板\n{{module_brief}}\n{{module_book_analysis}}"
    )
    try:
        validate_user_template(album, "{{module_brief}}\n没有材料占位符")
    except ValueError as error:
        assert "必要材料占位符" in str(error)
    else:
        raise AssertionError("专辑模板必须保留至少一个材料占位符")

    outline = PROMPT_TEMPLATE_SPECS["episode_outline"]
    validate_user_template(
        outline, "{{episode_framework}}\n{{module_book_analysis}}"
    )
    validate_user_template(outline, "{{episode_framework}}\n{{source_text}}")


def test_runtime_repairs_legacy_album_template_without_module_material(
    tmp_path,
) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = PromptConfigurationService(database)
    template = database.row(
        "SELECT * FROM prompt_templates WHERE stage_key = 'album_outline'"
    )
    assert template
    system = service.effective("album_outline")
    legacy_id = uuid.uuid4().hex
    database.execute(
        """
        INSERT INTO prompt_versions
          (id, template_id, scope, project_id, version, user_template,
           base_system_version_id, created_at)
        VALUES (?, ?, 'global', NULL, 1, ?, ?, ?)
        """,
        (
            legacy_id,
            template["id"],
            "旧版只写创作要求，没有材料占位符。",
            system["system_version_id"],
            now_iso(),
        ),
    )
    database.execute(
        """
        UPDATE prompt_templates SET active_global_version_id = ?
        WHERE id = ?
        """,
        (legacy_id, template["id"]),
    )

    snapshot = service.snapshot(
        "album_outline",
        {
            "module_book_analysis": "当前模块真实拆书稿",
            "module_brief": "当前模块任务",
        },
    )

    assert "旧版只写创作要求" in snapshot.source
    assert "当前模块真实拆书稿" in snapshot.source
    assert snapshot.user_template == "旧版只写创作要求，没有材料占位符。"


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
    assert "{{episode_word_count_range}}" not in snapshot.source

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


def test_deepseek_v4_storytelling_defaults_keep_six_stage_responsibilities() -> None:
    expected_versions = {
        "mind_map": "2026-07-30.2",
        "album_module_plan": "2026-07-30.2",
        "album_outline": "2026-07-30.3",
        "episode_outline": "2026-07-30.3",
        "episode_draft": "2026-07-30.2",
        "episode_final": "2026-07-30.2",
    }
    assert {
        key: spec.system_version
        for key, spec in PROMPT_TEMPLATE_SPECS.items()
    } == expected_versions

    mind_map = PROMPT_TEMPLATE_SPECS["mind_map"]
    assert "不负责声音标题" in mind_map.default_user_template
    assert "未读听众的理解路径" in mind_map.default_user_template

    module_plan = PROMPT_TEMPLATE_SPECS["album_module_plan"]
    assert "只设计知识模块" in module_plan.default_user_template
    assert "不生成逐集" in module_plan.protected_suffix

    album = PROMPT_TEMPLATE_SPECS["album_outline"]
    assert "听众钩子" in album.default_user_template
    assert "唯一中心问题" in album.default_user_template
    assert "连续收听" in album.default_user_template

    outline = PROMPT_TEMPLATE_SPECS["episode_outline"]
    assert "听众最终应能复述的判断" in outline.default_user_template
    assert "故事抓手" in outline.default_user_template
    assert "字数预算" in outline.default_user_template
    assert "明确舍弃" in outline.default_user_template

    draft = PROMPT_TEMPLATE_SPECS["episode_draft"]
    assert "现实现象或生活困惑" in draft.default_user_template
    assert "常识预期" in draft.default_user_template
    assert "反差结果" in draft.default_user_template
    assert "不连续堆叠" in draft.protected_suffix
    assert "不换一种说法重复总结" in draft.protected_suffix

    final = PROMPT_TEMPLATE_SPECS["episode_final"]
    assert "只做减法编辑" in final.default_user_template
    assert "不重新选题" in final.default_user_template
    assert "不得增加初稿未覆盖的新知识点" in final.protected_suffix
    assert "内部检查" in final.protected_suffix


def test_episode_prompt_snapshot_and_preview_branch_by_book_type(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    service = PromptConfigurationService(database)
    stage_values = {
        "episode_outline": {
            "episode_framework": "本集框架",
            "module_book_analysis": "模块拆书稿",
            "episode_word_count_range": "2000–2500 字",
        },
        "episode_draft": {
            "episode_outline": "声音细纲",
            "source_text": "段落级原文",
            "previous_episode_final": "当前没有可用的上一集终稿。",
            "episode_word_count_range": "2000–2500 字",
        },
        "episode_final": {
            "episode_draft": "声音初稿",
            "source_text": "段落级原文",
            "previous_episode_final": "上一集终稿正文",
            "episode_word_count_range": "2000–2500 字",
        },
    }

    for stage_key, values in stage_values.items():
        narrative = service.snapshot(
            stage_key, values, book_type="narrative"
        )
        non_narrative = service.snapshot(
            stage_key, values, book_type="non_narrative"
        )
        assert "人物处境" in narrative.protected_suffix
        assert "原文明示的动机" in narrative.protected_suffix
        assert "问题、概念、机制" in non_narrative.protected_suffix
        assert "明确标注为“假设”" in non_narrative.protected_suffix

        narrative_preview = service.preview(
            stage_key,
            PROMPT_TEMPLATE_SPECS[stage_key].default_user_template,
            values,
            book_type="narrative",
        )
        non_narrative_preview = service.preview(
            stage_key,
            PROMPT_TEMPLATE_SPECS[stage_key].default_user_template,
            values,
            book_type="non_narrative",
        )
        assert "人物处境" in narrative_preview["protected_suffix"]
        assert "问题、概念、机制" in non_narrative_preview["protected_suffix"]

    outline = service.snapshot(
        "episode_outline",
        stage_values["episode_outline"],
        book_type="non_narrative",
    )
    assert "具体回顾措辞" in outline.protected_suffix
    assert "当前没有可用的上一集终稿" not in outline.source

    draft = service.snapshot(
        "episode_draft",
        stage_values["episode_draft"],
        book_type="non_narrative",
    )
    final = service.snapshot(
        "episode_final",
        stage_values["episode_final"],
        book_type="non_narrative",
    )
    assert "只有输入中确实提供可用的上一集终稿" in draft.protected_suffix
    assert "只有输入中确实提供可用的上一集终稿" in final.protected_suffix


def test_legacy_locked_system_suffix_keeps_legacy_book_type_behavior() -> None:
    legacy_outline = protected_suffix_for_runtime(
        "episode_outline",
        "# 旧版细纲保护层",
        {},
        "narrative",
    )
    legacy_draft = protected_suffix_for_runtime(
        "episode_draft",
        "# 旧版初稿保护层",
        {},
        "narrative",
    )

    assert "只能使用输入提及的人物与事件" in legacy_outline
    assert "人物处境" not in legacy_outline
    assert legacy_draft == "# 旧版初稿保护层"
