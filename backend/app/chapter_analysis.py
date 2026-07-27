from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChapterSource:
    root: dict[str, Any]
    sections: list[dict[str, Any]]
    source: str
    index_to_section_id: dict[str, str]


def content_index(section_id: str) -> str:
    return f"content_{section_id}"


def build_chapter_source(
    root: dict[str, Any], all_sections: list[dict[str, Any]]
) -> ChapterSource:
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    for section in all_sections:
        by_parent.setdefault(section["parent_id"], []).append(section)
    for children in by_parent.values():
        children.sort(key=lambda item: item["position"])

    ordered: list[dict[str, Any]] = []

    def visit(section: dict[str, Any]) -> None:
        ordered.append(section)
        for child in by_parent.get(section["id"], []):
            visit(child)

    visit(root)
    mapping = {content_index(item["id"]): item["id"] for item in ordered}
    blocks: list[str] = []
    for item in ordered:
        index = content_index(item["id"])
        heading = "#" * max(1, min(int(item["level"]), 6))
        blocks.append(
            f"{heading} {item['title']}\n"
            f"[content_index: {index}]\n"
            f"{item['content'].strip()}"
        )
    return ChapterSource(
        root=root,
        sections=ordered,
        source="\n\n".join(blocks).strip(),
        index_to_section_id=mapping,
    )


def parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return data


def validate_chapter_analysis(
    data: dict[str, Any], allowed_indexes: set[str]
) -> dict[str, Any]:
    chapter_title = data.get("chapter_title")
    chapter_theme = data.get("chapter_theme")
    subtopics = data.get("subtopics")
    if not isinstance(chapter_title, str) or not chapter_title.strip():
        raise ValueError("章节拆书输出缺少 chapter_title")
    if not isinstance(chapter_theme, str) or not chapter_theme.strip():
        raise ValueError("章节拆书输出缺少 chapter_theme")
    if not isinstance(subtopics, list) or not subtopics:
        raise ValueError("章节拆书输出缺少 subtopics")
    for subtopic in subtopics:
        if not isinstance(subtopic, dict):
            raise ValueError("章节拆书 subtopic 结构无效")
        if not isinstance(subtopic.get("title"), str) or not subtopic["title"].strip():
            raise ValueError("章节拆书子主题缺少标题")
        index = subtopic.get("content_index")
        if index not in allowed_indexes:
            raise ValueError(f"章节拆书引用了无效 content_index：{index}")
        for key in ("definitions", "quotes", "viewpoints"):
            value = subtopic.get(key, [])
            if not isinstance(value, list):
                raise ValueError(f"章节拆书字段 {key} 必须是数组")
        for definition in subtopic.get("definitions", []):
            if not isinstance(definition, dict) or not all(
                isinstance(definition.get(key), str) and definition[key].strip()
                for key in ("name", "definition")
            ):
                raise ValueError("概念定义结构无效")
        for quote in subtopic.get("quotes", []):
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError("金句结构无效")
        for viewpoint in subtopic.get("viewpoints", []):
            if not isinstance(viewpoint, dict):
                raise ValueError("主要观点结构无效")
            if not isinstance(viewpoint.get("text"), str) or not viewpoint["text"].strip():
                raise ValueError("主要观点缺少原文表述")
            arguments = viewpoint.get("arguments", [])
            if not isinstance(arguments, list) or not all(
                isinstance(argument, str) and argument.strip()
                for argument in arguments
            ):
                raise ValueError("论据结构无效")
            case = viewpoint.get("case")
            if case is not None and (
                not isinstance(case, dict)
                or not isinstance(case.get("summary", ""), str)
                or not isinstance(case.get("relation", ""), str)
            ):
                raise ValueError("案例故事结构无效")
    return data


def render_chapter_markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# {data['chapter_title'].strip()}",
        f"**章节主题：** {data['chapter_theme'].strip()}",
    ]
    for subtopic in data["subtopics"]:
        lines.extend(
            [
                "",
                f"## 子主题：{subtopic['title'].strip()}",
                f"**内容索引：** {subtopic['content_index']}",
            ]
        )
        definitions = subtopic.get("definitions", [])
        if definitions:
            lines.extend(["", "### 概念定义"])
            for item in definitions:
                lines.append(
                    f"**{item['name'].strip()}：** {item['definition'].strip()}"
                )
        quotes = subtopic.get("quotes", [])
        if quotes:
            lines.extend(["", "### 金句"])
            lines.extend(f"- {quote.strip()}" for quote in quotes)
        for number, viewpoint in enumerate(subtopic.get("viewpoints", []), start=1):
            lines.extend(
                [
                    "",
                    f"### 主要观点{number}：{viewpoint['text'].strip()}",
                ]
            )
            arguments = viewpoint.get("arguments", [])
            if arguments:
                lines.append("**论据：**")
                lines.extend(f"- {argument.strip()}" for argument in arguments)
            case = viewpoint.get("case")
            if isinstance(case, dict) and (
                case.get("summary", "").strip() or case.get("relation", "").strip()
            ):
                lines.extend(["", "**案例故事：**"])
                if case.get("summary", "").strip():
                    lines.append(f"- **概述：** {case['summary'].strip()}")
                if case.get("relation", "").strip():
                    lines.append(f"- **关联：** {case['relation'].strip()}")
    return "\n".join(lines).strip()


def derive_knowledge_cards(
    data: dict[str, Any], index_to_section_id: dict[str, str]
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for subtopic in data["subtopics"]:
        source_ids = [index_to_section_id[subtopic["content_index"]]]
        prefix = subtopic["title"].strip()
        for item in subtopic.get("definitions", []):
            cards.append(
                {
                    "kind": "概念",
                    "title": item["name"].strip(),
                    "body": item["definition"].strip(),
                    "source_section_ids": source_ids,
                }
            )
        for quote in subtopic.get("quotes", []):
            cards.append(
                {
                    "kind": "金句",
                    "title": f"{prefix} · 金句",
                    "body": quote.strip(),
                    "source_section_ids": source_ids,
                }
            )
        for viewpoint in subtopic.get("viewpoints", []):
            cards.append(
                {
                    "kind": "观点",
                    "title": prefix,
                    "body": viewpoint["text"].strip(),
                    "source_section_ids": source_ids,
                }
            )
            for argument in viewpoint.get("arguments", []):
                cards.append(
                    {
                        "kind": "论据",
                        "title": f"{prefix} · 论据",
                        "body": argument.strip(),
                        "source_section_ids": source_ids,
                    }
                )
            case = viewpoint.get("case")
            if isinstance(case, dict) and case.get("summary", "").strip():
                body = case["summary"].strip()
                if case.get("relation", "").strip():
                    body += f"\n\n关联：{case['relation'].strip()}"
                cards.append(
                    {
                        "kind": "案例",
                        "title": f"{prefix} · 案例",
                        "body": body,
                        "source_section_ids": source_ids,
                    }
                )
    return cards
