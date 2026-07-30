from __future__ import annotations

import re
from dataclasses import dataclass


MIN_EPISODE_WORD_COUNT = 300
MAX_EPISODE_WORD_COUNT = 10_000
DEFAULT_EPISODE_WORD_COUNT_MIN = 2_000
DEFAULT_EPISODE_WORD_COUNT_MAX = 2_500

_COUNTABLE_TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    r"|[0-9]"
    r"|[A-Za-z]+(?:['’\-][A-Za-z]+)*"
)


def count_spoken_words(text: str) -> int:
    """Count Chinese characters, digits and continuous English words."""
    return sum(1 for _ in _COUNTABLE_TOKEN_RE.finditer(text or ""))


def format_episode_word_count_range(minimum: int, maximum: int) -> str:
    return (
        f"每集 {minimum}–{maximum} 字；汉字和阿拉伯数字逐字计数，"
        "连续英文单词计 1 字，标点、Markdown 标记和空白不计入。"
    )


def validate_episode_word_count_range(minimum: int, maximum: int) -> None:
    if minimum < MIN_EPISODE_WORD_COUNT:
        raise ValueError(
            f"每集最少字数不能低于 {MIN_EPISODE_WORD_COUNT}"
        )
    if maximum > MAX_EPISODE_WORD_COUNT:
        raise ValueError(
            f"每集最多字数不能超过 {MAX_EPISODE_WORD_COUNT}"
        )
    if minimum > maximum:
        raise ValueError("每集最少字数不能大于最多字数")


@dataclass(frozen=True)
class WordCountResult:
    actual: int
    minimum: int
    maximum: int

    @property
    def within_range(self) -> bool:
        return self.minimum <= self.actual <= self.maximum

    @property
    def delta(self) -> int:
        if self.actual < self.minimum:
            return self.minimum - self.actual
        if self.actual > self.maximum:
            return self.maximum - self.actual
        return 0

    @property
    def instruction(self) -> str:
        if self.actual < self.minimum:
            return f"需要增加至少 {self.minimum - self.actual} 字"
        if self.actual > self.maximum:
            return f"需要减少至少 {self.actual - self.maximum} 字"
        return "当前字数已经位于目标范围内"


def inspect_word_count(
    text: str, minimum: int, maximum: int
) -> WordCountResult:
    validate_episode_word_count_range(minimum, maximum)
    return WordCountResult(
        actual=count_spoken_words(text),
        minimum=minimum,
        maximum=maximum,
    )
