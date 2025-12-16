## quant-script-api

`quant-script-api` 是一个可安装的 Python 包，用于 **统一管理和执行 Python 脚本**。
它基于 **FastAPI + subprocess / asyncio** 实现，提供标准化的 **脚本执行 API** 与运行时管理能力，并且 **不侵入脚本本身**（脚本不需要 import runner）。

### 核心设计原则

- Runner 与脚本共用同一个 Python 环境（venv / conda / uv）
- Runner 不负责依赖隔离，只负责调度与执行
- 脚本路径通过运行时参数指定，不写死在包内
- 脚本可独立运行，完全不需要 import runner

### 安装与启动

```

uv add git+https://github.com/xzsean666/python-scripts-api.git

uv run quant-script-api serve --scripts-path ./scripts
```



安装：

```bash
pip install quant-script-api
```

从 GitHub 安装（适合未发布 PyPI 的场景）：

```bash
# 安装为项目依赖（写入 pyproject.toml）
uv add "quant-script-api @ git+https://github.com/<OWNER>/<REPO>.git"

# 或安装为全局工具（推荐用来跑服务）
uv tool install "quant-script-api @ git+https://github.com/<OWNER>/<REPO>.git"
```

固定到 tag / commit：

```bash
uv add "quant-script-api @ git+https://github.com/<OWNER>/<REPO>.git@v0.1.0"
# 或 @<commit_sha>
```

启动（指定脚本目录）：

```bash
quant-script-api serve --scripts-path strategies/binances
```

也可以通过环境变量指定脚本目录（CLI 参数优先级更高）：

```env
SCRIPT_SCRIPTS_PATH=strategies/binances
```

启动后可访问：

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`

### API（最小列表）

默认前缀为 `/v1`（可通过 `SCRIPT_API_PREFIX` 配置）：

- `GET /v1/health`：健康检查
- `GET /v1/scripts`：扫描结果（脚本列表）
- `POST /v1/scripts/rescan`：重新扫描脚本目录
- `POST /v1/runs`：启动脚本
- `GET /v1/runs`：运行列表
- `GET /v1/runs/{run_id}`：运行详情/状态
- `POST /v1/runs/{run_id}/stop`：停止运行
- `GET /v1/runs/{run_id}/logs`：读取日志（stdout/stderr）

### 运行与日志规范（默认）

- 运行命令：`sys.executable -u <script.py> ...`（与 Runner 共享同一 Python 环境）
- 默认工作目录：`--scripts-path` 指定的目录（可在 `POST /v1/runs` 中用 `cwd` 覆盖，且必须在 scripts root 下）
- 日志目录：默认 `.quant-script-api/logs/`
  - stdout：`<run_id>.stdout.log`
  - stderr：`<run_id>.stderr.log`
  - 可用 `SCRIPT_STATE_DIR` / `SCRIPT_LOGS_DIR` 覆盖

### 可选鉴权（JWT）

在 `.env` 中开启（示例见 `.env.example`）：

```env
SCRIPT_JWT_AUTH=true
SCRIPT_JWT_SECRET=change_me
SCRIPT_JWT_ISS=quant-script-api
SCRIPT_JWT_AUD=quant-internal
SCRIPT_JWT_EXPIRE_SECONDS=3600
```

当 `SCRIPT_JWT_AUTH=true` 时：

- 需要 `Authorization: Bearer <token>` 才能访问受保护 API
- 本项目 **不带用户系统**：只做 JWT 校验与 scope 校验

Scope 约定（最小集合）：

- 读脚本/运行状态：`scripts:read`
- 启动/停止脚本：`scripts:run`
- 读日志：`logs:read`

#### 人类用户 Token（示例）

你可以用任意系统签发，只要用同一个 `SCRIPT_JWT_SECRET`（HS256）即可：

```json
{
  "iss": "quant-script-api",
  "aud": "quant-internal",
  "sub": "user_12345",
  "type": "user",
  "role": "operator",
  "scopes": ["scripts:read", "scripts:run", "logs:read"],
  "iat": 1736840000,
  "exp": 1736843600,
  "jti": "c2c4b1c2-0a1d-4b8a-9d34-9f92caa11111"
}
```

#### 管理员 Secret 换取 Bearer Token

如果设置了 `SCRIPT_JWT_ADMIN_SECRET`，则开放一个简单的“管理员 Secret → Token”接口：

```env
SCRIPT_JWT_ADMIN_SECRET=change_me_too
```

请求：

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/auth/admin/token \
  -H 'content-type: application/json' \
  -d '{"secret":"change_me_too"}'
```

返回：

```json
{ "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }
```

然后带上 Token 调用接口：

```bash
curl -sS http://127.0.0.1:8000/v1/scripts \
  -H "authorization: Bearer $TOKEN"
```

### 调用示例（运行脚本）

启动一个脚本（脚本路径相对 `--scripts-path`）：

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/runs \
  -H 'content-type: application/json' \
  -d '{"script":"hello.py","args":["--foo","bar"]}'
```

读取日志（默认 stdout，支持 `stream=stderr|both`）：

```bash
curl -sS "http://127.0.0.1:8000/v1/runs/<run_id>/logs?stream=both&tail_bytes=65536"
```

### 不包含的职责（刻意不做）

- 不管理 Python 依赖、不做环境隔离
- 不耦合业务逻辑、不要求脚本遵循特定框架

一句话定位：`quant-script-api` 是一个“Python 脚本运行时控制平面”，而不是脚本本身的一部分。
