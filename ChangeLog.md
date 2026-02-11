# ChangeLog

## 2026-02-11

### 新增功能
- **WebSocket 实时日志流**: 新增 `WS /v1/runs/{run_id}/logs/stream` 端点，支持实时推送 stdout/stderr 日志
- **脚本上传接口**: 新增 `POST /v1/scripts/upload` 端点，支持通过 multipart form 上传脚本文件到指定目录
- **Docker 容器监控**: 新增 `GET /v1/docker/metrics` 端点，通过 cgroup v2 读取容器 CPU/内存使用
- **宿主机系统监控**: 新增 `GET /v1/system/metrics` 端点，通过 /proc 读取系统 CPU/内存使用
- **文件日志**: 服务启动时自动将 uvicorn + 应用日志写入 `logs/app-{date}.log`
- **on_completion 回调**: RunManager 支持 `on_completion` 回调参数，脚本执行完成后可触发自定义逻辑
- **scan_scripts 深度控制**: `scan_scripts()` 新增 `max_depth` 参数，`rescan` API 支持 `max_depth` 查询参数

### 优化改进
- **ANSI 转义码过滤**: 日志写入时自动过滤 ANSI 转义码，保证日志文件干净可读
- **双 PIPE 日志捕获**: stdout 和 stderr 均通过 asyncio.PIPE 捕获，stderr 同时合并到 stdout 日志文件
- **日志读取改用行数**: `read_logs` 和 `/logs` API 从 `tail_bytes` 改为 `tail_lines`，更符合实际使用习惯
- **日志尾部读取优化**: 使用 `collections.deque` 高效读取文件末尾指定行数

### Bug 修复
- **config.py**: 修复 `Settings` dataclass 中 `jwt_iss` 字段重复定义的问题
- **app.py**: 移除重复的 `list_runs` 路由定义
- **app.py**: 修复 `stop_all_runs` 中变量名 `status` 与模块名冲突的问题
