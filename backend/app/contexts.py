from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .album_planning import AlbumPlanningService
from .db import Database
from .text_metrics import (
    DEFAULT_EPISODE_WORD_COUNT_MAX,
    DEFAULT_EPISODE_WORD_COUNT_MIN,
    format_episode_word_count_range,
)


STAGE_PROMPTS = {
    "draft": "episode_draft",
    "final": "episode_final",
}


@dataclass(frozen=True)
class StageContext:
    prompt_id: str
    source: str
    project_id: str
    book_type: str
    variables: dict[str, str]


class EpisodeContextBuilder:
    def __init__(self, database: Database):
        self.database = database
        self.album_planning = AlbumPlanningService(database)

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
        valid_book_ids = {book["id"] for book in books}
        book_info = self._format_books(books)
        narrative = books[0]["book_type"] == "narrative"
        previous_episode = self.database.row(
            """
            SELECT previous_artifact.content
            FROM episodes previous_episode
            JOIN artifact_versions previous_artifact
              ON previous_artifact.episode_id = previous_episode.id
            WHERE previous_episode.project_id = ?
              AND previous_episode.position < ?
              AND previous_artifact.stage = 'final'
            ORDER BY previous_episode.position DESC,
                     previous_artifact.version DESC
            LIMIT 1
            """,
            (episode["project_id"], episode["position"]),
        )
        common_variables = {
            "book_title": "、".join(book["title"] for book in books),
            "book_author": "、".join(
                book["author"] or "未填写" for book in books
            ),
            "episode_title": episode["title"],
            "episode_framework": episode["content_framework"].strip(),
            "previous_episode_final": (
                previous_episode["content"]
                if previous_episode
                else "当前没有可用的上一集终稿。"
            ),
            "episode_word_count_range": format_episode_word_count_range(
                int(
                    project.get("episode_word_count_min")
                    or DEFAULT_EPISODE_WORD_COUNT_MIN
                ),
                int(
                    project.get("episode_word_count_max")
                    or DEFAULT_EPISODE_WORD_COUNT_MAX
                ),
            ),
        }
        if stage == "outline":
            framework = episode["content_framework"].strip()
            if not framework:
                raise ValueError("声音内容框架不能为空")
            module_analysis = self._module_analysis(
                episode, project, books
            )
            common_variables.update(
                {
                    "module_book_analysis": module_analysis,
                    "source_text": module_analysis,
                    "character_relationships": "",
                }
            )
            parts = [
                book_info,
                (
                    "# 当前声音\n"
                    f"标题：{episode['title']}\n"
                    f"内容类型：{episode['content_type']}\n"
                    f"风格：{episode['style']}"
                ),
                f"# 声音内容框架\n{framework}",
                f"# 所属模块详细拆书稿\n{module_analysis}",
            ]
            return StageContext(
                prompt_id=(
                    "episode_outline_narrative"
                    if narrative
                    else "episode_outline_non_narrative"
                ),
                source="\n\n".join(parts),
                project_id=project["id"],
                book_type=books[0]["book_type"],
                variables=common_variables,
            )

        bundle = self.evidence_bundle(episode_id)
        typed_sections = bundle["legacy_sections"]
        if any(section["book_id"] not in valid_book_ids for section in typed_sections):
            raise ValueError("声音关联了项目来源书籍之外的原文块")
        evidence = self._format_bundle(bundle)
        relationships = (
            self._format_relationships(books, set(source_ids))
            if narrative
            else "非故事类书籍无须提供人物关系。"
        )
        common_variables.update(
            {
                "source_text": evidence,
                "character_relationships": relationships,
            }
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
        variables = {
            **common_variables,
            (
                "episode_outline"
                if stage == "draft"
                else "episode_draft"
            ): previous["content"],
        }
        return StageContext(
            prompt_id=STAGE_PROMPTS[stage],
            source=(
                f"{book_info}\n\n"
                f"# 上一步产物\n{previous['content']}\n\n"
                f"# 原文证据\n{evidence}"
            ),
            project_id=project["id"],
            book_type=books[0]["book_type"],
            variables=variables,
        )

    def _module_analysis(
        self,
        episode: dict[str, Any],
        project: dict[str, Any],
        books: list[dict[str, Any]],
    ) -> str:
        planning_run_id = str(episode.get("planning_run_id") or "")
        module_key = str(episode.get("module_key") or "")
        if planning_run_id and module_key:
            artifact = self.album_planning.artifacts.get(
                planning_run_id, "module_source", module_key
            )
            if artifact and artifact["status"] == "succeeded":
                return artifact["content"]

        source_ids = set(episode.get("source_section_ids") or [])
        runs = self.database.rows(
            """
            SELECT * FROM workflow_runs
            WHERE scope_type = 'project_generation' AND scope_id = ?
            ORDER BY created_at DESC
            """,
            (project["id"],),
        )
        for run in runs:
            candidates = [
                item
                for item in self.album_planning.artifacts.list_for_run(
                    run["id"], "module_outline"
                )
                if item["status"] == "succeeded"
                and source_ids.issubset(
                    set(item["source_chapter_ids_json"])
                )
            ]
            exact = [
                item
                for item in candidates
                if set(item["source_chapter_ids_json"]) == source_ids
            ]
            selected = exact or candidates
            if len(selected) > 1:
                raise ValueError(
                    "当前历史声音对应多个知识模块，无法唯一恢复模块拆书稿；"
                    "请重新生成并确认专辑大纲"
                )
            if len(selected) == 1:
                planning_run_id = run["id"]
                module_key = selected[0]["module_key"]
                break

        if not planning_run_id:
            if not runs:
                if module_key:
                    return self._render_module_analysis_for_sources(
                        books[0]["id"], source_ids
                    )
                raise ValueError(
                    "当前声音没有可恢复的知识模块，请重新生成并确认专辑大纲"
                )
            planning_run_id = runs[0]["id"]
            module_key = f"RECOVERED_{int(episode['position']):03d}"

        entries, _ = self.album_planning.build_chapter_catalog(books[0]["id"])
        selected_keys = [
            entry.chapter_key
            for entry in entries
            if entry.section_id in source_ids
        ]
        if not selected_keys:
            raise ValueError(
                "当前声音的来源章节无法恢复模块拆书稿；"
                "请重新生成并确认专辑大纲"
            )
        content = self.album_planning.render_module_source(
            entries, selected_keys
        )
        self.album_planning.artifacts.upsert(
            run_id=planning_run_id,
            project_id=project["id"],
            artifact_type="module_source",
            module_key=module_key,
            position=int(episode["position"]),
            source_chapter_ids=[
                entry.section_id
                for entry in entries
                if entry.chapter_key in selected_keys
            ],
            content=content,
        )
        self.database.execute(
            """
            UPDATE episodes
            SET planning_run_id = ?, module_key = ?
            WHERE id = ?
            """,
            (planning_run_id, module_key, episode["id"]),
        )
        return content

    def _render_module_analysis_for_sources(
        self, book_id: str, source_ids: set[str]
    ) -> str:
        entries, _ = self.album_planning.build_chapter_catalog(book_id)
        selected_keys = [
            entry.chapter_key
            for entry in entries
            if entry.section_id in source_ids
        ]
        if not selected_keys:
            raise ValueError(
                "当前声音的来源章节无法恢复模块拆书稿；"
                "请重新生成并确认专辑大纲"
            )
        return self.album_planning.render_module_source(
            entries, selected_keys
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

    def evidence_bundle(self, episode_id: str) -> dict[str, Any]:
        episode = self.database.row(
            "SELECT * FROM episodes WHERE id = ?", (episode_id,)
        )
        if not episode:
            raise KeyError(episode_id)
        links = self.database.rows(
            """
            SELECT item.*, link.position, link.role
            FROM episode_knowledge_items link
            JOIN knowledge_items item ON item.id = link.knowledge_item_id
            WHERE link.episode_id = ?
            ORDER BY link.position
            """,
            (episode_id,),
        )
        if not links:
            source_ids = episode["source_section_ids"]
            if not source_ids:
                raise ValueError("声音没有关联原文块")
            sections = [
                self.database.row(
                    "SELECT * FROM sections WHERE id = ?", (section_id,)
                )
                for section_id in source_ids
            ]
            if any(section is None for section in sections):
                raise ValueError("声音关联的原文块不存在")
            typed = [section for section in sections if section]
            if any(section.get("parent_id") is None for section in typed):
                chapter_books = {
                    section["book_id"]
                    for section in typed
                    if section.get("parent_id") is None
                }
                if any(
                    self.database.row(
                        """
                        SELECT id FROM chapter_analyses
                        WHERE book_id = ? LIMIT 1
                        """,
                        (book_id,),
                    )
                    for book_id in chapter_books
                ):
                    raise ValueError(
                        "当前声音尚未匹配具体知识资产和原文块，请先执行来源匹配"
                    )
            typed.sort(key=lambda item: (item["book_id"], item["position"]))
            return {
                "knowledge_items": [],
                "direct_fragments": [],
                "auxiliary_fragments": [],
                "legacy_sections": typed,
            }
        if any(
            item["status"] != "active"
            or item["source_scheme"] != "paragraph_evidence_v1"
            for item in links
        ):
            raise ValueError("声音引用的知识资产已过期，请重新生成或调整专辑大纲")

        knowledge_items: list[dict[str, Any]] = []
        direct_by_index: dict[str, dict[str, Any]] = {}
        for item in links:
            sources = self.database.rows(
                """
                SELECT f.*, source.source_order, member.fragment_set_id,
                       member.section_path_json, member.book_position,
                       member.section_position
                FROM knowledge_item_sources source
                JOIN source_fragments f
                  ON f.content_index = source.content_index
                JOIN source_fragment_set_members member
                  ON member.content_index = source.content_index
                JOIN source_fragment_sets fragment_set
                  ON fragment_set.id = member.fragment_set_id
                WHERE source.knowledge_item_id = ?
                  AND fragment_set.status = 'current'
                ORDER BY source.source_order
                """,
                (item["id"],),
            )
            if not sources:
                raise ValueError(f"知识资产“{item['title']}”缺少当前版本原文证据")
            item_copy = dict(item)
            item_copy["source_content_indexes"] = [
                source["content_index"] for source in sources
            ]
            knowledge_items.append(item_copy)
            for source in sources:
                direct_by_index.setdefault(source["content_index"], source)

        auxiliary_by_index: dict[str, dict[str, Any]] = {}
        for source in direct_by_index.values():
            neighbors = self.database.rows(
                """
                SELECT f.*, member.fragment_set_id, member.section_path_json,
                       member.book_position, member.section_position
                FROM source_fragment_set_members member
                JOIN source_fragments f
                  ON f.content_index = member.content_index
                WHERE member.fragment_set_id = ?
                  AND f.source_section_id = ?
                  AND member.book_position BETWEEN ? AND ?
                ORDER BY member.book_position
                """,
                (
                    source["fragment_set_id"],
                    source["source_section_id"],
                    int(source["book_position"]) - 1,
                    int(source["book_position"]) + 1,
                ),
            )
            for neighbor in neighbors:
                if neighbor["content_index"] not in direct_by_index:
                    auxiliary_by_index.setdefault(neighbor["content_index"], neighbor)
        return {
            "knowledge_items": knowledge_items,
            "direct_fragments": sorted(
                direct_by_index.values(),
                key=lambda item: (item["book_id"], item["book_position"]),
            ),
            "auxiliary_fragments": sorted(
                auxiliary_by_index.values(),
                key=lambda item: (item["book_id"], item["book_position"]),
            ),
            "legacy_sections": [],
        }

    def _format_bundle(self, bundle: dict[str, Any]) -> str:
        if bundle["legacy_sections"]:
            return self._format_evidence(bundle["legacy_sections"])
        assets = "\n\n".join(
            (
                f"## [{item['id']}] {item['kind']} · {item['title']}\n"
                f"{item['body']}\n"
                f"证据索引：{', '.join(item['source_content_indexes'])}"
            )
            for item in bundle["knowledge_items"]
        )
        direct = "\n\n".join(
            (
                f"## 直接证据 [{item['content_index']}]\n"
                f"章节路径：{' / '.join(item['section_path_json'])}\n"
                f"{item['text']}"
            )
            for item in bundle["direct_fragments"]
        )
        auxiliary = "\n\n".join(
            (
                f"## 辅助上下文 [{item['content_index']}]\n"
                f"章节路径：{' / '.join(item['section_path_json'])}\n"
                f"{item['text']}"
            )
            for item in bundle["auxiliary_fragments"]
        )
        return (
            f"# 本集知识资产\n{assets}\n\n"
            f"# 直接原文证据（可作为事实与引用依据）\n{direct}\n\n"
            f"# 邻接辅助上下文（仅帮助理解，不代表本集必须覆盖）\n"
            f"{auxiliary or '无'}"
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
