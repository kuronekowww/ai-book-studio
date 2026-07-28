from __future__ import annotations

import argparse
import json

from .config import get_settings
from .db import Database
from .providers import DemoProvider
from .workflows import WorkflowService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Book Studio 本地维护工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    revalidate = subparsers.add_parser(
        "revalidate-partial",
        help="按当前规则重新校验一本书的最新部分成功章节",
    )
    revalidate.add_argument("--book-id", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    database = Database(settings.database_path)
    database.init()
    service = WorkflowService(database, DemoProvider())
    if args.command == "revalidate-partial":
        result = service.revalidate_partial_chapters(args.book_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
