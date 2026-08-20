from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from perpmirror.app import PerpMirrorApp
from perpmirror.config import load_config
from perpmirror.exceptions import PerpMirrorError
from perpmirror.logging_utils import configure_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PerpMirror target-position perpetual copier")
    parser.add_argument("--config", default="config.yaml", help="YAML configuration path")
    parser.add_argument("--env", default=".env", help="dotenv path")
    parser.add_argument(
        "--dry-run", action="store_true", help="force dry-run even if configuration says live"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check-config", action="store_true", help="run authenticated startup checks and exit"
    )
    group.add_argument("--once", action="store_true", help="run exactly one full reconciliation and exit")
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    settings = load_config(args.config, args.env, force_dry_run=args.dry_run)
    app = PerpMirrorApp(settings)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        with __import__("contextlib").suppress(NotImplementedError):
            loop.add_signal_handler(name, app.request_stop)
    try:
        await app.run(once=args.once, check_only=args.check_config)
        return 0
    finally:
        await app.close()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging()
    try:
        exit_code = asyncio.run(async_main(args))
    except (PerpMirrorError, OSError, ValueError) as exc:
        logging.getLogger(__name__).error("startup_failed error=%s", exc)
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main(sys.argv[1:])
