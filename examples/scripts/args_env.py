from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="world")
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args()

    for i in range(args.count):
        print(f"[{i+1}/{args.count}] hello {args.name}")

    print("env.FOO:", os.getenv("FOO"))
    print("argv:", sys.argv)


if __name__ == "__main__":
    main()

