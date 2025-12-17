from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from . import __version__
from .auth import issue_admin_token, require_scopes
from .config import Settings, load_settings
from .registry import resolve_script, scan_scripts
from .runner import RunManager


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
        title="quant-script-api",
        version=__version__,
        description="A small control plane to run Python scripts via REST APIs.",
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
    async def rescan_scripts() -> dict[str, Any]:
        app.state.scripts = {s.path: s for s in scan_scripts(settings.scripts_root)}
        return {"count": len(app.state.scripts)}

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
                # Check if the absolute path matches (argv[2] is the script path)
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

    @app.get(f"{settings.api_prefix}/runs", dependencies=[_auth({"scripts:read"})])
    async def list_runs() -> dict[str, Any]:
        runs = await app.state.runner.list_runs()
        return {"count": len(runs), "runs": runs}

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
                status = record.status if record else "not_found"
                results.append({"run_id": run_id, "status": status})
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
        tail_bytes: int = 65536,
    ) -> dict[str, Any]:
        logs = await app.state.runner.read_logs(run_id, stream=stream, tail_bytes=tail_bytes)
        if logs is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return {"run_id": run_id, "stream": stream, "tail_bytes": tail_bytes, **logs}

    @app.post(f"{settings.api_prefix}/auth/admin/token")
    async def admin_token(req: AdminTokenRequest) -> dict[str, Any]:
        return issue_admin_token(settings, req.secret)

    return app


app = create_app()
