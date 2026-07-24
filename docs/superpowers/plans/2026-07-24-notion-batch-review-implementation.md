# Notion 风格与批量终稿审核实施计划

日期：2026-07-24
对应规格：`docs/superpowers/specs/2026-07-24-notion-batch-review-design.md`

## 目标

在保留现有本地数据和单条重跑能力的前提下，加入专辑级 5 路并行生产、人工终稿版本，并把内容项目重构为 Notion 风格三栏审核台。

## 实施顺序

### 任务 1：数据库兼容升级

涉及：

- `backend/app/db.py`
- `backend/tests/`

步骤：

1. 为 `workflow_runs` 增加 `parent_run_id`、`error_stage`、`position` 和 `metadata_json`。
2. 为 `artifact_versions` 增加 `author_type`，默认 `model`。
3. 在数据库初始化中加入幂等列升级，兼容已有 SQLite。
4. 更新 JSON 字段解码和测试数据库辅助逻辑。
5. 增加旧数据库升级测试。

### 任务 2：批次协调器

涉及：

- `backend/app/main.py`
- `backend/app/workflows.py`
- `backend/tests/test_batch.py`

步骤：

1. 将单条声音生成拆为可观察的阶段执行函数。
2. 每个阶段开始和结束时更新声音状态。
3. 创建项目级父运行记录和声音级子运行记录。
4. 使用 `asyncio.Semaphore(5)` 和隔离的子协程并行执行。
5. 已有终稿或活动任务的声音跳过。
6. 单条失败记录 `error_stage`，其他任务继续。
7. 汇总项目状态为 `review` 或 `partial_failed`。
8. 启动时恢复批次和未完成子任务。
9. 重复启动同一项目批次时返回活动批次。

### 任务 3：批次与人工版本 API

涉及：

- `backend/app/main.py`
- `backend/app/workflows.py`
- `backend/tests/test_batch.py`

步骤：

1. 新增 `POST /api/projects/{id}/generate-all`。
2. 新增 `GET /api/projects/{id}/batch`。
3. 新增 `POST /api/episodes/{id}/final-versions`。
4. 人工版本写入 `stage=final`、`author_type=human`、`provider=human`。
5. 保持现有单条生成接口向后兼容。

### 任务 4：前端数据流

涉及：

- `app/page.tsx`

步骤：

1. 扩展 Run、Episode、ArtifactVersion 和 Project 类型。
2. 加入批次启动、进度轮询和项目刷新。
3. 加入终稿草稿、未保存状态和人工保存。
4. 切换声音时保护未保存内容。
5. 细纲、初稿使用 `<details>` 折叠，终稿使用大型文本编辑区。
6. 右栏展示证据和版本，支持选择历史版本作为编辑起点。

### 任务 5：Notion 视觉与三栏布局

涉及：

- `app/globals.css`
- `app/layout.tsx`

步骤：

1. 替换全局颜色、字体、圆角和阴影变量。
2. 把应用侧栏、顶栏、卡片、按钮、输入框统一为 Notion 工作区风格。
3. 实现约 300px / 自适应 / 280px 的内容项目三栏。
4. 确保终稿编辑器在桌面和小屏上拥有最高空间优先级。
5. 右栏在中等视口折叠为抽屉，小屏时目录和右栏均折叠。
6. 更新页面元信息中的产品描述，不增加营销页装饰。

### 任务 6：验证与文档

涉及：

- `backend/tests/`
- `tests/rendered-html.test.mjs`
- `README.md`
- `task_plan.md`
- `progress.md`

验证：

1. `cd backend && ../.venv/bin/pytest -q`
2. `npm run lint`
3. `npm test`
4. 用本地《圆圈正义》项目启动批次，验证最多 5 条活跃声音。
5. 验证单条失败不影响其他声音。
6. 编辑终稿并保存，确认模型版本和人工版本同时存在。
7. 检查原书、API Key、SQLite 和生成内容未进入 Git。
8. 提交实现并保留 `npm run studio` 本地预览。

## 完成标准

- 所有自动化测试通过；
- 12 条样例声音可由一个批次连续生产；
- 任一时刻声音级并发不超过 5；
- 终稿在内容项目中占据主要编辑空间；
- 细纲和初稿默认折叠；
- 人工编辑生成新版本；
- 工作区视觉符合已选三栏 Notion 方向；
- 原有导入、拆书、单条重跑和 Obsidian 同步能力无回归。
