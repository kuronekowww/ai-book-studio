# DeepGate Anthropic 供应商接入设计

日期：2026-07-24
状态：已通过对话确认，待书面规格复核

## 目标

为知声工坊增加原生 Anthropic Messages 供应商，并将本地运行配置切换到用户指定的 DeepGate 模型路由。保留演示供应商和 OpenAI 兼容供应商，避免影响现有测试与后续模型切换。

## 已确认配置

- `provider`: `anthropic`
- `base_url`: `http://deepgate.ximalaya.local/claude-sonnet-4-6-wangsu-anthropic/api`
- `model`: `claude-sonnet-4-6-wangsu-anthropic`
- 协议：Anthropic Messages
- 请求地址：`base_url.rstrip("/") + "/v1/messages"`
- 访问密钥：仅写入本机 `.env`，不进入规格、Git、数据库、日志、网页或 Obsidian

## 供应商实现

新增 `AnthropicProvider`，沿用现有 `ModelProvider` 契约：

```python
async def generate(prompt: PromptDefinition, source: str) -> str
```

请求使用现有 `httpx` 依赖：

- `httpx.AsyncClient(timeout=120, trust_env=False)`
- Header `x-api-key`
- Header `anthropic-version: 2023-06-01`
- Header `content-type: application/json`
- Body 包含 `model`、`max_tokens` 和 `messages`

DeepGate 路由不会稳定传递顶层 `system` 内容，因此不发送单独的 `system` 字段。完整的系统角色、输出约束和任务正文合并到 user 消息：

```text
【系统要求】
{prompt.system}

【任务】
{prompt.user_template.format(source=source)}
```

响应从 Anthropic `content` 数组中收集所有 `type=text` 的文本块并合并。若响应没有文本块，返回可理解的错误。

## 配置与选择

`build_provider` 按 `AI_BOOK_STUDIO_PROVIDER` 选择：

- `demo` → `DemoProvider`
- `anthropic` → `AnthropicProvider`
- 其他值 → `OpenAICompatibleProvider`

`.env.example` 增加无密钥的 Anthropic 示例，并保留当前变量名：

- `AI_BOOK_STUDIO_PROVIDER`
- `AI_BOOK_STUDIO_API_BASE`
- `AI_BOOK_STUDIO_API_KEY`
- `AI_BOOK_STUDIO_MODEL`

本机 `.env` 写入实际配置。该文件已被 `.gitignore` 忽略。

## 错误与安全

- 未配置密钥时，在发起请求前直接报错；
- HTTP 错误转换为包含状态码的简短消息；
- 不在错误消息中输出请求头、完整请求体或访问密钥；
- `trust_env=False` 避免公司 `.local` 域名错误继承代理环境；
- 设置页只显示供应商、模型和“已配置密钥”，不返回密钥内容；
- 真实请求只做一次最小验证，不批量生成专辑。

## 验证

1. 单元测试验证 Anthropic URL、请求头、`trust_env=False`、user 消息合并和文本块解析；
2. 单元测试验证 `provider=anthropic` 正确选择新供应商；
3. 后端全部回归测试通过；
4. 前端设置页显示 `anthropic`、指定模型和密钥已配置；
5. 发起一次最小真实 Messages 请求，确认网关可达且返回非空文本；
6. 检查 `.env`、访问密钥和响应正文未进入 Git。

## 本次不做

- 在网页中编辑或保存访问密钥；
- 把 Anthropic SDK 加入依赖；
- 自动回退到其他供应商；
- 使用真实模型重新批量生成现有专辑；
- 将本地配置同步到云端。
