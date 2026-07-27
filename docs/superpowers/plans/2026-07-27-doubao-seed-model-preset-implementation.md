# Doubao Seed 2.0 Pro 模型预设实施计划

## 任务 1：模型目录

- 在 `MODEL_PRESETS` 增加 `doubao-seed-2.0-pro`。
- 使用 `openai-compatible` provider。
- 保存 `/api/v1` 基础地址，由 provider 追加 `/chat/completions`。

## 任务 2：测试与文档

- 将模型数量断言从 7 更新为 8。
- 校验豆包预设的模型名、协议和基础地址。
- 模拟 OpenAI 兼容请求，校验最终 URL、模型名和鉴权。
- 更新 README 和 `.env.example` 的模型列表。

## 任务 3：验证与提交

```bash
cd backend
../.venv/bin/pytest -q
cd ..
npm run lint
npm test
git diff --check
```

- 检查 API Key、`.env` 和本地模型选择未进入 Git。
- 提交实现，不自动切换用户当前的全局模型。
