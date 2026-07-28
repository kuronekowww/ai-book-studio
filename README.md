# 知声工坊（AI Book Studio）

一个本地优先的讲书知识与文稿工作台。它把“拆书知识库”和“内容创作库”分开：先将原书整理为可追溯的观点、论据、案例和金句，再从这些资产生成专辑大纲、声音细纲、初稿与终稿。

## 当前能力

- 导入 EPUB、TXT 和 Markdown；
- 上传时区分叙事类与非叙事类书籍；
- 恢复异常换行，识别标题树和长文语义分段；
- 两个人工检查点：章节切分、专辑大纲；
- 非叙事类按一级章节逐章调用模型，最多 5 章并行，失败章节可单独重跑；
- 在一级章节内部生成约 300–800 字的段落级原文块，索引可稳定回到原章节；
- 版本化保存完整章节拆书稿，并让每条概念、观点、论据、案例和金句独立绑定一个或多个原文索引；
- 对定义、观点、论据、金句和案例证据执行逐字原文校验，拒绝错误或越界来源；
- 完整拆书稿过长时逐章压缩，稳定 `content_index` 不变；
- 基于同一份拆书稿并行生成 Markdown 思维导图和结构化专辑大纲；
- 专辑特殊要求和期望集数可选，过渡声音仅按内容需要生成；
- 生成声音细纲、初稿、终稿；
- 专辑大纲逐条审核多行声音内容框架；
- 叙事类拆书按原文块提取人物关系；
- 专辑按知识资产编排，段落索引只承担原文证据职责；
- 细纲、初稿和终稿始终重新带入当前声音知识资产的直接证据，并单独标记相邻辅助上下文；
- 确认专辑大纲后，一键批量生成全部终稿；
- 单条声音内部顺序生产，不同声音最多 5 条并行；
- Notion 风格三栏审核台，终稿为主编辑区，细纲与初稿默认折叠；
- 人工修改保存为新版本，模型原稿始终保留；
- 从指定节点重跑并保留旧版本；
- 持久任务记录和重启恢复；
- 增量同步到 Obsidian，保留个人批注区块；
- Obsidian 自动生成原文片段笔记，并建立片段、知识资产和声音稿之间的双向链接；
- 演示模型无需 API Key，真实模型支持 Anthropic Messages 和 OpenAI 兼容接口。
- 设置页支持 8 个 DeepGate 模型全局即时切换；新任务使用新模型，运行中任务保持原模型。

## 本地安装

需要 Node.js 22.13+ 和 Python 3.11+。

```bash
npm ci
python3 -m venv .venv
cd backend
../.venv/bin/pip install -e '.[dev]'
cd ..
cp .env.example .env
```

## 启动

```bash
npm run studio
```

然后打开 [http://localhost:3000](http://localhost:3000)。前端使用 3000 端口，FastAPI 使用 8000 端口。

如果希望分别启动：

```bash
npm run backend
npm run dev
```

## 模型配置

默认 `.env` 使用确定性演示模型，可完整体验工作流且不会产生模型费用。

要连接 Anthropic Messages 接口：

```dotenv
AI_BOOK_STUDIO_PROVIDER=anthropic
AI_BOOK_STUDIO_API_BASE=http://你的网关/模型路由/api
AI_BOOK_STUDIO_API_KEY=你的本机密钥
AI_BOOK_STUDIO_MODEL=你的模型路由名称
```

Anthropic 供应商使用 Messages 协议，并自动在配置地址后追加 `/v1/messages`。
密钥只从后端进程环境读取，不写入数据库、日志、Obsidian 或浏览器存储。

仍可使用 `openai-compatible` 供应商连接兼容 `/chat/completions` 的接口。

启动应用后，可在“设置与同步 → 全局模型”中选择：

- Claude Sonnet 4.6
- Kimi K3
- Claude Sonnet 5
- GLM 5.2
- Kimi K2.6
- DeepSeek V4 Pro
- HY3
- Doubao Seed 2.0 Pro

模型切换无需重启。切换只影响随后启动的新任务；已经运行的章节拆书、专辑生成或声音生产会继续使用任务启动时锁定的模型。全局选择保存在本地 `data` 目录，API Key 仍只从 `.env` 读取。

升级前已有的章节拆书稿会继续保留，但不会被自动猜测成段落级来源。页面会标记“历史结果，需重跑升级溯源”；逐章重跑后，新的知识资产和专辑编排才会使用精准原文索引。

如需按当前校验规则升级一本书已经保存的“部分成功”章节，可在没有运行中任务时执行：

```bash
bash scripts/revalidate-partial-chapters.sh <book_id>
```

该命令只重新校验已保存的结构化结果并创建新版本，不调用模型，也不删除历史版本。

## 使用《圆圈正义》验收

1. 在书籍知识库选择“非叙事类”，上传 `/Users/xmly/Downloads/拆书/圆圈正义.md`；
2. 检查系统识别的 8 个主题和 49 篇文章；
3. 调整并确认章节；
4. 执行“拆书与知识入库”，观察一级章节最多 5 章并行；
5. 从该书创建内容项目，填写可选特殊要求与期望集数，生成并审核专辑大纲；
6. 点击“生成全部终稿”，观察最多 5 条声音并行生产；
7. 选择一条声音，在三栏审核台中展开细纲或初稿并编辑终稿；
8. 点击“保存修改”，确认出现新的人工编辑版本；
9. 从初稿节点重跑，查看历史模型版本；
10. 在设置页填写 Obsidian Vault 绝对路径并同步。

原书、数据库、API Key、模型输出和 Obsidian 测试内容均被 Git 忽略。

## 测试

```bash
cd backend
../.venv/bin/pytest -q
cd ..
npm run lint
npm test
```

## 目录

```text
app/                 React 产品界面
backend/app/         FastAPI、解析、工作流、版本和 Obsidian
backend/tests/       后端回归测试
docs/superpowers/    设计规格和实施计划
scripts/             本地启动脚本
workflows/dify-source/  原始 Dify 工作流迁移参考
data/                本地数据库和导入文件（不进入 Git）
```
