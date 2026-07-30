import pytest

from app.text_metrics import (
    count_spoken_words,
    format_episode_word_count_range,
    inspect_word_count,
    validate_episode_word_count_range,
)


def test_count_spoken_words_uses_chinese_audio_script_rules() -> None:
    text = "# 标题\n中文，123！ABC and don't。\n**重点**"

    assert count_spoken_words(text) == 2 + 2 + 3 + 1 + 1 + 1 + 2
    assert "2000–2500" in format_episode_word_count_range(2000, 2500)


def test_word_count_range_validation_and_delta() -> None:
    assert inspect_word_count("字" * 320, 300, 400).within_range
    assert inspect_word_count("字" * 250, 300, 400).instruction == "需要增加至少 50 字"
    assert inspect_word_count("字" * 450, 300, 400).instruction == "需要减少至少 50 字"

    with pytest.raises(ValueError, match="最少字数不能大于"):
        validate_episode_word_count_range(500, 400)
    with pytest.raises(ValueError, match="不能低于"):
        validate_episode_word_count_range(299, 400)
    with pytest.raises(ValueError, match="不能超过"):
        validate_episode_word_count_range(300, 10001)
