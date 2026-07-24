# Dify 工作流迁移源

本目录保存用户提供的六个 Dify 工作流原始导出，用于核对提示词、模型配置、条件分支和代码节点。运行中的网页产品不调用 Dify。

| 文件 | 项目内职责 |
| --- | --- |
| `01-long-chapter-segmentation.yml` | 长章节规范化、分段分析与合并 |
| `02-narrative-book-analysis.yml` | 故事/非故事拆书与章节迭代 |
| `03-mind-map-and-album-outline.yml` | 思维导图与专辑大纲 |
| `04-episode-outline.yml` | 解读、过渡、故事和多书细纲分支 |
| `05-episode-draft.yml` | 观点、鸡汤、过渡和故事初稿分支 |
| `06-style-polish.yml` | 文本风格与前序声音承接 |

项目内标准化的提示词契约位于 `backend/app/prompts.py`，执行分支位于 `backend/app/workflows.py`。原始 YAML 用于迁移核对，不在运行时解析，因此不会重新引入 Dify 依赖。

这些导出文件已检查：没有 API Key、Token 或密码；其中两个 HTTP 节点明确为 `no-auth`，首版已由本地书籍上传替代。
