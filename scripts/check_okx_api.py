"""Safely validate an OKX API key and inspect its declared permissions.

This script is strictly read-only: it never submits orders or changes account settings.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from perpmirror.exceptions import PerpMirrorError
from perpmirror.exchanges.okx import OkxSwapClient
from perpmirror.logging_utils import SecretRedactionFilter, configure_logging
from perpmirror.okx_check import mask_key, parse_permissions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OKX API authentication and permission check")
    parser.add_argument("--env", default=".env", help="dotenv file; default: .env")
    parser.add_argument("--api-key-env", default="OKX_API_KEY")
    parser.add_argument("--secret-key-env", default="OKX_SECRET_KEY")
    parser.add_argument("--passphrase-env", default="OKX_PASSPHRASE")
    parser.add_argument("--demo", action="store_true", help="check an OKX Demo Trading API key")
    return parser.parse_args()


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is empty: {name}")
    return value


async def check(args: argparse.Namespace) -> int:
    load_dotenv(Path(args.env), override=False)
    api_key = required_env(args.api_key_env)
    secret_key = required_env(args.secret_key_env)
    passphrase = required_env(args.passphrase_env)
    client = OkxSwapClient(
        api_key,
        secret_key,
        passphrase,
        demo_trading=args.demo,
    )
    try:
        offset = await client.sync_time()
        instruments = await client.get_instruments()
        account = await client.get_account_configuration()
        permissions = parse_permissions(account.get("perm"))
        equity = await client.get_equity()
        positions = await client.get_positions()
    finally:
        await client.close()

    print("OKX API CHECK: AUTHENTICATED")
    print(f"environment={'DEMO' if args.demo else 'PRODUCTION'}")
    print(f"api_key={mask_key(api_key)}")
    print(f"clock_offset_ms={offset}")
    print(f"usdt_swap_instruments={len(instruments)}")
    print(f"account_level={account.get('acctLv', 'unknown')}")
    print(f"position_mode={account.get('posMode', 'unknown')}")
    print(f"permissions={','.join(sorted(permissions)) or 'unknown'}")
    print(f"equity_usd={equity}")
    print(f"open_positions={len(positions)}")
    if "withdraw" in permissions:
        print("SECURITY WARNING: this key has WITHDRAW permission; revoke it and create a safer key")
    if "trade" not in permissions:
        print("RESULT: AUTHENTICATION OK, TRADE PERMISSION MISSING")
        return 4
    print("RESULT: AUTHENTICATION OK, TRADE PERMISSION PRESENT")
    return 0


def main() -> None:
    args = parse_args()
    configure_logging()
    try:
        code = asyncio.run(check(args))
    except (PerpMirrorError, OSError, ValueError) as exc:
        print(f"RESULT: FAILED: {SecretRedactionFilter.redact(exc)}")
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
