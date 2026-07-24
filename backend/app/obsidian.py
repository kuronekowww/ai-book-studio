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
            "SELECT * FROM knowledge_items WHERE book_id = ? ORDER BY kind, title",
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

        kind_folder = {"观点": "02_知识点", "案例": "03_案例", "金句": "04_金句"}
        for item in knowledge:
            links = "\n".join(
                f"- 来源小节 ID：`{section_id}`"
                for section_id in item["source_section_ids"]
            )
            note = frontmatter(
                {
                    "id": item["id"],
                    "type": item["kind"],
                    "book_id": book_id,
                    "source_section_ids": item["source_section_ids"],
                }
            ) + f"\n\n# {item['title']}\n\n{item['body']}\n\n## 来源\n{links}\n"
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
                    }
                ) + f"\n\n# {episode['title']} · {filename}\n\n{artifact['content']}\n"
                result = self._write(episode_folder / f"{filename}.md", note, manifest)
                if result:
                    changed.append(result)
        return changed
