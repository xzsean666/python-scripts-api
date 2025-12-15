from __future__ import annotations

import signal
import time


_stop = False


def _handle(sig: int, _frame) -> None:
    global _stop
    _stop = True
    print(f"received signal {sig}, stopping...", flush=True)


def main() -> None:
    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    print("long_task start", flush=True)
    i = 0
    while not _stop:
        i += 1
        print(f"tick {i}", flush=True)
        time.sleep(1)
    print("long_task end", flush=True)


if __name__ == "__main__":
    main()

