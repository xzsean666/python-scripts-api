from __future__ import annotations

from datetime import datetime, timezone
import os
import sys


def main() -> None:
    print("hello from python-script-api demo script")
    print("python:", sys.executable)
    print("cwd:", os.getcwd())
    print("argv:", sys.argv)
    print("utc:", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    main()
