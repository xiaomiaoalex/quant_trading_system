"""Fail-closed validation of a branch-preview environment file.

The preview overlay starts a real Control API, a real PostgreSQL and a real
console.  A preview that starts with placeholder metadata is worse than one
that refuses to start: the console would report a fabricated release identity
and a fabricated image digest in ``/health/trading``.

This validator therefore rejects, before anything is started:

* missing or empty required values;
* placeholder values (``REPLACE_WITH``, ``CHANGE_ME``, ``example.com`` ...);
* a commit that is not a full 40 character hex sha;
* a runtime image id that is not ``sha256:<64 hex>``;
* a preview origin that is not a loopback HTTP origin;
* a database password that is still the base default.

With ``--check-images`` it additionally compares the recorded runtime image id
with the id of the image that is actually present on the host, so the preview
is bound to one concrete build instead of a remembered tag.

The file-only mode needs no Docker, so the review and test loop can verify the
contract without a container runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_VALUES = (
    "QTS_PREVIEW_ID",
    "QTS_PREVIEW_COMMIT",
    "QTS_PREVIEW_RUNTIME_IMAGE",
    "QTS_PREVIEW_RUNTIME_IMAGE_ID",
    "QTS_PREVIEW_UI_IMAGE",
    "QTS_PREVIEW_POSTGRES_PASSWORD",
    "QTS_PREVIEW_PASSWORD_HASH",
)
PLACEHOLDER_MARKERS = (
    "REPLACE_WITH",
    "CHANGE_ME",
    "CHANGEME",
    "PLACEHOLDER",
    "example.com",
    "EXAMPLE",
    "TODO",
)
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PASSWORD_HASH_RE = re.compile(r"^scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}$")
LOOPBACK_ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
# The base Compose file still carries this default; a preview must never use it.
WEAK_PASSWORDS = {"trader_pwd", "postgres", "password", "qts"}


def _unquote_single(value: str) -> str:
    """Strip one pair of surrounding single quotes.

    Compose interpolates ``$NAME`` inside an unquoted (or double-quoted) dotenv
    value, which silently truncates a ``scrypt$<salt>$<hash>`` password hash to
    the ``scrypt`` prefix.  Single quotes are the only dotenv form that keeps
    the value literal, so the validator must accept them; double quotes are
    deliberately left untouched because they do *not* prevent interpolation.
    """
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return value


def _env_pairs(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs.append((key.strip(), value.strip()))
    return pairs


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a ``KEY=VALUE`` environment file, ignoring comments and blanks."""
    return {key: _unquote_single(value) for key, value in _env_pairs(path)}


def _compose_keeps_literal(raw: str) -> bool:
    """True when Compose will not expand ``$NAME`` inside this dotenv value."""
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return True
    # Every ``$`` must be half of a ``$$`` escape.
    return raw.count("$") == 2 * raw.count("$$")


def _is_placeholder(value: str) -> str | None:
    upper = value.upper()
    for marker in PLACEHOLDER_MARKERS:
        if marker.upper() in upper:
            return marker
    return None


def validate(values: dict[str, str], raw_values: dict[str, str] | None = None) -> list[str]:
    """Return every problem found in the preview environment."""
    problems: list[str] = []
    for key in REQUIRED_VALUES:
        value = values.get(key, "")
        if not value:
            problems.append(f"{key}: missing or empty")
            continue
        marker = _is_placeholder(value)
        if marker is not None:
            problems.append(f"{key}: placeholder value ({marker})")

    commit = values.get("QTS_PREVIEW_COMMIT", "")
    if commit and not COMMIT_RE.match(commit):
        problems.append("QTS_PREVIEW_COMMIT: not a 40 character hex commit")

    image_id = values.get("QTS_PREVIEW_RUNTIME_IMAGE_ID", "")
    if image_id and not IMAGE_ID_RE.match(image_id):
        problems.append("QTS_PREVIEW_RUNTIME_IMAGE_ID: not sha256:<64 hex>")

    password_hash = values.get("QTS_PREVIEW_PASSWORD_HASH", "")
    # ``$$`` is the Compose dotenv escape for one literal ``$``; accepting it
    # keeps the escaped form (which renders correctly) from being rejected.
    if password_hash and not PASSWORD_HASH_RE.match(password_hash.replace("$$", "$")):
        problems.append(
            "QTS_PREVIEW_PASSWORD_HASH: not a scrypt$<salt>$<hash> value; "
            "quote it with single quotes so Compose does not expand the $ parts"
        )
    raw_hash = (raw_values or {}).get("QTS_PREVIEW_PASSWORD_HASH", "")
    if password_hash and raw_hash and not _compose_keeps_literal(raw_hash):
        problems.append(
            "QTS_PREVIEW_PASSWORD_HASH: unquoted $ in the dotenv value; Compose "
            "would expand it and truncate the hash - wrap the value in single "
            "quotes or escape each $ as $$"
        )

    origin = values.get("QTS_PREVIEW_ORIGIN", "")
    if origin and not LOOPBACK_ORIGIN_RE.match(origin):
        problems.append(
            "QTS_PREVIEW_ORIGIN: must be a loopback HTTP origin "
            "(http://127.0.0.1[:port]) so the session cookie stays loopback-only"
        )

    password = values.get("QTS_PREVIEW_POSTGRES_PASSWORD", "")
    if password and password.lower() in WEAK_PASSWORDS:
        problems.append("QTS_PREVIEW_POSTGRES_PASSWORD: still a well-known default password")

    port = values.get("QTS_PREVIEW_HTTP_PORT", "")
    if port and not port.isdigit():
        problems.append("QTS_PREVIEW_HTTP_PORT: not a port number")
    return problems


def actual_image_id(image: str) -> str | None:
    """Return the image id the host actually has, or ``None`` when unknown."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def check_images(values: dict[str, str]) -> list[str]:
    """Compare the recorded identity with the images present on the host."""
    problems: list[str] = []
    recorded = values.get("QTS_PREVIEW_RUNTIME_IMAGE_ID", "")
    image = values.get("QTS_PREVIEW_RUNTIME_IMAGE", "")
    if image and recorded:
        actual = actual_image_id(image)
        if actual is None:
            problems.append(
                f"QTS_PREVIEW_RUNTIME_IMAGE: {image} is not present on this host; "
                "the preview host must load the image instead of pulling it"
            )
        elif actual != recorded:
            problems.append(
                "QTS_PREVIEW_RUNTIME_IMAGE_ID: recorded " f"{recorded} but {image} is {actual}"
            )
    ui_image = values.get("QTS_PREVIEW_UI_IMAGE", "")
    if ui_image and actual_image_id(ui_image) is None:
        problems.append(
            f"QTS_PREVIEW_UI_IMAGE: {ui_image} is not present on this host; "
            "the preview host must load the image instead of pulling it"
        )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a branch-preview environment file")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument(
        "--check-images",
        action="store_true",
        help="also compare the recorded image id with the image present on this host",
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    problems: list[str] = []
    if not args.env_file.is_file():
        problems.append(f"env file not found: {args.env_file}")
        values: dict[str, str] = {}
    else:
        values = parse_env_file(args.env_file)
        raw_values = dict(_env_pairs(args.env_file))
        problems.extend(validate(values, raw_values))
        if args.check_images:
            problems.extend(check_images(values))

    report = {
        "env_file": str(args.env_file),
        "checked_images": bool(args.check_images),
        "problems": problems,
        "ok": not problems,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if not problems else 1)


if __name__ == "__main__":
    main()
