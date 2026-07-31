import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(
  new URL("../scripts/check-node-runtime.mjs", import.meta.url),
);

function runCheck(overrides) {
  return spawnSync(process.execPath, [script], {
    encoding: "utf8",
    env: {
      ...process.env,
      AI_BOOK_STUDIO_REQUIRE_ARM64: "1",
      AI_BOOK_STUDIO_TEST_NODE_PLATFORM: "darwin",
      ...overrides,
    },
  });
}

test("accepts an arm64 runtime with a resolvable binding", () => {
  const result = runCheck({
    AI_BOOK_STUDIO_TEST_NODE_ARCH: "arm64",
    AI_BOOK_STUDIO_TEST_BINDING: "rolldown",
  });

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /arm64/);
});

test("rejects an x64 runtime when native arm64 is required", () => {
  const result = runCheck({
    AI_BOOK_STUDIO_TEST_NODE_ARCH: "x64",
    AI_BOOK_STUDIO_TEST_BINDING: "rolldown",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /arm64/);
  assert.match(result.stderr, /Node/);
});

test("reports an actionable command when the native binding is missing", () => {
  const result = runCheck({
    AI_BOOK_STUDIO_TEST_NODE_ARCH: "arm64",
    AI_BOOK_STUDIO_TEST_BINDING: "@rolldown/binding-does-not-exist",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /npm install --include=optional/);
});
