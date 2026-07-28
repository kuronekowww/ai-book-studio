from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS books (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  book_type TEXT NOT NULL DEFAULT 'non_narrative',
  filename TEXT NOT NULL,
  status TEXT NOT NULL,
  source_type TEXT NOT NULL,
  parse_version INTEGER NOT NULL DEFAULT 1,
  analysis_model_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  parent_id TEXT REFERENCES sections(id) ON DELETE CASCADE,
  level INTEGER NOT NULL,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'section',
  status TEXT NOT NULL DEFAULT 'draft',
  analysis_enabled INTEGER NOT NULL DEFAULT 1,
  analysis_exclusion_reason TEXT NOT NULL DEFAULT '',
  analysis_selection_source TEXT NOT NULL DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_sections_book_position
  ON sections(book_id, position);

CREATE TABLE IF NOT EXISTS knowledge_items (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  source_section_ids TEXT NOT NULL,
  chapter_analysis_id TEXT,
  origin TEXT NOT NULL DEFAULT 'legacy',
  source_scheme TEXT NOT NULL DEFAULT 'legacy_section_source',
  status TEXT NOT NULL DEFAULT 'active',
  stable_key TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapter_analyses (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  root_section_id TEXT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  status TEXT NOT NULL,
  structured_json TEXT NOT NULL,
  rendered_markdown TEXT NOT NULL,
  compressed_markdown TEXT NOT NULL DEFAULT '',
  prompt_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_snapshot TEXT NOT NULL DEFAULT '',
  fragment_set_id TEXT,
  validation_issues_json TEXT NOT NULL DEFAULT '[]',
  valid_item_count INTEGER NOT NULL DEFAULT 0,
  invalid_item_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(root_section_id, version)
);

CREATE INDEX IF NOT EXISTS idx_chapter_analyses_book_root
  ON chapter_analyses(book_id, root_section_id, version);

CREATE TABLE IF NOT EXISTS source_fragment_sets (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(book_id, version)
);

CREATE INDEX IF NOT EXISTS idx_fragment_sets_book_status
  ON source_fragment_sets(book_id, status);

CREATE TABLE IF NOT EXISTS source_fragments (
  content_index TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  source_section_id TEXT NOT NULL,
  text TEXT NOT NULL,
  text_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_fragment_set_members (
  fragment_set_id TEXT NOT NULL
    REFERENCES source_fragment_sets(id) ON DELETE CASCADE,
  content_index TEXT NOT NULL
    REFERENCES source_fragments(content_index) ON DELETE RESTRICT,
  root_section_id TEXT NOT NULL,
  section_path_json TEXT NOT NULL,
  book_position INTEGER NOT NULL,
  section_position INTEGER NOT NULL,
  PRIMARY KEY(fragment_set_id, content_index)
);

CREATE INDEX IF NOT EXISTS idx_fragment_members_root_position
  ON source_fragment_set_members(fragment_set_id, root_section_id, book_position);

CREATE TABLE IF NOT EXISTS knowledge_item_sources (
  knowledge_item_id TEXT NOT NULL
    REFERENCES knowledge_items(id) ON DELETE CASCADE,
  content_index TEXT NOT NULL
    REFERENCES source_fragments(content_index) ON DELETE RESTRICT,
  source_order INTEGER NOT NULL,
  PRIMARY KEY(knowledge_item_id, content_index)
);

CREATE TABLE IF NOT EXISTS chapter_analysis_knowledge_items (
  chapter_analysis_id TEXT NOT NULL
    REFERENCES chapter_analyses(id) ON DELETE CASCADE,
  knowledge_item_id TEXT NOT NULL
    REFERENCES knowledge_items(id) ON DELETE RESTRICT,
  PRIMARY KEY(chapter_analysis_id, knowledge_item_id)
);

CREATE TABLE IF NOT EXISTS mind_maps (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'unknown',
  model TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  book_ids TEXT NOT NULL,
  status TEXT NOT NULL,
  album_special_requirements TEXT NOT NULL DEFAULT '',
  desired_episode_count INTEGER,
  episode_count_notice TEXT NOT NULL DEFAULT '',
  album_outline_draft_json TEXT NOT NULL DEFAULT '',
  album_outline_draft_signature TEXT NOT NULL DEFAULT '',
  album_prompt_version_id TEXT,
  album_prompt_system_version_id TEXT,
  model_overrides_json TEXT NOT NULL DEFAULT '{"album_outline":"kimi-k3"}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  content_type TEXT NOT NULL,
  style TEXT NOT NULL,
  content_framework TEXT NOT NULL DEFAULT '',
  section_identifier TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  source_section_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episode_knowledge_items (
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  knowledge_item_id TEXT NOT NULL
    REFERENCES knowledge_items(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'primary',
  PRIMARY KEY(episode_id, knowledge_item_id, role)
);

CREATE INDEX IF NOT EXISTS idx_episodes_project_position
  ON episodes(project_id, position);

CREATE TABLE IF NOT EXISTS artifact_versions (
  id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  prompt_version_id TEXT,
  prompt_system_version_id TEXT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  author_type TEXT NOT NULL DEFAULT 'model',
  input_snapshot TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(episode_id, stage, version)
);

CREATE TABLE IF NOT EXISTS prompt_templates (
  id TEXT PRIMARY KEY,
  stage_key TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  allowed_placeholders_json TEXT NOT NULL,
  required_placeholders_json TEXT NOT NULL,
  active_system_version_id TEXT,
  active_global_version_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id TEXT PRIMARY KEY,
  template_id TEXT NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
  scope TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  user_template TEXT NOT NULL,
  system_prompt TEXT NOT NULL DEFAULT '',
  protected_suffix TEXT NOT NULL DEFAULT '',
  system_version TEXT NOT NULL DEFAULT '',
  base_system_version_id TEXT,
  source_version_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(template_id, scope, project_id, version)
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_lookup
  ON prompt_versions(template_id, scope, project_id, version);

CREATE TABLE IF NOT EXISTS prompt_bindings (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  template_id TEXT NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
  active_version_id TEXT NOT NULL REFERENCES prompt_versions(id) ON DELETE RESTRICT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(project_id, template_id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  parent_run_id TEXT,
  error_stage TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

"""


MIGRATION_COLUMNS = {
    "books": {
        "book_type": "TEXT NOT NULL DEFAULT 'non_narrative'",
        "analysis_model_id": "TEXT",
    },
    "episodes": {
        "content_framework": "TEXT NOT NULL DEFAULT ''",
        "section_identifier": "TEXT NOT NULL DEFAULT ''",
    },
    "sections": {
        "analysis_enabled": "INTEGER NOT NULL DEFAULT 1",
        "analysis_exclusion_reason": "TEXT NOT NULL DEFAULT ''",
        "analysis_selection_source": "TEXT NOT NULL DEFAULT 'auto'",
    },
    "knowledge_items": {
        "chapter_analysis_id": "TEXT",
        "origin": "TEXT NOT NULL DEFAULT 'legacy'",
        "source_scheme": "TEXT NOT NULL DEFAULT 'legacy_section_source'",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "stable_key": "TEXT NOT NULL DEFAULT ''",
    },
    "projects": {
        "album_special_requirements": "TEXT NOT NULL DEFAULT ''",
        "desired_episode_count": "INTEGER",
        "episode_count_notice": "TEXT NOT NULL DEFAULT ''",
        "album_outline_draft_json": "TEXT NOT NULL DEFAULT ''",
        "album_outline_draft_signature": "TEXT NOT NULL DEFAULT ''",
        "album_prompt_version_id": "TEXT",
        "album_prompt_system_version_id": "TEXT",
        "model_overrides_json": (
            "TEXT NOT NULL DEFAULT '{\"album_outline\":\"kimi-k3\"}'"
        ),
    },
    "mind_maps": {
        "provider": "TEXT NOT NULL DEFAULT 'unknown'",
        "model": "TEXT NOT NULL DEFAULT 'unknown'",
    },
    "artifact_versions": {
        "author_type": "TEXT NOT NULL DEFAULT 'model'",
        "input_snapshot": "TEXT NOT NULL DEFAULT ''",
        "prompt_version_id": "TEXT",
        "prompt_system_version_id": "TEXT",
    },
    "chapter_analyses": {
        "fragment_set_id": "TEXT",
        "validation_issues_json": "TEXT NOT NULL DEFAULT '[]'",
        "valid_item_count": "INTEGER NOT NULL DEFAULT 0",
        "invalid_item_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "workflow_runs": {
        "parent_run_id": "TEXT",
        "error_stage": "TEXT NOT NULL DEFAULT ''",
        "position": "INTEGER NOT NULL DEFAULT 0",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            for table, columns in MIGRATION_COLUMNS.items():
                existing = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for column, definition in columns.items():
                    if column not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                        )
            self._initialize_analysis_candidates(connection)
            connection.execute(
                """
                UPDATE episodes
                SET content_framework =
                  '围绕“' || title || '”展开：先说明本集要解决的问题，'
                  || '再梳理关联原文中的核心观点、事件或案例，最后总结其意义。'
                WHERE TRIM(content_framework) = ''
                """
            )
            connection.execute(
                """
                UPDATE books
                SET status = 'ready_to_analyze', updated_at = ?
                WHERE book_type = 'non_narrative' AND status = 'analyzed'
                  AND NOT EXISTS (
                    SELECT 1 FROM knowledge_items
                    WHERE knowledge_items.book_id = books.id
                  )
                """,
                (now_iso(),),
            )
            connection.execute(
                """
                DELETE FROM mind_maps
                WHERE book_id IN (
                  SELECT id FROM books
                  WHERE book_type = 'non_narrative'
                    AND status IN (
                      'ready_to_analyze', 'analysis_partial', 'analysis_partial_failed'
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_parent
                ON workflow_runs(parent_run_id, position)
                """
            )

    @staticmethod
    def _initialize_analysis_candidates(connection: sqlite3.Connection) -> None:
        from .ingestion import ANALYSIS_EXCLUDE_RE, ANALYSIS_INCLUDE_RE

        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, parent_id, title, content, analysis_selection_source
                FROM sections ORDER BY position
                """
            ).fetchall()
        ]
        by_parent: dict[str | None, list[dict[str, Any]]] = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)

        def total_size(root: dict[str, Any]) -> int:
            total = len(root["content"].strip())
            stack = list(by_parent.get(root["id"], []))
            while stack:
                current = stack.pop()
                total += len(current["title"]) + len(current["content"].strip())
                stack.extend(by_parent.get(current["id"], []))
            return total

        connection.execute(
            """
            UPDATE sections
            SET analysis_enabled = 0, analysis_exclusion_reason = ''
            WHERE analysis_selection_source = 'auto' AND parent_id IS NOT NULL
            """
        )
        for root in by_parent.get(None, []):
            if root["analysis_selection_source"] != "auto":
                continue
            title = root["title"].strip()
            if ANALYSIS_EXCLUDE_RE.search(title):
                enabled, reason = 0, "疑似目录、版权或表格注释"
            elif not ANALYSIS_INCLUDE_RE.search(title) and (
                len(title) > 36 or title.endswith(("。", "；", ";"))
            ):
                enabled, reason = 0, "疑似正文注释或误识别标题"
            elif total_size(root) < 500 and not ANALYSIS_INCLUDE_RE.search(title):
                enabled, reason = 0, "正文过短，疑似异常一级标题"
            else:
                enabled, reason = 1, ""
            connection.execute(
                """
                UPDATE sections
                SET analysis_enabled = ?, analysis_exclusion_reason = ?
                WHERE id = ?
                """,
                (enabled, reason, root["id"]),
            )

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            result = connection.execute(query, params).fetchall()
        return [decode_row(dict(row)) for row in result]

    def row(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            result = connection.execute(query, params).fetchone()
        return decode_row(dict(result)) if result else None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(query, params)

    def executemany(self, query: str, params: list[tuple[Any, ...]]) -> None:
        with self.connect() as connection:
            connection.executemany(query, params)


JSON_COLUMNS = {
    "book_ids",
    "metadata_json",
    "source_section_ids",
    "structured_json",
    "section_path_json",
    "validation_issues_json",
    "model_overrides_json",
    "allowed_placeholders_json",
    "required_placeholders_json",
}


def decode_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in JSON_COLUMNS:
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except json.JSONDecodeError:
                row[key] = {} if key == "metadata_json" else []
    return row
