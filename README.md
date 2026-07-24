# 知声工坊（AI Book Studio）

一个本地优先的讲书知识与文稿工作台。它把“拆书知识库”和“内容创作库”分开：先将原书整理为可追溯的观点、论据、案例和金句，再从这些资产生成专辑大纲、声音细纲、初稿与终稿。

## 当前能力

- 导入 EPUB、TXT 和 Markdown；
- 恢复异常换行，识别标题树和长文语义分段；
- 两个人工检查点：章节切分、专辑大纲；
- 生成知识资产和 Markdown 思维导图；
- 生成声音细纲、初稿、终稿；
- 从指定节点重跑并保留旧版本；
- 持久任务记录和重启恢复；
- 增量同步到 Obsidian，保留个人批注区块；
- 演示模型无需 API Key，真实模型支持 OpenAI 兼容接口。

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

要连接 OpenAI 兼容接口：

```dotenv
AI_BOOK_STUDIO_PROVIDER=openai-compatible
AI_BOOK_STUDIO_API_BASE=https://api.openai.com/v1
AI_BOOK_STUDIO_API_KEY=你的本机密钥
AI_BOOK_STUDIO_MODEL=gpt-4.1-mini
```

密钥只从后端进程环境读取，不写入数据库、日志、Obsidian 或浏览器存储。

## 使用《圆圈正义》验收

1. 在书籍知识库上传 `/Users/xmly/Downloads/拆书/圆圈正义.md`；
2. 检查系统识别的 8 个主题和 49 篇文章；
3. 调整并确认章节；
4. 执行“拆书与知识入库”；
5. 从该书创建内容项目并确认专辑大纲；
6. 选择一条声音，生成细纲、初稿和终稿；
7. 从初稿节点重跑，查看 `v1`、`v2`；
8. 在设置页填写 Obsidian Vault 绝对路径并同步。

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
