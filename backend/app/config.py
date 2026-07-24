from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    provider: str
    api_base: str
    api_key: str
    model: str


def get_settings() -> Settings:
    data_dir = Path(os.getenv("AI_BOOK_STUDIO_DATA_DIR", "../data")).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "studio.sqlite3",
        provider=os.getenv("AI_BOOK_STUDIO_PROVIDER", "demo"),
        api_base=os.getenv("AI_BOOK_STUDIO_API_BASE", "https://api.openai.com/v1"),
        api_key=os.getenv("AI_BOOK_STUDIO_API_KEY", ""),
        model=os.getenv("AI_BOOK_STUDIO_MODEL", "gpt-4.1-mini"),
    )
