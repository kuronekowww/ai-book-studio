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
        version="2026-07-28.1",
        system=(
            "你是严谨的书籍拆解编辑。只使用给定章节原文，禁止补充无法从原文"
            "确认的事实。观点、论据、定义和案例可以忠实概括；金句优先保留原文。"
        ),
        user_template=(
            "# 你的任务\n"
            "仔细阅读书籍章节，根据章节主题提炼作者的主要观点和论据。\n"
            "1. 一句话提炼章节主题；\n"
            "2. 按内容划分子主题，优先使用原文表述；\n"
            "3. 识别全部主要观点，以及相关论据、金句、概念定义和案例；\n"
            "4. 案例使用问题引导法完成分析后，只输出整合后的概述和关联。\n\n"
            "# 约束\n"
            "主要观点、论据、概念定义和案例可以在忠于原意的前提下概括；"
            "金句优先保留原文表述，允许对标点、摘录范围和措辞做轻微整理。"
            "每一项知识内容都必须独立给出 source_content_indexes 数组，可按原文顺序"
            "引用一个或多个输入中的段落级 content_index。禁止编造或改写索引。"
            "所有知识资产都会校验来源索引是否真实存在，不做逐字原文匹配。"
            "案例概述可以归纳，但必须提供对应原文证据。"
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
            '"viewpoints":[{{"text":"主要观点",'
            '"source_content_indexes":["content_x"],'
            '"arguments":[{{"text":"论据",'
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
        version="2026-07-28.1",
        system="你负责无损压缩章节拆书稿，来源索引是不可修改的事实键。",
        user_template=(
            "以下内容可能是完整章节，也可能是长章节中按原顺序截取的一段。"
            "请独立压缩当前输入，保留当前输入中的标题、全部 content_index、概念定义、"
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
        version="2026-07-29.2",
        system=(
            "你是一位资深讲书专辑总编，擅长从知识材料中发现听众真正关心的"
            "问题，把一本书编排成准确、通俗、有故事感并且有连续收听动力的"
            "有声专辑。"
        ),
        user_template=(
            "请根据当前知识模块和来源章节，为未读过原书的听众设计一组连续声音。"
            "每集只解决一个明确问题，标题具体、有吸引力，讲述轻松但克制，"
            "并用清楚的因果链保留原书精华。\n\n"
            "# 听众前提\n"
            "听众此前没有阅读过原书，将主要通过连续收听这张专辑获取本书内容。"
            "不得假设听众已理解书中概念或前文结论。\n\n"
            "只输出 Markdown 专辑设计，不要 JSON、知识资产 ID、段落索引、"
            "分析过程或完整口播稿。每集必须包含标题、听众钩子、核心主题、"
            "2 至 4 条核心要点、内容类型和一个或多个输入中的 CHAPTER 标识。\n\n"
            "{source}"
        ),
    ),
    "album_outline_count_repair": PromptDefinition(
        id="album_outline_count_repair",
        version="2026-07-29.1",
        system=(
            "你是专辑大纲责任编辑，只负责把一个知识模块的声音条目调整为指定数量。"
        ),
        user_template=(
            "把首次生成的模块大纲重组为严格指定数量。可以合并重复选题、删减重复条目"
            "或重新拆分，但不得新增事实、观点、章节标识或输出 JSON。每集必须保留"
            "标题、听众钩子、核心主题、2 至 4 条核心要点、内容类型和来源章节。"
            "只输出修正后的 Markdown 声音条目。\n\n{source}"
        ),
    ),
    "album_module_plan": PromptDefinition(
        id="album_module_plan",
        version="2026-07-29.2",
        system="你负责把一本书的轻量章节目录组织成循序渐进的讲书知识模块。",
        user_template=(
            "根据章节目录设计完整知识模块。覆盖所有 CHAPTER 标识，不得编造标识，"
            "不展开每集，不输出 JSON。若输入给出目标集数和允许浮动范围，所有模块的"
            "建议声音数之和必须落在该范围内；需要时合并相邻章节，避免模块过碎。"
            "每个模块按以下 Markdown 格式输出：\n"
            "## 模块N：模块标题\n"
            "听众问题：这个模块为未读听众解决什么问题？\n"
            "认知顺序：如何承接前后模块。\n"
            "来源章节：[CHAPTER_001]、[CHAPTER_002]\n"
            "建议声音数：数字\n\n{source}"
        ),
    ),
    "album_outline_structure": PromptDefinition(
        id="album_outline_structure",
        version="2026-07-30.1",
        system="你只负责把已完成的 Markdown 专辑大纲转换为结构化数据，不做创作。",
        user_template=(
            "把输入 Markdown 逐集转换为合法 JSON，保留标题、听众钩子、核心主题、"
            "核心要点、内容类型、每段开头给出的 MODULE 标识和 CHAPTER 标识，"
            "不得改写、增删或排序。\n"
            '只输出：{{"album_outline":[{{"title":"声音标题",'
            '"main_points":"听众钩子：……\\n核心主题：……\\n核心要点：\\n1. ……",'
            '"module_key":"MODULE_001","chapter_keys":["CHAPTER_001"],'
            '"content_type":"解读"}}]}}\n\n{source}'
        ),
    ),
    "episode_source_match": PromptDefinition(
        id="episode_source_match",
        version="2026-07-29.1",
        system="你只负责从候选知识资产中选择当前声音真正需要的来源。",
        user_template=(
            "根据声音标题和内容框架，从候选目录中选择直接相关、足以支撑本集的"
            "知识资产。不得返回候选目录之外的 ID，不要解释。\n"
            '只输出合法 JSON：{{"knowledge_item_ids":["knowledge_x"]}}\n\n{source}'
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
    "episode_word_count_repair": PromptDefinition(
        id="episode_word_count_repair",
        version="2026-07-30.1",
        system=(
            "你是讲书口播稿的篇幅责任编辑。只调整篇幅和表达密度，"
            "不得增加输入之外的事实、观点、案例、人物或数据。"
        ),
        user_template=(
            "把当前文稿调整到指定字数范围。保留中心问题、事实、因果、观点顺序、"
            "直接引语和结尾方向；过长时删除重复、合并同义论据并压缩背景，"
            "过短时只展开当前已有观点的解释、因果和输入中已有的例子。"
            "不得新增独立主题，不得输出标题、字数说明、分析过程或修改说明。"
            "只输出调整后的连续口播正文。\n\n{source}"
        ),
    ),
}
