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
  status TEXT NOT NULL DEFAULT 'draft'
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
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mind_maps (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  book_ids TEXT NOT NULL,
  status TEXT NOT NULL,
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
  status TEXT NOT NULL,
  source_section_ids TEXT NOT NULL
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
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  author_type TEXT NOT NULL DEFAULT 'model',
  input_snapshot TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(episode_id, stage, version)
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
    },
    "episodes": {
        "content_framework": "TEXT NOT NULL DEFAULT ''",
    },
    "artifact_versions": {
        "author_type": "TEXT NOT NULL DEFAULT 'model'",
        "input_snapshot": "TEXT NOT NULL DEFAULT ''",
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
                CREATE INDEX IF NOT EXISTS idx_workflow_runs_parent
                ON workflow_runs(parent_run_id, position)
                """
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
