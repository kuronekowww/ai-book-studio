# 逐章模型拆书与专辑生成实施计划

日期：2026-07-27

## 目标

落实已批准的逐章拆书规格：修复 EPUB 零输入误完成，按一级章节最多五章并行调用模型，版本化保存完整拆书稿和知识卡片；在全书拆书稿过长时逐章压缩，并并行生成 Markdown 思维导图与可审核专辑大纲；同时修复上传文件选择后缺少状态反馈。

## 实施顺序

### 任务 1：数据库迁移与章节候选规则

文件：

- `backend/app/db.py`
- `backend/app/ingestion.py`
- `backend/tests/test_ingestion.py`
- `backend/tests/test_batches.py`

步骤：

1. 为 `sections` 增加 `analysis_enabled` 和 `analysis_exclusion_reason`。
2. 新增版本化 `chapter_analyses` 表，保存结构化结果、Markdown、压缩结果、模型与输入快照。
3. 为 `knowledge_items` 增加章节分析版本追溯字段和模型来源标记。
4. 为 `projects` 增加专辑特殊要求、期望集数和集数差异提示。
5. 为既有 SQLite 提供幂等列迁移和建表。
6. 实现一级章节自动纳入/排除规则，并允许保存人工覆盖。
7. 增加一至三级 EPUB、目录、版权和表格注释的候选测试。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_ingestion.py backend/tests/test_batches.py -q
```

### 任务 2：逐章提示词、结构解析与 Markdown 渲染

文件：

- `backend/app/prompts.py`
- `backend/app/providers.py`
- `backend/app/chapter_analysis.py`
- `backend/tests/test_chapter_analysis.py`

步骤：

1. 迁移用户提供的非叙事逐章拆书提示词。
2. 定义严格 JSON 契约，保留章节主题、子主题、概念、金句、观点、论据和案例。
3. 实现稳定 `content_index` 生成与章节聚合器。
4. 校验模型只引用当前章节允许的索引。
5. 将结构化结果确定性渲染成目标 Markdown。
6. 从结构化结果派生五类知识卡片。
7. 扩展演示供应商，返回可重复的合法章节 JSON。
8. 增加聚合顺序、非法索引、Markdown 和知识卡片测试。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_chapter_analysis.py backend/tests/test_providers.py -q
```

### 任务 3：章节批次、并发、版本与单章重跑

文件：

- `backend/app/workflows.py`
- `backend/app/main.py`
- `backend/tests/test_workflows.py`
- `backend/tests/test_chapter_analysis.py`

步骤：

1. 将非叙事类拆书切换到章节分析服务。
2. 创建书籍父运行和章节子运行，最多五章并行。
3. 成功章节在单事务中写入章节版本、Markdown 和知识卡片。
4. 失败章节隔离并记录可读错误，其他章节继续。
5. 增加单章重跑接口；新结果创建新版本。
6. 零候选、零成功或部分失败时禁止书籍标记完成。
7. 将《当代中国社会分层》的空知识错误状态迁回待拆书。
8. 增加并发上限、部分失败、重跑、幂等与状态测试。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_workflows.py backend/tests/test_chapter_analysis.py -q
```

### 任务 4：全书汇总、逐章压缩、思维导图与专辑大纲

文件：

- `backend/app/prompts.py`
- `backend/app/workflows.py`
- `backend/app/main.py`
- `backend/tests/test_workflows.py`

步骤：

1. 按章节顺序汇总最新成功的完整拆书稿。
2. 增加可配置输入阈值，超限时逐章压缩并按章节版本缓存。
3. 校验压缩结果完整保留允许的 `content_index`。
4. 迁移用户提供的思维导图与专辑大纲提示词。
5. 为项目保存可选特殊要求和期望集数。
6. 使用同一份完整或压缩事实输入，并行生成思维导图和专辑大纲。
7. 校验专辑 JSON、内容类型、来源索引、重复拆分和过渡声音。
8. 将 `main_points` 写入 `content_framework`，确定性映射原文块。
9. 一项失败时保存另一项成功结果，并支持独立重跑。
10. 增加未填写集数、填写集数、差异提示、可选过渡和非法索引测试。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_workflows.py -q
```

### 任务 5：前端上传、章节审核和下游生成界面

文件：

- `app/page.tsx`
- `app/globals.css`
- `tests/rendered-html.test.mjs`

步骤：

1. 把上传文件改为受控状态，选择后显示文件名、类型、大小和勾选状态。
2. 增加重新选择、移除、未选择禁用和失败保留。
3. 章节审核页展示一至三级目录树、一级纳入开关和排除原因。
4. 展示章节批次总数、成功、运行、失败和排除数量。
5. 增加章节完整拆书稿、版本、知识卡片和失败单章重跑入口。
6. 增加专辑特殊要求多行输入和可选期望集数。
7. 分别展示压缩、思维导图和专辑大纲状态。
8. 展示期望集数与实际集数的非阻断差异提示。
9. 扩展前端结构与渲染测试。

验证：

```bash
npm run lint
npm test
npm run build
```

### 任务 6：迁移验收、真实样书与交付

文件：

- `README.md`
- `task_plan.md`
- `progress.md`
- `findings.md`

步骤：

1. 运行后端全量测试、前端 lint、测试和生产构建。
2. 验证既有 SQLite 幂等升级和空知识误完成状态修复。
3. 用合成 EPUB 验证一至三级章节和垃圾一级标题排除。
4. 用《当代中国社会分层》验证章节候选、一级聚合和至少一个真实章节 DeepGate 调用。
5. 验证完整或压缩拆书稿到思维导图、专辑大纲的输入链。
6. 验证上传 EPUB、TXT、Markdown 后即时显示选择状态。
7. 更新 README。
8. 确认 `.env`、原书、SQLite、模型输入快照和密钥未进入 Git。
9. 提交实现并保持本地应用可启动。

验证：

```bash
.venv/bin/python -m pytest backend/tests -q
npm run lint
npm test
npm run build
git diff --check
git status --short
```

## 完成标准

- EPUB 一至三级目录能够正确审核和按一级章节逐章拆书。
- 章节模型调用最多五个并行，失败隔离并支持单章重跑。
- 完整章节拆书稿、Markdown、知识卡片、提示词和输入均可版本追溯。
- 零章节或部分失败不会再误标为拆书完成。
- 超长全书拆书稿逐章压缩且稳定索引不变。
- 思维导图和专辑大纲共享事实输入并独立保存。
- 专辑集数和特殊要求可选，过渡声音不强制。
- 文件选择后界面立即显示明确状态。
- 既有声音生产、人工版本、Obsidian 同步和 DeepGate 配置保持兼容。
