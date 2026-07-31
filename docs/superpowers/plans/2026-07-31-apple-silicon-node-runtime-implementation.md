# Apple Silicon Node 运行时统一实施计划

## 目标

统一 Apple Silicon 本地启动使用 `/opt/homebrew` 的 arm64 Node，补齐 Rolldown
arm64 原生绑定，并验证前后端均能稳定启动。

## 任务 1：启动预检脚本

- 新增一个只读 Node 运行时预检脚本。
- 在 Darwin arm64 上验证当前 Node 为 arm64。
- 验证当前 Rolldown 平台绑定可以解析。
- 错误信息包含当前 Node 路径、架构和精确修复命令。
- 在正常、错误架构和缺失绑定场景增加脚本测试。

## 任务 2：统一开发启动入口

- 修改 `scripts/dev.sh`，Apple Silicon 上优先使用 `/opt/homebrew/bin`。
- 在启动前调用预检脚本。
- 保持现有后端 arm64 Python 启动与进程清理逻辑。
- 不在启动脚本中自动联网安装依赖。

## 任务 3：修复本机依赖

- 使用 `/opt/homebrew/bin/npm install --include=optional`。
- 保持现有依赖版本和锁文件语义。
- 确认 `@rolldown/binding-darwin-arm64` 可被原生 Node 解析。

## 任务 4：构建与启动验收

- 使用原生 Node 执行 ESLint、前端测试和生产构建。
- 运行 `npm run studio`。
- 检查 3000、8000 端口及首页、`/docs`、`/api/projects`。
- 确认启动日志不再包含 Rolldown 原生绑定错误。

## 任务 5：文档、回归与提交

- 更新 README 的 Apple Silicon 运行说明和故障提示。
- 运行后端测试和 Git 差异检查。
- 确认数据库、密钥和运行产物未进入提交。
- 提交实现并保留已启动服务供用户使用。
