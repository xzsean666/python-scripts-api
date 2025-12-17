from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path | None, *, override: bool = False) -> bool:
    if path is None:
        return False
    dotenv_path = Path(path)
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return False

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if not override and key in os.environ:
            continue
        os.environ[key] = value

    return True

