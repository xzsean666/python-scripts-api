from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .auth import issue_admin_token, require_scopes
from .config import Settings, load_settings
from .registry import resolve_script, scan_scripts
from .runner import RunManager

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    script: str = Field(..., description="Script path relative to scripts root")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = Field(
        default=None,
        description="Optional working directory relative to scripts root (default: scripts root)",
    )
    duplicate: bool = Field(
        default=False,
        description="Allow multiple instances of the same script to run simultaneously",
    )


class RunAllRequest(BaseModel):
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = Field(
        default=None,
        description="Optional working directory relative to scripts root (default: scripts root)",
    )
    duplicate: bool = Field(
        default=False,
        description="Allow multiple instances of the same script to run simultaneously",
    )


class AdminTokenRequest(BaseModel):
    secret: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(
        title="python-script-api",
        version=__version__,
        description="A small control plane to run Python scripts via REST APIs.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.runner = RunManager(
        scripts_root=settings.scripts_root,
        logs_dir=settings.logs_dir,
        state_dir=settings.state_dir,
        terminate_timeout_seconds=settings.terminate_timeout_seconds,
    )
    app.state.scripts = {s.path: s for s in scan_scripts(settings.scripts_root)}

    def _auth(scopes: set[str]) -> Any:
        return Depends(require_scopes(settings, scopes))

    @app.get(f"{settings.api_prefix}/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "scripts_root": str(settings.scripts_root),
            "jwt_auth": settings.jwt_auth,
        }

    @app.get(f"{settings.api_prefix}/docker/metrics", dependencies=[_auth({"scripts:read"})])
    async def get_docker_metrics() -> dict[str, Any]:
        def _read_cgroup(path: str) -> str:
            try:
                with open(path, "r") as f:
                    return f.read().strip()
            except Exception:
                return ""

        def _get_cpu_usec() -> int:
            data = _read_cgroup("/sys/fs/cgroup/cpu.stat")
            for line in data.split("\n"):
                if line.startswith("usage_usec"):
                    try:
                        return int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
            return 0

        t1 = _get_cpu_usec()
        ts1 = asyncio.get_running_loop().time()

        await asyncio.sleep(0.5)

        t2 = _get_cpu_usec()
        ts2 = asyncio.get_running_loop().time()

        cpu_percent = 0.0
        if ts2 > ts1:
            usage_diff = (t2 - t1) / 1e6
            raw_cpu_percent = (usage_diff / (ts2 - ts1)) * 100

            num_cores = os.cpu_count() or 1
            cpu_percent = raw_cpu_percent / num_cores

        mem_curr_str = _read_cgroup("/sys/fs/cgroup/memory.current")
        mem_max_str = _read_cgroup("/sys/fs/cgroup/memory.max")

        memory_used_gb = int(mem_curr_str) / (1024**3) if mem_curr_str.isdigit() else 0.0
        memory_total_gb = int(mem_max_str) / (1024**3) if mem_max_str.isdigit() else 0.0

        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_used_gb": round(memory_used_gb, 1),
            "memory_total_gb": round(memory_total_gb, 1),
        }

    @app.get(f"{settings.api_prefix}/system/metrics", dependencies=[_auth({"scripts:read"})])
    async def get_system_metrics() -> dict[str, Any]:
        def _read_proc_stat() -> tuple[int, int]:
            try:
                with open("/proc/stat", "r") as f:
                    for line in f:
                        if line.startswith("cpu "):
                            parts = line.split()
                            values = [int(x) for x in parts[1:]]
                            total_time = sum(values)
                            idle_time = values[3]
                            if len(values) > 4:
                                idle_time += values[4]
                            return total_time, idle_time
            except Exception as e:
                logger.error(f"Error reading /proc/stat: {e}")
            return 0, 0

        def _read_meminfo() -> tuple[float, float]:
            mem_total = 0
            mem_available = 0
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split()
                        key = parts[0].rstrip(":")
                        if len(parts) > 1:
                            value_kb = int(parts[1])
                            if key == "MemTotal":
                                mem_total = value_kb * 1024
                            elif key == "MemAvailable":
                                mem_available = value_kb * 1024
            except Exception as e:
                logger.error(f"Error reading /proc/meminfo: {e}")
            return mem_total, mem_available

        t1, i1 = _read_proc_stat()
        await asyncio.sleep(0.5)
        t2, i2 = _read_proc_stat()

        cpu_percent = 0.0
        delta_total = t2 - t1
        delta_idle = i2 - i1
        if delta_total > 0:
            cpu_usage = delta_total - delta_idle
            cpu_percent = (cpu_usage / delta_total) * 100

        mem_total_bytes, mem_available_bytes = _read_meminfo()
        mem_used_bytes = mem_total_bytes - mem_available_bytes

        memory_total_gb = mem_total_bytes / (1024**3)
        memory_used_gb = mem_used_bytes / (1024**3)

        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_used_gb": round(memory_used_gb, 1),
            "memory_total_gb": round(memory_total_gb, 1),
        }

    @app.get(f"{settings.api_prefix}/scripts", dependencies=[_auth({"scripts:read"})])
    async def list_scripts() -> dict[str, Any]:
        scripts = list(app.state.scripts.values())
        return {
            "root": str(settings.scripts_root),
            "count": len(scripts),
            "scripts": [
                {
                    "path": s.path,
                    "size_bytes": s.size_bytes,
                    "mtime": s.mtime,
                }
                for s in scripts
            ],
        }

    @app.post(
        f"{settings.api_prefix}/scripts/rescan",
        dependencies=[_auth({"scripts:read"})],
    )
    async def rescan_scripts(max_depth: int | None = None) -> dict[str, Any]:
        app.state.scripts = {
            s.path: s for s in scan_scripts(settings.scripts_root, max_depth=max_depth)
        }
        return {"count": len(app.state.scripts)}

    @app.post(
        f"{settings.api_prefix}/scripts/upload",
        dependencies=[_auth({"scripts:write"})],
    )
    async def upload_script(
        file: UploadFile = File(..., description="File content to upload"),
        file_name: str = Form(..., description="Target file name"),
        file_path: str = Form(
            "", description="Target directory relative to scripts root"
        ),
    ) -> dict[str, Any]:
        cleaned_name = file_name.strip()
        if not cleaned_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="file_name is required"
            )
        if cleaned_name in {".", ".."} or "/" in cleaned_name or "\\" in cleaned_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_name must be a base file name",
            )

        cleaned_path = file_path.strip()
        if "\\" in cleaned_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_path must use '/' separators",
            )
        if cleaned_path and Path(cleaned_path).is_absolute():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_path must be relative to scripts_root",
            )

        scripts_root = settings.scripts_root.expanduser().resolve(strict=False)
        target_dir = scripts_root if cleaned_path in {"", ".", "./"} else scripts_root / cleaned_path
        target_dir = target_dir.expanduser().resolve(strict=False)

        if not target_dir.is_relative_to(scripts_root):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_path must be under scripts_root",
            )
        if target_dir.exists() and not target_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="file_path must point to a directory",
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / cleaned_name

        try:
            with target_path.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        finally:
            await file.close()

        app.state.scripts = {s.path: s for s in scan_scripts(settings.scripts_root)}
        relative_path = target_path.relative_to(scripts_root).as_posix()
        return {
            "status": "ok",
            "path": relative_path,
            "size_bytes": target_path.stat().st_size,
        }

    @app.get(f"{settings.api_prefix}/runs", dependencies=[_auth({"scripts:read"})])
    async def list_runs() -> dict[str, Any]:
        runs = await app.state.runner.list_runs()
        return {"count": len(runs), "runs": runs}

    @app.get(
        f"{settings.api_prefix}/runs/active", dependencies=[_auth({"scripts:read"})]
    )
    async def list_active_runs() -> dict[str, Any]:
        runs = await app.state.runner.list_active_runs()
        return {"count": len(runs), "runs": runs}

    @app.post(f"{settings.api_prefix}/runs", dependencies=[_auth({"scripts:run"})])
    async def start_run(req: RunRequest) -> dict[str, Any]:
        try:
            absolute = resolve_script(settings.scripts_root, req.script)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Script not found"
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        run_cwd: Path | None = None
        if req.cwd:
            try:
                run_cwd = (settings.scripts_root / req.cwd).expanduser().resolve()
                if not run_cwd.is_relative_to(settings.scripts_root.resolve()):
                    raise ValueError("cwd must be under scripts_root")
                if not run_cwd.exists() or not run_cwd.is_dir():
                    raise ValueError("cwd must exist and be a directory")
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
                )

        if not req.duplicate:
            active_runs = await app.state.runner.list_active_runs()
            resolved_absolute_str = str(absolute)
            for run in active_runs:
                if len(run["argv"]) > 2 and run["argv"][2] == resolved_absolute_str:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Script is already running. Set 'duplicate' to true to allow multiple instances.",
                    )

        record = await app.state.runner.start(
            script=req.script,
            absolute_script_path=absolute,
            args=req.args,
            env=req.env,
            cwd=run_cwd,
        )
        return record.to_public()

    @app.post(f"{settings.api_prefix}/runs/all", dependencies=[_auth({"scripts:run"})])
    async def run_all_scripts(req: RunAllRequest) -> dict[str, Any]:
        scripts = list(app.state.scripts.values())
        results = []

        active_runs = []
        if not req.duplicate:
            active_runs = await app.state.runner.list_active_runs()

        for s in scripts:
            try:
                absolute = resolve_script(settings.scripts_root, s.path)
            except Exception:
                results.append(
                    {
                        "script": s.path,
                        "status": "error",
                        "error": "Script resolution failed",
                    }
                )
                continue

            if not req.duplicate:
                resolved_absolute_str = str(absolute)
                is_running = False
                for run in active_runs:
                    if len(run["argv"]) > 2 and run["argv"][2] == resolved_absolute_str:
                        is_running = True
                        break
                if is_running:
                    results.append(
                        {
                            "script": s.path,
                            "status": "skipped",
                            "reason": "Already running",
                        }
                    )
                    continue

            run_cwd: Path | None = None
            if req.cwd:
                try:
                    run_cwd = (settings.scripts_root / req.cwd).expanduser().resolve()
                    if not run_cwd.is_relative_to(settings.scripts_root.resolve()):
                        results.append(
                            {
                                "script": s.path,
                                "status": "error",
                                "error": "Invalid cwd",
                            }
                        )
                        continue
                except Exception:
                    results.append(
                        {"script": s.path, "status": "error", "error": "Invalid cwd"}
                    )
                    continue

            try:
                record = await app.state.runner.start(
                    script=s.path,
                    absolute_script_path=absolute,
                    args=req.args,
                    env=req.env,
                    cwd=run_cwd,
                )
                results.append(
                    {"script": s.path, "status": "started", "run_id": record.run_id}
                )
            except Exception as e:
                results.append({"script": s.path, "status": "error", "error": str(e)})

        return {"count": len(results), "results": results}

    @app.post(
        f"{settings.api_prefix}/runs/stop_all", dependencies=[_auth({"scripts:run"})]
    )
    async def stop_all_runs() -> dict[str, Any]:
        active_runs = await app.state.runner.list_active_runs()
        results = []
        for run in active_runs:
            run_id = run["run_id"]
            try:
                record = await app.state.runner.stop(run_id)
                run_status = record.status if record else "not_found"
                results.append({"run_id": run_id, "status": run_status})
            except Exception as e:
                results.append({"run_id": run_id, "status": "error", "error": str(e)})

        return {"count": len(results), "results": results}

    @app.get(
        f"{settings.api_prefix}/runs/{{run_id}}", dependencies=[_auth({"scripts:read"})]
    )
    async def get_run(run_id: str) -> dict[str, Any]:
        record = await app.state.runner.get(run_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return record.to_public()

    @app.post(
        f"{settings.api_prefix}/runs/{{run_id}}/stop",
        dependencies=[_auth({"scripts:run"})],
    )
    async def stop_run(run_id: str) -> dict[str, Any]:
        record = await app.state.runner.stop(run_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return record.to_public()

    @app.get(
        f"{settings.api_prefix}/runs/{{run_id}}/logs",
        dependencies=[_auth({"logs:read"})],
    )
    async def get_logs(
        run_id: str,
        stream: Literal["stdout", "stderr", "both"] = "stdout",
        tail_lines: int = 1000,
    ) -> dict[str, Any]:
        logs = await app.state.runner.read_logs(run_id, stream=stream, tail_lines=tail_lines)
        if logs is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return {"run_id": run_id, "stream": stream, "tail_lines": tail_lines, **logs}

    @app.websocket(f"{settings.api_prefix}/runs/{{run_id}}/logs/stream")
    async def stream_logs(websocket: WebSocket, run_id: str):
        await websocket.accept()
        record = await app.state.runner.get(run_id)
        if record is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Run not found")
            return

        stdout_offset = 0
        stderr_offset = 0

        try:
            while True:
                sent_data = False

                if record.stdout_path.exists():
                    try:
                        with open(record.stdout_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(stdout_offset)
                            data = f.read(65536)
                            if data:
                                await websocket.send_json({"stream": "stdout", "data": data})
                                stdout_offset = f.tell()
                                sent_data = True
                    except Exception as e:
                        logger.error(f"Error reading stdout for {run_id}: {e}")

                if record.stderr_path.exists():
                    try:
                        with open(record.stderr_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(stderr_offset)
                            data = f.read(65536)
                            if data:
                                await websocket.send_json({"stream": "stderr", "data": data})
                                stderr_offset = f.tell()
                                sent_data = True
                    except Exception as e:
                        logger.error(f"Error reading stderr for {run_id}: {e}")

                if not sent_data:
                    if record.status in ("stopped", "succeeded", "failed", "terminated"):
                        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                        break
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(0.5)

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for run {run_id}")
        except Exception as e:
            logger.error(f"WebSocket error for run {run_id}: {e}")
            try:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            except Exception:
                pass

    @app.post(f"{settings.api_prefix}/auth/admin/token")
    async def admin_token(req: AdminTokenRequest) -> dict[str, Any]:
        return issue_admin_token(settings, req.secret)

    return app


app = create_app()
