from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


_stop = False


def _handle(sig: int, _frame) -> None:
    global _stop
    _stop = True
    print(f"parent received signal {sig}", flush=True)


def main() -> None:
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    print("parent pid:", os.getpid(), flush=True)

    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import os,time; print('child pid:', os.getpid(), flush=True); time.sleep(1000)",
        ],
    )
    print("spawned child pid:", child.pid, flush=True)

    while not _stop:
        time.sleep(1)


if __name__ == "__main__":
    main()

