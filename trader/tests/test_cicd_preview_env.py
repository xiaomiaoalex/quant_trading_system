"""Contract tests for the fail-closed preview environment validator.

The Compose dotenv parser expands ``$NAME`` inside unquoted values.  A
``scrypt$<salt>$<hash>`` password hash written without quotes is silently
truncated to ``scrypt`` and every preview login fails, so the validator must
reject that form instead of accepting it and letting the deploy break later.
"""

from __future__ import annotations

from pathlib import Path

from scripts.validate_preview_env import parse_env_file, validate

SALT = "0123456789abcdef0123456789abcdef"
DIGEST = "f" * 128
HASH = f"scrypt${SALT}${DIGEST}"


def _base_values() -> dict[str, str]:
    return {
        "QTS_PREVIEW_ID": "prev-test-1",
        "QTS_PREVIEW_COMMIT": "a" * 40,
        "QTS_PREVIEW_RUNTIME_IMAGE": "qts-runtime:preview-test",
        "QTS_PREVIEW_RUNTIME_IMAGE_ID": "sha256:" + "b" * 64,
        "QTS_PREVIEW_UI_IMAGE": "qts-preview-ui:preview-test",
        "QTS_PREVIEW_POSTGRES_PASSWORD": "pg_not_a_default_1234567890",
        "QTS_PREVIEW_PASSWORD_HASH": HASH,
    }


def _write(tmp_path: Path, hash_line: str) -> tuple[dict[str, str], dict[str, str]]:
    values = _base_values()
    lines = [
        f"{key}={value}" for key, value in values.items() if key != "QTS_PREVIEW_PASSWORD_HASH"
    ]
    lines.append(hash_line)
    path = tmp_path / "preview.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    parsed = parse_env_file(path)
    raw: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            raw[key.strip()] = value.strip()
    return parsed, raw


def test_single_quoted_password_hash_is_accepted(tmp_path):
    parsed, raw = _write(tmp_path, f"QTS_PREVIEW_PASSWORD_HASH='{HASH}'")
    assert parsed["QTS_PREVIEW_PASSWORD_HASH"] == HASH
    assert validate(parsed, raw) == []


def test_dollar_escaped_password_hash_is_accepted(tmp_path):
    escaped = HASH.replace("$", "$$")
    parsed, raw = _write(tmp_path, f"QTS_PREVIEW_PASSWORD_HASH={escaped}")
    assert validate(parsed, raw) == []


def test_unquoted_password_hash_is_rejected(tmp_path):
    """The plain form parses cleanly but Compose would expand it."""
    parsed, raw = _write(tmp_path, f"QTS_PREVIEW_PASSWORD_HASH={HASH}")
    problems = validate(parsed, raw)
    assert any("unquoted $ in the dotenv value" in problem for problem in problems), problems


def test_invalid_password_hash_shape_is_rejected(tmp_path):
    parsed, raw = _write(tmp_path, "QTS_PREVIEW_PASSWORD_HASH='scrypt$deadbeef$cafe'")
    assert any("not a scrypt$<salt>$<hash> value" in problem for problem in validate(parsed, raw))


def test_placeholder_values_are_rejected(tmp_path):
    values = _base_values()
    values["QTS_PREVIEW_ID"] = "REPLACE_WITH_PREVIEW_ID"
    values["QTS_PREVIEW_PASSWORD_HASH"] = "'REPLACE_WITH_GENERATED_SCRYPT_HASH'"
    problems = validate(values, {"QTS_PREVIEW_PASSWORD_HASH": values["QTS_PREVIEW_PASSWORD_HASH"]})
    assert any("placeholder value" in problem for problem in problems)


def test_non_loopback_origin_is_rejected():
    values = _base_values()
    values["QTS_PREVIEW_ORIGIN"] = "http://192.168.0.105:18080"
    problems = validate(values, {"QTS_PREVIEW_PASSWORD_HASH": f"'{HASH}'"})
    assert any("loopback HTTP origin" in problem for problem in problems)


def test_weak_database_password_is_rejected():
    values = _base_values()
    values["QTS_PREVIEW_POSTGRES_PASSWORD"] = "trader_pwd"
    problems = validate(values, {"QTS_PREVIEW_PASSWORD_HASH": f"'{HASH}'"})
    assert any("well-known default password" in problem for problem in problems)


def test_short_commit_is_rejected():
    values = _base_values()
    values["QTS_PREVIEW_COMMIT"] = "abc123"
    problems = validate(values, {"QTS_PREVIEW_PASSWORD_HASH": f"'{HASH}'"})
    assert any("40 character hex commit" in problem for problem in problems)
