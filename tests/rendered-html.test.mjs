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
  assert.match(styles, /--green: #5645d4/);
  assert.match(styles, /grid-template-columns: minmax\(280px, 300px\).*minmax\(250px, 280px\)/);
  assert.match(styles, /\.final-editor \{/);
});
