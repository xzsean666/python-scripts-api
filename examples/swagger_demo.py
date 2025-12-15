from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def _request_json(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict | str]:
    body: bytes | None = None
    req_headers = {"accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["content-type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method)
    for k, v in req_headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, {}
            return resp.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace") if raw else str(e)
        return e.code, text


def _wait_ready(url: str, *, timeout_seconds: float = 10.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            code, _ = _request_json("GET", url, timeout=1.0)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start demo server for Swagger testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--auth", action="store_true", help="Enable JWT auth for demo")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    scripts_path = repo_root / "examples" / "scripts"
    state_dir = repo_root / ".quant-script-api"

    env = os.environ.copy()
    src_dir = repo_root / "src"
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(src_dir)
    )
    env["SCRIPT_API_PREFIX"] = "/v1"
    env["SCRIPT_JWT_AUTH"] = "true" if args.auth else "false"

    demo_jwt_secret = "dev_jwt_secret_change_me"
    demo_admin_secret = "dev_admin_secret_change_me"
    if args.auth:
        env["SCRIPT_JWT_SECRET"] = demo_jwt_secret
        env["SCRIPT_JWT_ADMIN_SECRET"] = demo_admin_secret
        env.setdefault("SCRIPT_JWT_ISS", "quant-script-api")
        env.setdefault("SCRIPT_JWT_AUD", "quant-internal")

    cmd = [
        sys.executable,
        "-m",
        "quant_script_api",
        "serve",
        "--scripts-path",
        str(scripts_path),
        "--state-dir",
        str(state_dir),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--env-file",
        "",
    ]
    if args.reload:
        cmd.append("--reload")

    popen_kwargs: dict = {"cwd": str(repo_root), "env": env}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    print("Starting server:")
    print("  cmd:", " ".join(cmd))
    proc = subprocess.Popen(cmd, **popen_kwargs)

    base_url = f"http://{args.host}:{args.port}"
    prefix = env["SCRIPT_API_PREFIX"]
    health_url = f"{base_url}{prefix}/health"

    try:
        if not _wait_ready(health_url, timeout_seconds=10.0):
            print("Server not ready in time. Check logs above.")
            raise SystemExit(1)

        docs_url = f"{base_url}/docs"
        print("\nSwagger:")
        print("  ", docs_url)

        headers: dict[str, str] = {}
        token: str | None = None
        if args.auth:
            code, data = _request_json(
                "POST",
                f"{base_url}{prefix}/auth/admin/token",
                data={"secret": demo_admin_secret},
            )
            if code == 200 and isinstance(data, dict) and "access_token" in data:
                token = str(data["access_token"])
                headers["authorization"] = f"Bearer {token}"
                print("\nJWT enabled:")
                print("  admin secret:", demo_admin_secret)
                print("  access token:", token)
                print("  Swagger 右上角 Authorize -> 填 access token（不带 Bearer）")
            else:
                print("\nJWT enabled, but failed to issue admin token:", code, data)

        print("\nAPI 用例（可在 Swagger 里 Try it out）：")
        print("  1) GET  /v1/health")
        print("  2) GET  /v1/scripts")
        print("  3) POST /v1/runs  body: {\"script\":\"hello.py\"}")
        print(
            "  4) POST /v1/runs  body: {\"script\":\"args_env.py\",\"args\":[\"--name\",\"sean\",\"--count\",\"2\"],\"env\":{\"FOO\":\"bar\"}}"
        )
        print("  5) POST /v1/runs  body: {\"script\":\"fail.py\"}")
        print("  6) POST /v1/runs  body: {\"script\":\"long_task.py\"}  (再用 /stop 停止)")
        print("  7) GET  /v1/runs/{run_id}/logs?stream=both")

        # Basic smoke calls to verify everything works.
        code, health = _request_json("GET", health_url)
        print("\nSmoke:")
        print("  health:", code, health if isinstance(health, dict) else str(health)[:200])

        code, scripts = _request_json(
            "GET", f"{base_url}{prefix}/scripts", headers=headers
        )
        print("  scripts:", code, (scripts if isinstance(scripts, dict) else str(scripts)[:200]))

        code, run = _request_json(
            "POST",
            f"{base_url}{prefix}/runs",
            headers=headers,
            data={"script": "hello.py", "args": []},
        )
        run_id = run.get("run_id") if isinstance(run, dict) else None
        print("  run hello:", code, ("run_id=" + run_id) if run_id else run)

        if run_id:
            time.sleep(0.2)
            code, logs = _request_json(
                "GET",
                f"{base_url}{prefix}/runs/{run_id}/logs?stream=both",
                headers=headers,
            )
            print(
                "  logs hello:",
                code,
                ("ok" if isinstance(logs, dict) else str(logs)[:200]),
            )

        print("\nServer is running. Press Ctrl+C to stop.")
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping server...")
        _terminate_process(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    main()

