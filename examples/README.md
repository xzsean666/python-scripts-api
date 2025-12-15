## 本地快速测试（无鉴权）

准备几个示例脚本在 `examples/scripts/`，启动服务：

```bash
quant-script-api serve --scripts-path examples/scripts --reload
```

也可以直接跑一个“一键启动 + 冒烟”的脚本（更适合配合 Swagger）：  

```bash
uv run examples/swagger_demo.py
# 或开启鉴权
uv run examples/swagger_demo.py --auth
```

然后用 curl 试一下：

```bash
curl -sS http://127.0.0.1:8000/v1/health
curl -sS http://127.0.0.1:8000/v1/scripts
curl -sS -X POST http://127.0.0.1:8000/v1/runs -H 'content-type: application/json' -d '{"script":"hello.py"}'
```

长任务 + 停止：

```bash
# 先启动 long_task.py，拿到返回的 run_id
curl -sS -X POST http://127.0.0.1:8000/v1/runs -H 'content-type: application/json' -d '{"script":"long_task.py"}'

# 停止
curl -sS -X POST http://127.0.0.1:8000/v1/runs/<run_id>/stop
```

查看日志：

```bash
curl -sS "http://127.0.0.1:8000/v1/runs/<run_id>/logs?stream=both"
```

## 本地快速测试（开启 JWT）

复制 `.env.example` 为 `.env`，并至少配置：

```env
SCRIPT_JWT_AUTH=true
SCRIPT_JWT_SECRET=change_me
SCRIPT_JWT_ADMIN_SECRET=change_me_too
```

启动服务后，用管理员 secret 换 token：

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8000/v1/auth/admin/token -H 'content-type: application/json' -d '{"secret":"change_me_too"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
curl -sS http://127.0.0.1:8000/v1/scripts -H "authorization: Bearer $TOKEN"
```
