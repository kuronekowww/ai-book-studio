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
    "character_relationships": PromptDefinition(
        id="character_relationships",
        version="2026-07-27.1",
        system=(
            "你是严谨的叙事类书籍拆解编辑。只依据当前原文块识别人物关系，"
            "不得使用书外知识或补写原文没有说明的关系。"
        ),
        user_template=(
            "请提取当前原文块中能够确认的人物关系。没有明确人物关系时返回空数组。\n"
            "只输出合法 JSON，不要输出 Markdown 代码围栏：\n"
            '{{"relationships":[{{"characters":["人物A","人物B"],'
            '"relationship":"原文能够确认的关系","evidence":"简短原文依据"}}]}}\n\n'
            "# 当前原文块\n{source}"
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
    "episode_outline_narrative": PromptDefinition(
        id="episode_outline_narrative",
        version="2026-07-27.1",
        system=(
            "你是一位专业的有声讲书专辑制作人，擅长书籍的深度解读，"
            "能把剧情内容讲清楚，并对关键剧情作出讲解。"
        ),
        user_template=(
            "# 你的任务\n"
            "根据声音内容框架和书籍内容，设计一份声音内容细纲"
            "（能支撑约1500字正文）。在思考过程中完成以下分析，再输出细纲：\n"
            "1. 结合声音主题，分析如何将书籍内容做通俗化解读，把人物和剧情讲清楚；\n"
            "2. 识别大纲中用户可能不了解但未展开的概念、人物或背景，补充必要说明；\n"
            "3. 理清故事发展和情节，补充细节，避免遗漏关键情节。\n\n"
            "# 大纲框架\n"
            "1. **声音主题**\n"
            "2. **开篇**：直接简要预告声音的主要内容，提供清晰的认知地图\n"
            "3. **过渡句**：在每两部分之间，用一句话承接上下内容\n"
            "4. **正文部分**\n"
            "   - 背景（如需）：解释陌生概念、名词和人物\n"
            "   - 主要事件：核心情节点、事件经过、影响与人物分析\n"
            "5. **结尾**：简单一句话，引导用户继续收听下一集\n\n"
            "# 要求\n"
            "1. 正文情节点仅限输入原文中提及的事件和人物，禁止超出事件发展范围；\n"
            "2. 原封不动保留引用的原文金句或表述，禁止虚构、夸大或改动书籍内容；\n"
            "3. 从用户视角补充必要说明，确保剧情连贯易懂；\n"
            "4. 语言简洁精准，只输出细纲正文。\n\n"
            "# 输出格式\n"
            "# 声音主题\n"
            "## 开篇\n- 预告：\n"
            "## 过渡句\n"
            "## 第一部分\n"
            "### 主要事件1：\n"
            "- 背景（optional）\n"
            "事件经过：\n- 情节点1\n- 情节点2\n"
            "  - 事件影响（optional）：\n"
            "  - 人物分析（optional）：\n"
            "## 结尾\n- 简单一句话，引导用户继续收听下一集\n\n"
            "# 输入材料\n{source}"
        ),
    ),
    "episode_outline_non_narrative": PromptDefinition(
        id="episode_outline_non_narrative",
        version="2026-07-27.1",
        system="你是专业的有声讲书专辑制作人，擅长把复杂观点讲得清晰、准确、易懂。",
        user_template=(
            "# 你的任务\n"
            "根据声音内容框架和原文证据设计一份能支撑约1500字正文的声音细纲。\n"
            "解释听众可能不了解的概念，梳理作者观点、论据、案例和结论之间的关系。\n\n"
            "# 大纲框架\n"
            "1. 声音主题\n2. 开篇预告\n3. 分部分展开观点与案例\n"
            "4. 部分之间的过渡句\n5. 一句话结尾\n\n"
            "# 要求\n"
            "1. 所有事实、观点和案例必须来自输入原文；\n"
            "2. 原封不动保留引用的原文金句，禁止虚构、夸大或改动书籍内容；\n"
            "3. 区分作者观点、原文案例和编辑解释；\n"
            "4. 语言简洁精准，只输出细纲正文。\n\n"
            "# 输入材料\n{source}"
        ),
    ),
    "episode_draft": PromptDefinition(
        id="episode_draft",
        version="2026-07-27.1",
        system="你是讲书口播稿作者。忠于原书，引用案例，正文约 1500 字。",
        user_template=(
            "根据输入中的声音细纲和原文证据生成声音初稿。"
            "以细纲为结构，以原文为唯一事实边界；不得虚构或扩展原文没有的信息。\n\n"
            "{source}"
        ),
    ),
    "episode_final": PromptDefinition(
        id="episode_final",
        version="2026-07-27.1",
        system="你负责把文稿改成自然、清晰、适合听觉场景的中文口播。",
        user_template=(
            "对输入中的声音初稿做口语化调整。保留初稿的事实范围与原文引用，"
            "只优化结构、节奏和表达，不得借原文证据增加初稿未覆盖的新事实段落。\n\n"
            "{source}"
        ),
    ),
}
