from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import sys
import uuid
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RunRecord:
    run_id: str
    script: str
    argv: list[str]
    status: str
    pid: int | None
    return_code: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    stdout_path: Path
    stderr_path: Path
    error: str | None

    _process: asyncio.subprocess.Process | None = None
    _stdout_file: Any | None = None
    _stderr_file: Any | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "script": self.script,
            "argv": self.argv,
            "status": self.status,
            "pid": self.pid,
            "return_code": self.return_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "error": self.error,
        }


class RunManager:
    def __init__(self, *, scripts_root: Path, logs_dir: Path, terminate_timeout_seconds: int = 10):
        self._scripts_root = scripts_root.expanduser().resolve()
        self._logs_dir = logs_dir.expanduser()
        self._terminate_timeout_seconds = max(1, int(terminate_timeout_seconds))
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        script: str,
        absolute_script_path: Path,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> RunRecord:
        args = args or []
        run_id = str(uuid.uuid4())
        self._logs_dir.mkdir(parents=True, exist_ok=True)

        cmd = [sys.executable, "-u", str(absolute_script_path), *args]
        stdout_path = (self._logs_dir / f"{run_id}.stdout.log").resolve()
        stderr_path = (self._logs_dir / f"{run_id}.stderr.log").resolve()

        record = RunRecord(
            run_id=run_id,
            script=script,
            argv=cmd,
            status="starting",
            pid=None,
            return_code=None,
            created_at=_utc_now(),
            started_at=None,
            finished_at=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            error=None,
        )

        async with self._lock:
            self._runs[run_id] = record

        full_env = os.environ.copy()
        if env:
            full_env.update({str(k): str(v) for k, v in env.items()})
        full_env.setdefault("PYTHONUNBUFFERED", "1")

        run_cwd = cwd or self._scripts_root
        stdout_file = open(stdout_path, "ab", buffering=0)
        stderr_file = open(stderr_path, "ab", buffering=0)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(run_cwd),
                env=full_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=(os.name != "nt"),
            )
        except Exception as e:
            stdout_file.close()
            stderr_file.close()
            async with self._lock:
                record.status = "failed"
                record.error = str(e)
                record.finished_at = _utc_now()
            return record

        async with self._lock:
            record._process = proc
            record._stdout_file = stdout_file
            record._stderr_file = stderr_file
            record.pid = proc.pid
            record.status = "running"
            record.started_at = _utc_now()

        asyncio.create_task(self._watch(run_id))
        return record

    async def _watch(self, run_id: str) -> None:
        async with self._lock:
            record = self._runs.get(run_id)
            proc = record._process if record else None
        if record is None or proc is None:
            return

        try:
            rc = await proc.wait()
        except Exception as e:
            async with self._lock:
                record.status = "failed"
                record.error = str(e)
                record.finished_at = _utc_now()
            self._close_files(record)
            return

        async with self._lock:
            record.return_code = rc
            record.finished_at = _utc_now()
            if record.status in {"stopping", "stopped"}:
                record.status = "stopped"
            elif rc == 0:
                record.status = "succeeded"
            else:
                record.status = "failed"
        self._close_files(record)

    def _close_files(self, record: RunRecord) -> None:
        for f in (record._stdout_file, record._stderr_file):
            try:
                if f:
                    f.close()
            except Exception:
                pass
        record._stdout_file = None
        record._stderr_file = None

    async def list_runs(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [r.to_public() for r in self._runs.values()]

    async def get(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def stop(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None
            proc = record._process
            if proc is None or record.status not in {"running", "starting"}:
                return record
            record.status = "stopping"

        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            return record
        except Exception as e:
            async with self._lock:
                record.status = "failed"
                record.error = str(e)
            return record

        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=self._terminate_timeout_seconds)
        except asyncio.TimeoutError:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except Exception:
                pass
        else:
            async with self._lock:
                record.return_code = rc
                record.finished_at = _utc_now()
                record.status = "stopped"

        return record

    async def read_logs(
        self, run_id: str, *, stream: str = "stdout", tail_bytes: int = 65536
    ) -> dict[str, str] | None:
        record = await self.get(run_id)
        if record is None:
            return None

        tail_bytes = max(0, int(tail_bytes))
        result: dict[str, str] = {}

        if stream in {"stdout", "both"}:
            result["stdout"] = _tail_text_file(record.stdout_path, tail_bytes)
        if stream in {"stderr", "both"}:
            result["stderr"] = _tail_text_file(record.stderr_path, tail_bytes)
        return result


def _tail_text_file(path: Path, tail_bytes: int) -> str:
    try:
        if not path.exists():
            return ""
        with open(path, "rb") as f:
            if tail_bytes > 0:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - tail_bytes))
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""
