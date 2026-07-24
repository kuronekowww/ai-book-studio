from app.obsidian import USER_BEGIN, USER_END, merge_note


def test_merge_note_preserves_user_notes() -> None:
    existing = (
        "<!-- AI_BOOK_STUDIO:BEGIN -->\n旧内容\n<!-- AI_BOOK_STUDIO:END -->\n"
        f"{USER_BEGIN}\n我的批注\n{USER_END}\n"
    )
    merged = merge_note(existing, "# 新内容")
    assert "# 新内容" in merged
    assert "旧内容" not in merged
    assert "我的批注" in merged
