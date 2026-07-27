from pathlib import Path

from app.ingestion import analysis_candidate_map, parse_markdown


def test_literal_newlines_and_heading_tree() -> None:
    parsed = parse_markdown(
        "# 正文\n### 主题\\n内容\\n#### 文章\\n第一段。\\n第二段。[1]",
        "测试.md",
    )
    assert parsed.diagnostics["literal_newlines_restored"] == 4
    assert parsed.diagnostics["h3_count"] == 1
    assert parsed.diagnostics["h4_count"] == 1
    article = next(section for section in parsed.sections if section.level == 4)
    assert "第一段" in article.content


def test_circle_justice_structure_when_fixture_exists() -> None:
    fixture = Path("/Users/xmly/Downloads/拆书/圆圈正义.md")
    if not fixture.exists():
        return
    parsed = parse_markdown(fixture.read_text("utf-8"), fixture.name)
    assert parsed.diagnostics["h3_count"] == 8
    assert parsed.diagnostics["h4_count"] == 49
    assert parsed.diagnostics["literal_newlines_restored"] == 1286


def test_analysis_candidates_include_chapters_and_exclude_noise() -> None:
    parsed = parse_markdown(
        "# 目录\n很短\n"
        "# 第一章 社会结构\n"
        + ("开篇。" * 200)
        + "\n## 第一节 分层\n"
        + ("正文。" * 200)
        + "\n# 年龄组下各栏注：数据说明\n很短",
        "测试.md",
    )
    candidates = analysis_candidate_map(parsed.sections)
    roots = [section for section in parsed.sections if section.parent_id is None]

    assert candidates[roots[0].id][0] is False
    assert candidates[roots[1].id][0] is True
    assert candidates[roots[2].id][0] is False
