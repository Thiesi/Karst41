"""Entry point for `python -m karst41.server --install-dir DIR`."""

import argparse
import asyncio
import os
import signal
import sqlite3
import sys
from pathlib import Path

from karst41.server.app import DEADLINE_EXIT, Server


async def run(args: argparse.Namespace, server: Server | None = None) -> None:
    if server is None:
        server = Server(args.install_dir, args.tcp, args.log_level, spawn_zone=args.spawn_zone)
    previous: dict[signal.Signals, object] = {}

    def stop(signum: int, frame: object) -> None:
        server.request_stop()

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        previous[sig] = signal.signal(sig, stop)
    try:
        await server.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='karst41-server', description="Karst 41 server")
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--tcp", action="store_true")
    parser.add_argument("--spawn-zone", help="preview a supplied zone before character entry")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    # #277 (D5-GC): the production start-up path freezes the start-up heap (server/heap.py).
    server = Server(args.install_dir, args.tcp, args.log_level, spawn_zone=args.spawn_zone, freeze_heap=True)
    code = 0
    try:
        asyncio.run(run(args, server))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"karst41 server: {exc}", file=sys.stderr)
        code = 1
    if server.exit_code == DEADLINE_EXIT:
        # Architecture §3.4: the writer may still be inside an fsync, holding
        # the connection and the log lock. End the process now; the OS drops
        # the lease and WAL recovery applies at the next start.
        print("karst41 server: stopped at the deadline", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(DEADLINE_EXIT)
    return code or server.exit_code  # A lost writer exits 1 (restartable) even without an exception.


if __name__ == "__main__":
    sys.exit(main())
