# 单条声音上下文链实施计划

日期：2026-07-27

## 目标

落实已批准的单条声音上下文链规格：书籍区分叙事类与非叙事类，专辑大纲保存可审核的声音内容框架，叙事类拆书按原文块提取人物关系，并确保细纲、初稿、终稿都带上正确的上一步产物与同一组完整原文块。

## 实施顺序

### 任务 1：数据库迁移与领域字段

文件：

- `backend/app/db.py`
- `backend/tests/test_batches.py`

步骤：

1. 为新数据库的 `books` 增加 `book_type`，默认 `non_narrative`。
2. 为新数据库的 `episodes` 增加 `content_framework`，默认空字符串。
3. 为 `artifact_versions` 增加 `input_snapshot`，保存每次模型调用的结构化输入。
4. 将三列加入幂等迁移映射，确保现有 SQLite 可直接升级。
5. 扩展旧数据库迁移测试，验证三张表的新列和默认值。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_batches.py -q
```

### 任务 2：提示词与阶段上下文组装器

文件：

- `backend/app/prompts.py`
- `backend/app/contexts.py`
- `backend/app/providers.py`
- `backend/tests/test_contexts.py`

步骤：

1. 新增叙事类声音细纲模板，迁移用户提供的角色、任务、结构、事实边界和输出格式。
2. 新增非叙事类声音细纲模板，移除人物关系与剧情限定。
3. 更新声音初稿和终稿模板，明确分隔“上一步产物”和“原文证据”。
4. 新增人物关系提取提示词，要求 JSON 输出且只依据当前原文块。
5. 新增 `EpisodeContextBuilder`：
   - 查询声音、项目、书籍和来源块；
   - 校验来源块属于项目书籍；
   - 按原书顺序拼接完整原文；
   - 细纲读取声音框架和当前块人物关系；
   - 初稿读取最新细纲；
   - 终稿读取最新初稿；
   - 返回提示词 ID 与完整输入快照。
6. 更新演示供应商，使新提示词 ID 仍返回确定性内容和合法人物关系 JSON。
7. 新增捕获输入的单元测试，验证叙事/非叙事分支、关系过滤、完整原文和缺失上一步错误。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_contexts.py backend/tests/test_providers.py -q
```

### 任务 3：叙事类人物关系拆书

文件：

- `backend/app/workflows.py`
- `backend/app/main.py`
- `backend/tests/test_workflows.py`

步骤：

1. 将 `WorkflowService.analyze_book` 改为异步方法。
2. 保留现有观点、案例、金句和思维导图逻辑。
3. 仅对叙事类书籍按确认原文块调用人物关系提示词。
4. 使用最多五个并发模型请求，结果返回后再写数据库。
5. 由服务端把当前原文块 ID 写入 `source_section_ids`，不让模型生成 ID。
6. 解析 JSON，关系为空视为成功且不创建空资产。
7. 使用 `workflow_runs` 按原文块记录人物关系提取成功或失败；重跑时只处理未成功块。
8. 成功资产按原文块幂等替换；部分失败时保留成功资产并将书籍标记为 `analysis_partial_failed`。
9. API 端点改为异步等待服务，返回成功数和失败原文块。
10. 扩展测试覆盖非叙事类不调用模型、叙事类关联 ID、空关系和部分失败。

验证：

```bash
.venv/bin/python -m pytest backend/tests/test_workflows.py -q
```

### 任务 4：专辑大纲、确认校验与生成链

文件：

- `backend/app/main.py`
- `backend/app/workflows.py`
- `backend/app/obsidian.py`
- `backend/tests/test_workflows.py`
- `backend/tests/test_batches.py`

步骤：

1. 上传接口接收并校验 `book_type`。
2. 增加书籍类型更新端点；类型变化时清理需重新生成的知识资产并回到待拆书状态。
3. `EpisodeUpdate` 增加 `content_framework`，大纲保存时一同写回。
4. 创建项目时为每条声音生成非空的初始内容框架，供用户审核修改。
5. 确认项目时校验框架非空、来源非空、来源属于项目书籍。
6. `generate_episode` 在每个阶段调用上下文组装器，不再沿用单个 `previous` 字符串。
7. 保存产物时同时保存该版本的 `input_snapshot`。
8. 保持从指定阶段重跑、批次失败隔离和最多五条并行规则。
9. Obsidian 专辑大纲文件写出声音内容框架。
10. 更新工作流与批次测试种子，使其包含有效框架和来源。

验证：

```bash
.venv/bin/python -m pytest backend/tests -q
```

### 任务 5：前端审核界面

文件：

- `app/page.tsx`
- `app/globals.css`
- `tests/rendered-html.test.mjs`

步骤：

1. 为 `Book` 增加 `book_type`，为 `Episode` 增加 `content_framework`。
2. 上传表单增加“叙事类 / 非叙事类”选择。
3. 书籍卡片和书籍工作台显示书籍类型。
4. 书籍工作台提供类型修改操作，并明确修改后需要重新拆书。
5. 专辑大纲编辑器增加多行声音内容框架。
6. 保存前校验标题、框架和来源，错误定位到具体声音。
7. 更新辅助文案，说明确认后每个阶段都会重新带入关联原文。
8. 增加 CSS，保持 Notion 风格的紧凑多行编辑体验。
9. 扩展前端结构测试。

验证：

```bash
npm run lint
npm test
```

### 任务 6：全量验收与交付

文件：

- `README.md`
- `task_plan.md`
- `progress.md`
- `findings.md`

步骤：

1. 运行后端全量测试、前端 lint、前端测试和生产构建。
2. 用非叙事类测试数据确认声音细纲没有人物关系区。
3. 用短叙事测试数据确认人物关系只进入匹配原文块的细纲。
4. 捕获细纲、初稿和终稿输入，确认三阶段都包含同一组完整原文。
5. 在本地服务回归设置、书籍列表和项目接口。
6. 更新 README 中的书籍类型、声音框架和阶段输入说明。
7. 检查 `.env`、原书、SQLite 数据和输入快照未进入 Git。
8. 提交实现并保留本地预览。

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

- 叙事类和非叙事类拥有清晰且可测试的提示词分支。
- 专辑大纲确认前必须审核每条声音的多行内容框架。
- 细纲输入为声音框架、匹配人物关系和原文块。
- 初稿输入为最新细纲和同一原文块。
- 终稿输入为最新初稿和同一原文块。
- 任意阶段重跑都重新按稳定 ID 读取当前来源。
- 旧数据库无损升级，旧模型产物和人工版本继续可回看。
- 批次并发、失败隔离、Obsidian 同步和 DeepGate 配置保持正常。
