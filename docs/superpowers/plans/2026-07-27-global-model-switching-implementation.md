# 全局模型切换实施计划

## 目标

按照已批准的设计，在设置页提供 7 个 DeepGate 模型的全局即时切换；新任务使用新模型，已运行和重启恢复的任务保持原模型；密钥只保留在本机环境变量。

## 任务 1：模型预设与选择管理器

涉及文件：

- 新建 `backend/app/model_catalog.py`
- 修改 `backend/app/providers.py`
- 新建或扩展 `backend/tests/test_model_catalog.py`
- 扩展 `backend/tests/test_providers.py`

步骤：

1. 定义不可变的 7 个模型预设，ID 唯一且前端不可提交任意地址。
2. 实现 Anthropic Messages URL 规范化，兼容 `/api` 与 `/api/v1`。
3. 实现 `ModelManager`：从数据目录读取选择、回退环境变量、生成 provider 快照。
4. 用临时文件和原子替换持久化 `{model_id}`，不保存密钥。
5. 增加目录枚举、地址、持久化、损坏文件回退和未知 ID 测试。

## 任务 2：工作流 provider 快照

涉及文件：

- 修改 `backend/app/workflows.py`
- 修改 `backend/app/batches.py`
- 扩展 `backend/tests/test_workflows.py`
- 扩展 `backend/tests/test_batches.py`
- 扩展 `backend/tests/test_chapter_analysis.py`

步骤：

1. 为顶层工作流方法增加可选 provider 参数，并在入口固定为局部 `task_provider`。
2. 将章节拆书、JSON 修复、人物关系、拆书稿压缩、思维导图、专辑大纲和声音三阶段全部改为使用该局部 provider。
3. 保存章节分析和声音版本时显式写入局部 provider 的名称与模型。
4. 批次创建时把非敏感 `model_id` 写入父运行元数据；运行和重启恢复时据此重建 provider。
5. 单条声音运行同样把 `model_id` 写入运行元数据并在执行时使用。
6. 测试运行期间切换不会改变后续阶段，新任务使用新快照。

## 任务 3：设置 API 与应用接线

涉及文件：

- 修改 `backend/app/main.py`
- 新增设置接口测试

步骤：

1. 用 `ModelManager` 取代模块级固定 provider 的直接读取。
2. 扩展 `GET /api/settings/status`，返回当前选择、来源与模型列表。
3. 新增 `PUT /api/settings/model`，只接受 `model_id`。
4. 所有顶层生成路由在任务创建时捕获当前模型快照。
5. 健康接口继续报告当前 provider/model。
6. 验证状态响应不包含 API Key 或鉴权头。

## 任务 4：设置页全局切换

涉及文件：

- 修改 `app/page.tsx`
- 修改 `app/globals.css`
- 扩展 `tests/rendered-html.test.mjs`

步骤：

1. 扩展设置状态类型，加入模型列表、当前模型 ID 和来源。
2. 在设置页增加模型选择控件和“设为全局模型”按钮。
3. 保存时禁用重复提交；成功更新全局状态和提示；失败恢复服务端当前状态。
4. 显示“只影响新任务，运行中任务不受影响”和密钥边界说明。
5. 保持现有 Notion 风格、响应式和键盘可用性。

## 任务 5：说明、全量验证与提交

涉及文件：

- 修改 `.env.example`
- 修改 `README.md`
- 更新 `task_plan.md`、`findings.md`、`progress.md`

验证命令：

```bash
cd backend
../.venv/bin/pytest -q
cd ..
npm run lint
npm test
git diff --check
git status --short
```

额外检查：

1. 搜索密钥特征和用户提供的实际密钥，确认未进入 Git 差异。
2. 检查 `data/model-settings.json` 被现有 `data/` 忽略。
3. 本地启动后读取设置状态，切换模型并确认无需重启。
4. 如进行真实模型验证，只运行一个最小短请求，不触发批量付费生成。
5. 完成后提交实现，并在交付信息中说明默认模型、切换位置和运行任务规则。
