import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const platform =
  process.env.AI_BOOK_STUDIO_TEST_NODE_PLATFORM || process.platform;
const arch = process.env.AI_BOOK_STUDIO_TEST_NODE_ARCH || process.arch;
const requireArm64 = process.env.AI_BOOK_STUDIO_REQUIRE_ARM64 === "1";
const binding =
  process.env.AI_BOOK_STUDIO_TEST_BINDING ||
  (platform === "darwin"
    ? `@rolldown/binding-darwin-${arch}`
    : "rolldown");

function fail(message, details = []) {
  const lines = [
    `[AI Book Studio] ${message}`,
    `当前 Node：${process.execPath}`,
    `当前运行时：${platform}/${arch} ${process.version}`,
    ...details,
  ];
  process.stderr.write(`${lines.join("\n")}\n`);
  process.exit(1);
}

if (requireArm64 && platform === "darwin" && arch !== "arm64") {
  fail("Apple Silicon 必须使用原生 arm64 Node。", [
    "请确认 /opt/homebrew/bin 位于 PATH 最前面。",
  ]);
}

try {
  require.resolve(binding);
} catch {
  fail(`缺少当前架构需要的原生依赖：${binding}`, [
    "请在项目根目录执行：",
    "PATH=/opt/homebrew/bin:$PATH npm install --include=optional",
  ]);
}

process.stdout.write(
  `[AI Book Studio] Node ${process.version} (${platform}/${arch}) · ${process.execPath}\n`,
);
