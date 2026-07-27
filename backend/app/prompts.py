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
        version="2026-07-27.3",
        system=(
            "你是严谨的书籍拆解编辑。只使用给定章节原文，禁止补充无法从原文"
            "确认的事实。完整保留主要观点、论据、金句和定义的原文表述。"
        ),
        user_template=(
            "# 你的任务\n"
            "仔细阅读书籍章节，根据章节主题提炼作者的主要观点和论据。\n"
            "1. 一句话提炼章节主题；\n"
            "2. 按内容划分子主题，优先使用原文表述；\n"
            "3. 识别全部主要观点，以及相关论据、金句、概念定义和案例；\n"
            "4. 案例使用问题引导法完成分析后，只输出整合后的概述和关联。\n\n"
            "# 约束\n"
            "概念定义、主要观点、论据、金句和案例证据必须逐字保留原文表述。"
            "每一项知识内容都必须独立给出 source_content_indexes 数组，可按原文顺序"
            "引用一个或多个输入中的段落级 content_index。禁止编造或改写索引。"
            "模型输出会做逐字校验：定义、观点、论据、金句和案例 evidence_quotes "
            "必须能在对应原文块中连续找到。案例概述可以归纳，但必须提供原文证据。"
            "没有的可选内容使用空数组或 null。"
            "JSON 字符串内部出现英文双引号时必须使用反斜杠转义，或改用中文引号。\n\n"
            "# 输出\n"
            "只输出合法 JSON，不要代码围栏或解释：\n"
            '{{"chapter_title":"完整章节标题","chapter_theme":"一句话主题",'
            '"subtopics":[{{"title":"子主题标题",'
            '"definitions":[{{"name":"概念","definition":"完整定义原文",'
            '"source_content_indexes":["content_x"]}}],'
            '"quotes":[{{"text":"金句原文",'
            '"source_content_indexes":["content_x"]}}],'
            '"viewpoints":[{{"text":"主要观点完整原文",'
            '"source_content_indexes":["content_x"],'
            '"arguments":[{{"text":"论据原文",'
            '"source_content_indexes":["content_x"]}}],'
            '"case":{{"summary":"案例完整概述","relation":"与观点的关联",'
            '"source_content_indexes":["content_x"],'
            '"evidence_quotes":[{{"text":"案例证据原文",'
            '"source_content_indexes":["content_x"]}}]}}}}]}}]}}\n\n'
            "# 章节原文\n{source}"
        ),
    ),
    "json_repair": PromptDefinition(
        id="json_repair",
        version="2026-07-27.1",
        system="你只负责修复 JSON 语法，不得删减、概括、改写或新增任何内容。",
        user_template=(
            "把以下模型输出修复成合法 JSON。修正字符串引号转义、逗号和括号，"
            "保持字段、文本、数组顺序和 content_index 完全不变。"
            "只输出合法 JSON，不要代码围栏或解释。\n\n{source}"
        ),
    ),
    "chapter_compression": PromptDefinition(
        id="chapter_compression",
        version="2026-07-27.2",
        system="你负责无损压缩章节拆书稿，来源索引是不可修改的事实键。",
        user_template=(
            "压缩以下章节拆书稿，保留章节标题、全部 content_index、概念定义、"
            "主要观点、关键论据、案例和金句。不得新增、删除或修改任何 "
            "content_index 或 knowledge_item_id。只输出 Markdown 压缩稿。\n\n{source}"
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
        version="2026-07-27.2",
        system="你是讲书类内容创作者，擅长金字塔结构和书籍知识地图。",
        user_template=(
            "根据完整拆书稿判断书籍内容性质，建立循序渐进、通俗易懂的系统化"
            "知识体系。第二层分支标明引用章节，优先使用原书表述，末梢可增加"
            "加粗的用户视角金句。只输出完整 Markdown 思维导图，不要分析过程，"
            "不要 Mermaid。\n\n# 拆书稿\n{source}"
        ),
    ),
    "album_outline": PromptDefinition(
        id="album_outline",
        version="2026-07-27.3",
        system="你是书籍解读有声内容创作者。每条声音必须明确引用有效知识资产。",
        user_template=(
            "根据输入的完整拆书稿、书籍信息和可选创作要求设计有声专辑目录。"
            "优先遵循拆书稿叙述顺序，每条普通声音选择一个或多个"
            " knowledge_item_id 作为组稿知识资产；同一知识资产不得作为多条普通声音"
            "的主要来源。段落级 content_index 只用于这些知识资产的原文证据，"
            "不要把它作为专辑编排键。标题要有趣吸睛，不生成单独导入或尾声。"
            "叙事类只使用“解读类”；非叙事类仅在确有承上启下需要时增加“过渡类”，"
            "不得用过渡声音凑集数。期望集数是目标，不能通过虚构来源满足。\n\n"
            "只输出合法 JSON，不要代码围栏或解释：\n"
            '{{"album_outline":[{{"title":"声音标题",'
            '"main_points":"主要观点与内容框架",'
            '"knowledge_item_ids":["knowledge_x","knowledge_y"],'
            '"content_type":"解读类/过渡类"}}]}}\n\n{source}'
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
