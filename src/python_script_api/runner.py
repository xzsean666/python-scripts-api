from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
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

    def __init__(
        self,
        *,
        scripts_root: Path,
        logs_dir: Path,
        state_dir: Path,
        terminate_timeout_seconds: int = 10,
    ):
        self._scripts_root = scripts_root.expanduser().resolve()
        self._logs_dir = logs_dir.expanduser()
        self._state_dir = state_dir.expanduser()
        self._terminate_timeout_seconds = max(1, int(terminate_timeout_seconds))
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()

        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._state_dir / "runs.db"
        self._init_db()
        self._load_runs()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    script TEXT,
                    argv TEXT,
                    status TEXT,
                    pid INTEGER,
                    return_code INTEGER,
                    created_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    stdout_path TEXT,
                    stderr_path TEXT,
                    error TEXT
                )
            """
            )
            conn.commit()

    def _load_runs(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM runs")
            for row in cursor:
                record = RunRecord(
                    run_id=row["run_id"],
                    script=row["script"],
                    argv=json.loads(row["argv"]),
                    status=row["status"],
                    pid=row["pid"],
                    return_code=row["return_code"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    stdout_path=Path(row["stdout_path"]),
                    stderr_path=Path(row["stderr_path"]),
                    error=row["error"],
                )

                # Check if process is still alive
                if record.status in ("starting", "running", "stopping") and record.pid:
                    if self._is_process_alive(record.pid):
                        # It's alive! Start watching it
                        asyncio.create_task(
                            self._watch_orphaned_run(record.run_id, record.pid)
                        )
                    else:
                        # It's dead
                        record.status = "terminated"
                        if not record.finished_at:
                            record.finished_at = _utc_now()
                        record.error = (
                            f"{record.error}\nServer restarted and process not found"
                            if record.error
                            else "Server restarted and process not found"
                        )
                        self._save_run_sync(record)
                elif record.status in ("starting", "running", "stopping"):
                    # No PID recorded, mark as terminated
                    record.status = "terminated"
                    if not record.finished_at:
                        record.finished_at = _utc_now()
                    record.error = (
                        f"{record.error}\nServer restarted"
                        if record.error
                        else "Server restarted"
                    )
                    self._save_run_sync(record)

                self._runs[record.run_id] = record

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    async def _watch_orphaned_run(self, run_id: str, pid: int) -> None:
        while True:
            if not self._is_process_alive(pid):
                async with self._lock:
                    record = self._runs.get(run_id)
                    if record and record.status not in (
                        "stopped",
                        "succeeded",
                        "failed",
                    ):
                        record.status = "terminated"
                        record.finished_at = _utc_now()
                        self._save_run_sync(record)
                break
            await asyncio.sleep(1)

    def _save_run_sync(self, record: RunRecord) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, script, argv, status, pid, return_code,
                    created_at, started_at, finished_at, stdout_path, stderr_path, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    record.run_id,
                    record.script,
                    json.dumps(record.argv),
                    record.status,
                    record.pid,
                    record.return_code,
                    record.created_at,
                    record.started_at,
                    record.finished_at,
                    str(record.stdout_path),
                    str(record.stderr_path),
                    record.error,
                ),
            )
            conn.commit()

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
        self._save_run_sync(record)

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
            async with self._lock:
                record.status = "failed"
                record.error = str(e)
                record.finished_at = _utc_now()
            self._save_run_sync(record)
            return record

        async with self._lock:
            record._process = proc
            record._stdout_file = stdout_file
            record._stderr_file = stderr_file
            record.pid = proc.pid
            record.status = "running"
            record.started_at = _utc_now()
        self._save_run_sync(record)

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
            self._save_run_sync(record)
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
        self._save_run_sync(record)
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

    async def list_active_runs(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                r.to_public()
                for r in self._runs.values()
                if r.status in ("starting", "running", "stopping")
            ]

    async def get(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def stop(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return None

            if record.status not in {"running", "starting"}:
                return record

            record.status = "stopping"
            proc = record._process
            pid = record.pid

        self._save_run_sync(record)

        if proc:
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
                self._save_run_sync(record)
                return record

            try:
                rc = await asyncio.wait_for(
                    proc.wait(), timeout=self._terminate_timeout_seconds
                )
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
                self._save_run_sync(record)

            return record

        elif pid:
            try:
                if os.name != "nt":
                    os.killpg(pid, signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

            start_time = asyncio.get_running_loop().time()
            while True:
                if not self._is_process_alive(pid):
                    break
                if (
                    asyncio.get_running_loop().time() - start_time
                    > self._terminate_timeout_seconds
                ):
                    try:
                        if os.name != "nt":
                            os.killpg(pid, signal.SIGKILL)
                        else:
                            os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    break
                await asyncio.sleep(0.1)

            if not self._is_process_alive(pid):
                async with self._lock:
                    record.status = "stopped"
                    record.finished_at = _utc_now()
                self._save_run_sync(record)

            return record

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
