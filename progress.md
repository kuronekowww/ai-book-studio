# Progress Log

## Session: 2026-07-24

### Phase 1: 实施规划与工程初始化

- **Status:** complete
- **Started:** 2026-07-24
- Actions taken:
  - 完成需求文档、SOP、六个 Dify YAML 和《圆圈正义》结构检查。
  - 完成架构、页面、数据模型、Obsidian 和验收标准设计确认。
  - 创建独立 Git 项目。
  - 写入并提交设计规格。
  - 创建持续实施计划、发现记录和进度日志。
  - 使用 Sites 标准模板初始化 vinext/Vite 工程并完成依赖安装。
  - 启动 `http://localhost:3000/` 本地预览。
- Files created/modified:
  - `.gitignore`
  - `docs/superpowers/specs/2026-07-24-ai-book-studio-design.md`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 2: 后端领域模型与书籍入库

- **Status:** complete
- Actions taken:
  - 检查站点结构和本机 Python 依赖。
  - 确认系统 Pydantic 二进制架构不兼容，决定创建项目私有虚拟环境。
  - 建立 FastAPI、SQLite、书籍/章节/知识/项目/声音/版本实体。
  - 实现 Markdown、TXT、EPUB 解析和章节确认接口。
  - 《圆圈正义》真实导入准确识别 8 个主题、49 篇文章和 4 个长文语义分段。
- Files created/modified:
  - `backend/app/config.py`
  - `backend/app/db.py`
  - `backend/app/ingestion.py`
  - `backend/app/main.py`

### Phase 3-5: 工作流、前端与 Obsidian

- **Status:** complete
- Actions taken:
  - 实现演示与 OpenAI 兼容模型供应商。
  - 建立提示词版本、持久运行、声音版本和重跑。
  - 完成书籍库、章节编辑、知识工作台、项目大纲编辑和声音工作台。
  - 保存六个原始 Dify YAML 作为迁移源。
  - 实现 Obsidian 增量同步、同步清单和批注保留。
- Files created/modified:
  - `app/page.tsx`
  - `app/globals.css`
  - `backend/app/providers.py`
  - `backend/app/prompts.py`
  - `backend/app/workflows.py`
  - `backend/app/obsidian.py`
  - `workflows/dify-source/*.yml`

### Phase 6: 集成验证与修复

- **Status:** complete
- Actions taken:
  - 后端 4 项测试通过。
  - 前端 lint、构建和服务器渲染测试通过。
  - 《圆圈正义》端到端生成与 Obsidian 幂等性通过。
  - 修复 x86_64 Homebrew Node 与 arm64 Python 虚拟环境混用导致的 Rosetta 启动问题。
  - 使用 `npm run studio` 统一启动并确认网页、API 和已有样例数据均正常。
- Files created/modified:
  - `backend/tests/`
  - `tests/rendered-html.test.mjs`
  - `scripts/dev-backend.sh`

### Phase 7: 本地交付

- **Status:** complete
- Actions taken:
  - 完成安装、环境配置、统一启动和验收说明。
  - 确认原书、SQLite 运行数据、虚拟环境与密钥均未纳入 Git。
  - 完成全量复测并提交首版本地产品。

### Phase 8: Notion 审核台实施规划

- **Status:** complete
- **Started:** 2026-07-24
- Actions taken:
  - 阅读用户提供的 Notion 设计上下文并提取工作区视觉规则。
  - 通过视觉伴随比较三种终稿审核布局，用户选择三栏审核台。
  - 确认专辑大纲后手动触发批量生产。
  - 确认单条三阶段串行、不同声音最多 5 条并行。
  - 确认失败隔离继续生产和人工终稿新版本规则。
  - 写入并提交 `2026-07-24-notion-batch-review-design.md`。
  - 写入详细实施计划，进入批量生产与人工版本实现阶段。
  - 第一轮数据库迁移测试发现旧表建索引顺序问题，已改为先补字段再创建索引。

### Phase 9: 批量生产与人工版本

- **Status:** complete
- Actions taken:
  - 为运行记录增加父子关系、失败阶段、顺序和进度元数据。
  - 为产物版本增加模型/人工来源标记，并实现旧 SQLite 幂等升级。
  - 实现项目级批次、最多 5 条声音并行和单条三阶段顺序。
  - 实现失败隔离、活动批次去重、重启恢复和失败后单条重跑归并。
  - 实现人工终稿新版本接口。

### Phase 10: Notion 风格三栏审核台

- **Status:** complete
- Actions taken:
  - 全站切换为暖白、浅灰、克制紫色的 Notion 工作区视觉。
  - 左栏加入批次进度和声音阶段状态。
  - 中栏将终稿放大为可编辑主区域，细纲与初稿默认折叠。
  - 右栏加入原文证据和模型/人工版本记录。
  - 加入未保存保护和响应式布局。

### Phase 11: 端到端验收

- **Status:** complete
- Actions taken:
  - 后端 7 项测试通过，覆盖并发上限、失败隔离、旧库升级和人工版本。
  - 前端 lint、生产构建和 2 项渲染/结构测试通过。
  - 《圆圈正义》批次跳过已有 2 条，成功生成剩余 10 条终稿。
  - 保存一条人工终稿 v2，并确认模型终稿 v1 保留。

### Phase 12: 本地交付

- **Status:** complete
- Actions taken:
  - 更新 README 中的批量生产和三栏审核说明。
  - 完成 Git 范围、密钥、原书和运行数据检查。
  - 提交实现并保留本地前后端预览。

### Phase 13: DeepGate Anthropic 接入

- **Status:** complete
- **Started:** 2026-07-24
- Actions taken:
  - 确认 DeepGate 使用 Anthropic Messages 协议。
  - 确认关闭环境代理并将完整提示要求合并到 user 消息。
  - 写入并提交不含访问密钥的接入设计规格。
  - 写入实施计划并完成 Anthropic 供应商适配。
  - 增加模拟请求测试，校验请求地址、鉴权头、消息结构和响应解析。
  - 首次独立烟雾测试因 `.env` 变量未导出而回落到演示供应商；已定位为 shell 导出方式问题。

### Phase 14: 本机配置与真实验证

- **Status:** complete
- Actions taken:
  - 将实际 DeepGate 配置写入 Git 忽略的本机 `.env`。
  - 重启统一开发服务，确认后端报告 Anthropic 供应商和目标模型已就绪。
  - 通过显式导出环境变量完成一次最小真实调用，获得非空模型响应。
  - 后端 9 项测试、前端 2 项测试、lint 和生产构建全部通过。
  - 确认 `.env` 未被 Git 跟踪，提交范围不包含访问密钥。

### Phase 15: 单条声音上下文链设计

- **Status:** in_progress
- **Started:** 2026-07-24
- Actions taken:
  - 检查现有提示词注册表、声音生成服务和原始 Dify 声音细纲工作流。
  - 确认当前细纲阶段只收到原文块，未收到专辑大纲中的当前声音框架。
  - 确认当前初稿只收到细纲，终稿只收到初稿，两个阶段都没有再次附带原文块。
  - 用户确认三阶段统一采用“上一步产物 + 对应原文块”的输入规则。
  - 检查现有专辑大纲数据结构，确认每条声音目前只有标题、内容类型、风格和来源小节 ID。
  - 用户确认新增可编辑的多行声音框架，并在确认专辑大纲时一起审核。
  - 用户确认书籍上传时选择叙事类或非叙事类，仅叙事类提示词包含人物关系。
  - 用户确认叙事类人物关系在拆书阶段由模型提取，按原文块 ID 存为知识资产，声音细纲按关联块筛选。
  - 比较结构化字段、JSON 元数据和运行时推断三种实现，用户确认采用结构化字段与知识资产方案。
  - 写入单条声音上下文链设计规格，覆盖数据、拆书、审核、三阶段输入、错误处理和验收标准。
  - 完成规格自检，移除让模型生成来源 ID 的歧义，并明确超长输入由供应商显式失败而不静默截断。

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| 设计规格自检 | 规格 Markdown | 无占位、范围矛盾和空白要求 | 通过 | ✓ |
| Git 项目初始化 | 新项目目录 | 独立 main 分支和首个提交 | `d8113ad` | ✓ |
| 《圆圈正义》导入 | Markdown 原书 | 8 主题、49 文章 | 8 主题、49 文章、4 语义分段 | ✓ |
| 知识拆解 | 已确认章节 | 观点/案例/金句和思维导图 | 102 条资产、56 行导图 | ✓ |
| 声音生成 | 第一条声音 | 细纲、初稿、终稿 | 三阶段完成 | ✓ |
| 指定节点重跑 | 从初稿开始 | 保留旧版本 | draft v1/v2、final v1/v2 | ✓ |
| Obsidian 幂等 | 同一内容同步两次 | 第二次无变更 | 171 → 0 | ✓ |
| 后端测试 | pytest | 全部通过 | 4 passed | ✓ |
| 前端 lint | ESLint | 无错误 | 通过 | ✓ |
| 前端构建与渲染 | npm test | 构建并渲染产品壳 | 通过 | ✓ |
| 统一启动 | npm run studio | 前端与 API 同时可用 | 3000/8000 均正常 | ✓ |
| 批次并发 | 7 条测试声音 | 同时活跃不超过 5 | 最大活跃数 5 | ✓ |
| 失败隔离 | 1 条模拟失败 | 其余继续并可单条归并 | 6 成功、1 失败、重跑后全成功 | ✓ |
| 《圆圈正义》批次 | 10 条待生成声音 | 一次生成全部终稿 | 10/10 成功，专辑共 12 条 | ✓ |
| 人工终稿版本 | 编辑第 3 条终稿 | 新版本且模型原稿保留 | human v2 + model v1 | ✓ |
| 本次后端测试 | pytest | 全部通过 | 7 passed | ✓ |
| 本次前端测试 | npm test | 构建和结构校验通过 | 2 passed | ✓ |
| Anthropic 模拟请求 | pytest | URL、鉴权、消息与解析正确 | 2 项新增用例通过 | ✓ |
| DeepGate 最小真实请求 | Anthropic Messages | 返回非空内容 | 目标模型返回 5 字符内容 | ✓ |
| DeepGate 接入回归 | pytest / lint / test / build | 全部通过 | 后端 9、前端 2、lint 与构建通过 | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-07-24 | 在不存在的工作目录中执行初始化命令 | 1 | 从父目录创建项目后重试 |
| 2026-07-24 | 视觉伴随脚本无执行权限 | 1 | 改用 bash 调用 |
| 2026-07-24 | 沙箱拒绝本地监听端口 | 2 | 获批后重新启动 |
| 2026-07-24 | 初始化器拒绝非空项目目录 | 1 | 临时移出文档并在初始化后恢复 |
| 2026-07-24 | npm 无法写入用户缓存日志 | 1 | 获批后完成安装 |
| 2026-07-24 | 开发服务器无法监听调试端口 | 1 | 获批后启动本地预览 |
| 2026-07-24 | 系统 Pydantic Core 为错误 CPU 架构 | 1 | 改用项目私有虚拟环境 |
| 2026-07-24 | 统一启动经 x86_64 Node 派生后端，选中错误 Python 架构 | 1 | Apple Silicon 上显式用 arm64 Python 启动 |
| 2026-07-24 | pip 在沙箱中无法访问依赖索引 | 1 | 获批后安装 |
| 2026-07-24 | React effect 触发同步 setState lint 错误 | 1 | 改为异步回调并去掉派生状态 effect |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | 已完成本地首版和 DeepGate Anthropic 接入 |
| Where am I going? | 后续可扩展更多模型供应商和多书融合创作 |
| What's the goal? | 交付可用的本地 AI 讲书知识与文稿工作台 |
| What have I learned? | 见 `findings.md` |
| What have I done? | 已完成产品、真实样书闭环、真实模型接入、测试、文档和 Git 交付 |
