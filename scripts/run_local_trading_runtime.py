"""Local launcher for the trading runtime against the configured Binance sandbox.

Loads the repository .env files, provisions a local operator password when
none is configured, and starts the Control API + trading runtime. The password
is written to a temporary file and is never printed.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT / ".env.postgres", override=False)

from trader.api.auth import hash_password  # noqa: E402


def provision_password() -> None:
    if os.getenv("QTS_PASSWORD_HASH"):
        return
    target = Path(tempfile.gettempdir()) / "qts_operator_password.txt"
    password = os.getenv("QTS_LOCAL_PASSWORD")
    if not password and target.exists():
        # Reuse the local password across restarts so observation tooling keeps
        # a valid login without ever printing the secret.
        password = target.read_text(encoding="utf-8").strip()
    if not password:
        password = secrets.token_urlsafe(18)
        target.write_text(password, encoding="utf-8")
        print(f"[runbook] operator password stored in {target}")
    os.environ["QTS_PASSWORD_HASH"] = hash_password(password)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    provision_password()
    os.environ.setdefault("QTS_AUTH_REQUIRED", "1")
    os.environ.setdefault("QTS_ORIGIN", "http://127.0.0.1:5173")
    os.environ.setdefault("QTS_API_HOST", "127.0.0.1")
    os.environ.setdefault("QTS_TRADING_API_PORT", "8090")
    os.environ.setdefault("QTS_SYMBOLS", "BTCUSDT,ETHUSDT")
    os.environ.setdefault("QTS_RECONCILE_INTERVAL_SECONDS", "15")

    import uvicorn

    from trader.runtime.trading_main import app

    uvicorn.run(
        app,
        host=os.environ["QTS_API_HOST"],
        port=int(os.environ["QTS_TRADING_API_PORT"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
