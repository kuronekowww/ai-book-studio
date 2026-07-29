from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .db import Database, now_iso


CHAPTER_KEY_RE = re.compile(r"\[?(CHAPTER_\d{3})\]?")
KNOWLEDGE_ID_RE = re.compile(r"\bknowledge_[0-9a-f]+\b")
CONTENT_INDEX_RE = re.compile(r"\bcontent_[0-9a-f]+\b")
MODULE_INPUT_TARGET_CHARS = 12_000


@dataclass(frozen=True)
class ChapterPlanningEntry:
    chapter_key: str
    section_id: str
    title: str
    theme: str
    subtopic_titles: tuple[str, ...]
    concise_points: tuple[str, ...]
    position: int

    def catalog_markdown(self, *, include_points: bool = False) -> str:
        lines = [
            f"[{self.chapter_key}] {self.title}",
            f"章节主题：{self.theme}",
        ]
        if self.subtopic_titles:
            lines.append("子主题：" + "；".join(self.subtopic_titles))
        if include_points and self.concise_points:
            lines.append("主要观点：" + "；".join(self.concise_points))
        return "\n".join(lines)

    def detail_markdown(self) -> str:
        return self.catalog_markdown(include_points=True)


@dataclass(frozen=True)
class AlbumModule:
    key: str
    title: str
    listener_question: str
    chapter_keys: tuple[str, ...]
    suggested_episode_count: int
    position: int


@dataclass(frozen=True)
class AlbumEpisodeBudget:
    desired_count: int
    minimum_count: int
    maximum_count: int
    selected_count: int


class AlbumPlanningArtifactRepository:
    def __init__(self, database: Database):
        self.database = database

    def upsert(
        self,
        *,
        run_id: str,
        project_id: str,
        artifact_type: str,
        module_key: str = "",
        position: int = 0,
        source_chapter_ids: list[str] | tuple[str, ...] = (),
        content: str = "",
        status: str = "succeeded",
        error_message: str = "",
    ) -> dict[str, Any]:
        existing = self.get(run_id, artifact_type, module_key)
        now = now_iso()
        artifact_id = existing["id"] if existing else uuid.uuid4().hex
        self.database.execute(
            """
            INSERT INTO album_planning_artifacts
              (id, run_id, project_id, artifact_type, module_key, position,
               source_chapter_ids_json, content, status, error_message,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, artifact_type, module_key) DO UPDATE SET
              position = excluded.position,
              source_chapter_ids_json = excluded.source_chapter_ids_json,
              content = excluded.content,
              status = excluded.status,
              error_message = excluded.error_message,
              updated_at = excluded.updated_at
            """,
            (
                artifact_id,
                run_id,
                project_id,
                artifact_type,
                module_key,
                position,
                json.dumps(list(source_chapter_ids), ensure_ascii=False),
                content,
                status,
                error_message[:2000],
                now,
                now,
            ),
        )
        return self.get(run_id, artifact_type, module_key) or {}

    def get(
        self, run_id: str, artifact_type: str, module_key: str = ""
    ) -> dict[str, Any] | None:
        return self.database.row(
            """
            SELECT * FROM album_planning_artifacts
            WHERE run_id = ? AND artifact_type = ? AND module_key = ?
            """,
            (run_id, artifact_type, module_key),
        )

    def list_for_run(
        self, run_id: str, artifact_type: str | None = None
    ) -> list[dict[str, Any]]:
        if artifact_type is None:
            return self.database.rows(
                """
                SELECT * FROM album_planning_artifacts
                WHERE run_id = ?
                ORDER BY position, created_at
                """,
                (run_id,),
            )
        return self.database.rows(
            """
            SELECT * FROM album_planning_artifacts
            WHERE run_id = ? AND artifact_type = ?
            ORDER BY position, created_at
            """,
            (run_id, artifact_type),
        )

    def completed_module_keys(self, run_id: str) -> set[str]:
        return {
            item["module_key"]
            for item in self.list_for_run(run_id, "module_outline")
            if item["status"] == "succeeded"
        }

    def mark_failed(
        self,
        *,
        run_id: str,
        project_id: str,
        artifact_type: str,
        module_key: str,
        position: int,
        source_chapter_ids: list[str] | tuple[str, ...],
        error: Exception | str,
    ) -> dict[str, Any]:
        return self.upsert(
            run_id=run_id,
            project_id=project_id,
            artifact_type=artifact_type,
            module_key=module_key,
            position=position,
            source_chapter_ids=source_chapter_ids,
            status="failed",
            error_message=str(error),
        )


class AlbumPlanningService:
    def __init__(self, database: Database):
        self.database = database
        self.artifacts = AlbumPlanningArtifactRepository(database)

    def build_chapter_catalog(
        self, book_id: str
    ) -> tuple[list[ChapterPlanningEntry], dict[str, str]]:
        roots = self.database.rows(
            """
            SELECT * FROM sections
            WHERE book_id = ? AND parent_id IS NULL
              AND status = 'confirmed' AND analysis_enabled = 1
            ORDER BY position
            """,
            (book_id,),
        )
        fragment_set = self.database.row(
            """
            SELECT * FROM source_fragment_sets
            WHERE book_id = ? AND status IN ('current', 'active')
            ORDER BY version DESC LIMIT 1
            """,
            (book_id,),
        )
        if not fragment_set:
            raise ValueError("没有可用的原文片段版本，请先完成逐章拆书")
        entries: list[ChapterPlanningEntry] = []
        for ordinal, root in enumerate(roots, start=1):
            analysis = self.database.row(
                """
                SELECT * FROM chapter_analyses
                WHERE root_section_id = ? AND fragment_set_id = ?
                  AND status = 'succeeded'
                ORDER BY version DESC LIMIT 1
                """,
                (root["id"], fragment_set["id"]),
            )
            if not analysis:
                raise ValueError(
                    f"章节“{root['title']}”尚未完成拆书，请先重跑该章"
                )
            structured = analysis.get("structured_json") or {}
            subtopics = structured.get("subtopics") or []
            subtopic_titles: list[str] = []
            points: list[str] = []
            for subtopic in subtopics:
                if not isinstance(subtopic, dict):
                    continue
                title = str(subtopic.get("title") or "").strip()
                if title:
                    subtopic_titles.append(title)
                for viewpoint in subtopic.get("viewpoints") or []:
                    if not isinstance(viewpoint, dict):
                        continue
                    text = self._clean_planning_text(viewpoint.get("text"))
                    if text:
                        points.append(text)
            theme = self._clean_planning_text(structured.get("chapter_theme"))
            entries.append(
                ChapterPlanningEntry(
                    chapter_key=f"CHAPTER_{ordinal:03d}",
                    section_id=root["id"],
                    title=root["title"].strip(),
                    theme=theme or "本章主题待模型结合子主题理解。",
                    subtopic_titles=tuple(dict.fromkeys(subtopic_titles)),
                    concise_points=tuple(dict.fromkeys(points[:12])),
                    position=int(root["position"]),
                )
            )
        if not entries:
            raise ValueError("没有纳入拆书的一级章节")
        return entries, {entry.chapter_key: entry.section_id for entry in entries}

    @staticmethod
    def render_catalog(entries: list[ChapterPlanningEntry]) -> str:
        return "\n\n".join(entry.catalog_markdown() for entry in entries)

    @staticmethod
    def render_module_source(
        entries: list[ChapterPlanningEntry], chapter_keys: list[str] | tuple[str, ...]
    ) -> str:
        wanted = set(chapter_keys)
        selected = [entry for entry in entries if entry.chapter_key in wanted]
        return "\n\n".join(entry.detail_markdown() for entry in selected)

    @staticmethod
    def parse_module_plan(
        markdown: str, allowed_keys: set[str]
    ) -> list[AlbumModule]:
        blocks = re.split(r"(?=^##\s+)", markdown.strip(), flags=re.M)
        modules: list[AlbumModule] = []
        for block in blocks:
            if not block.strip():
                continue
            heading = re.search(r"^##\s+(?:模块[^：:]*[：:]\s*)?(.+)$", block, re.M)
            keys = tuple(dict.fromkeys(CHAPTER_KEY_RE.findall(block)))
            if not heading or not keys:
                continue
            unknown = [key for key in keys if key not in allowed_keys]
            if unknown:
                raise ValueError(f"模块计划引用未知章节：{'、'.join(unknown)}")
            question_match = re.search(
                r"^(?:听众问题|要解决的问题)[：:]\s*(.+)$", block, re.M
            )
            count_match = re.search(r"建议(?:声音|集)数[：:]\s*(\d+)", block)
            count = int(count_match.group(1)) if count_match else 4
            modules.append(
                AlbumModule(
                    key=f"MODULE_{len(modules) + 1:03d}",
                    title=heading.group(1).strip(),
                    listener_question=(
                        question_match.group(1).strip()
                        if question_match
                        else heading.group(1).strip()
                    ),
                    chapter_keys=keys,
                    suggested_episode_count=max(1, min(count, 8)),
                    position=len(modules) + 1,
                )
            )
        if not modules:
            raise ValueError("模块计划没有可识别的模块或章节标识")
        covered = {key for module in modules for key in module.chapter_keys}
        missing = allowed_keys - covered
        if missing:
            raise ValueError(
                "模块计划遗漏章节：" + "、".join(sorted(missing))
            )
        return modules

    @staticmethod
    def split_oversized_modules(
        modules: list[AlbumModule],
        entries: list[ChapterPlanningEntry],
        *,
        max_chars: int = MODULE_INPUT_TARGET_CHARS,
    ) -> list[AlbumModule]:
        by_key = {entry.chapter_key: entry for entry in entries}
        result: list[AlbumModule] = []
        for module in modules:
            groups: list[list[str]] = []
            current: list[str] = []
            current_chars = 0
            for key in module.chapter_keys:
                if key not in by_key:
                    raise ValueError(f"模块引用未知章节：{key}")
                size = len(by_key[key].detail_markdown())
                if size > max_chars:
                    raise ValueError(
                        f"章节 {key} 的精简策划材料仍超过 {max_chars} 字符，"
                        "请缩短该章拆书观点后重试"
                    )
                if current and current_chars + size + 2 > max_chars:
                    groups.append(current)
                    current = []
                    current_chars = 0
                current.append(key)
                current_chars += size + 2
            if current:
                groups.append(current)
            for group_position, group in enumerate(groups, start=1):
                suffix = (
                    ""
                    if len(groups) == 1
                    else f"（第 {group_position}/{len(groups)} 部分）"
                )
                result.append(
                    AlbumModule(
                        key=f"MODULE_{len(result) + 1:03d}",
                        title=module.title + suffix,
                        listener_question=module.listener_question,
                        chapter_keys=tuple(group),
                        suggested_episode_count=max(
                            1,
                            round(
                                module.suggested_episode_count
                                * len(group)
                                / max(1, len(module.chapter_keys))
                            ),
                        ),
                        position=len(result) + 1,
                    )
                )
        return result

    @staticmethod
    def apply_episode_budget(
        modules: list[AlbumModule],
        entries: list[ChapterPlanningEntry],
        *,
        desired_episode_count: int,
        tolerance: int = 2,
        max_chars: int = MODULE_INPUT_TARGET_CHARS,
    ) -> tuple[list[AlbumModule], AlbumEpisodeBudget]:
        if not modules:
            raise ValueError("模块计划不能为空")
        if desired_episode_count < 1:
            raise ValueError("目标集数必须大于 0")
        minimum = max(1, desired_episode_count - tolerance)
        maximum = desired_episode_count + tolerance
        normalized = list(modules)
        if len(normalized) > maximum:
            merged: list[AlbumModule] = []
            count = len(normalized)
            for group_position in range(maximum):
                start = group_position * count // maximum
                end = (group_position + 1) * count // maximum
                group = normalized[start:end]
                chapter_keys = tuple(
                    dict.fromkeys(
                        key for module in group for key in module.chapter_keys
                    )
                )
                merged.append(
                    AlbumModule(
                        key=f"MODULE_{group_position + 1:03d}",
                        title=" / ".join(module.title for module in group),
                        listener_question="；".join(
                            dict.fromkeys(
                                module.listener_question for module in group
                            )
                        ),
                        chapter_keys=chapter_keys,
                        suggested_episode_count=sum(
                            module.suggested_episode_count for module in group
                        ),
                        position=group_position + 1,
                    )
                )
            normalized = merged

        normalized = [
            AlbumModule(
                key=f"MODULE_{position:03d}",
                title=module.title,
                listener_question=module.listener_question,
                chapter_keys=module.chapter_keys,
                suggested_episode_count=max(1, module.suggested_episode_count),
                position=position,
            )
            for position, module in enumerate(normalized, start=1)
        ]
        for module in normalized:
            source_chars = len(
                AlbumPlanningService.render_module_source(
                    entries, module.chapter_keys
                )
            )
            if source_chars > max_chars:
                raise ValueError(
                    f"目标 {desired_episode_count} 集的允许上限为 {maximum} 集，"
                    f"但合并后的“{module.title}”材料仍有 {source_chars} 字符。"
                    "请提高目标集数，避免单次模型输入过长。"
                )

        suggested_total = sum(
            module.suggested_episode_count for module in normalized
        )
        selected = max(minimum, min(maximum, suggested_total))
        selected = max(len(normalized), selected)
        if selected > maximum:
            raise ValueError(
                f"至少需要 {len(normalized)} 集才能覆盖全部知识模块，"
                f"已超过目标允许上限 {maximum} 集"
            )

        if suggested_total == selected:
            allocated = normalized
        else:
            remaining = selected - len(normalized)
            weights = [
                max(1, module.suggested_episode_count)
                for module in normalized
            ]
            weight_total = sum(weights)
            floors = [
                remaining * weight // weight_total
                for weight in weights
            ]
            remainders = [
                (remaining * weight) % weight_total
                for weight in weights
            ]
            extras = remaining - sum(floors)
            bonus_positions = set(
                sorted(
                    range(len(normalized)),
                    key=lambda index: (-remainders[index], index),
                )[:extras]
            )
            allocated = [
                AlbumModule(
                    key=module.key,
                    title=module.title,
                    listener_question=module.listener_question,
                    chapter_keys=module.chapter_keys,
                    suggested_episode_count=(
                        1
                        + floors[index]
                        + (1 if index in bonus_positions else 0)
                    ),
                    position=module.position,
                )
                for index, module in enumerate(normalized)
            ]

        return allocated, AlbumEpisodeBudget(
            desired_count=desired_episode_count,
            minimum_count=minimum,
            maximum_count=maximum,
            selected_count=selected,
        )

    @staticmethod
    def render_module_plan(
        modules: list[AlbumModule],
        budget: AlbumEpisodeBudget | None = None,
    ) -> str:
        lines: list[str] = []
        if budget:
            lines.extend(
                [
                    f"目标集数：{budget.desired_count}",
                    (
                        f"允许范围：{budget.minimum_count}"
                        f"至{budget.maximum_count}集"
                    ),
                    f"本次规划总数：{budget.selected_count}集",
                    "",
                ]
            )
        for module in modules:
            lines.extend(
                [
                    f"## 模块{module.position}：{module.title}",
                    f"听众问题：{module.listener_question}",
                    "来源章节："
                    + "、".join(f"[{key}]" for key in module.chapter_keys),
                    f"建议声音数：{module.suggested_episode_count}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def validate_module_outline(
        markdown: str,
        allowed_keys: set[str],
        *,
        expected_episode_count: int | None = None,
    ) -> str:
        if not markdown.strip():
            raise ValueError("模块没有生成专辑大纲")
        episode_blocks = re.split(r"(?=^##\s+第?\d+\s*集)", markdown, flags=re.M)
        valid_blocks = [
            block
            for block in episode_blocks
            if re.search(r"^##\s+第?\d+\s*集", block, flags=re.M)
        ]
        if not valid_blocks:
            raise ValueError("模块大纲没有可识别的声音条目")
        if (
            expected_episode_count is not None
            and len(valid_blocks) != expected_episode_count
        ):
            raise ValueError(
                f"本模块分配 {expected_episode_count} 集，"
                f"模型实际生成 {len(valid_blocks)} 集"
            )
        for position, block in enumerate(valid_blocks, start=1):
            keys = set(CHAPTER_KEY_RE.findall(block))
            if not keys:
                raise ValueError(f"模块第 {position} 集缺少来源章节")
            unknown = keys - allowed_keys
            if unknown:
                raise ValueError(
                    f"模块第 {position} 集引用未知章节："
                    + "、".join(sorted(unknown))
                )
            if KNOWLEDGE_ID_RE.search(block) or CONTENT_INDEX_RE.search(block):
                raise ValueError("专辑大纲不得包含知识资产 ID 或段落原文索引")
        return markdown.strip()

    @staticmethod
    def validate_structured_outline(
        data: dict[str, Any],
        entries: list[ChapterPlanningEntry],
        *,
        book_type: str,
        desired_episode_count: int | None,
        expected_episode_count: int | None = None,
        allowed_episode_range: tuple[int, int] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        raw_episodes = data.get("album_outline")
        if not isinstance(raw_episodes, list) or not raw_episodes:
            raise ValueError("结构化专辑大纲缺少 album_outline")
        entry_map = {entry.chapter_key: entry for entry in entries}
        episodes: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_episodes, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"专辑第 {position} 条结构无效")
            title = str(raw.get("title") or "").strip()
            main_points = str(raw.get("main_points") or "").strip()
            content_type = str(raw.get("content_type") or "").strip().replace(
                "类", ""
            )
            chapter_keys = raw.get("chapter_keys")
            if (
                not title
                or not main_points
                or content_type not in {"解读", "过渡"}
                or not isinstance(chapter_keys, list)
                or not chapter_keys
            ):
                raise ValueError(f"专辑第 {position} 条字段不完整")
            normalized_keys = list(
                dict.fromkeys(
                    str(key).strip().strip("[]")
                    for key in chapter_keys
                    if str(key).strip()
                )
            )
            unknown = [key for key in normalized_keys if key not in entry_map]
            if unknown:
                raise ValueError(
                    f"专辑第 {position} 条引用未知章节：{'、'.join(unknown)}"
                )
            if book_type == "narrative" and content_type != "解读":
                raise ValueError("叙事类书籍不能生成过渡声音")
            for marker in ("听众钩子：", "核心主题：", "核心要点："):
                if marker not in main_points:
                    raise ValueError(
                        f"专辑第 {position} 条主要内容缺少“{marker}”"
                    )
            selected = [entry_map[key] for key in normalized_keys]
            episodes.append(
                {
                    "id": uuid.uuid4().hex,
                    "position": position,
                    "title": title,
                    "content_type": content_type,
                    "style": "观点",
                    "content_framework": main_points,
                    "section_identifier": "、".join(
                        f"[{entry.chapter_key}] {entry.title}"
                        for entry in selected
                    ),
                    "source_section_ids": [entry.section_id for entry in selected],
                    "knowledge_item_ids": [],
                    "source_content_indexes": [],
                }
            )
        if (
            expected_episode_count is not None
            and len(episodes) != expected_episode_count
        ):
            raise ValueError(
                f"本次规划总数为 {expected_episode_count} 集，"
                f"结构化结果却有 {len(episodes)} 集"
            )
        if allowed_episode_range is not None:
            minimum, maximum = allowed_episode_range
            if not minimum <= len(episodes) <= maximum:
                raise ValueError(
                    f"专辑实际 {len(episodes)} 集，超出允许范围"
                    f" {minimum} 至 {maximum} 集"
                )
        notice = ""
        if desired_episode_count is not None:
            if allowed_episode_range is not None:
                minimum, maximum = allowed_episode_range
                notice = (
                    f"目标 {desired_episode_count} 集，允许 {minimum} 至"
                    f" {maximum} 集，本次生成 {len(episodes)} 集。"
                )
            elif desired_episode_count != len(episodes):
                notice = (
                    f"期望 {desired_episode_count} 集，模型根据内容结构生成"
                    f" {len(episodes)} 集。"
                )
        return episodes, notice

    @staticmethod
    def _clean_planning_text(value: Any) -> str:
        text = str(value or "").strip()
        text = KNOWLEDGE_ID_RE.sub("", text)
        text = CONTENT_INDEX_RE.sub("", text)
        return re.sub(r"\s+", " ", text).strip(" ，,；;")
