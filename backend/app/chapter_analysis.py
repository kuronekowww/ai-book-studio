from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .evidence import build_fragment_records, compact_text


@dataclass(frozen=True)
class ChapterSource:
    root: dict[str, Any]
    sections: list[dict[str, Any]]
    source: str
    index_to_section_id: dict[str, str]
    fragment_set_id: str
    fragments_by_index: dict[str, dict[str, Any]]


def content_index(section_id: str) -> str:
    """Return the legacy section-level index used by historical records."""
    return f"content_{section_id.replace('-', '')}"


def build_chapter_source(
    root: dict[str, Any],
    all_sections: list[dict[str, Any]],
    fragments: list[dict[str, Any]] | None = None,
    fragment_set_id: str = "",
) -> ChapterSource:
    by_id = {section["id"]: section for section in all_sections}

    def belongs_to_root(section: dict[str, Any]) -> bool:
        current = section
        while current.get("parent_id") in by_id:
            current = by_id[current["parent_id"]]
        return current["id"] == root["id"]

    chapter_sections = [
        section
        for section in sorted(all_sections, key=lambda item: item["position"])
        if section.get("status") == "confirmed" and belongs_to_root(section)
    ]
    if fragments is None:
        generated = build_fragment_records(root["book_id"], all_sections)
        fragments = [
            {
                "content_index": item.content_index,
                "source_section_id": item.source_section_id,
                "root_section_id": item.root_section_id,
                "section_path_json": item.section_path,
                "book_position": item.book_position,
                "section_position": item.section_position,
                "text": item.text,
            }
            for item in generated
            if item.root_section_id == root["id"]
        ]
    fragments_by_section: dict[str, list[dict[str, Any]]] = {}
    for fragment in fragments:
        fragments_by_section.setdefault(fragment["source_section_id"], []).append(
            fragment
        )
    lines: list[str] = []
    fragments_by_index: dict[str, dict[str, Any]] = {}
    index_to_section_id: dict[str, str] = {}
    for section in chapter_sections:
        heading_level = max(1, min(6, int(section["level"])))
        lines.append(f"{'#' * heading_level} {section['title']}")
        section_fragments = sorted(
            fragments_by_section.get(section["id"], []),
            key=lambda item: (
                int(item.get("book_position", 0)),
                int(item.get("section_position", 0)),
            ),
        )
        for fragment in section_fragments:
            index = fragment["content_index"]
            path = fragment.get("section_path_json") or [section["title"]]
            if isinstance(path, str):
                path = json.loads(path)
            lines.extend(
                [
                    f"[content_index: {index}]",
                    f"[章节路径: {' / '.join(path)}]",
                    fragment["text"],
                    "",
                ]
            )
            stored = dict(fragment)
            stored["section_path_json"] = path
            fragments_by_index[index] = stored
            index_to_section_id[index] = fragment["source_section_id"]
    return ChapterSource(
        root=root,
        sections=chapter_sections,
        source="\n".join(lines).strip(),
        index_to_section_id=index_to_section_id,
        fragment_set_id=fragment_set_id,
        fragments_by_index=fragments_by_index,
    )


def parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 不能为空")
    return value.strip()


def _source_indexes(
    value: Any,
    field: str,
    fragments: dict[str, dict[str, Any]],
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} 必须包含至少一个 source_content_indexes")
    indexes: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} 的 source_content_indexes 无效")
        index = item.strip()
        if index not in fragments:
            raise ValueError(f"{field} 引用了无效 content_index：{index}")
        if index not in indexes:
            indexes.append(index)
    order = {
        index: int(fragment.get("book_position", position))
        for position, (index, fragment) in enumerate(fragments.items())
    }
    return sorted(indexes, key=lambda index: order[index])


def _validate_exact_source(
    text: str,
    indexes: list[str],
    fragments: dict[str, dict[str, Any]],
    field: str,
) -> None:
    evidence = compact_text(
        "\n".join(str(fragments[index]["text"]) for index in indexes)
    )
    if compact_text(text) not in evidence:
        raise ValueError(f"{field} 不是所引用原文块中的连续原文")


@dataclass(frozen=True)
class ChapterValidationResult:
    data: dict[str, Any]
    issues: list[dict[str, Any]]
    valid_item_count: int
    invalid_item_count: int


def _issue(
    asset_type: str,
    title: str,
    error: Exception,
    raw_item: Any,
) -> dict[str, Any]:
    indexes: list[str] = []
    if isinstance(raw_item, dict):
        raw_indexes = raw_item.get("source_content_indexes")
        if isinstance(raw_indexes, list):
            indexes = [str(item) for item in raw_indexes if isinstance(item, str)]
    return {
        "asset_type": asset_type,
        "title": compact_text(title)[:120] or "未命名条目",
        "error": str(error),
        "source_content_indexes": indexes,
    }


def validate_chapter_analysis_partial(
    data: dict[str, Any],
    allowed_fragments: dict[str, dict[str, Any]] | set[str],
) -> ChapterValidationResult:
    if isinstance(allowed_fragments, set):
        fragments = {
            index: {"text": "", "book_position": position}
            for position, index in enumerate(allowed_fragments)
        }
    else:
        fragments = allowed_fragments
    chapter_title = _require_text(data.get("chapter_title"), "chapter_title")
    chapter_theme = _require_text(data.get("chapter_theme"), "chapter_theme")
    raw_subtopics = data.get("subtopics")
    if not isinstance(raw_subtopics, list) or not raw_subtopics:
        raise ValueError("subtopics 必须是非空数组")

    subtopics: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    valid_item_count = 0
    for subtopic_position, raw_subtopic in enumerate(raw_subtopics, start=1):
        if not isinstance(raw_subtopic, dict):
            raise ValueError(f"第 {subtopic_position} 个子主题结构无效")
        raw_title = raw_subtopic.get("title")
        title = (
            raw_title.strip()
            if isinstance(raw_title, str) and raw_title.strip()
            else f"子主题 {subtopic_position}"
        )
        definitions: list[dict[str, Any]] = []
        for raw_definition in raw_subtopic.get("definitions") or []:
            try:
                if not isinstance(raw_definition, dict):
                    raise ValueError("definitions 条目结构无效")
                name = _require_text(raw_definition.get("name"), "definition.name")
                definition = _require_text(
                    raw_definition.get("definition"), "definition.definition"
                )
                indexes = _source_indexes(
                    raw_definition.get("source_content_indexes"),
                    f"概念“{name}”",
                    fragments,
                )
                definitions.append(
                    {
                        "name": name,
                        "definition": definition,
                        "source_content_indexes": indexes,
                    }
                )
                valid_item_count += 1
            except (TypeError, ValueError) as error:
                raw_name = (
                    raw_definition.get("name", "概念")
                    if isinstance(raw_definition, dict)
                    else "概念"
                )
                issues.append(_issue("概念", str(raw_name), error, raw_definition))

        quotes: list[dict[str, Any]] = []
        for raw_quote in raw_subtopic.get("quotes") or []:
            try:
                if not isinstance(raw_quote, dict):
                    raise ValueError("quotes 条目必须包含 text 和 source_content_indexes")
                text = _require_text(raw_quote.get("text"), "quote.text")
                indexes = _source_indexes(
                    raw_quote.get("source_content_indexes"), "金句", fragments
                )
                _validate_exact_source(text, indexes, fragments, "金句")
                quotes.append({"text": text, "source_content_indexes": indexes})
                valid_item_count += 1
            except (TypeError, ValueError) as error:
                raw_text = (
                    raw_quote.get("text", "金句")
                    if isinstance(raw_quote, dict)
                    else "金句"
                )
                issues.append(_issue("金句", str(raw_text), error, raw_quote))

        viewpoints: list[dict[str, Any]] = []
        orphan_arguments: list[dict[str, Any]] = []
        orphan_cases: list[dict[str, Any]] = []
        for raw_viewpoint in raw_subtopic.get("viewpoints") or []:
            if not isinstance(raw_viewpoint, dict):
                issues.append(
                    _issue(
                        "观点",
                        "结构无效的观点",
                        ValueError("viewpoints 条目结构无效"),
                        raw_viewpoint,
                    )
                )
                continue
            viewpoint_valid = True
            text = ""
            indexes: list[str] = []
            try:
                text = _require_text(raw_viewpoint.get("text"), "viewpoint.text")
                indexes = _source_indexes(
                    raw_viewpoint.get("source_content_indexes"),
                    "主要观点",
                    fragments,
                )
            except (TypeError, ValueError) as error:
                viewpoint_valid = False
                issues.append(
                    _issue(
                        "观点",
                        str(raw_viewpoint.get("text", "主要观点")),
                        error,
                        raw_viewpoint,
                    )
                )

            arguments: list[dict[str, Any]] = []
            for raw_argument in raw_viewpoint.get("arguments") or []:
                try:
                    if not isinstance(raw_argument, dict):
                        raise ValueError(
                            "arguments 条目必须包含 text 和 source_content_indexes"
                        )
                    argument_text = _require_text(
                        raw_argument.get("text"), "argument.text"
                    )
                    argument_indexes = _source_indexes(
                        raw_argument.get("source_content_indexes"), "论据", fragments
                    )
                    arguments.append(
                        {
                            "text": argument_text,
                            "source_content_indexes": argument_indexes,
                        }
                    )
                    valid_item_count += 1
                except (TypeError, ValueError) as error:
                    raw_text = (
                        raw_argument.get("text", "论据")
                        if isinstance(raw_argument, dict)
                        else "论据"
                    )
                    issues.append(_issue("论据", str(raw_text), error, raw_argument))

            case: dict[str, Any] | None = None
            raw_case = raw_viewpoint.get("case")
            if raw_case is not None:
                try:
                    if not isinstance(raw_case, dict):
                        raise ValueError("case 结构无效")
                    summary = _require_text(raw_case.get("summary"), "case.summary")
                    relation = _require_text(raw_case.get("relation"), "case.relation")
                    case_indexes = _source_indexes(
                        raw_case.get("source_content_indexes"), "案例", fragments
                    )
                    raw_evidence_quotes = raw_case.get("evidence_quotes")
                    if (
                        not isinstance(raw_evidence_quotes, list)
                        or not raw_evidence_quotes
                    ):
                        raise ValueError("案例必须包含至少一条 evidence_quotes")
                    evidence_quotes: list[dict[str, Any]] = []
                    for raw_evidence in raw_evidence_quotes:
                        try:
                            if not isinstance(raw_evidence, dict):
                                raise ValueError("案例 evidence_quotes 结构无效")
                            evidence_text = _require_text(
                                raw_evidence.get("text"),
                                "case.evidence_quote.text",
                            )
                            evidence_indexes = _source_indexes(
                                raw_evidence.get("source_content_indexes"),
                                "案例证据",
                                fragments,
                            )
                            if not set(evidence_indexes).issubset(case_indexes):
                                raise ValueError(
                                    "案例证据索引必须包含在案例来源索引中"
                                )
                            evidence_quotes.append(
                                {
                                    "text": evidence_text,
                                    "source_content_indexes": evidence_indexes,
                                }
                            )
                        except (TypeError, ValueError) as error:
                            raw_text = (
                                raw_evidence.get("text", "案例证据")
                                if isinstance(raw_evidence, dict)
                                else "案例证据"
                            )
                            issues.append(
                                _issue(
                                    "案例证据",
                                    str(raw_text),
                                    error,
                                    raw_evidence,
                                )
                            )
                    if not evidence_quotes:
                        raise ValueError("案例没有通过来源校验的 evidence_quotes")
                    case = {
                        "summary": summary,
                        "relation": relation,
                        "source_content_indexes": case_indexes,
                        "evidence_quotes": evidence_quotes,
                    }
                    valid_item_count += 1
                except (TypeError, ValueError) as error:
                    raw_summary = (
                        raw_case.get("summary", "案例")
                        if isinstance(raw_case, dict)
                        else "案例"
                    )
                    issues.append(
                        _issue("案例", str(raw_summary), error, raw_case)
                    )
                    case = None
            if viewpoint_valid:
                viewpoints.append(
                    {
                        "text": text,
                        "source_content_indexes": indexes,
                        "arguments": arguments,
                        "case": case,
                    }
                )
                valid_item_count += 1
            else:
                orphan_arguments.extend(arguments)
                if case:
                    orphan_cases.append(case)
        subtopics.append(
            {
                "title": title,
                "definitions": definitions,
                "quotes": quotes,
                "viewpoints": viewpoints,
                "orphan_arguments": orphan_arguments,
                "orphan_cases": orphan_cases,
            }
        )
    normalized = {
        "chapter_title": chapter_title,
        "chapter_theme": chapter_theme,
        "subtopics": subtopics,
    }
    return ChapterValidationResult(
        data=normalized,
        issues=issues,
        valid_item_count=valid_item_count,
        invalid_item_count=len(issues),
    )


def validate_chapter_analysis(
    data: dict[str, Any],
    allowed_fragments: dict[str, dict[str, Any]] | set[str],
) -> dict[str, Any]:
    result = validate_chapter_analysis_partial(data, allowed_fragments)
    if result.issues:
        raise ValueError(result.issues[0]["error"])
    return result.data


def _stable_key(
    book_id: str, kind: str, body: str, source_content_indexes: list[str]
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "book_id": book_id,
                "kind": kind,
                "body": compact_text(body),
                "sources": source_content_indexes,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _card(
    book_id: str,
    kind: str,
    title: str,
    body: str,
    indexes: list[str],
    index_to_section_id: dict[str, str],
) -> dict[str, Any]:
    key = _stable_key(book_id, kind, body, indexes)
    return {
        "id": f"knowledge_{key[:24]}",
        "stable_key": key,
        "kind": kind,
        "title": title,
        "body": body,
        "source_content_indexes": indexes,
        "source_section_ids": list(
            dict.fromkeys(index_to_section_id[index] for index in indexes)
        ),
    }


def derive_knowledge_cards(
    data: dict[str, Any],
    index_to_section_id: dict[str, str],
    book_id: str = "",
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for subtopic in data["subtopics"]:
        for definition in subtopic["definitions"]:
            cards.append(
                _card(
                    book_id,
                    "概念",
                    definition["name"],
                    definition["definition"],
                    definition["source_content_indexes"],
                    index_to_section_id,
                )
            )
        for quote in subtopic["quotes"]:
            cards.append(
                _card(
                    book_id,
                    "金句",
                    f"{subtopic['title']} · 金句",
                    quote["text"],
                    quote["source_content_indexes"],
                    index_to_section_id,
                )
            )
        for viewpoint in subtopic["viewpoints"]:
            cards.append(
                _card(
                    book_id,
                    "观点",
                    subtopic["title"],
                    viewpoint["text"],
                    viewpoint["source_content_indexes"],
                    index_to_section_id,
                )
            )
            for argument in viewpoint["arguments"]:
                cards.append(
                    _card(
                        book_id,
                        "论据",
                        f"{subtopic['title']} · 论据",
                        argument["text"],
                        argument["source_content_indexes"],
                        index_to_section_id,
                    )
                )
            case = viewpoint["case"]
            if case:
                body = f"{case['summary']}\n\n关联：{case['relation']}"
                cards.append(
                    _card(
                        book_id,
                        "案例",
                        f"{subtopic['title']} · 案例",
                        body,
                        case["source_content_indexes"],
                        index_to_section_id,
                    )
                )
        for argument in subtopic.get("orphan_arguments", []):
            cards.append(
                _card(
                    book_id,
                    "论据",
                    f"{subtopic['title']} · 论据（观点未通过校验）",
                    argument["text"],
                    argument["source_content_indexes"],
                    index_to_section_id,
                )
            )
        for case in subtopic.get("orphan_cases", []):
            body = f"{case['summary']}\n\n关联：{case['relation']}"
            cards.append(
                _card(
                    book_id,
                    "案例",
                    f"{subtopic['title']} · 案例（观点未通过校验）",
                    body,
                    case["source_content_indexes"],
                    index_to_section_id,
                )
            )
    return cards


def _render_source(indexes: list[str]) -> str:
    return "、".join(indexes)


def render_chapter_markdown(
    data: dict[str, Any],
    cards: list[dict[str, Any]] | None = None,
) -> str:
    card_lookup: dict[tuple[str, str, tuple[str, ...]], str] = {}
    for card in cards or []:
        key = (card["kind"], card["body"], tuple(card["source_content_indexes"]))
        card_lookup[key] = card["id"]

    def identity(kind: str, body: str, indexes: list[str]) -> str:
        return card_lookup.get((kind, body, tuple(indexes)), "")

    lines = [
        f"# {data['chapter_title']}",
        f"**章节主题：** {data['chapter_theme']}",
    ]
    for subtopic in data["subtopics"]:
        lines.extend(["", f"## 子主题：{subtopic['title']}"])
        for definition in subtopic["definitions"]:
            indexes = definition["source_content_indexes"]
            lines.extend(
                [
                    "",
                    "### 概念定义",
                    f"**知识资产 ID：** {identity('概念', definition['definition'], indexes)}",
                    f"**原文索引：** {_render_source(indexes)}",
                    f"**{definition['name']}：** {definition['definition']}",
                ]
            )
        if subtopic["quotes"]:
            lines.extend(["", "### 金句"])
            for quote in subtopic["quotes"]:
                indexes = quote["source_content_indexes"]
                lines.extend(
                    [
                        f"- {quote['text']}",
                        f"  - 知识资产 ID：{identity('金句', quote['text'], indexes)}",
                        f"  - 原文索引：{_render_source(indexes)}",
                    ]
                )
        for position, viewpoint in enumerate(subtopic["viewpoints"], start=1):
            indexes = viewpoint["source_content_indexes"]
            lines.extend(
                [
                    "",
                    f"### 主要观点{position}：{viewpoint['text']}",
                    f"**知识资产 ID：** {identity('观点', viewpoint['text'], indexes)}",
                    f"**原文索引：** {_render_source(indexes)}",
                    "**论据：**",
                ]
            )
            for argument in viewpoint["arguments"]:
                argument_indexes = argument["source_content_indexes"]
                lines.extend(
                    [
                        f"- {argument['text']}",
                        "  - 知识资产 ID："
                        f"{identity('论据', argument['text'], argument_indexes)}",
                        f"  - 原文索引：{_render_source(argument_indexes)}",
                    ]
                )
            case = viewpoint["case"]
            if case:
                case_indexes = case["source_content_indexes"]
                case_body = f"{case['summary']}\n\n关联：{case['relation']}"
                lines.extend(
                    [
                        "",
                        "**案例故事：**",
                        f"- **概述：** {case['summary']}",
                        f"- **关联：** {case['relation']}",
                        f"- **知识资产 ID：** {identity('案例', case_body, case_indexes)}",
                        f"- **原文索引：** {_render_source(case_indexes)}",
                        "- **证据原文：**",
                    ]
                )
                for evidence in case["evidence_quotes"]:
                    lines.append(
                        f"  - {evidence['text']} "
                        f"（{_render_source(evidence['source_content_indexes'])}）"
                    )
        if subtopic.get("orphan_arguments"):
            lines.extend(["", "### 独立保留的论据（所属观点未通过原文校验）"])
            for argument in subtopic["orphan_arguments"]:
                argument_indexes = argument["source_content_indexes"]
                lines.extend(
                    [
                        f"- {argument['text']}",
                        "  - 知识资产 ID："
                        f"{identity('论据', argument['text'], argument_indexes)}",
                        f"  - 原文索引：{_render_source(argument_indexes)}",
                    ]
                )
        if subtopic.get("orphan_cases"):
            lines.extend(["", "### 独立保留的案例（所属观点未通过原文校验）"])
            for case in subtopic["orphan_cases"]:
                case_indexes = case["source_content_indexes"]
                case_body = f"{case['summary']}\n\n关联：{case['relation']}"
                lines.extend(
                    [
                        f"- **概述：** {case['summary']}",
                        f"- **关联：** {case['relation']}",
                        f"- **知识资产 ID：** {identity('案例', case_body, case_indexes)}",
                        f"- **原文索引：** {_render_source(case_indexes)}",
                        "- **证据原文：**",
                    ]
                )
                for evidence in case["evidence_quotes"]:
                    lines.append(
                        f"  - {evidence['text']} "
                        f"（{_render_source(evidence['source_content_indexes'])}）"
                    )
    return "\n".join(lines)
