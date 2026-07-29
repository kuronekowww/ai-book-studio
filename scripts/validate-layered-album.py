#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.album_planning import AlbumPlanningService  # noqa: E402
from app.chapter_analysis import parse_json_object  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.prompt_config import PromptConfigurationService  # noqa: E402
from app.prompts import PROMPTS  # noqa: E402
from app.providers import build_provider  # noqa: E402


async def validate(database_path: Path, book_id: str) -> dict[str, object]:
    database = Database(database_path)
    database.init()
    planner = AlbumPlanningService(database)
    prompts = PromptConfigurationService(database)
    provider = build_provider(get_settings())
    book = database.row("SELECT * FROM books WHERE id = ?", (book_id,))
    if not book:
        raise ValueError(f"书籍不存在：{book_id}")
    entries, _ = planner.build_chapter_catalog(book_id)
    catalog = planner.render_catalog(entries)
    module_plan_source = (
        f"# 书籍信息\n书名：{book['title']}\n作者：{book['author'] or '未填写'}\n"
        f"书籍类型：{'叙事类' if book['book_type'] == 'narrative' else '非叙事类'}\n\n"
        "# 专辑特殊要求\n无\n\n# 期望集数\n由模型根据内容自行决定\n\n"
        f"# 轻量章节目录\n{catalog}"
    )
    module_plan = await provider.generate(
        PROMPTS["album_module_plan"], module_plan_source
    )
    modules = planner.parse_module_plan(
        module_plan, {entry.chapter_key for entry in entries}
    )
    modules = planner.split_oversized_modules(modules, entries)
    first = modules[0]
    module_source = planner.render_module_source(entries, first.chapter_keys)
    module_brief = (
        f"模块标题：{first.title}\n听众问题：{first.listener_question}\n"
        f"建议声音数：{first.suggested_episode_count}\n来源章节："
        + "、".join(f"[{key}]" for key in first.chapter_keys)
    )
    snapshot = prompts.snapshot(
        "album_outline",
        {
            "book_analysis": module_source,
            "chapter_catalog": catalog,
            "module_brief": module_brief,
            "module_source": module_source,
            "book_title": book["title"],
            "book_author": book["author"] or "未填写",
            "book_type": (
                "叙事类" if book["book_type"] == "narrative" else "非叙事类"
            ),
            "album_special_requirements": "无",
            "desired_episode_count": "由模型根据内容自行决定",
        },
        prompt_id="album_outline",
        book_type=book["book_type"],
    )
    module_outline = await provider.generate(snapshot.prompt, snapshot.source)
    module_outline = planner.validate_module_outline(
        module_outline, set(first.chapter_keys)
    )
    structure_source = (
        "# 合法章节标识\n"
        + "、".join(f"[{key}]" for key in first.chapter_keys)
        + "\n\n# 已完成 Markdown 专辑大纲\n"
        + module_outline
    )
    structured_raw = await provider.generate(
        PROMPTS["album_outline_structure"], structure_source
    )
    structured = parse_json_object(structured_raw)
    episodes, _ = planner.validate_structured_outline(
        structured,
        entries,
        book_type=book["book_type"],
        desired_episode_count=None,
    )
    return {
        "provider": provider.name,
        "model": provider.model,
        "chapter_count": len(entries),
        "catalog_chars": len(catalog),
        "module_count": len(modules),
        "first_module": first.title,
        "first_module_chapters": list(first.chapter_keys),
        "first_module_input_chars": len(module_source),
        "first_module_episode_count": len(episodes),
        "module_plan_preview": module_plan[:1000],
        "module_outline_preview": module_outline[:1500],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--book-id", required=True)
    args = parser.parse_args()
    result = asyncio.run(validate(args.database.resolve(), args.book_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
