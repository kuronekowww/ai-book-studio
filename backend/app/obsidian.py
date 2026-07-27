from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .db import Database


SYSTEM_BEGIN = "<!-- AI_BOOK_STUDIO:BEGIN -->"
SYSTEM_END = "<!-- AI_BOOK_STUDIO:END -->"
USER_BEGIN = "<!-- USER_NOTES:BEGIN -->"
USER_END = "<!-- USER_NOTES:END -->"


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return cleaned[:80] or "未命名"


def frontmatter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def merge_note(existing: str, generated: str) -> str:
    user_notes = ""
    if USER_BEGIN in existing and USER_END in existing:
        user_notes = existing.split(USER_BEGIN, 1)[1].split(USER_END, 1)[0].strip()
    return (
        f"{SYSTEM_BEGIN}\n{generated.strip()}\n{SYSTEM_END}\n\n"
        f"{USER_BEGIN}\n{user_notes}\n{USER_END}\n"
    )


class ObsidianSyncService:
    def __init__(self, database: Database):
        self.database = database

    def sync(
        self, vault_path: str, book_id: str | None = None, project_id: str | None = None
    ) -> dict[str, Any]:
        root = Path(vault_path).expanduser().resolve()
        if root == Path(root.anchor):
            raise ValueError("不能把文件系统根目录设为 Vault")
        root.mkdir(parents=True, exist_ok=True)
        base = root / "AI讲书知识库"
        manifest_path = base / "99_同步清单" / "sync-manifest.json"
        manifest: dict[str, str] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text("utf-8"))
            except json.JSONDecodeError:
                manifest = {}

        changed: list[str] = []
        if book_id:
            changed.extend(self._sync_book(base, book_id, manifest))
        if project_id:
            changed.extend(self._sync_project(base, project_id, manifest))

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"root": str(base), "changed": changed, "changed_count": len(changed)}

    def _write(self, path: Path, generated: str, manifest: dict[str, str]) -> str | None:
        existing = path.read_text("utf-8") if path.exists() else ""
        merged = merge_note(existing, generated)
        digest = hashlib.sha256(merged.encode("utf-8")).hexdigest()
        key = str(path)
        if manifest.get(key) == digest and path.exists():
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged, encoding="utf-8")
        manifest[key] = digest
        return key

    def _sync_book(
        self, base: Path, book_id: str, manifest: dict[str, str]
    ) -> list[str]:
        book = self.database.row("SELECT * FROM books WHERE id = ?", (book_id,))
        if not book:
            raise KeyError(book_id)
        folder = base / "01_书籍" / f"{safe_name(book['title'])}__{book_id[:8]}"
        changed: list[str] = []
        sections = self.database.rows(
            "SELECT * FROM sections WHERE book_id = ? ORDER BY position", (book_id,)
        )
        knowledge = self.database.rows(
            """
            SELECT * FROM knowledge_items
            WHERE book_id = ? AND status = 'active'
            ORDER BY kind, title
            """,
            (book_id,),
        )
        mind_map = self.database.row(
            "SELECT * FROM mind_maps WHERE book_id = ? ORDER BY version DESC LIMIT 1",
            (book_id,),
        )
        homepage = (
            frontmatter({"id": book_id, "type": "book", "status": book["status"]})
            + f"\n\n# {book['title']}\n\n作者：{book['author'] or '未填写'}\n\n"
            + f"- 原文小节：{len(sections)}\n- 知识资产：{len(knowledge)}\n"
        )
        result = self._write(folder / "00_书籍主页.md", homepage, manifest)
        if result:
            changed.append(result)

        for section in sections:
            note = frontmatter(
                {
                    "id": section["id"],
                    "type": "section",
                    "book_id": book_id,
                }
            ) + f"\n\n# {section['title']}\n\n{section['content']}\n"
            path = (
                folder
                / "01_原文小节"
                / f"{section['position']:03d}_{safe_name(section['title'])}__{section['id'][:8]}.md"
            )
            result = self._write(path, note, manifest)
            if result:
                changed.append(result)

        current_set = self.database.row(
            """
            SELECT * FROM source_fragment_sets
            WHERE book_id = ? AND status = 'current'
            ORDER BY version DESC LIMIT 1
            """,
            (book_id,),
        )
        fragments: list[dict[str, Any]] = []
        if current_set:
            fragments = self.database.rows(
                """
                SELECT fragment.*, member.section_path_json,
                       member.book_position, member.section_position
                FROM source_fragment_set_members member
                JOIN source_fragments fragment
                  ON fragment.content_index = member.content_index
                WHERE member.fragment_set_id = ?
                ORDER BY member.book_position
                """,
                (current_set["id"],),
            )
        section_map = {section["id"]: section for section in sections}
        for fragment in fragments:
            linked_assets = self.database.rows(
                """
                SELECT item.id, item.title
                FROM knowledge_item_sources source
                JOIN knowledge_items item ON item.id = source.knowledge_item_id
                WHERE source.content_index = ? AND item.status = 'active'
                ORDER BY item.kind, item.title
                """,
                (fragment["content_index"],),
            )
            source_section = section_map.get(fragment["source_section_id"])
            asset_links = "\n".join(
                f"- [[{safe_name(item['title'])}__{item['id'][:8]}]]"
                for item in linked_assets
            )
            note = (
                frontmatter(
                    {
                        "id": fragment["content_index"],
                        "type": "source_fragment",
                        "book_id": book_id,
                        "source_section_id": fragment["source_section_id"],
                        "section_path": fragment["section_path_json"],
                        "fragment_set_version": current_set["version"],
                    }
                )
                + f"\n\n# 原文片段 {fragment['content_index']}\n\n"
                + (
                    f"原章节：[[{source_section['position']:03d}_"
                    f"{safe_name(source_section['title'])}__"
                    f"{source_section['id'][:8]}]]\n\n"
                    if source_section
                    else ""
                )
                + f"{fragment['text']}\n\n## 被以下知识资产引用\n"
                + (asset_links or "- 暂无")
                + "\n"
            )
            result = self._write(
                folder
                / "01_原文片段"
                / f"{fragment['content_index']}.md",
                note,
                manifest,
            )
            if result:
                changed.append(result)

        kind_folder = {
            "观点": "02_知识点",
            "论据": "02_知识点",
            "概念": "02_知识点",
            "案例": "03_案例",
            "金句": "04_金句",
        }
        for item in knowledge:
            source_indexes = self.database.rows(
                """
                SELECT content_index FROM knowledge_item_sources
                WHERE knowledge_item_id = ? ORDER BY source_order
                """,
                (item["id"],),
            )
            links = "\n".join(
                f"- [[{source['content_index']}|{source['content_index']}]]"
                for source in source_indexes
            )
            note = frontmatter(
                {
                    "id": item["id"],
                    "type": item["kind"],
                    "book_id": book_id,
                    "source_section_ids": item["source_section_ids"],
                    "source_content_indexes": [
                        source["content_index"] for source in source_indexes
                    ],
                    "source_scheme": item["source_scheme"],
                }
            ) + (
                f"\n\n# {item['title']}\n\n{item['body']}\n\n"
                f"## 段落级原文证据\n{links or '- 历史资产暂无段落级索引'}\n"
            )
            path = (
                folder
                / kind_folder.get(item["kind"], "02_知识点")
                / f"{safe_name(item['title'])}__{item['id'][:8]}.md"
            )
            result = self._write(path, note, manifest)
            if result:
                changed.append(result)

        if mind_map:
            note = frontmatter(
                {"id": mind_map["id"], "type": "mind_map", "book_id": book_id}
            ) + f"\n\n{mind_map['content']}\n"
            result = self._write(folder / "05_思维导图.md", note, manifest)
            if result:
                changed.append(result)
        return changed

    def _sync_project(
        self, base: Path, project_id: str, manifest: dict[str, str]
    ) -> list[str]:
        project = self.database.row("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            raise KeyError(project_id)
        folder = base / "02_内容项目" / f"{safe_name(project['title'])}__{project_id[:8]}"
        episodes = self.database.rows(
            "SELECT * FROM episodes WHERE project_id = ? ORDER BY position",
            (project_id,),
        )
        changed: list[str] = []
        outline_lines = [f"# {project['title']} · 专辑大纲"]
        for episode in episodes:
            outline_lines.append(
                f"- {episode['position']:02d} {episode['title']}（{episode['content_type']} / {episode['style']}）"
            )
            framework = (
                episode["content_framework"].strip() or "未填写"
            ).replace("\n", "\n    ")
            outline_lines.append(f"  - 内容框架：{framework}")
        homepage = frontmatter(
            {"id": project_id, "type": "project", "status": project["status"]}
        ) + f"\n\n# {project['title']}\n\n声音数量：{len(episodes)}\n"
        for path, content in (
            (folder / "00_项目主页.md", homepage),
            (folder / "02_专辑大纲.md", "\n".join(outline_lines)),
        ):
            result = self._write(path, content, manifest)
            if result:
                changed.append(result)

        label = {"outline": "声音细纲", "draft": "声音初稿", "final": "声音终稿"}
        for episode in episodes:
            episode_assets = self.database.rows(
                """
                SELECT item.id, item.title
                FROM episode_knowledge_items link
                JOIN knowledge_items item ON item.id = link.knowledge_item_id
                WHERE link.episode_id = ? ORDER BY link.position
                """,
                (episode["id"],),
            )
            asset_ids = [item["id"] for item in episode_assets]
            asset_links = "\n".join(
                f"- [[{safe_name(item['title'])}__{item['id'][:8]}]]"
                for item in episode_assets
            )
            episode_folder = (
                folder
                / "03_声音"
                / f"{episode['position']:02d}_{safe_name(episode['title'])}__{episode['id'][:8]}"
            )
            for stage, filename in label.items():
                artifact = self.database.row(
                    """
                    SELECT * FROM artifact_versions
                    WHERE episode_id = ? AND stage = ?
                    ORDER BY version DESC LIMIT 1
                    """,
                    (episode["id"], stage),
                )
                if not artifact:
                    continue
                note = frontmatter(
                    {
                        "id": artifact["id"],
                        "type": stage,
                        "project_id": project_id,
                        "episode_id": episode["id"],
                        "source_section_ids": episode["source_section_ids"],
                        "knowledge_item_ids": asset_ids,
                    }
                ) + (
                    f"\n\n# {episode['title']} · {filename}\n\n"
                    f"## 本集知识资产\n{asset_links or '- 历史声音暂无知识资产关联'}\n\n"
                    f"## 文稿\n{artifact['content']}\n"
                )
                result = self._write(episode_folder / f"{filename}.md", note, manifest)
                if result:
                    changed.append(result)
        return changed
