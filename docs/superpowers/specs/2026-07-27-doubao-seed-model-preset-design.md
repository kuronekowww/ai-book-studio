# Doubao Seed 2.0 Pro 全局模型预设设计

## 目标

在现有全局模型切换中增加 `doubao-seed-2.0-pro`，继续沿用任务启动时锁定模型快照的行为，不引入项目级覆盖或新的密钥管理方式。

## 模型配置

- 预设 ID：`doubao-seed-2.0-pro`
- 显示名称：`Doubao Seed 2.0 Pro`
- 模型名称：`doubao-seed-2.0-pro`
- 协议：OpenAI 兼容 Chat Completions
- 基础地址：`http://deepgate.ximalaya.local/doubao-seed-2.0-pro/api/v1`
- 最终请求地址：`http://deepgate.ximalaya.local/doubao-seed-2.0-pro/api/v1/chat/completions`
- 鉴权：复用后端环境中的 `AI_BOOK_STUDIO_API_KEY`

基础地址不包含 `/chat/completions`，由现有 OpenAI 兼容适配器统一追加，防止路径重复。

## 产品行为

- 设置页的全局模型列表从 7 个增加到 8 个。
- 用户选择并保存后，新启动的章节拆书、思维导图、专辑大纲和声音生产使用豆包模型。
- 已经运行的任务继续使用启动时保存的模型快照。
- 本地选择只持久化模型 ID，不保存或下发 API Key。
- 请求失败沿用现有工作流错误记录和单项重跑机制。

## 实现范围

1. 在后端模型目录增加 OpenAI 兼容预设。
2. 更新模型目录和设置接口测试中的数量及唯一性断言。
3. 增加最终 Chat Completions 地址与请求模型名测试。
4. 更新 README 和 `.env.example` 中的可选模型说明。
5. 不修改数据库结构、前端组件结构或工作流阶段逻辑。

## 验收

- 设置接口返回 8 个模型并包含豆包预设。
- 全局切换到豆包后，状态接口返回 `openai-compatible` 和 `doubao-seed-2.0-pro`。
- 请求发送到确认的完整 Chat Completions 地址。
- 持久化文件中不包含 API Key。
- 后端测试、前端测试、lint 和生产构建通过。
