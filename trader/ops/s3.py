"""Small S3-compatible object-storage adapter for backup evidence."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


class S3TargetError(RuntimeError):
    """Stable operational failure code; never contains credentials."""


def _required(environ: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(environ.get(name, "") or "").strip()
        if value:
            return value
    raise S3TargetError("S3_CONFIGURATION_MISSING:" + names[0])


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str
    bucket: str
    prefix: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "S3Config":
        env = os.environ if environ is None else environ
        endpoint = _required(env, "QTS_S3_ENDPOINT_URL")
        bucket = _required(env, "QTS_S3_BUCKET")
        return cls(
            endpoint_url=endpoint,
            bucket=bucket,
            prefix=str(env.get("QTS_S3_PREFIX", "qts/backups")).strip("/"),
            access_key=_required(env, "QTS_S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
            secret_key=_required(env, "QTS_S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
            region=str(env.get("QTS_S3_REGION", env.get("AWS_DEFAULT_REGION", "us-east-1"))),
        )

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.endpoint_url).hostname or ""

    def identity(self) -> dict[str, Any]:
        return {
            "kind": "s3_compatible",
            "endpoint_host": self.endpoint_host,
            "bucket": self.bucket,
            "prefix": self.prefix,
        }


class S3Target:
    def __init__(self, config: S3Config):
        self.config = config
        self._client = None

    def _client_instance(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise S3TargetError("S3_CLIENT_UNAVAILABLE") from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint_url,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

    def upload(self, path: str | os.PathLike[str], key: str, metadata: dict[str, str]) -> dict:
        candidate = Path(path)
        try:
            with candidate.open("rb") as handle:
                self._client_instance().put_object(
                    Bucket=self.config.bucket,
                    Key=key,
                    Body=handle,
                    Metadata={str(k): str(v) for k, v in metadata.items()},
                    ContentType="application/octet-stream",
                )
        except S3TargetError:
            raise
        except Exception as exc:
            raise S3TargetError(f"S3_UPLOAD_FAILED:{type(exc).__name__}") from exc
        return {"uploaded_at_ms": int(time.time() * 1000), "object_key": key}

    def head(self, key: str) -> dict:
        try:
            response = self._client_instance().head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            raise S3TargetError(f"S3_HEAD_FAILED:{type(exc).__name__}") from exc
        return {
            "content_length": int(response.get("ContentLength", -1)),
            # S3 servers differ in metadata key casing (e.g. rclone returns
            # ``Sha256``); normalise for the receipt comparison.
            "metadata": {
                str(k).lower(): str(v) for k, v in (response.get("Metadata") or {}).items()
            },
            "etag": str(response.get("ETag", "")).strip('"'),
            "last_modified_ms": (
                int(response["LastModified"].timestamp() * 1000)
                if response.get("LastModified")
                else None
            ),
        }

    def download_and_hash(self, key: str, destination: str | os.PathLike[str]) -> dict:
        """Download the object and hash the received bytes.

        Metadata echoed by the server is not integrity evidence by itself; this
        reads back the stored object and hashes what the target actually holds.
        """
        target = Path(destination)
        digest = hashlib.sha256()
        size = 0
        try:
            body = self._client_instance().get_object(Bucket=self.config.bucket, Key=key)["Body"]
            with target.open("wb") as handle:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        except S3TargetError:
            raise
        except Exception as exc:
            raise S3TargetError(f"S3_DOWNLOAD_FAILED:{type(exc).__name__}") from exc
        return {
            "path": str(target),
            "sha256": digest.hexdigest(),
            "size_bytes": size,
            "verified_at_ms": int(time.time() * 1000),
        }

    def delete(self, key: str) -> None:
        try:
            self._client_instance().delete_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            raise S3TargetError(f"S3_DELETE_FAILED:{type(exc).__name__}") from exc
