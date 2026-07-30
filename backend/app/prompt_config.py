from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .db import Database, now_iso
from .prompts import PromptDefinition


CONFIGURABLE_PROMPT_STAGES = (
    "mind_map",
    "album_module_plan",
    "album_outline",
    "episode_outline",
    "episode_draft",
    "episode_final",
)

TOKEN_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
MAX_TEMPLATE_CHARS = 50_000


@dataclass(frozen=True)
class PromptTemplateSpec:
    stage_key: str
    label: str
    system_version: str
    system_prompt: str
    default_user_template: str
    protected_suffix: str
    placeholders: dict[str, str]
    required_placeholders: tuple[str, ...]
    required_one_of: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class PromptSnapshot:
    stage_key: str
    prompt_id: str
    source: str
    prompt_version_id: str
    system_version_id: str
    version_label: str
    source_scope: str
    source_label: str
    user_template: str
    system_prompt: str
    protected_suffix: str

    @property
    def prompt(self) -> PromptDefinition:
        return PromptDefinition(
            id=self.prompt_id,
            version=self.version_label,
            system=self.system_prompt,
            user_template="{source}",
        )

    def lock(self) -> dict[str, str]:
        return {
            "prompt_version_id": self.prompt_version_id,
            "system_version_id": self.system_version_id,
        }


MIND_MAP_DEFAULT_TEMPLATE = """请根据全书拆书稿，为此前没有读过原书、将主要通过
听书理解内容的听众，设计一份循序渐进、通俗易懂的 Markdown 知识地图。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}
书籍类型：{{book_type}}

# 全书拆书稿
{{full_book_analysis}}

# 设计方法
1. 先识别全书试图回答的核心问题、内容性质和听众读完后应建立的总体认识。
2. 按金字塔结构归并知识，让上层是核心判断，下层是解释、证据、案例或事件。
3. 用因果、递进、并列或对照关系连接章节，按未读听众的理解路径安排顺序，避免照抄目录。
4. 第二层分支标明来源一级章节，优先使用原书中的准确表述。
5. 末梢可以增加用户视角的简短总结，但不得引入材料之外的新事实。

思维导图不负责声音标题、逐集策划或口播表达，只负责展示全书知识体系。"""

MIND_MAP_PROTECTED_SUFFIX = """# 系统保护约束
所有知识、事实、人物、案例、数据和因果只能来自输入拆书稿。不得为了完整性补充外部
背景或常识推断。只输出完整 Markdown 思维导图，不得输出分析过程、JSON、Mermaid、
数据库 ID、段落索引、声音标题或口播稿。"""

ALBUM_MODULE_PLAN_DEFAULT_TEMPLATE = """请根据覆盖全书的策划版拆书稿和章节目录，
为此前没有读过原书、将主要通过听书理解内容的听众设计完整、连续的知识模块。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}
书籍类型：{{book_type}}

# 专辑要求
特殊要求：{{album_special_requirements}}
目标集数与允许范围：{{desired_episode_count}}
每集目标字数：{{episode_word_count_range}}

# 全书章节目录
{{chapter_catalog}}

# 策划版全书拆书稿
{{planning_book_analysis}}

# 模块设计方法
1. 识别书籍类型、全书核心问题和听众获取这本书内容时最需要建立的知识路径。
2. 覆盖全部一级章节；优先尊重原书顺序，但当未读听众需要先补背景或概念时，可调整理解顺序。
3. 每个模块解决一个清楚的听众问题，模块之间形成因果、递进、对照或从现象到机制的关系。
4. 结合目标集数控制模块数量；相邻且高度相关、共同回答同一问题的章节可以合并。
5. 说明每个模块怎样承接前文、又为后文准备什么，避免模块之间互相重复。
6. 此处只设计知识模块，不展开逐集标题、听众钩子、声音细纲或口播稿。"""

ALBUM_MODULE_PLAN_PROTECTED_SUFFIX = """# 系统保护约束
不得遗漏或编造 CHAPTER 标识，不得增加输入材料之外的事实。只设计知识模块，不生成逐集
声音标题、听众钩子、声音细纲或口播正文。只输出以下 Markdown 结构，不要输出 JSON、
逐集大纲、数据库 ID、知识资产 ID 或段落索引：

## 模块N：模块标题
听众问题：这个模块为未读听众解决什么问题？
认知顺序：如何承接前后模块。
来源章节：[CHAPTER_001]、[CHAPTER_002]
建议声音数：数字"""

ALBUM_DEFAULT_TEMPLATE = """请面向此前没有读过原书、主要通过连续收听理解本书的听众，
根据当前知识模块和详细拆书稿设计有吸引力、循序渐进的声音目录。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}
书籍类型：{{book_type}}

# 专辑要求
特殊要求：{{album_special_requirements}}
目标集数与允许范围：{{desired_episode_count}}
每集目标字数：{{episode_word_count_range}}

# 当前模块
{{module_brief}}

# 全书章节目录
{{chapter_catalog}}

# 组稿方法
1. 先找出当前模块最值得听众追问的异常、冲突、人物选择、反常识结果、现实困惑或因果悬念。
2. 每集设置一个唯一中心问题和一句听众钩子，让没读过书的人立刻知道“为什么值得听”。
3. 每集安排 2 至 4 个递进核心要点，沿“建立背景或发现异常—提出问题—解释原因或机制—
   展开结果与影响—回答开场问题”推进，而不是罗列章节摘要。
4. 标题优先使用具体问题、矛盾、选择、反差或因果追问；轻松但克制，不堆夸张词、
   网络梗或空泛宏大概念。
5. 根据每集目标字数控制信息容量；一个能够独立成集的主题不要塞进“全景概述”，
   同一观点也不要换标题重复凑数。
6. 相邻声音要形成自然的认知递进和连续收听动力，但不强制每集回顾或预告下一集。
7. 保留原书的关键观点、概念、案例和数据意义，不机械地一章一集；允许一集关联多个
   一级章节，也允许同一一级章节支持多集。

# 当前模块详细拆书稿
{{module_book_analysis}}"""

ALBUM_PROTECTED_SUFFIX = """# 系统保护约束
所有事实、观点、人物、案例和数据必须来自当前模块材料。不得输出 knowledge_item_id、
content_index、数据库 ID、完整口播稿或 JSON；不得编造 CHAPTER 标识。每集至少选择
一个、允许选择多个来源章节，同一章节可以用于多集。不生成单独导入或尾声；叙事类
只使用“解读”，非叙事类仅在确有必要时使用“过渡”。
如果“当前模块”中包含“本模块分配集数”，必须严格输出该数量的声音条目。
当前调用只处理一个知识模块。即使用户模板仍出现“全书”“整张专辑”或“全专辑集数”
等旧措辞，也不得扩展到其他模块；唯一数量约束是当前模块分配集数。

# 当前模块任务（系统强制注入）
{{module_brief}}

本项目的每集目标字数是：{{episode_word_count_range}}
该范围优先于用户模板中的“约 1500 字”等旧篇幅要求。每集只解决一个中心问题，
通常使用 2 至 4 个递进要点；禁止把多个能够独立成集的大主题压成一集全景概述。

只输出以下 Markdown 结构：
## 第1集：声音标题
听众钩子：一句话说明为什么值得听。
核心主题：一句话说明本集解决的问题。
核心要点：
1. 第一条递进内容；
2. 第二条递进内容；
内容类型：解读
来源章节：[CHAPTER_001]、[CHAPTER_002]"""

EPISODE_OUTLINE_DEFAULT_TEMPLATE = """根据当前声音框架和所属模块拆书稿，
设计一份能够支撑目标篇幅的声音细纲。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}

# 当前声音
标题：{{episode_title}}

# 每集目标字数
{{episode_word_count_range}}

# 当前声音框架（来自已确认的专辑大纲）
{{episode_framework}}

# 所属模块详细拆书稿
{{module_book_analysis}}

# 细纲方法
1. 明确本集唯一中心问题，以及听众听完后最终应能复述的判断。
2. 开篇直接预告要解决的问题和理解路线，不铺陈与主题无关的背景。
3. 根据目标字数安排 2 至 4 个递进正文部分；每部分只承担一个主要认知任务，并标注
   主要观点、支撑论据、故事抓手和大致字数预算。
4. 故事抓手优先选择材料中的人物选择、案例、调查、数据变化或结果反差，案例最多两个。
   没有案例时，只设计明确标注为假设、且不承担事实论据功能的生活场景。
5. 对听众可能不认识的人物、概念、制度和数据安排最低必要解释，先说事实，再解释含义。
6. 在每两部分之间写一句承上启下的过渡；结尾用一两句回答开场问题，不强行引用金句。
7. 超出目标篇幅、偏离唯一中心问题或与其他声音重复的知识点必须明确舍弃。

# 输出结构
# 声音主题
- 中心问题：
- 听众最终应能复述的判断：
## 开篇
- 预告：
## 第一部分
- 认知任务：
- 主要观点：
- 论据：
- 故事抓手（如有）：
- 字数预算：
## 过渡句
- 可直接进入正文的完整句子
## 第二部分
……
## 结尾
- 回答开场问题的一两句话
## 明确舍弃
- 本集不展开的内容及原因"""

EPISODE_OUTLINE_PROTECTED_SUFFIX = """# 系统保护约束
所有事实、观点、人物和案例仅限当前声音框架与所属模块拆书稿，不得虚构、夸大或
超出模块范围。声音细纲不得读取或引用段落级原文块、来源匹配结果或上一集终稿。
解释必要背景并区分作者观点、书中案例和编辑解释。原文金句只在输入中确实存在且与
主题直接相关时引用；没有合适金句时直接总结，禁止编造。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的“约 1500 字”等旧要求。细纲必须控制内容负载，
每集只解决一个中心问题，不能因为原文中存在就纳入所有知识点。
只输出 Markdown 细纲正文，包含声音主题、开篇预告、分部分展开、部分间过渡、结尾和
明确舍弃项；不要输出分析过程。

{{book_type_rules}}"""

EPISODE_DRAFT_DEFAULT_TEMPLATE = """根据声音细纲和关联原文生成目标篇幅内的完整声音
初稿。以细纲决定范围和逻辑顺序，以原文提供事实、数据、案例细节和准确表述。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}

# 当前声音
标题：{{episode_title}}

# 目标字数
{{episode_word_count_range}}

# 上一步结果：声音细纲
{{episode_outline}}

# 当前声音关联原文
{{source_text}}

# 上一集终稿（如有）
{{previous_episode_final}}

# 口播方法
1. 开篇先建立一个具体问题，再用简短预告给听众认知地图；不要从宏大背景或作者介绍讲起。
2. 根据系统给出的书籍类型选择讲述路径：
   - 非叙事类：现实现象或生活困惑 → 常识预期 → 反差结果 → 概念解释 → 原书证据 → 现实意义；
   - 叙事类：人物处境 → 原文明示的目标或动机 → 行动 → 结果 → 关键剧情意味着什么。
3. 专业概念按“日常说法—准确含义—具体例子或场景—现实意义”展开；事实和解释分清。
4. 把原书案例、调查和数据写成有起因、变化、结果和意义的微型故事；数据后立即解释
   “这意味着什么”，不要连续堆叠年份、比例、定义和政策名称。
5. 多用短句、自然问答、对比和承接句，每段只推进一个主要意思；每 500 字推进
   1 至 2 个理解步骤，而不是增加新的中心观点。
6. 幽默来自事实反差、人物选择和结果落差，轻松但克制；不机械重复固定问候、口头禅、
   “想象一下”或网络梗。
7. 不反复写“作者认为”“书中提到”；仅在作者亲历、直接引用、需要区分归属或不标注
   会造成误解时提作者，同一篇称呼保持一致。
8. 原文中没有进入细纲的材料不能因为出现在输入里就扩写进正文；内容过多时删除次要
   论据，不能牺牲可理解性。
9. 结尾直接回答开场问题。只有前后集确实递进且输入提供可靠依据时，才自然回顾或预告。"""

EPISODE_DRAFT_PROTECTED_SUFFIX = """# 系统保护约束
所有事实、人物、数据、案例、定义、动机和因果只能来自声音细纲与关联原文。不得补充
外部新闻、历史背景、真实人物、现实案例或未经原文支持的常识推断。引用必须忠于原意。
输入没有案例时可以使用明确标注为“假设”的生活场景，但假设只能解释概念，不能成为新论据。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的任何旧篇幅要求。

针对 DeepSeek V4：不把原文目录逐项复述成文章；不用“第一、第二、第三、综上所述”
组织整篇；不连续堆叠数据、定义和政策名称；不换一种说法重复总结同一观点；不为了显得
深刻添加抽象升华；不把全部原文压缩成高密度清单。段落过长时主动拆分，每段只完成一个
推进动作。内容超出容量时删除次要论据，不得扩展细纲范围。

只输出连续、可逐字转成音频的口播正文，不要输出标题、小标题、序号、加粗、括号式
写作说明、分析过程或自检结果。

{{book_type_rules}}"""

EPISODE_FINAL_DEFAULT_TEMPLATE = """对声音初稿只做减法编辑和听觉优化，形成自然、清晰、
适合连续收听的中文口播终稿。保持初稿的选题、逻辑顺序、中心判断和事实范围。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}

# 当前声音
标题：{{episode_title}}

# 目标字数
{{episode_word_count_range}}

# 当前声音框架
{{episode_framework}}

# 上一步结果：声音初稿
{{episode_draft}}

# 当前声音关联原文
{{source_text}}

# 上一集终稿（如有）
{{previous_episode_final}}

# 编辑方法
1. 不重新选题，不改变初稿的逻辑顺序和中心判断，不借原文输入增加初稿没有展开的知识。
2. 删除重复解释、论文腔、清单腔、无意义套话和只是在重复结论的次要论据。
3. 拆分过长句子和段落，每段只完成一个推进动作；补足必要承接，但不新增事实。
4. 数据和概念之后保留面向未读听众的意义解释，删除连续定义、年份和比例堆叠。
5. 保留自然问答、对比、必要场景和有新逻辑作用的概念复现；不要机械复刻固定问候、
   口头禅、“想象一下”或网络梗。
6. 不反复标注作者。只有作者亲历、直接引用、需要区分归属或不标注会误解时保留称呼。
7. 结尾回答开场问题，不过度升华；仅在内容自然递进且有可靠输入时保留下一集悬念。"""

EPISODE_FINAL_PROTECTED_SUFFIX = """# 系统保护约束
只能优化初稿已经覆盖的内容，不得增加初稿未覆盖的新知识点、事实段落、外部新闻、
历史背景、现实案例或常识推断，不得虚构或夸大。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的任何旧篇幅要求。允许删除重复、合并同义论据、拆分长段，
但不得改变核心事实、因果和观点方向。

针对 DeepSeek V4：不按原文目录重写初稿；不用“第一、第二、第三、综上所述”重组全文；
不连续堆叠数据、定义和政策名称；不换一种说法重复总结；不添加抽象升华；不把正文压成
高密度清单。篇幅过长时优先删除次要证据和重复解释，篇幅不足时只能把初稿已有内容讲得
更清楚，不能引入新知识。

输出前在内部检查人物、数字、术语、定义、动机、事件顺序、因果、篇幅和段落衔接是否
与输入一致，但不得输出内部检查过程。只输出完整、连续、可逐字转成音频的声音终稿正文，
不要输出标题、小标题、序号、加粗、分析过程或修改说明。

{{book_type_rules}}"""


PROMPT_TEMPLATE_SPECS = {
    "mind_map": PromptTemplateSpec(
        stage_key="mind_map",
        label="思维导图",
        system_version="2026-07-30.2",
        system_prompt="你是讲书知识架构师，负责为未读听众建立清晰、准确、可理解的全书知识地图。",
        default_user_template=MIND_MAP_DEFAULT_TEMPLATE,
        protected_suffix=MIND_MAP_PROTECTED_SUFFIX,
        placeholders={
            "full_book_analysis": "完整或压缩后的全书拆书稿",
            "book_analysis": "旧版兼容：完整或压缩后的全书拆书稿",
            "book_title": "书名",
            "book_author": "作者",
            "book_type": "叙事类或非叙事类",
        },
        required_placeholders=(),
        required_one_of=(("full_book_analysis", "book_analysis"),),
    ),
    "album_module_plan": PromptTemplateSpec(
        stage_key="album_module_plan",
        label="全书知识模块设计",
        system_version="2026-07-30.2",
        system_prompt="你是讲书专辑架构师，负责把策划版全书拆书稿组织成覆盖完整、循序渐进的知识模块。",
        default_user_template=ALBUM_MODULE_PLAN_DEFAULT_TEMPLATE,
        protected_suffix=ALBUM_MODULE_PLAN_PROTECTED_SUFFIX,
        placeholders={
            "planning_book_analysis": "覆盖全部章节的策划版全书拆书稿",
            "book_analysis": "旧版兼容：策划版全书拆书稿",
            "chapter_catalog": "全书轻量章节目录",
            "book_title": "书名",
            "book_author": "作者",
            "book_type": "叙事类或非叙事类",
            "album_special_requirements": "用户填写的专辑特殊要求",
            "desired_episode_count": "目标集数与允许浮动范围",
            "episode_word_count_range": "项目配置的每集字数范围与计数口径",
        },
        required_placeholders=("chapter_catalog",),
        required_one_of=(("planning_book_analysis", "book_analysis"),),
    ),
    "album_outline": PromptTemplateSpec(
        stage_key="album_outline",
        label="分模块专辑大纲",
        system_version="2026-07-30.3",
        system_prompt="你是资深讲书专辑总编，负责把当前知识模块编排成准确、通俗、有选题吸引力和连续收听动力的声音目录。",
        default_user_template=ALBUM_DEFAULT_TEMPLATE,
        protected_suffix=ALBUM_PROTECTED_SUFFIX,
        placeholders={
            "book_analysis": "兼容旧版本：当前模块的精简拆书材料",
            "chapter_catalog": "全书轻量章节目录",
            "module_brief": "当前知识模块的目标、顺序和分配集数",
            "module_book_analysis": "当前模块关联章节的详细拆书稿",
            "module_source": "旧版兼容：当前模块详细拆书稿",
            "book_title": "书名",
            "book_author": "作者",
            "book_type": "叙事类或非叙事类",
            "album_special_requirements": "用户填写的专辑特殊要求",
            "desired_episode_count": "当前模块分配集数；未分配时说明由模型决定",
            "episode_word_count_range": "项目配置的每集字数范围与计数口径",
        },
        required_placeholders=(),
        required_one_of=(
            ("module_book_analysis", "module_source", "book_analysis"),
        ),
    ),
    "episode_outline": PromptTemplateSpec(
        stage_key="episode_outline",
        label="声音细纲",
        system_version="2026-07-30.3",
        system_prompt="你是专业的有声讲书制作人，负责把一集声音框架设计成准确、可讲述、能控制信息负载的内容路线。",
        default_user_template=EPISODE_OUTLINE_DEFAULT_TEMPLATE,
        protected_suffix=EPISODE_OUTLINE_PROTECTED_SUFFIX,
        placeholders={
            "episode_framework": "当前声音在专辑大纲中的内容框架",
            "module_book_analysis": "当前声音所属模块的详细拆书稿",
            "source_text": "旧版兼容：当前声音所属模块的详细拆书稿",
            "book_title": "书名",
            "book_author": "作者",
            "episode_title": "当前声音标题",
            "character_relationships": "当前关联原文块的人物关系；非故事类自动说明无需提供",
            "episode_word_count_range": "项目配置的每集字数范围与计数口径",
        },
        required_placeholders=("episode_framework",),
        required_one_of=(("module_book_analysis", "source_text"),),
    ),
    "episode_draft": PromptTemplateSpec(
        stage_key="episode_draft",
        label="声音初稿",
        system_version="2026-07-30.2",
        system_prompt="你是讲书口播稿作者，负责依据声音细纲和原文证据，为未读听众写出忠于原书、通俗而有故事感的完整初稿。",
        default_user_template=EPISODE_DRAFT_DEFAULT_TEMPLATE,
        protected_suffix=EPISODE_DRAFT_PROTECTED_SUFFIX,
        placeholders={
            "episode_outline": "上一步生成的声音细纲",
            "source_text": "当前声音关联的原文块与辅助上下文",
            "book_title": "书名",
            "book_author": "作者",
            "episode_title": "当前声音标题",
            "episode_framework": "当前声音在专辑大纲中的内容框架",
            "previous_episode_final": "上一集最新终稿；没有时自动说明",
            "episode_word_count_range": "项目配置的每集字数范围与计数口径",
        },
        required_placeholders=("episode_outline", "source_text"),
    ),
    "episode_final": PromptTemplateSpec(
        stage_key="episode_final",
        label="声音终稿",
        system_version="2026-07-30.2",
        system_prompt="你是讲书口播终审编辑，负责在不重新选题和不增加知识的前提下，把初稿调整成自然、清晰、适合听觉场景的终稿。",
        default_user_template=EPISODE_FINAL_DEFAULT_TEMPLATE,
        protected_suffix=EPISODE_FINAL_PROTECTED_SUFFIX,
        placeholders={
            "episode_draft": "上一步生成的声音初稿",
            "source_text": "当前声音关联的原文块与辅助上下文",
            "book_title": "书名",
            "book_author": "作者",
            "episode_title": "当前声音标题",
            "episode_framework": "当前声音在专辑大纲中的内容框架",
            "previous_episode_final": "上一集最新终稿；没有时自动说明",
            "episode_word_count_range": "项目配置的每集字数范围与计数口径",
        },
        required_placeholders=("episode_draft", "source_text"),
    ),
}


def validate_user_template(spec: PromptTemplateSpec, template: str) -> None:
    if not isinstance(template, str) or not template.strip():
        raise ValueError("提示词模板不能为空")
    if len(template) > MAX_TEMPLATE_CHARS:
        raise ValueError(f"提示词模板不能超过 {MAX_TEMPLATE_CHARS} 个字符")
    stripped = TOKEN_RE.sub("", template)
    if "{{" in stripped or "}}" in stripped:
        raise ValueError("提示词占位符花括号不完整")
    used = set(TOKEN_RE.findall(template))
    allowed = set(spec.placeholders)
    unknown = sorted(used - allowed)
    if unknown:
        raise ValueError(f"存在未知占位符：{', '.join(unknown)}")
    missing = sorted(set(spec.required_placeholders) - used)
    if missing:
        raise ValueError(f"缺少必要占位符：{', '.join(missing)}")
    missing_groups = [
        group for group in spec.required_one_of if not used.intersection(group)
    ]
    if missing_groups:
        choices = "；".join(" 或 ".join(group) for group in missing_groups)
        raise ValueError(f"缺少必要材料占位符：{choices}")


def render_user_template(
    spec: PromptTemplateSpec, template: str, values: dict[str, str]
) -> str:
    validate_user_template(spec, template)
    normalized = {key: str(values.get(key, "")) for key in spec.placeholders}
    return TOKEN_RE.sub(lambda match: normalized[match.group(1)], template)


def render_protected_suffix(template: str, values: dict[str, str]) -> str:
    normalized = {key: str(value) for key, value in values.items()}
    return TOKEN_RE.sub(
        lambda match: normalized.get(match.group(1), ""),
        template,
    )


def episode_book_type_rules(stage_key: str, book_type: str) -> str:
    if stage_key not in {
        "episode_outline",
        "episode_draft",
        "episode_final",
    }:
        return ""
    if book_type == "narrative":
        type_rules = (
            "本书为叙事类。围绕人物处境、原文明示的动机或目标、行动、结果和事件后果"
            "组织内容；人物动机、关系、事件顺序和因果必须来自输入，不得用常理补写。"
            "背景解释只保留理解关键剧情所必需的部分，讲解服务于剧情，不能把故事改写"
            "成观点清单。"
        )
    else:
        type_rules = (
            "本书为非叙事类。围绕问题、概念、机制、证据、数据含义和现实影响组织内容；"
            "优先把输入中的人物、调查、案例、变化和结果讲成微型故事。输入没有故事时，"
            "可使用明确标注为“假设”的生活场景帮助解释，但假设不能增加事实、真实人物、"
            "机构数据、未经原文支持的因果或新论据，不能把内容写成定义与观点清单。"
        )
    if stage_key == "episode_outline":
        continuity_rules = (
            "声音细纲没有上一集终稿输入，不得生成具体回顾措辞；只有当前声音框架已经"
            "明确前后集承接意图时，才标记需要承接的知识点。第一集不设计回顾。"
        )
    else:
        continuity_rules = (
            "只有输入中确实提供可用的上一集终稿，且当前内容与上一集存在直接递进时，"
            "才可用一句话回顾相关知识点；不得复述上一集摘要。下一集方向只有当前声音"
            "框架已经提供时才可预告，否则直接结束本集。"
        )
    return f"# 书籍类型与连续专辑约束\n{type_rules}\n{continuity_rules}"


def protected_suffix_for_runtime(
    stage_key: str,
    template: str,
    values: dict[str, str],
    book_type: str,
) -> str:
    if "{{book_type_rules}}" in template:
        return render_protected_suffix(
            template,
            {
                **values,
                "book_type_rules": episode_book_type_rules(
                    stage_key, book_type
                ),
            },
        )
    protected = render_protected_suffix(template, values)
    if stage_key == "episode_outline":
        protected += (
            "\n叙事类书籍必须把人物和剧情讲清楚，只能使用输入提及的人物与事件。"
            if book_type == "narrative"
            else "\n非叙事类书籍应重点梳理概念、观点、论据、案例和结论关系。"
        )
    return protected


class PromptConfigurationService:
    def __init__(self, database: Database):
        self.database = database
        self.ensure_defaults()

    @staticmethod
    def _template_id(stage_key: str) -> str:
        return f"prompt_template_{stage_key}"

    @staticmethod
    def _system_version_id(stage_key: str, system_version: str) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL, f"ai-book-studio:{stage_key}:{system_version}"
        ).hex

    def ensure_defaults(self) -> None:
        now = now_iso()
        for stage_key, spec in PROMPT_TEMPLATE_SPECS.items():
            template_id = self._template_id(stage_key)
            system_id = self._system_version_id(stage_key, spec.system_version)
            existing = self.database.row(
                "SELECT id FROM prompt_templates WHERE id = ?", (template_id,)
            )
            if not existing:
                self.database.execute(
                    """
                    INSERT INTO prompt_templates
                      (id, stage_key, label, allowed_placeholders_json,
                       required_placeholders_json, active_system_version_id,
                       active_global_version_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        template_id,
                        stage_key,
                        spec.label,
                        json.dumps(spec.placeholders, ensure_ascii=False),
                        json.dumps(spec.required_placeholders, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            system = self.database.row(
                "SELECT id FROM prompt_versions WHERE id = ?", (system_id,)
            )
            if not system:
                current = self.database.row(
                    """
                    SELECT COALESCE(MAX(version), 0) AS version
                    FROM prompt_versions
                    WHERE template_id = ? AND scope = 'system'
                    """,
                    (template_id,),
                )
                version = int(current["version"]) + 1 if current else 1
                self.database.execute(
                    """
                    INSERT INTO prompt_versions
                      (id, template_id, scope, project_id, version,
                       user_template, system_prompt, protected_suffix,
                       system_version, created_at)
                    VALUES (?, ?, 'system', NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        template_id,
                        version,
                        spec.default_user_template,
                        spec.system_prompt,
                        spec.protected_suffix,
                        spec.system_version,
                        now,
                    ),
                )
            self.database.execute(
                """
                UPDATE prompt_templates
                SET label = ?, allowed_placeholders_json = ?,
                    required_placeholders_json = ?,
                    active_system_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    spec.label,
                    json.dumps(spec.placeholders, ensure_ascii=False),
                    json.dumps(spec.required_placeholders, ensure_ascii=False),
                    system_id,
                    now,
                    template_id,
                ),
            )

    def _template(self, stage_key: str) -> dict[str, Any]:
        if stage_key not in PROMPT_TEMPLATE_SPECS:
            raise ValueError("提示词环节无效")
        template = self.database.row(
            "SELECT * FROM prompt_templates WHERE stage_key = ?", (stage_key,)
        )
        if not template:
            raise KeyError(stage_key)
        return template

    def _system_version(self, template: dict[str, Any]) -> dict[str, Any]:
        version = self.database.row(
            "SELECT * FROM prompt_versions WHERE id = ?",
            (template["active_system_version_id"],),
        )
        if not version:
            raise ValueError("系统提示词版本不存在")
        return version

    def effective(self, stage_key: str, project_id: str | None = None) -> dict[str, Any]:
        template = self._template(stage_key)
        if project_id and not self.database.row(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ):
            raise KeyError(project_id)
        system = self._system_version(template)
        selected: dict[str, Any] | None = None
        source_scope = "system"
        if project_id:
            binding = self.database.row(
                """
                SELECT active_version_id FROM prompt_bindings
                WHERE project_id = ? AND template_id = ?
                """,
                (project_id, template["id"]),
            )
            if binding:
                selected = self.database.row(
                    "SELECT * FROM prompt_versions WHERE id = ?",
                    (binding["active_version_id"],),
                )
                source_scope = "project"
        if selected is None and template.get("active_global_version_id"):
            selected = self.database.row(
                "SELECT * FROM prompt_versions WHERE id = ?",
                (template["active_global_version_id"],),
            )
            source_scope = "global"
        selected = selected or system
        if selected["template_id"] != template["id"]:
            raise ValueError("提示词活动版本与模板不匹配")
        return {
            "stage_key": stage_key,
            "label": template["label"],
            "source_scope": source_scope,
            "source_label": (
                "系统默认"
                if source_scope == "system"
                else f"{'全局' if source_scope == 'global' else '项目'} v{selected['version']}"
            ),
            "user_template": selected["user_template"],
            "prompt_version_id": selected["id"],
            "version": selected["version"],
            "system_version_id": system["id"],
            "system_version": system["system_version"],
            "system_prompt": system["system_prompt"],
            "protected_suffix": system["protected_suffix"],
            "allowed_placeholders": template["allowed_placeholders_json"],
            "required_placeholders": template["required_placeholders_json"],
            "required_placeholder_groups": [
                list(group)
                for group in PROMPT_TEMPLATE_SPECS[stage_key].required_one_of
            ],
            "has_project_override": source_scope == "project",
            "has_global_override": bool(template.get("active_global_version_id")),
        }

    def list_templates(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id and not self.database.row(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ):
            raise KeyError(project_id)
        return [
            self.effective(stage_key, project_id)
            for stage_key in CONFIGURABLE_PROMPT_STAGES
        ]

    def create_version(
        self,
        stage_key: str,
        scope: str,
        user_template: str,
        project_id: str | None = None,
        source_version_id: str | None = None,
    ) -> dict[str, Any]:
        if scope not in {"global", "project"}:
            raise ValueError("提示词作用范围无效")
        if scope == "project":
            if not project_id or not self.database.row(
                "SELECT id FROM projects WHERE id = ?", (project_id,)
            ):
                raise KeyError(project_id or "")
        else:
            project_id = None
        template = self._template(stage_key)
        spec = PROMPT_TEMPLATE_SPECS[stage_key]
        validate_user_template(spec, user_template)
        current = self.database.row(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM prompt_versions
            WHERE template_id = ? AND scope = ?
              AND ((project_id IS NULL AND ? IS NULL) OR project_id = ?)
            """,
            (template["id"], scope, project_id, project_id),
        )
        version = int(current["version"]) + 1 if current else 1
        version_id = uuid.uuid4().hex
        now = now_iso()
        self.database.execute(
            """
            INSERT INTO prompt_versions
              (id, template_id, scope, project_id, version, user_template,
               base_system_version_id, source_version_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                template["id"],
                scope,
                project_id,
                version,
                user_template,
                template["active_system_version_id"],
                source_version_id,
                now,
            ),
        )
        if scope == "global":
            self.database.execute(
                """
                UPDATE prompt_templates
                SET active_global_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (version_id, now, template["id"]),
            )
        else:
            self.database.execute(
                """
                INSERT INTO prompt_bindings
                  (project_id, template_id, active_version_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, template_id)
                DO UPDATE SET active_version_id = excluded.active_version_id,
                              updated_at = excluded.updated_at
                """,
                (project_id, template["id"], version_id, now),
            )
        return self.effective(stage_key, project_id)

    def history(
        self, stage_key: str, scope: str, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        template = self._template(stage_key)
        if scope not in {"global", "project"}:
            raise ValueError("提示词作用范围无效")
        if scope == "project" and not project_id:
            raise ValueError("项目级历史需要 project_id")
        if scope == "project" and not self.database.row(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ):
            raise KeyError(project_id)
        if scope == "global":
            project_id = None
        return self.database.rows(
            """
            SELECT id, scope, project_id, version, user_template,
                   source_version_id, created_at
            FROM prompt_versions
            WHERE template_id = ? AND scope = ?
              AND ((project_id IS NULL AND ? IS NULL) OR project_id = ?)
            ORDER BY version DESC
            """,
            (template["id"], scope, project_id, project_id),
        )

    def restore(
        self,
        stage_key: str,
        scope: str,
        version_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        template = self._template(stage_key)
        version = self.database.row(
            """
            SELECT * FROM prompt_versions
            WHERE id = ? AND template_id = ?
            """,
            (version_id, template["id"]),
        )
        if not version or version["scope"] != scope:
            raise ValueError("提示词历史版本与当前作用范围不匹配")
        if scope == "project" and version["project_id"] != project_id:
            raise ValueError("提示词历史版本不属于当前项目")
        return self.create_version(
            stage_key,
            scope,
            version["user_template"],
            project_id,
            source_version_id=version_id,
        )

    def reset_global(self, stage_key: str) -> dict[str, Any]:
        template = self._template(stage_key)
        self.database.execute(
            """
            UPDATE prompt_templates
            SET active_global_version_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), template["id"]),
        )
        return self.effective(stage_key)

    def clear_project(self, stage_key: str, project_id: str) -> dict[str, Any]:
        template = self._template(stage_key)
        if not self.database.row(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ):
            raise KeyError(project_id)
        self.database.execute(
            "DELETE FROM prompt_bindings WHERE project_id = ? AND template_id = ?",
            (project_id, template["id"]),
        )
        return self.effective(stage_key, project_id)

    def lock_stage(
        self, stage_key: str, project_id: str | None = None
    ) -> dict[str, str]:
        effective = self.effective(stage_key, project_id)
        return {
            "prompt_version_id": effective["prompt_version_id"],
            "system_version_id": effective["system_version_id"],
        }

    def lock_episode_stages(self, project_id: str) -> dict[str, dict[str, str]]:
        return {
            stage: self.lock_stage(f"episode_{stage}", project_id)
            for stage in ("outline", "draft", "final")
        }

    def snapshot(
        self,
        stage_key: str,
        values: dict[str, str],
        *,
        project_id: str | None = None,
        locked: dict[str, str] | None = None,
        prompt_id: str | None = None,
        book_type: str = "non_narrative",
    ) -> PromptSnapshot:
        template = self._template(stage_key)
        if locked:
            selected = self.database.row(
                "SELECT * FROM prompt_versions WHERE id = ?",
                (locked.get("prompt_version_id"),),
            )
            system = self.database.row(
                "SELECT * FROM prompt_versions WHERE id = ?",
                (locked.get("system_version_id"),),
            )
            if (
                not selected
                or not system
                or selected["template_id"] != template["id"]
                or system["template_id"] != template["id"]
                or system["scope"] != "system"
                or (
                    selected["scope"] == "project"
                    and selected["project_id"] != project_id
                )
            ):
                raise ValueError("锁定的提示词版本不存在")
            source_scope = selected["scope"]
            source_label = (
                "系统默认"
                if source_scope == "system"
                else f"{'全局' if source_scope == 'global' else '项目'} v{selected['version']}"
            )
            effective = {
                "user_template": selected["user_template"],
                "prompt_version_id": selected["id"],
                "system_version_id": system["id"],
                "system_version": system["system_version"],
                "system_prompt": system["system_prompt"],
                "protected_suffix": system["protected_suffix"],
                "source_scope": source_scope,
                "source_label": source_label,
                "version": selected["version"],
            }
        else:
            effective = self.effective(stage_key, project_id)
        spec = PROMPT_TEMPLATE_SPECS[stage_key]
        runtime_template = effective["user_template"]
        if stage_key == "album_outline":
            used = set(TOKEN_RE.findall(runtime_template))
            if not any(
                used.intersection(group) for group in spec.required_one_of
            ):
                runtime_template += (
                    "\n\n# 当前模块详细拆书稿（系统兼容旧版本补入）\n"
                    "{{module_book_analysis}}"
                )
        rendered = render_user_template(spec, runtime_template, values)
        protected = protected_suffix_for_runtime(
            stage_key,
            effective["protected_suffix"],
            values,
            book_type,
        )
        source = f"{rendered.strip()}\n\n{protected.strip()}"
        version_label = (
            f"{effective['system_version']} · {effective['source_label']}"
        )
        return PromptSnapshot(
            stage_key=stage_key,
            prompt_id=prompt_id or stage_key,
            source=source,
            prompt_version_id=effective["prompt_version_id"],
            system_version_id=effective["system_version_id"],
            version_label=version_label,
            source_scope=effective["source_scope"],
            source_label=effective["source_label"],
            user_template=effective["user_template"],
            system_prompt=effective["system_prompt"],
            protected_suffix=protected,
        )

    def preview(
        self,
        stage_key: str,
        user_template: str,
        values: dict[str, str],
        *,
        project_id: str | None = None,
        book_type: str = "non_narrative",
    ) -> dict[str, Any]:
        effective = self.effective(stage_key, project_id)
        spec = PROMPT_TEMPLATE_SPECS[stage_key]
        rendered = render_user_template(spec, user_template, values)
        protected = protected_suffix_for_runtime(
            stage_key,
            effective["protected_suffix"],
            values,
            book_type,
        )
        return {
            "rendered_user_template": rendered,
            "protected_suffix": protected,
            "source_label": effective["source_label"],
            "truncated": any(
                len(str(value)) > 6_000 for value in values.values()
            ),
        }
