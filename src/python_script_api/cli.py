from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from . import __version__
from .config import load_settings
from .dotenv import load_dotenv


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python-script-api")
    parser.add_argument("--version", action="version", version=__version__)

    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the REST API server")
    serve.add_argument("--scripts-path", required=False, help="Scripts root directory")
    serve.add_argument("--state-dir", required=False, help="State directory (logs, etc)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--env-file", default=".env")
    serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "serve":
        env_file = args.env_file if args.env_file not in {None, ""} else None
        load_dotenv(env_file, override=False)

        settings = load_settings(
            scripts_path=args.scripts_path,
            state_dir=args.state_dir,
            host=args.host,
            port=args.port,
        )

        if not settings.scripts_root.exists() or not settings.scripts_root.is_dir():
            print(
                f"ERROR: scripts root is not a directory: {settings.scripts_root}",
                file=sys.stderr,
            )
            raise SystemExit(2)

        if settings.jwt_auth and not settings.jwt_secret:
            print(
                "ERROR: SCRIPT_JWT_AUTH=true but SCRIPT_JWT_SECRET is not configured",
                file=sys.stderr,
            )
            raise SystemExit(2)

        if settings.jwt_admin_secret and not settings.jwt_secret:
            print(
                "ERROR: SCRIPT_JWT_ADMIN_SECRET set but SCRIPT_JWT_SECRET is not configured",
                file=sys.stderr,
            )
            raise SystemExit(2)

        os.environ["SCRIPT_SCRIPTS_PATH"] = str(settings.scripts_root)
        os.environ["SCRIPT_STATE_DIR"] = str(settings.state_dir)
        os.environ["SCRIPT_LOGS_DIR"] = str(settings.logs_dir)
        os.environ["SCRIPT_API_PREFIX"] = settings.api_prefix

        uvicorn.run(
            "python_script_api.app:app",
            host=settings.host,
            port=settings.port,
            reload=bool(args.reload),
        )
