#!/bin/sh
# Fail-closed startup check for the branch-preview UI container.
#
# The preview console reports the release identity of the runtime it talks to.
# Starting with placeholder metadata would publish a fabricated identity in
# /health/trading, so the container refuses to start instead.
#
# The check is deliberately small and POSIX-only: the runtime image is nginx
# based and has no other interpreter.

set -eu

fail() {
    echo "$1" >&2
    exit "$2"
}

require_value() {
    name="$1"
    value="${2:-}"
    [ -n "$value" ] || fail "PREVIEW_METADATA_MISSING:${name}" 2
    case "$value" in
        *REPLACE_WITH* | *CHANGE_ME* | *PLACEHOLDER* | *TODO*)
            fail "PREVIEW_METADATA_PLACEHOLDER:${name}" 3
            ;;
    esac
}

require_value QTS_PREVIEW_ID "${QTS_PREVIEW_ID:-}"
require_value QTS_PREVIEW_COMMIT "${QTS_PREVIEW_COMMIT:-}"
require_value QTS_PREVIEW_RUNTIME_IMAGE_ID "${QTS_PREVIEW_RUNTIME_IMAGE_ID:-}"

case "$QTS_PREVIEW_COMMIT" in
    *[!0-9a-f]*) fail "PREVIEW_COMMIT_INVALID:${QTS_PREVIEW_COMMIT}" 4 ;;
esac
[ "${#QTS_PREVIEW_COMMIT}" -eq 40 ] || fail "PREVIEW_COMMIT_INVALID:${QTS_PREVIEW_COMMIT}" 4

case "$QTS_PREVIEW_RUNTIME_IMAGE_ID" in
    sha256:*) ;;
    *) fail "PREVIEW_IMAGE_ID_INVALID:${QTS_PREVIEW_RUNTIME_IMAGE_ID}" 5 ;;
esac
[ "${#QTS_PREVIEW_RUNTIME_IMAGE_ID}" -eq 71 ] || \
    fail "PREVIEW_IMAGE_ID_INVALID:${QTS_PREVIEW_RUNTIME_IMAGE_ID}" 5

exec "$@"
