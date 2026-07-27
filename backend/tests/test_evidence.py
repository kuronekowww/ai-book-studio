from app.db import Database
from app.evidence import (
    EvidenceService,
    build_fragment_records,
    compact_text,
    semantic_paragraph_fragments,
)
from test_chapter_analysis import seed_chapter_book


def test_semantic_fragments_are_bounded_and_lossless() -> None:
    text = "\n\n".join(
        [
            "短段一。" * 20,
            "短段二。" * 30,
            "这是一个很长的句子。" * 120,
            "结尾。" * 10,
        ]
    )

    fragments = semantic_paragraph_fragments(text)

    assert len(fragments) > 1
    assert all(len(fragment) <= 800 for fragment in fragments)
    assert compact_text("\n\n".join(fragments)) == compact_text(text)


def test_fragment_indexes_are_stable_and_do_not_cross_sections(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    book_id = seed_chapter_book(database)
    sections = database.rows(
        "SELECT * FROM sections WHERE book_id = ? ORDER BY position", (book_id,)
    )

    first = build_fragment_records(book_id, sections)
    second = build_fragment_records(book_id, sections)

    assert [item.content_index for item in first] == [
        item.content_index for item in second
    ]
    assert all(item.source_section_id for item in first)
    assert all(item.root_section_id == sections[0]["id"] for item in first)


def test_fragment_set_versions_only_when_content_changes(tmp_path) -> None:
    database = Database(tmp_path / "studio.sqlite3")
    database.init()
    book_id = seed_chapter_book(database)
    evidence = EvidenceService(database)

    first = evidence.ensure_current_fragment_set(book_id)
    same = evidence.ensure_current_fragment_set(book_id)
    database.execute(
        """
        UPDATE sections SET content = content || '新增内容。'
        WHERE book_id = ? AND parent_id IS NOT NULL
        """,
        (book_id,),
    )
    changed = evidence.ensure_current_fragment_set(book_id)

    assert same["id"] == first["id"]
    assert changed["id"] != first["id"]
    assert changed["version"] == 2
    assert database.row(
        "SELECT status FROM source_fragment_sets WHERE id = ?", (first["id"],)
    )["status"] == "historical"
