# 轻量分层专辑规划实施计划

## 目标

把当前“完整拆书稿一次输入、一次输出严格 JSON”的专辑大纲流程，改为可恢复的
五阶段任务：

1. 程序生成轻量一级章节目录；
2. 模型设计全书知识模块；
3. 每个模块独立生成 Markdown 专辑大纲；
4. 小输入模型把合并后的 Markdown 转为页面数据；
5. 后端校验一级章节标识并保存。

专辑审核阶段只确定“每集讲什么、来自哪些一级章节”。概念、观点、论据、案例、
金句及段落级原文块，在单条声音生成细纲前通过 `match_episode_sources` 独立匹配。

实施过程中不得改写已确认或已经进入声音生产的历史项目。

## 任务 1：建立专辑规划产物表和仓储服务

### 涉及文件

- 修改：`backend/app/db.py`
- 新增：`backend/app/album_planning.py`
- 新增：`backend/tests/test_album_planning.py`

### 实施步骤

1. 先增加失败测试，覆盖：
   - 旧数据库启动时可以补建新表和索引；
   - 同一个 `run_id + artifact_type + module_key` 重复写入时更新原记录；
   - 不同运行之间的产物互不复用；
   - 产物按 `position` 稳定排序；
   - 失败产物保留错误信息，成功重跑后可以覆盖为成功。
2. 在数据库初始化和兼容迁移中创建 `album_planning_artifacts`：
   - `id`
   - `run_id`
   - `project_id`
   - `artifact_type`
   - `module_key`
   - `position`
   - `source_chapter_ids_json`
   - `content`
   - `status`
   - `error_message`
   - `created_at`
   - `updated_at`
3. 建立唯一索引：
   - `(run_id, artifact_type, module_key)`
4. 建立查询索引：
   - `(project_id, created_at)`
   - `(run_id, position)`
5. 在 `album_planning.py` 中实现小型仓储类：
   - `upsert(...)`
   - `get(...)`
   - `list_for_run(...)`
   - `list_modules(...)`
   - `mark_failed(...)`
   - `completed_module_keys(...)`
6. JSON 字段只在仓储边界编码和解码，业务层始终使用 `list[str]`。
7. 产物只允许在同一个运行中恢复；新建项目生成任务时不得跨运行复用旧模块，
   防止提示词、模型或拆书版本改变后误用历史内容。

### 验证

```bash
cd backend
../.venv/bin/pytest tests/test_album_planning.py -q
```

## 任务 2：生成稳定章节目录和模块输入

### 涉及文件

- 修改：`backend/app/album_planning.py`
- 修改：`backend/app/chapter_analysis.py`
- 修改：`backend/app/workflows.py`
- 修改：`backend/tests/test_album_planning.py`
- 修改：`backend/tests/test_chapter_analysis.py`

### 实施步骤

1. 先增加失败测试，构造 25 个一级章节和多级子标题，验证：
   - 所有一级章节都进入目录，顺序与原书一致；
   - 稳定键为 `CHAPTER_001`、`CHAPTER_002` 等；
   - 每个键可以准确映射回当前书籍的一级 `section_id`；
   - 目录不包含 `knowledge_`、`content_`、完整论据、案例证据或原文正文；
   - 空章节分析、部分失败章节和不属于当前书籍的章节不会被错误纳入；
   - 超过输入上限时继续拆成相邻章节模块，不静默截断或丢章。
2. 从当前书籍最新成功的逐章拆书结果生成 `ChapterPlanningEntry`：
   - `chapter_key`
   - `section_id`
   - `title`
   - `theme`
   - `subtopic_titles`
   - `concise_points`
   - `position`
3. 章节键按当前原书一级章节的持久顺序生成，并把“键到真实一级章节 ID”的映射
   写入本次运行的 `chapter_catalog` 产物。模型只看到键和可读内容，不看到数据库 ID。
4. 全书模块规划输入默认只含标题、章节主题和子主题；仅在章节主题相近、无法区分时
   补充精简观点。
5. 模块展开输入只包含该模块关联章节的：
   - 章节标题和主题；
   - 子主题；
   - 精简观点、概念和案例概述。
6. 模块展开输入目标上限设为 12,000 字符：
   - 优先按相邻一级章节拆分；
   - 单章仍超限时按连续子主题拆成子模块；
   - 每个子模块保留原一级章节键；
   - 任何拆分都必须通过“输入章节集合等于输出模块章节集合”校验。
7. 保留现有完整拆书稿生成思维导图的逻辑，本任务只替换专辑大纲输入路径。

### 验证

```bash
cd backend
../.venv/bin/pytest tests/test_album_planning.py tests/test_chapter_analysis.py -q
```

## 任务 3：新增 Markdown 创作和内部结构化提示词

### 涉及文件

- 修改：`backend/app/prompts.py`
- 修改：`backend/app/prompt_config.py`
- 修改：`backend/app/providers.py`
- 修改：`backend/tests/test_prompt_config.py`
- 修改：`backend/tests/test_prompt_api.py`
- 修改：`backend/tests/test_providers.py`

### 实施步骤

1. 先增加失败测试，验证：
   - 用户可配置的“专辑大纲”提示词要求输出 Markdown，不再要求严格 JSON；
   - 创作提示词包含章节目录、模块目标、特殊要求和期望集数占位符；
   - 受保护后缀禁止输出知识资产 ID、段落索引和完整稿件；
   - 内部结构化提示词的输入只有已完成 Markdown 和合法章节键目录；
   - 内部来源匹配提示词只返回候选知识资产 ID；
   - 三个内部提示词不会出现在“提示词”标签页；
   - 旧的全局和项目级用户提示词版本仍能查看，系统默认版本升级后可继续覆盖。
2. 将可配置 `album_outline` 用户模板调整为 Markdown 创作模板，继续支持：
   - `book_title`
   - `book_author`
   - `book_type`
   - `album_special_requirements`
   - `desired_episode_count`
   - `chapter_catalog`
   - `module_brief`
   - `module_source`
3. 受保护系统要求固定单集格式：

   ```markdown
   ## 第1集：声音标题
   听众钩子：……
   核心主题：……
   核心要点：
   1. ……
   2. ……
   内容类型：解读
   来源章节：[CHAPTER_002]、[CHAPTER_003]
   ```

4. 新增程序内部提示词：
   - `album_module_plan`：根据轻量目录设计模块；
   - `album_outline_structure`：把合并 Markdown 转成简单 JSON；
   - `episode_source_match`：从限定候选目录选择知识资产。
5. 内部结构化 JSON 仅允许：
   - `title`
   - `main_points`
   - `chapter_keys`
   - `content_type`
6. 为受保护提示词增加系统版本号，把版本号写入运行元数据；用户不能编辑或删除
   内部协议字段。
7. 更新演示模型：
   - 模块规划返回确定性 Markdown；
   - 模块展开返回含真实章节键的 Markdown；
   - 结构化阶段返回简单 JSON；
   - 来源匹配阶段从候选资产中返回确定性 ID。
8. 保持 Kimi K3 作为专辑大纲项目默认模型。模块规划、模块展开和结构化调用都使用
   任务启动时锁定的专辑大纲模型快照，避免任务中途切换模型。

### 验证

```bash
cd backend
../.venv/bin/pytest \
  tests/test_prompt_config.py \
  tests/test_prompt_api.py \
  tests/test_providers.py -q
```

## 任务 4：实现分层专辑规划编排和模块级恢复

### 涉及文件

- 修改：`backend/app/album_planning.py`
- 修改：`backend/app/workflows.py`
- 修改：`backend/app/runs.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_album_planning.py`
- 修改：`backend/tests/test_workflows.py`
- 修改：`backend/tests/test_runs.py`
- 修改：`backend/tests/test_run_api.py`

### 实施步骤

1. 先增加失败测试，覆盖完整状态机：
   - `prepare_chapter_catalog`
   - `design_album_modules`
   - `expand_album_modules`
   - `structure_album_outline`
   - `save_project_outline`
2. 把 `generate_project_knowledge_outputs` 中原有专辑“一次大调用”拆到
   `AlbumPlanningService`，思维导图仍可与专辑任务独立执行。
3. `prepare_chapter_catalog`：
   - 生成章节目录和键映射；
   - 持久化 `chapter_catalog` 产物；
   - 目录为空时在本阶段给出“请先完成逐章拆书”的明确错误。
4. `design_album_modules`：
   - 只输入轻量目录；
   - 生成 Markdown 模块计划；
   - 提取并校验每个模块的章节键；
   - 禁止未知键和空模块；
   - 持久化 `module_plan`。
5. `expand_album_modules`：
   - 为每个模块创建 `project_album_module` 子运行；
   - 最多两个模块并行；
   - 每个模块只输入自身章节内容；
   - 每完成一个模块立即写入 `module_outline` 产物和子运行输出；
   - 单模块失败时继续其他模块，父运行标记 `partial_failed`；
   - 同一运行恢复时跳过已成功模块，只重跑失败或未完成模块。
6. 如果模块输入超过 12,000 字符，由程序创建连续子模块；合并时仍按原模块和章节
   顺序恢复，不允许模型自行遗漏章节。
7. `structure_album_outline`：
   - 按模块 `position` 合并 Markdown；
   - 保存 `combined_outline`；
   - 只把合并 Markdown 和章节键目录传给结构化模型；
   - 保存原始结构化返回为 `structured_outline`；
   - 格式转换失败时保留全部 Markdown，重跑只执行本阶段。
8. `save_project_outline`：
   - 调用新的章节级校验器；
   - 保存声音记录；
   - 成功后才清理旧的临时 `album_outline_draft_json`；
   - 运行输出只保存产物 ID、模块统计和短摘要，不复制完整拆书稿。
9. 为模块子运行增加受控重试接口：
   - `POST /api/runs/{run_id}/modules/{module_key}/retry`
   - 只允许父运行属于当前专辑规划、模块真实存在且状态为失败；
   - 重试沿用父运行锁定的提示词和模型快照；
   - 成功后自动重新检查父运行是否可以进入合并和结构化阶段。
10. 应用重启恢复时，以数据库中的运行阶段和规划产物为准，不依赖内存任务对象。
11. 重复点击“生成专辑大纲”继续复用当前活动父运行，不创建并行的重复任务。
12. 增加取消检查：
   - 模块调用前；
   - 每个模块完成后；
   - 结构化和保存前。

### 验证

```bash
cd backend
../.venv/bin/pytest \
  tests/test_album_planning.py \
  tests/test_workflows.py \
  tests/test_runs.py \
  tests/test_run_api.py -q
```

## 任务 5：改为章节级专辑校验和兼容保存

### 涉及文件

- 修改：`backend/app/workflows.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_chapter_analysis.py`
- 修改：`backend/tests/test_workflows.py`
- 修改：`backend/tests/test_run_api.py`

### 实施步骤

1. 先用失败测试锁定新规则：
   - 一集可关联多个一级章节；
   - 同一一级章节可被多集使用；
   - 未知章节键被拒绝；
   - 每集至少有一个章节键；
   - 专辑保存后 `knowledge_item_ids` 和 `source_content_indexes` 为空；
   - `source_section_ids` 只包含真实一级章节 ID；
   - `section_identifier` 显示完整来源章节；
   - 用户指定集数不一致时只返回说明，不丢弃有效大纲；
   - 已确认的旧项目详情、旧知识资产关系和三栏审核数据保持不变。
2. 用新的 `_validate_chapter_level_album_outline` 替换创作阶段的知识资产校验：
   - 校验标题、主要内容、类型和章节键；
   - 根据本次运行的键映射恢复一级章节 ID；
   - 叙事类拒绝“过渡”类型；
   - 不校验知识资产唯一性、段落原文或金句。
3. 调整 `_save_generated_album`：
   - 写入一级 `source_section_ids`；
   - 写入可读 `section_identifier`；
   - 不写 `episode_knowledge_items`；
   - 新生成声音初始状态仍为大纲待确认。
4. 项目详情接口对新大纲返回空知识资产数组，不再把空数组解释为数据丢失。
5. 保留人工编辑专辑大纲时的章节选择能力；用户变更来源章节后，后续来源匹配必须
   使用新的章节范围。

### 验证

```bash
cd backend
../.venv/bin/pytest \
  tests/test_chapter_analysis.py \
  tests/test_workflows.py \
  tests/test_run_api.py -q
```

## 任务 6：在声音细纲前匹配知识资产和原文块

### 涉及文件

- 修改：`backend/app/contexts.py`
- 修改：`backend/app/workflows.py`
- 修改：`backend/app/batches.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_contexts.py`
- 修改：`backend/tests/test_workflows.py`
- 修改：`backend/tests/test_batches.py`
- 修改：`backend/tests/test_run_api.py`

### 实施步骤

1. 先增加失败测试，覆盖：
   - 新章节级声音在生成细纲前先执行 `match_episode_sources`；
   - 候选资产只来自该声音选择的一个或多个一级章节及其后代；
   - 模型返回越界、未知或空资产时只让当前声音失败；
   - 匹配成功后写入 `episode_knowledge_items`；
   - 知识资产恢复出的段落索引和原文块按原书顺序传给声音细纲；
   - 已有精确知识资产关系的历史声音跳过匹配；
   - 单条重跑和批量生成都执行相同逻辑；
   - 初稿和终稿继续分别使用“细纲 + 原文块”“初稿 + 原文块”；
   - 刷新或重启后，已成功的来源匹配不重复调用模型。
2. 新增 `match_episode_sources` 阶段，声音任务顺序变为：
   - `match_episode_sources`
   - `outline`
   - `draft`
   - `final`
3. 当声音已有有效 `episode_knowledge_items` 时，把来源匹配阶段直接标记为成功；
   历史精确来源和已完成匹配都不重复选择。
4. 构造候选知识资产目录，只包含：
   - 资产 ID；
   - 类型；
   - 标题或精简内容；
   - 所属子主题；
   - 一级章节键。
5. 如果候选目录仍超出模型稳定输入范围，按资产类型和子主题分批召回候选，再对
   合并后的短名单做最终选择；不得直接把整章正文交给选择模型。
6. 来源匹配模型使用任务启动时锁定的 `episode_outline` 阶段模型快照，不增加新的
   用户可见模型选择项。
7. 后端校验所选资产：
   - ID 存在；
   - 属于当前书籍；
   - 位于本集所选一级章节下；
   - 至少匹配一个可恢复原文块的资产。
8. 校验成功后在事务内替换该声音的 `episode_knowledge_items`，再由现有上下文构建器
   恢复有序原文块。
9. 来源匹配为空或失败时抛出 `StageGenerationError("match_episode_sources", ...)`；
   不得回退到模型常识、全书文本或整章无差别正文。
10. 批量任务保持最多五条声音并行；每条声音内部四个阶段严格顺序执行。某条来源
    匹配失败后，其他声音继续。
11. 人工修改专辑大纲并重新保存声音列表时，重建的声音没有知识资产关系，下一次
    生产会自然重新匹配；初稿或终稿的单阶段重跑则复用已匹配来源。

### 验证

```bash
cd backend
../.venv/bin/pytest \
  tests/test_contexts.py \
  tests/test_workflows.py \
  tests/test_batches.py \
  tests/test_run_api.py -q
```

## 任务 7：展示模块进度、Markdown 产物和章节来源

### 涉及文件

- 修改：`app/page.tsx`
- 修改：`app/globals.css`
- 修改：`tests/rendered-html.test.mjs`

### 实施步骤

1. 先增加渲染测试，验证：
   - 专辑规划五个父阶段有中文名称；
   - 模块卡显示总数、成功数、失败数；
   - 已完成模块可展开查看 Markdown；
   - 失败模块显示错误和“重跑此模块”按钮；
   - 专辑大纲卡把“内容索引”改为“来源章节”；
   - 多个章节以独立标签展示；
   - 知识资产与原文索引为空时不渲染空白区域；
   - 声音生产进度包含“匹配本集原文”阶段；
   - 刷新后从运行接口恢复相同进度和产物。
2. 扩展运行详情类型，读取父运行阶段输出和模块子运行。
3. 任务进度卡展示：
   - 当前五阶段；
   - 模块完成进度；
   - 每个模块状态、错误和短摘要；
   - 可折叠的模块 Markdown。
4. 重跑按钮调用受控模块重试接口，调用后继续轮询原父运行。
5. 审核页继续使用现有卡片编辑器：
   - 显示“来源章节”；
   - 支持一集多个章节；
   - 不展示内部章节键之外的数据库 ID；
   - 不显示空知识资产和空段落索引。
6. 声音任务进度把 `match_episode_sources` 显示为“匹配本集原文”，其后仍是声音细纲、
   声音初稿和声音终稿。
7. “提示词”标签页更新专辑大纲帮助文案，明确该模板输出 Markdown、章节来源使用
   系统占位符，知识资产由生产阶段自动匹配。

### 验证

```bash
npm run lint
npm run test
```

## 任务 8：全量回归和真实样书验收

### 涉及文件

- 修改：`README.md`（仅在运行方式或任务说明需要同步时）
- 修改：本计划涉及的测试文件

### 实施步骤

1. 运行全部后端测试：

   ```bash
   cd backend
   ../.venv/bin/pytest -q
   ```

2. 运行前端质量检查：

   ```bash
   npm run lint
   npm run test
   ```

3. 运行安全检查：
   - `git diff --check`
   - 确认没有访问密钥、完整模型输入、原书正文或本地配置进入 Git；
   - 确认 `backend/config.json` 的本地保护状态未被改变。
4. 使用独立临时测试项目引用《当代中国社会分层》的现有拆书结果，不改写现有已确认
   专辑，完成真实 Kimi K3 验收：
   - 25 个一级章节全部进入轻量目录；
   - 模块规划不含严格 JSON、知识资产 ID 或段落索引；
   - 每个模块输入不超过 12,000 字符，最多两个并行；
   - 模块 Markdown 可以边完成边查看；
   - 格式转换只读取合并 Markdown；
   - 生成声音允许一集对应多个一级章节；
   - 页面成功进入专辑大纲审核；
   - 选择一集生成细纲，先匹配知识资产，再恢复原文块；
   - 人为制造一个模块失败和一条声音来源匹配失败，确认均可独立重跑。
5. 验收结束后只清理明确创建的临时项目记录，不删除书籍、拆书稿、历史项目或历史
   运行记录。
6. 检查 `git status --short`，只提交本轮实现文件和测试，不包含用户的其他改动。

## 完成标准

- 专辑创作模型不再接收约 9.4 万字符并同时承担严格 JSON 输出；
- 全书模块规划、模块展开、格式转换和保存可以分别观察与重跑；
- 一集支持一个或多个一级章节，同一章节支持多集使用；
- 专辑审核阶段没有知识资产 ID 和段落索引；
- 声音细纲生成前自动匹配知识资产并恢复原文块；
- 刷新页面或重启应用后任务可以从持久进度继续；
- 模块失败不丢失其他成功模块，单条声音失败不阻塞后续声音；
- 旧项目、提示词历史版本和三栏审核台保持兼容；
- 《当代中国社会分层》使用 Kimi K3 完成真实闭环验收。
