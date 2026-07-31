# Apple Silicon Node 运行时统一设计

## 背景

当前机器同时安装了两套 Node：

- `/usr/local/bin/node`：x86_64 Node 24.7.0；
- `/opt/homebrew/bin/node`：arm64 Node 26.0.0。

项目的 `node_modules` 曾由 x86_64 Node 安装，因此只包含
`@rolldown/binding-darwin-x64`。用户终端启动项目时使用 arm64 Node，
Rolldown 会寻找 `@rolldown/binding-darwin-arm64`，在前端监听端口前直接退出。

## 目标

1. Apple Silicon 本机开发统一使用原生 arm64 Node。
2. 修复当前缺失的 Rolldown arm64 原生绑定。
3. 启动前发现架构依赖缺失时给出简洁、可执行的提示。
4. 不修改业务代码、数据库、模型配置或用户内容。

## 方案

### 运行时选择

`scripts/dev.sh` 在 Darwin arm64 机器上优先把 `/opt/homebrew/bin` 放到
`PATH` 前部。若该目录不存在，则保留当前 `PATH`，随后通过预检明确报错，
不静默切换到 Rosetta Node。

### 依赖预检

启动前读取当前 Node 的 `process.platform` 和 `process.arch`，并验证 Rolldown
对应的原生包能够被解析。Apple Silicon 的预期包为
`@rolldown/binding-darwin-arm64`。

若缺失，启动脚本停止并提示用户在项目根目录使用当前原生 npm 执行：

```bash
npm install --include=optional
```

预检只读取本地依赖，不自动联网安装，避免每次启动产生不可预期的依赖变更。

### 当前环境修复

实施时使用 `/opt/homebrew/bin/npm` 按现有 `package-lock.json` 重新安装可选依赖。
锁文件已包含 Rolldown 1.0.1 的 Darwin arm64 与 x64 条目，不升级业务依赖版本。

## 错误处理

- 未找到原生 Homebrew Node：显示安装或 PATH 修复提示；
- Node 仍不是 arm64：停止启动并显示当前 Node 路径和架构；
- 原生绑定缺失：显示精确的 `npm install --include=optional` 命令；
- 依赖安装失败：保留锁文件和现有项目数据，不删除数据库或源码；
- 后端启动失败：沿用现有 `dev-backend.sh` 的 arm64 Python 约束与错误输出。

## 验收

1. `/opt/homebrew/bin/node` 报告 `darwin/arm64`；
2. 原生 npm 能解析 `@rolldown/binding-darwin-arm64`；
3. `npm run build` 成功；
4. `npm run studio` 同时启动前端 3000 和后端 8000；
5. 首页、后端 `/docs` 和 `/api/projects` 返回成功；
6. 从不同终端 PATH 启动时，脚本仍选择同一原生 Node；
7. Git 差异不包含数据库、密钥或模型输出。

## 非目标

- 不升级 Node、Vite、Rolldown 或其他应用依赖版本；
- 不删除 `package-lock.json`；
- 不在启动过程中自动执行联网安装；
- 不支持在 Apple Silicon 上继续混用 x64 与 arm64 Node。
