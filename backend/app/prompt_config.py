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


MIND_MAP_DEFAULT_TEMPLATE = """请根据全书拆书稿，为此前没有读过原书的听众设计一份
循序渐进、通俗易懂的 Markdown 思维导图。

# 书籍信息
书名：{{book_title}}
作者：{{book_author}}
书籍类型：{{book_type}}

# 全书拆书稿
{{full_book_analysis}}

# 设计方法
1. 先识别全书的核心问题和内容性质，再按金字塔结构组织知识。
2. 建立章节之间的因果、递进、并列或对照关系，避免按目录机械摘抄。
3. 第二层分支标明来源一级章节，优先使用原书中的表述。
4. 末梢可以增加用户视角的总结句，但不得编造原书没有的事实。"""

MIND_MAP_PROTECTED_SUFFIX = """# 系统保护约束
所有知识、事实、人物、案例和数据只能来自输入拆书稿。只输出完整 Markdown 思维导图，
不得输出分析过程、JSON、Mermaid、数据库 ID 或段落索引。"""

ALBUM_MODULE_PLAN_DEFAULT_TEMPLATE = """请根据覆盖全书的策划版拆书稿和章节目录，
为未读过原书的听众设计完整、连续的知识模块。

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
1. 覆盖全部一级章节，按听众理解顺序组织模块。
2. 每个模块解决一个清楚的听众问题，并说明与前后模块的认知关系。
3. 结合目标集数控制模块数量，必要时合并相邻且高度相关的章节。
4. 此处只设计知识模块，不展开每一集。"""

ALBUM_MODULE_PLAN_PROTECTED_SUFFIX = """# 系统保护约束
不得遗漏或编造 CHAPTER 标识，不得增加输入材料之外的事实。只输出以下 Markdown 结构，
不要输出 JSON、逐集大纲、数据库 ID、知识资产 ID 或段落索引：

## 模块N：模块标题
听众问题：这个模块为未读听众解决什么问题？
认知顺序：如何承接前后模块。
来源章节：[CHAPTER_001]、[CHAPTER_002]
建议声音数：数字"""

ALBUM_DEFAULT_TEMPLATE = """请面向此前没有读过原书、主要通过连续收听理解本书的听众，
根据当前知识模块和来源章节设计连续的声音目录。

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
1. 识别当前模块的核心问题、关键背景、主要机制或事件线和最终启示。
2. 按“建立背景或发现异常—提出问题—解释原因与机制—展开影响—回到现实或总结出路”安排认知路线。
3. 每集只解决一个听众能够复述的中心问题，通常安排 2 至 4 个递进要点。
4. 根据每集目标字数控制内容负载；一个独立主题能单独成集时，不要塞进“全景概述”。
5. 标题从具体人物、事件、矛盾、反常识现象或因果悬念切入，轻松但克制。
6. 用清楚的因果链保留原书精华，不机械地一章一集，不重复观点凑集数。

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
1. 明确本集唯一中心问题和听众最终应能复述的判断。
2. 根据目标字数安排 2 至 4 个递进模块，并标注每个模块的大致字数预算。
3. 每个模块只承担一个主要认知任务，超出容量的知识点明确舍弃。
4. 从具体困惑、反常识现象、生活场景或关键冲突开场，先建立问题，再解释概念。
5. 开篇、过渡和结尾写成可以直接进入正文的完整句子。"""

EPISODE_OUTLINE_PROTECTED_SUFFIX = """# 系统保护约束
所有事实、观点、人物和案例仅限当前声音框架与所属模块拆书稿，不得虚构、夸大或
超出模块范围。声音细纲不得读取或引用段落级原文块、来源匹配结果或上一集终稿。
解释必要背景并区分作者观点、书中案例和编辑解释。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的“约 1500 字”等旧要求。细纲必须控制内容负载，
每集只解决一个中心问题，不能因为原文中存在就纳入所有知识点。
只输出 Markdown 细纲正文，包含声音主题、开篇预告、分部分展开、部分间过渡和一句话结尾；不要输出分析过程。"""

EPISODE_DRAFT_DEFAULT_TEMPLATE = """根据声音细纲和关联原文生成目标篇幅内的声音初稿。以细纲为结构，以原文为事实边界，把观点、论据和案例讲清楚。

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
1. 从具体困惑、反常识现象、生活场景或关键冲突开场，先让听众知道本集要破解什么。
2. 专业概念按“日常说法—准确含义—具体例子—现实意义”展开。
3. 数据后立即解释“这意味着什么”，不要连续堆叠年份、比例和政策名称。
4. 多用短句和自然问答，每段只推进一个主要意思。
5. 可以使用一个贯穿全文的解释性比喻，但不得把比喻写成事实。
6. 避免长篇使用“第一个、第二个、第三个”清单；必要枚举拆成可听懂的小段。
7. 结尾回答开头问题，不机械重复固定问候、口头禅或网络梗。"""

EPISODE_DRAFT_PROTECTED_SUFFIX = """# 系统保护约束
不得虚构或扩展原文没有的信息；引用必须忠于原意。只输出可以继续进入口语化调整的初稿正文，不要输出分析过程或写作说明。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的任何旧篇幅要求。只输出连续口播正文。"""

EPISODE_FINAL_DEFAULT_TEMPLATE = """把声音初稿调整为自然、清晰、适合听觉场景的中文口播稿。优化结构、节奏、衔接和表达，但保持初稿的事实范围。

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
1. 删除重复解释、论文腔和无意义套话；合并只是在重复同一结论的论据。
2. 拆分过长段落和长句，让每段只推进一个主要意思。
3. 数据和概念之后紧跟面向未读听众的意义解释。
4. 保留自然问答、对比和必要场景，避免连续定义与清单式枚举。
5. 结尾回扣开场问题；不要机械复刻固定问候、口头禅或网络梗。"""

EPISODE_FINAL_PROTECTED_SUFFIX = """# 系统保护约束
只能优化初稿已经覆盖的内容，不得借原文证据增加初稿未覆盖的新事实段落，不得虚构或夸大。只输出完整声音终稿正文，不要输出分析过程或修改说明。
本项目目标篇幅是：{{episode_word_count_range}}
该范围优先于用户模板中的任何旧篇幅要求。允许删除重复、合并同义论据、拆分长段，
但不得改变核心事实、因果和观点方向。"""


PROMPT_TEMPLATE_SPECS = {
    "mind_map": PromptTemplateSpec(
        stage_key="mind_map",
        label="思维导图",
        system_version="2026-07-30.1",
        system_prompt="你是讲书类内容创作者，擅长用金字塔结构建立清晰、准确的书籍知识地图。",
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
        system_version="2026-07-30.1",
        system_prompt="你负责把一本书的策划版拆书稿组织成循序渐进、覆盖完整的讲书知识模块。",
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
        system_version="2026-07-30.2",
        system_prompt="你是一位资深讲书专辑总编，负责把拆书稿编排成准确、通俗、有连续收听动力的有声专辑。",
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
        system_version="2026-07-30.2",
        system_prompt="你是专业的有声讲书专辑制作人，擅长把人物、剧情或复杂观点讲得清晰、准确、易懂。",
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
        system_version="2026-07-30.1",
        system_prompt="你是讲书口播稿作者，负责依据声音细纲和原文证据写出忠于原书的初稿。",
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
        system_version="2026-07-30.1",
        system_prompt="你负责把讲书初稿调整成自然、清晰、适合听觉场景的中文口播终稿。",
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
        protected = render_protected_suffix(
            effective["protected_suffix"], values
        )
        if stage_key == "episode_outline":
            protected += (
                "\n叙事类书籍必须把人物和剧情讲清楚，只能使用输入提及的人物与事件。"
                if book_type == "narrative"
                else "\n非叙事类书籍应重点梳理概念、观点、论据、案例和结论关系。"
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
        protected = render_protected_suffix(
            effective["protected_suffix"], values
        )
        if stage_key == "episode_outline":
            protected += (
                "\n叙事类书籍必须把人物和剧情讲清楚，只能使用输入提及的人物与事件。"
                if book_type == "narrative"
                else "\n非叙事类书籍应重点梳理概念、观点、论据、案例和结论关系。"
            )
        return {
            "rendered_user_template": rendered,
            "protected_suffix": protected,
            "source_label": effective["source_label"],
            "truncated": any(
                len(str(value)) > 6_000 for value in values.values()
            ),
        }
