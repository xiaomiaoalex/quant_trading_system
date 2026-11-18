"""Real-browser login smoke for an isolated branch-preview stack.

This script never starts production services and never uses venue credentials.
It expects an already-started preview at ``--origin`` and a generated operator
password supplied through ``--password-file``.  The password is sent only to
the local preview login endpoint and is never written to evidence.

The browser checks cover: unauthenticated deep link rejection, wrong-password
rejection, successful login, identity on a protected read-only view, and the
expected DR-blocked trading state.  A health 200 alone is not accepted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ALLOWED_ORIGINS = {"http://127.0.0.1", "http://localhost"}
REQUIRED_CHECKS = (
    "unauthenticated_deep_link_rejected",
    "wrong_password_rejected",
    "login_succeeded",
    "identity_visible",
    "protected_readonly_accessible",
    "blocked_state_visible",
)


def browser_available() -> tuple[bool, str]:
    """Return whether a real browser runtime is available, without faking one."""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as exc:  # dependency missing or broken installation
        return False, f"PLAYWRIGHT_UNAVAILABLE: {exc}"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"PLAYWRIGHT_BROWSER_CHECK_FAILED: {exc}"
    if result.returncode != 0:
        return (
            False,
            f"PLAYWRIGHT_BROWSER_UNAVAILABLE: {(result.stderr or result.stdout).strip()[:300]}",
        )
    return True, "ok"


def _safe_origin(value: str) -> str | None:
    parsed = urlparse(value)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None
    return origin


def _read_password(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("PASSWORD_FILE_EMPTY")
    return value


def _run_browser_checks(
    *,
    origin: str,
    username: str,
    password: str,
    paths: dict[str, str],
) -> dict[str, Any]:
    """Execute the checks in a real Chromium page.

    Imported lazily so static contract tests can inspect the plan without a
    browser installation.  The page never stores cookies or traces on disk.
    """
    from playwright.sync_api import sync_playwright

    outcomes: dict[str, Any] = {
        name: {"ok": False, "detail": "not_started"} for name in REQUIRED_CHECKS
    }
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(base_url=origin, viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        try:
            page.goto(paths["deep_link"], wait_until="domcontentloaded", timeout=30_000)
            body = page.locator("body").inner_text()
            outcomes["unauthenticated_deep_link_rejected"]["ok"] = (
                "Password" in body and "Sign in" in body
            )
            outcomes["unauthenticated_deep_link_rejected"]["detail"] = "login form required"

            page.get_by_label("Password").fill(password + "-definitely-wrong")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text()
            outcomes["wrong_password_rejected"]["ok"] = "Sign in" in body and "console" not in body
            outcomes["wrong_password_rejected"]["detail"] = "still on login form"

            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_timeout(2500)
            body = page.locator("body").inner_text()
            outcomes["login_succeeded"]["ok"] = "Sign in" not in body and len(body.strip()) > 0
            outcomes["login_succeeded"]["detail"] = "login form dismissed"

            page.goto(paths["profile"], wait_until="domcontentloaded", timeout=30_000)
            profile_body = page.locator("body").inner_text()
            outcomes["identity_visible"]["ok"] = username in profile_body
            outcomes["identity_visible"]["detail"] = "operator identity displayed"

            response = page.goto(paths["readonly"], wait_until="domcontentloaded", timeout=30_000)
            outcomes["protected_readonly_accessible"]["ok"] = bool(
                response and response.status < 400
            )
            outcomes["protected_readonly_accessible"][
                "detail"
            ] = f"status={response.status if response else None}"

            trading_body = (
                page.locator("body").inner_text()
                if page.goto(paths["trading"], wait_until="domcontentloaded", timeout=30_000)
                else ""
            )
            outcomes["blocked_state_visible"]["ok"] = (
                "DR_EVIDENCE_MISSING" in trading_body or "blocked" in trading_body.lower()
            )
            outcomes["blocked_state_visible"]["detail"] = "real blocked state required"
        finally:
            outcomes["page_errors"] = page_errors
            context.close()
            browser.close()
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--username", default="owner")
    parser.add_argument("--deep-link", default="/portfolio/research")
    parser.add_argument("--profile-path", default="/settings")
    parser.add_argument("--readonly-path", default="/health/trading")
    parser.add_argument("--trading-path", default="/trading")
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "origin": args.origin,
        "username": args.username,
        "checks": {},
        "page_errors": [],
        "problems": [],
    }

    origin = _safe_origin(args.origin)
    if origin is None:
        report["problems"].append("ORIGIN_NOT_LOOPBACK")
    available, reason = browser_available()
    report["browser_available"] = available
    report["browser_reason"] = reason
    if not available:
        report["problems"].append(reason)

    if not report["problems"]:
        try:
            password = _read_password(args.password_file)
        except (OSError, ValueError) as exc:
            report["problems"].append(f"PASSWORD_FILE_UNREADABLE: {exc}")
        else:
            try:
                report["checks"] = _run_browser_checks(
                    origin=origin,
                    username=args.username,
                    password=password,
                    paths={
                        "deep_link": args.deep_link,
                        "profile": args.profile_path,
                        "readonly": args.readonly_path,
                        "trading": args.trading_path,
                    },
                )
                report["page_errors"] = report["checks"].pop("page_errors", [])
                for name in REQUIRED_CHECKS:
                    if report["checks"].get(name, {}).get("ok") is not True:
                        report["problems"].append(f"BROWSER_CHECK_FAILED: {name}")
                if report["page_errors"]:
                    report["problems"].append("BROWSER_PAGE_ERRORS")
            except Exception as exc:
                # A browser launch/runtime failure is evidence, never a falsified
                # pass or an unhandled traceback.  The password never enters
                # this report.
                report["problems"].append(f"BROWSER_RUNTIME_FAILED: {type(exc).__name__}")

    report["ok"] = not report["problems"]
    report["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"ok": report["ok"], "problems": report["problems"], "evidence": str(args.evidence)},
            ensure_ascii=False,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
