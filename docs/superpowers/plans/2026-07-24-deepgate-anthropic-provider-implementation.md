# DeepGate Anthropic 供应商实施计划

日期：2026-07-24

## 目标

在现有模型适配层中加入 Anthropic Messages 协议，将用户指定的 DeepGate 配置写入仅本机可见的 `.env`，并通过一次最小真实调用验证可用性。

## 步骤

1. 在 `backend/app/providers.py` 新增 `AnthropicProvider`。
2. 使用 `httpx.AsyncClient(timeout=120, trust_env=False)`。
3. 请求 `${base_url}/v1/messages`，使用 Anthropic 鉴权头。
4. 将系统要求与任务合并到 user 消息，并解析全部文本内容块。
5. 按 `provider=anthropic` 选择新供应商。
6. 在 `backend/tests/test_providers.py` 模拟传输层，验证 URL、头、消息和解析。
7. 更新 `.env.example` 和 README 中的无密钥配置示例。
8. 将实际配置写入被 Git 忽略的 `.env`。
9. 重启本地后端，确认设置接口只显示供应商、模型和密钥状态。
10. 使用最小提示执行一次真实 Messages 请求，不触发专辑批量生成。
11. 运行后端测试、前端 lint 和构建测试。
12. 检查 Git 暂存区、代码和文档不含实际访问密钥后提交。
