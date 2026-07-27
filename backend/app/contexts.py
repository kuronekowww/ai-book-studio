from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Database


STAGE_PROMPTS = {
    "draft": "episode_draft",
    "final": "episode_final",
}


@dataclass(frozen=True)
class StageContext:
    prompt_id: str
    source: str


class EpisodeContextBuilder:
    def __init__(self, database: Database):
        self.database = database

    def build(self, episode_id: str, stage: str) -> StageContext:
        if stage not in {"outline", "draft", "final"}:
            raise ValueError("声音生成阶段无效")
        episode = self.database.row(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        if not episode:
            raise KeyError(episode_id)
        project = self.database.row(
            "SELECT * FROM projects WHERE id = ?", (episode["project_id"],)
        )
        if not project:
            raise ValueError("声音所属项目不存在")
        books = [
            self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
            for book_id in project["book_ids"]
        ]
        books = [book for book in books if book]
        if not books:
            raise ValueError("内容项目没有有效来源书籍")
        source_ids = episode["source_section_ids"]
        if not source_ids:
            raise ValueError("声音没有关联原文块")
        sections = [
            self.database.row("SELECT * FROM sections WHERE id = ?", (section_id,))
            for section_id in source_ids
        ]
        if any(section is None for section in sections):
            raise ValueError("声音关联的原文块不存在")
        valid_book_ids = {book["id"] for book in books}
        typed_sections = [section for section in sections if section]
        if any(section["book_id"] not in valid_book_ids for section in typed_sections):
            raise ValueError("声音关联了项目来源书籍之外的原文块")
        typed_sections.sort(key=lambda item: (item["book_id"], item["position"]))

        book_info = self._format_books(books)
        evidence = self._format_evidence(typed_sections)
        if stage == "outline":
            framework = episode["content_framework"].strip()
            if not framework:
                raise ValueError("声音内容框架不能为空")
            narrative = books[0]["book_type"] == "narrative"
            parts = [
                book_info,
                (
                    "# 当前声音\n"
                    f"标题：{episode['title']}\n"
                    f"内容类型：{episode['content_type']}\n"
                    f"风格：{episode['style']}"
                ),
                f"# 声音内容框架\n{framework}",
            ]
            if narrative:
                parts.append(
                    "# 人物关系\n"
                    + self._format_relationships(books, set(source_ids))
                )
            parts.append(f"# 书籍内容（原文证据）\n{evidence}")
            return StageContext(
                prompt_id=(
                    "episode_outline_narrative"
                    if narrative
                    else "episode_outline_non_narrative"
                ),
                source="\n\n".join(parts),
            )

        previous_stage = "outline" if stage == "draft" else "draft"
        previous = self.database.row(
            """
            SELECT * FROM artifact_versions
            WHERE episode_id = ? AND stage = ?
            ORDER BY version DESC LIMIT 1
            """,
            (episode_id, previous_stage),
        )
        if not previous:
            label = "声音细纲" if previous_stage == "outline" else "声音初稿"
            raise ValueError(f"缺少上一步产物：{label}")
        return StageContext(
            prompt_id=STAGE_PROMPTS[stage],
            source=(
                f"{book_info}\n\n"
                f"# 上一步产物\n{previous['content']}\n\n"
                f"# 原文证据\n{evidence}"
            ),
        )

    @staticmethod
    def _format_books(books: list[dict[str, Any]]) -> str:
        lines = ["# 需要解读的书籍"]
        for book in books:
            lines.append(f"书名：{book['title']}")
            lines.append(f"作者：{book['author'] or '未填写'}")
        return "\n".join(lines)

    @staticmethod
    def _format_evidence(sections: list[dict[str, Any]]) -> str:
        return "\n\n".join(
            f"## 原文块 [{section['id']}] {section['title']}\n{section['content']}"
            for section in sections
        )

    def _format_relationships(
        self, books: list[dict[str, Any]], source_ids: set[str]
    ) -> str:
        relationships: list[str] = []
        for book in books:
            items = self.database.rows(
                """
                SELECT * FROM knowledge_items
                WHERE book_id = ? AND kind = '人物关系'
                ORDER BY title
                """,
                (book["id"],),
            )
            relationships.extend(
                item["body"]
                for item in items
                if source_ids.intersection(item["source_section_ids"])
            )
        return (
            "\n".join(f"- {body}" for body in relationships)
            if relationships
            else "当前关联原文块未识别到人物关系。"
        )
