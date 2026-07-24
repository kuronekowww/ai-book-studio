from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptDefinition:
    id: str
    version: str
    system: str
    user_template: str


PROMPTS = {
    "book_analysis": PromptDefinition(
        id="book_analysis",
        version="2026-07-24.1",
        system="你是严谨的拆书编辑。只使用给定原文，不补充无法从原文确认的事实。",
        user_template=(
            "请把以下书籍小节结构化为子主题、主要观点、论据、案例与金句。"
            "每项必须保留来源小节 ID。\n\n{source}"
        ),
    ),
    "mind_map": PromptDefinition(
        id="mind_map",
        version="2026-07-24.1",
        system="你负责整理一本书的知识结构，不改变作者观点。",
        user_template="根据以下拆书资产生成 Markdown 思维导图：\n\n{source}",
    ),
    "album_outline": PromptDefinition(
        id="album_outline",
        version="2026-07-24.1",
        system="你是讲书专辑策划。每条声音必须明确引用来源。",
        user_template=(
            "根据知识资产设计专辑大纲。每条声音包含标题、3-6 条主要内容、"
            "内容类型、风格类型和来源 ID。\n\n{source}"
        ),
    ),
    "episode_outline": PromptDefinition(
        id="episode_outline",
        version="2026-07-24.1",
        system="你是声音细纲编辑，避免幻觉并突出观点与证据。",
        user_template="根据粗纲和来源资产生成声音细纲：\n\n{source}",
    ),
    "episode_draft": PromptDefinition(
        id="episode_draft",
        version="2026-07-24.1",
        system="你是讲书口播稿作者。忠于原书，引用案例，正文约 1500 字。",
        user_template="根据细纲和原文证据生成声音初稿：\n\n{source}",
    ),
    "episode_final": PromptDefinition(
        id="episode_final",
        version="2026-07-24.1",
        system="你负责把文稿改成自然、清晰、适合听觉场景的中文口播。",
        user_template="精修以下初稿，保留事实与来源边界：\n\n{source}",
    ),
}
