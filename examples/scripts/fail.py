from __future__ import annotations

import sys


def main() -> None:
    print("this script will fail with exit code 2")
    print("something went wrong", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

