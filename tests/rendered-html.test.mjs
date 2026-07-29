import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("renders AI Book Studio product shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /知声工坊/);
  assert.match(html, /AI 讲书知识与文稿工作台/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("keeps the final-first Notion review workspace in source", async () => {
  const [page, styles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(page, /生成全部终稿/);
  assert.match(page, /保存修改 · 新建版本/);
  assert.match(page, /supporting-artifacts/);
  assert.match(page, /review-inspector/);
  assert.match(page, /name="book_type"/);
  assert.match(page, /framework-editor/);
  assert.match(page, /声音内容框架/);
  assert.match(page, /selectedFile/);
  assert.match(page, /重新选择/);
  assert.match(page, /analysis_enabled/);
  assert.match(page, /生成思维导图与专辑大纲/);
  assert.match(page, /期望集数（可选）/);
  assert.match(page, /设为全局模型/);
  assert.match(page, /新任务使用新模型，正在运行的任务继续使用启动时的模型/);
  assert.match(page, /available_models/);
  assert.match(page, /章节拆书模型/);
  assert.match(page, /按生产环节选择模型/);
  assert.match(page, /projectModelStageLabels/);
  assert.match(page, /专辑大纲 ·/);
  assert.match(styles, /\.file-drop\.selected/);
  assert.match(styles, /\.album-generation-card/);
  assert.match(styles, /\.model-selector/);
  assert.match(styles, /\.project-model-config/);
  assert.match(styles, /--green: #5645d4/);
  assert.match(styles, /grid-template-columns: minmax\(280px, 300px\).*minmax\(250px, 280px\)/);
  assert.match(styles, /\.final-editor \{/);
});

test("keeps the versioned prompt workbench in source", async () => {
  const [page, styles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(page, /label: "提示词"/);
  assert.match(page, /全局默认/);
  assert.match(page, /项目覆盖/);
  assert.match(page, /保存为新版本/);
  assert.match(page, /预览填充结果/);
  assert.match(page, /恢复系统默认/);
  assert.match(page, /取消项目覆盖/);
  assert.match(page, /required_placeholders/);
  assert.match(page, /\{\{\$\{name\}\}\}/);
  assert.match(styles, /\.prompt-workbench/);
  assert.match(styles, /\.prompt-stage-rail/);
  assert.match(styles, /\.prompt-editor-panel/);
  assert.match(styles, /\.prompt-meta-panel/);
});

test("keeps persistent workflow progress and workspace recovery in source", async () => {
  const [page, styles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(page, /\/api\/runs\?limit=100/);
  assert.match(page, /ai-book-studio:view/);
  assert.match(page, /ai-book-studio:book/);
  assert.match(page, /ai-book-studio:project/);
  assert.match(page, /ai-book-studio:episode/);
  assert.match(page, /后台持久任务/);
  assert.match(page, /TaskProgressCard/);
  assert.match(page, /展开查看/);
  assert.match(styles, /\.task-progress-card/);
  assert.match(styles, /\.task-stage-list/);
  assert.match(styles, /\.task-output/);
});
