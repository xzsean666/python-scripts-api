from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    api_prefix: str
    scripts_root: Path
    state_dir: Path
    logs_dir: Path
    host: str
    port: int

    jwt_auth: bool
    jwt_secret: str | None
    jwt_iss: str
    jwt_aud: str
    jwt_leeway_seconds: int
    jwt_expire_seconds: int
    jwt_admin_secret: str | None

    terminate_timeout_seconds: int


def load_settings(
    *,
    scripts_path: str | Path | None = None,
    state_dir: str | Path | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Settings:
    api_prefix = _env("SCRIPT_API_PREFIX", "/v1") or "/v1"

    scripts_root_raw = (
        str(scripts_path)
        if scripts_path is not None
        else (_env("SCRIPT_SCRIPTS_PATH") or _env("SCRIPTS_PATH"))
    )
    if not scripts_root_raw:
        scripts_root_raw = "."
    scripts_root = Path(scripts_root_raw).expanduser()

    state_dir_raw = (
        str(state_dir)
        if state_dir is not None
        else (_env("SCRIPT_STATE_DIR") or ".quant-script-api")
    )
    state_dir_path = Path(state_dir_raw).expanduser()

    logs_dir_raw = _env("SCRIPT_LOGS_DIR")
    logs_dir_path = (
        Path(logs_dir_raw).expanduser() if logs_dir_raw else (state_dir_path / "logs")
    )

    resolved_host = host or (_env("SCRIPT_HOST", "127.0.0.1") or "127.0.0.1")
    resolved_port = port if port is not None else _env_int("SCRIPT_PORT", 8000)

    jwt_auth = _env_bool("SCRIPT_JWT_AUTH", False)
    jwt_secret = (
        _env("SCRIPT_JWT_SECRET")
        or _env("SCRIPT_JWT_SECRETE")  # common typo
        or None
    )
    jwt_iss = _env("SCRIPT_JWT_ISS", "quant-script-api") or "quant-script-api"
    jwt_aud = _env("SCRIPT_JWT_AUD", "quant-internal") or "quant-internal"
    jwt_leeway_seconds = _env_int("SCRIPT_JWT_LEEWAY_SECONDS", 30)
    jwt_expire_seconds = _env_int("SCRIPT_JWT_EXPIRE_SECONDS", _env_int("SCRIPT_JWT_EXPIRE", 3600))
    jwt_admin_secret = (
        _env("SCRIPT_JWT_ADMIN_SECRET")
        or _env("SCRIPT_JWT_ADMIN_SECRETE")  # common typo
        or None
    )

    terminate_timeout_seconds = _env_int("SCRIPT_TERMINATE_TIMEOUT_SECONDS", 10)

    return Settings(
        api_prefix=api_prefix,
        scripts_root=scripts_root,
        state_dir=state_dir_path,
        logs_dir=logs_dir_path,
        host=resolved_host,
        port=resolved_port,
        jwt_auth=jwt_auth,
        jwt_secret=jwt_secret,
        jwt_iss=jwt_iss,
        jwt_aud=jwt_aud,
        jwt_leeway_seconds=jwt_leeway_seconds,
        jwt_expire_seconds=jwt_expire_seconds,
        jwt_admin_secret=jwt_admin_secret,
        terminate_timeout_seconds=terminate_timeout_seconds,
    )

