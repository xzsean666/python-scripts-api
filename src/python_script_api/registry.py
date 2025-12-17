from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ScriptInfo:
    path: str
    absolute_path: Path
    size_bytes: int
    mtime: float


def _should_ignore_path(path: Path, root: Path) -> bool:
    for part in path.relative_to(root).parts:
        if part in {"__pycache__", ".git", ".venv", "venv", "env", "node_modules"}:
            return True
        if part.startswith("."):
            return True
    return False


def scan_scripts(root: Path) -> list[ScriptInfo]:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        return []

    scripts: list[ScriptInfo] = []
    for candidate in resolved_root.rglob("*.py"):
        if not candidate.is_file():
            continue
        if _should_ignore_path(candidate, resolved_root):
            continue
        if candidate.name.startswith("_"):
            continue
        stat = candidate.stat()
        scripts.append(
            ScriptInfo(
                path=candidate.relative_to(resolved_root).as_posix(),
                absolute_path=candidate,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )
        )

    scripts.sort(key=lambda s: s.path)
    return scripts


def resolve_script(root: Path, script_path: str) -> Path:
    resolved_root = root.expanduser().resolve()
    candidate = (resolved_root / script_path).expanduser().resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError("script_path must be under scripts_root")
    if candidate.suffix != ".py":
        raise ValueError("script_path must point to a .py file")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(script_path)
    return candidate

