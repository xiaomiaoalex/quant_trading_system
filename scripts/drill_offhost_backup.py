"""Real off-host S3 backup and restore drill over a strict SSH tunnel.

The designated machine hosts a task-owned rclone S3 service bound to its
loopback interface. The local client reaches it through an SSH local forward;
evidence records the real remote host, the tunnel endpoint, and the strict
host-key verification basis. It never relabels the local emulator as off-host.

The source and restore databases are fresh task-owned PostgreSQL resources.
The remote bucket/objects are retained on the designated machine.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402

from trader.ops.backup import (  # noqa: E402
    BackupConfig,
    ContainerPgDumpRunner,
    backup_once,
    summarize_rpo,
)
from trader.ops.crash_durability import write_committed_facts  # noqa: E402
from trader.ops.dr_evidence import evaluate_dr_evidence_file  # noqa: E402
from trader.ops.restore import (  # noqa: E402
    ContainerPgRestoreRunner,
    RestoreConfig,
    restore_and_verify,
    write_dr_evidence,
)
from trader.ops.s3 import S3Config, S3Target  # noqa: E402
from trader.ops.ssh_target import (  # noqa: E402
    RemoteTargetNotConfigured,
    resolve_host_key_alias,
    resolve_remote_host,
    ssh_base_argv,
)

RCLONE_SOURCE = "https://downloads.rclone.org/rclone-current-linux-amd64.zip"
RCLONE_VERSION = "v1.75.2"
RCLONE_BINARY_SHA256 = "ed7ab1f17876bb006c3df3949a3044959671afd9112362dacaa5dd1647735bc1"
RCLONE_ARCHIVE_SHA256 = "349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702"




def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"SSH_TUNNEL_NOT_READY:{port}")


def _run(argv: list[str], *, timeout: float = 120, input_bytes: bytes | None = None):
    return subprocess.run(
        argv,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _ssh(
    destination: str,
    script: str,
    *,
    timeout: float = 120,
    input_bytes: bytes | None = None,
    host_key_alias: str = "",
) -> subprocess.CompletedProcess:
    # sh -s receives LF bytes; text-mode Windows input would turn LF into CRLF.
    payload = (script.rstrip("\n") + "\n").encode("utf-8") if input_bytes is None else input_bytes
    return _run(
        ssh_base_argv(host_key_alias) + [destination, "sh", "-s"],
        timeout=timeout,
        input_bytes=payload,
    )


def _ssh_checked(
    destination: str,
    script: str,
    *,
    timeout: float = 120,
    host_key_alias: str = "",
) -> str:
    result = _ssh(destination, script, timeout=timeout, host_key_alias=host_key_alias)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"SSH_COMMAND_FAILED:{result.returncode}:{stderr}")
    return result.stdout.decode("utf-8", "replace")


def _remote_probe(destination: str, *, host_key_alias: str = "") -> dict[str, Any]:
    output = _ssh_checked(
        destination,
        """
set -eu
printf 'hostname='; hostname
printf 'user='; id -un
printf 'os='; uname -s
printf 'python='; python3 --version
printf 'docker='; docker version --format '{{.Server.Version}}'
printf 'home_kib='; df -Pk "$HOME" | tail -1 | awk '{print $4}'
printf 'qts_backups='; test -e "$HOME/qts-backups" && echo exists || echo absent
printf 'ssh_ok=1'
""",
        host_key_alias=host_key_alias,
    )
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()
    return parsed


def _remote_prepare(destination: str, remote_dir: str, *, host_key_alias: str = "") -> str:
    return _ssh_checked(
        destination,
        f"""
set -eu
umask 077
R="$HOME"/{shlex.quote(remote_dir)}
mkdir -p "$R/bootstrap" "$R/data" "$R/secrets" "$R/artifacts"
chmod 700 "$R" "$R/bootstrap" "$R/data" "$R/secrets" "$R/artifacts"
if [ ! -x "$R/bootstrap/rclone" ]; then
  echo RCLONE_BINARY_MISSING >&2
  exit 4
fi
"$R/bootstrap/rclone" version | head -1
sha256sum "$R/bootstrap/rclone" | awk '{{print $1}}'
""",
        host_key_alias=host_key_alias,
    )


def _remote_free_port(destination: str, *, host_key_alias: str = "") -> int:
    output = _ssh_checked(
        destination,
        """
python3 - <<'PY'
import socket
for port in range(19000, 19050):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        print(port)
        break
    except OSError:
        continue
    finally:
        s.close()
PY
""",
        host_key_alias=host_key_alias,
    )
    return int(output.strip().splitlines()[-1])


def _remote_write_secret(
    destination: str,
    path: str,
    content: str,
    *,
    host_key_alias: str = "",
) -> None:
    # No ``sh -s`` here: the remote command's stdin is the secret content, not
    # the shell script, so ``cat`` receives exactly the secret bytes.
    result = _run(
        ssh_base_argv(host_key_alias) + [destination, f"umask 077; cat > {shlex.quote(path)}"],
        timeout=60,
        input_bytes=content.encode("utf-8"),
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH_SECRET_WRITE_FAILED:{result.returncode}")


def _remote_start_storage(
    destination: str,
    remote_dir: str,
    run_id: str,
    image: str,
    remote_port: int,
    env_path: str,
    *,
    host_key_alias: str = "",
) -> dict[str, Any]:
    container = f"qts-p5-rclone-{run_id}"
    network = f"qts-p5-offhost-net-{run_id}"
    data_dir = f"{remote_dir}/data"
    script = f"""
set -eu
R="$HOME"/{shlex.quote(remote_dir)}
C={shlex.quote(container)}
N={shlex.quote(network)}
P={remote_port}
if docker ps -a --format '{{{{.Names}}}}' | grep -qx "$C"; then
  echo CONTAINER_EXISTS >&2
  exit 5
fi
docker network inspect "$N" >/dev/null 2>&1 || docker network create "$N" >/dev/null
cd "$R/bootstrap"
printf 'FROM scratch\nCOPY rclone /rclone\nENTRYPOINT ["/rclone"]\n' > Dockerfile
IMG={shlex.quote(image)}
if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  docker build -q -t "$IMG" . >/dev/null
fi
CID=$(docker run -d --name "$C" --network "$N" --restart no \
  -p "127.0.0.1:$P:9000" \
  -v "$R/data:/data:rw" \
  --env-file "$R/secrets/rclone.env" \
  "$IMG" serve s3 :local:/data --addr :9000)
printf 'container_id=%s\n' "$CID"
printf 'image_id='; docker image inspect "$IMG" --format '{{{{.Id}}}}'
printf 'image_ref=%s\n' "$IMG"
printf 'network=%s\n' "$N"
printf 'data_dir=%s\n' "$R/data"
"""
    return _parse_key_values(
        _ssh_checked(destination, script, timeout=300, host_key_alias=host_key_alias)
    )


def _parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _remote_health(destination: str, port: int, *, host_key_alias: str = "") -> int:
    output = _ssh_checked(
        destination,
        f"""
python3 - <<'PY'
import urllib.request
try:
    r = urllib.request.urlopen("http://127.0.0.1:{port}/", timeout=3)
    print(r.status)
except Exception as exc:
    print(getattr(exc, "code", 0))
PY
""",
        timeout=60,
        host_key_alias=host_key_alias,
    )
    return int(output.strip().splitlines()[-1] or "0")


def _start_tunnel(
    destination: str,
    remote_port: int,
    *,
    host_key_alias: str = "",
) -> tuple[subprocess.Popen, int]:
    local_port = _free_port()
    process = subprocess.Popen(
        ssh_base_argv(host_key_alias)
        + [
            "-N",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            destination,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_port(local_port, timeout=30)
    except Exception:
        process.terminate()
        stderr = process.stderr.read().decode("utf-8", "replace")[-500:] if process.stderr else ""
        raise RuntimeError(f"SSH_TUNNEL_FAILED:{stderr}") from None
    return process, local_port


def _s3_client(config: S3Config):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _create_bucket(config: S3Config) -> None:
    client = _s3_client(config)
    client.create_bucket(Bucket=config.bucket)


def _remote_verify_objects(config: S3Config, expected: dict[str, str]) -> dict[str, Any]:
    client = _s3_client(config)
    listed = client.list_objects_v2(Bucket=config.bucket, Prefix=config.prefix)
    objects = []
    for item in listed.get("Contents", []):
        key = str(item["Key"])
        head = client.head_object(Bucket=config.bucket, Key=key)
        body = client.get_object(Bucket=config.bucket, Key=key)["Body"].read()
        digest = hashlib.sha256(body).hexdigest()
        objects.append(
            {
                "key": key,
                "size": len(body),
                "sha256": digest,
                "etag": str(head.get("ETag", "")).strip('"'),
                "metadata": {
                    str(k).lower(): str(v) for k, v in (head.get("Metadata") or {}).items()
                },
                "digest_matches_backup": digest == expected.get(key),
            }
        )
    return {
        "bucket": config.bucket,
        "prefix": config.prefix,
        "objects": objects,
        "all_digests_match": all(item["digest_matches_backup"] for item in objects),
    }


async def _create_database(dsn: str, name: str) -> None:
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


def _local_pg_container(run_id: str, password: str, port: int) -> tuple[str, str, str, Path]:
    container = f"qts-p5-pg-{run_id}"
    volume = f"qts-p5-pgdata-{run_id}"
    fd, env_name = tempfile.mkstemp(prefix="qts-p5-pg-", suffix=".env")
    os.close(fd)
    env_file = Path(env_name)
    env_file.write_text(
        f"POSTGRES_USER=trader\nPOSTGRES_DB=postgres\nPOSTGRES_PASSWORD={password}\n",
        encoding="utf-8",
    )
    result = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--env-file",
            str(env_file),
            "-p",
            f"127.0.0.1:{port}:5432",
            "-v",
            f"{volume}:/var/lib/postgresql/data",
            "postgres:16",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "LOCAL_PG_START_FAILED:" + result.stderr.decode("utf-8", "replace")[-500:]
        )
    deadline = time.time() + 60
    while time.time() < deadline:
        check = _run(
            ["docker", "exec", container, "pg_isready", "-U", "trader"],
            timeout=20,
        )
        if check.returncode == 0:
            return (
                container,
                volume,
                f"postgresql://trader:{quote(password, safe='')}@127.0.0.1:{port}/postgres",
                env_file,
            )
        time.sleep(1)
    raise RuntimeError("LOCAL_PG_NOT_READY")


def _stop_local_pg(container: str, volume: str, env_file: Path) -> None:
    _run(["docker", "rm", "-f", container], timeout=60)
    _run(["docker", "volume", "rm", "-f", volume], timeout=60)
    with suppress(FileNotFoundError):
        env_file.unlink()


async def run(args) -> dict[str, Any]:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    destination = f"{args.ssh_user}@{args.remote_host}"
    host_key_alias = getattr(args, "host_key_alias", "") or ""
    evidence_dir = args.evidence_dir.resolve() / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "drill": "offhost_ssh_tunnel_s3",
        "run_id": run_id,
        "recorded_at_ms": int(time.time() * 1000),
        "remote_destination": destination,
        "remote_host_key_alias": host_key_alias,
        "remote_dir": args.remote_dir,
        "target_kind": "s3_compatible",
        "transport": "ssh_tunnel",
        "off_host": True,
        "source": "synthetic task-owned PostgreSQL facts",
    }
    tunnel = None
    pg_container = ""
    pg_volume = ""
    pg_env_file: Path | None = None
    try:
        evidence["remote_probe"] = _remote_probe(destination, host_key_alias=host_key_alias)
        if evidence["remote_probe"].get("hostname") != args.expected_hostname:
            raise RuntimeError(
                "REMOTE_HOSTNAME_MISMATCH:" + str(evidence["remote_probe"].get("hostname", ""))
            )
        prepared = _remote_prepare(destination, args.remote_dir, host_key_alias=host_key_alias)
        evidence["remote_prepare"] = prepared
        if RCLONE_VERSION not in prepared or RCLONE_BINARY_SHA256 not in prepared:
            raise RuntimeError("RCLONE_ARTIFACT_DOES_NOT_MATCH_PIN")
        evidence["storage_artifact"] = {
            "implementation": "rclone serve s3",
            "version": RCLONE_VERSION,
            "source": RCLONE_SOURCE,
            "archive_sha256": RCLONE_ARCHIVE_SHA256,
            "binary_sha256": RCLONE_BINARY_SHA256,
        }

        password = secrets.token_urlsafe(24)
        access_key = "qts" + secrets.token_hex(8)
        secret_key = secrets.token_urlsafe(24)
        remote_port = _remote_free_port(destination, host_key_alias=host_key_alias)
        env_path = f"{args.remote_dir}/secrets/rclone.env"
        _remote_write_secret(
            destination,
            env_path,
            f'RCLONE_AUTH_KEY="{access_key},{secret_key}"\n',
            host_key_alias=host_key_alias,
        )
        image = f"qts-p5-rclone:{run_id}"
        storage = _remote_start_storage(
            destination,
            args.remote_dir,
            run_id,
            image,
            remote_port,
            env_path,
            host_key_alias=host_key_alias,
        )
        evidence["storage"] = {
            **storage,
            "implementation": "rclone serve s3",
            "image": image,
            "remote_port": remote_port,
            "secret_env_path": env_path,
            "health_probe_note": "S3 API was verified by bucket create/put/head/get; GET / status is informational",
        }
        health_status = _remote_health(destination, remote_port, host_key_alias=host_key_alias)
        evidence["storage"]["health_status"] = health_status

        tunnel, local_port = _start_tunnel(
            destination, remote_port, host_key_alias=host_key_alias
        )
        evidence["tunnel"] = {
            "local_port": local_port,
            "remote_port": remote_port,
            "strict_host_key_checking": True,
            "host_key_alias": host_key_alias,
        }
        s3_config = S3Config(
            endpoint_url=f"http://127.0.0.1:{local_port}",
            bucket=f"qts-p5-offhost-{run_id.lower()}",
            prefix=f"phase5/{run_id}",
            access_key=access_key,
            secret_key=secret_key,
            region="us-east-1",
        )
        _create_bucket(s3_config)
        evidence["bucket"] = s3_config.bucket
        evidence["prefix"] = s3_config.prefix

        pg_port = _free_port()
        pg_container, pg_volume, admin_dsn, pg_env_file = _local_pg_container(
            run_id, password, pg_port
        )
        evidence["local_pg"] = {
            "container": pg_container,
            "volume": pg_volume,
            "port": pg_port,
        }
        source_db = "qts_p5_src_" + run_id.lower().replace("-", "_")
        restore_db = "qts_p5_rst_" + run_id.lower().replace("-", "_")
        await _create_database(admin_dsn, source_db)
        # restore_and_verify creates the target database itself and rejects an
        # existing target, so do not pre-create it here.
        source_dsn = (
            f"postgresql://trader:{quote(password, safe='')}@127.0.0.1:{pg_port}/{source_db}"
        )
        first_facts = await write_committed_facts("", dsn=source_dsn)
        evidence["initial_source_facts"] = first_facts["facts"]
        # Backward-compatible label used by the independent 5.7 evidence test.
        evidence["source_facts"] = first_facts["facts"]

        backup_config = BackupConfig(
            dsn=source_dsn,
            output_dir=evidence_dir / "backup",
            interval_seconds=120,
            s3=s3_config,
            off_host_attested=True,
            attested_by=(
                f"user-designated {destination}; StrictHostKeyChecking=yes; "
                f"remote hostname {evidence['remote_probe']['hostname']}"
                + (f"; HostKeyAlias={host_key_alias}" if host_key_alias else "")
            ),
            target_kind="s3_compatible",
            transport="ssh_tunnel",
            remote_host=args.remote_host,
            ssh_host_key_verified=True,
        )
        dump_runner = ContainerPgDumpRunner(pg_container, database=source_db, user="trader")
        s3_target = S3Target(s3_config)
        backup_records: list[dict[str, Any]] = []
        for index in range(args.runs):
            if index:
                await write_committed_facts("", f"offhost_extra_{index}", dsn=source_dsn)
            record = await backup_once(
                backup_config,
                dump_runner=dump_runner,
                s3_target=s3_target,
            )
            backup_records.append(record)
            if index + 1 < args.runs:
                time.sleep(args.interval_seconds)
        evidence["backup_runs"] = [
            {
                "status": record.get("status"),
                "reason": record.get("reason"),
                "started_at_ms": record.get("started_at_ms"),
                "completed_at_ms": record.get("completed_at_ms"),
                "object_key": (record.get("backup") or {}).get("object_key"),
                "sha256": (record.get("backup") or {}).get("sha256"),
                "size_bytes": (record.get("backup") or {}).get("size_bytes"),
                "target": (record.get("backup") or {}).get("target"),
                "receipt": (record.get("backup") or {}).get("receipt"),
            }
            for record in backup_records
        ]
        evidence["rpo"] = summarize_rpo(evidence_dir / "backup" / "backup-journal.jsonl", 300)
        if not all(record.get("status") == "succeeded" for record in backup_records):
            raise RuntimeError("OFFHOST_BACKUP_FAILED")
        if evidence["rpo"]["successful_backups"] < args.runs:
            raise RuntimeError("OFFHOST_BACKUP_COUNT_SHORT")
        gaps = evidence["rpo"]["gaps_seconds"]
        if not gaps or max(gaps) > 300 or max(gaps) <= 0:
            raise RuntimeError("OFFHOST_RPO_NOT_SATISFIED")

        expected = {
            (record.get("backup") or {})
            .get("object_key"): (record.get("backup") or {})
            .get("sha256")
            for record in backup_records
        }
        evidence["final_backup_facts"] = backup_records[-1].get("facts")
        evidence["remote_verification"] = _remote_verify_objects(s3_config, expected)
        if not evidence["remote_verification"]["all_digests_match"]:
            raise RuntimeError("OFFHOST_REMOTE_OBJECT_MISMATCH")

        manifest_path = evidence_dir / "backup" / "latest-backup.json"
        restore_config = RestoreConfig(
            dsn=source_dsn,
            manifest_path=manifest_path,
            target_database=restore_db,
            output_dir=evidence_dir / "restore",
            drop_after=True,
        )
        restore_runner = ContainerPgRestoreRunner(
            pg_container, restore_db, user="trader", source_db=source_db
        )
        restore_evidence = await restore_and_verify(
            restore_config,
            restore_runner=restore_runner,
            s3_target=S3Target(s3_config),
        )
        evidence["restore"] = {
            "started_at_ms": restore_evidence["restore"]["started_at_ms"],
            "completed_at_ms": restore_evidence["restore"]["completed_at_ms"],
            "rto_seconds": restore_evidence["restore"]["rto_seconds"],
            "verified": restore_evidence["restore"]["verified"],
            "checks": {
                "facts_match": restore_evidence["restore"]["checks"]["facts_match"],
                "invariants_ok": restore_evidence["restore"]["checks"]["invariants_ok"],
            },
            "target_database": restore_evidence["restore"]["target_database"],
        }
        if not restore_evidence["restore"]["verified"]:
            raise RuntimeError("OFFHOST_RESTORE_UNVERIFIED")
        if restore_evidence["restore"]["rto_seconds"] > 3600:
            raise RuntimeError("OFFHOST_RTO_EXCEEDED")

        dr_path = evidence_dir / "dr-evidence.json"
        before = evaluate_dr_evidence_file(dr_path)
        write_dr_evidence(restore_evidence, dr_path)
        after = evaluate_dr_evidence_file(dr_path)
        evidence["dr_gate_before"] = before.as_dict()
        evidence["dr_gate_after"] = after.as_dict()
        if before.ready or not after.ready:
            raise RuntimeError("OFFHOST_DR_GATE_TRANSITION_INVALID")
        evidence["passed"] = True
        return evidence
    except Exception as exc:
        evidence["passed"] = False
        evidence["reason"] = f"OFFHOST_DRILL_FAILED:{type(exc).__name__}"
        evidence["error"] = str(exc)[-1500:]
        return evidence
    finally:
        if tunnel is not None:
            tunnel.terminate()
            with suppress(Exception):
                tunnel.wait(timeout=10)
        if pg_container:
            (
                _stop_local_pg(pg_container, pg_volume, pg_env_file)
                if pg_env_file
                else _run(["docker", "rm", "-f", pg_container], timeout=60)
            )
        evidence["completed_at_ms"] = int(time.time() * 1000)
        evidence["remote_objects_retained"] = bool(evidence.get("passed"))
        (evidence_dir / "offhost-backup-restore.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS off-host S3 backup/restore drill")
    parser.add_argument(
        "--remote-host",
        default=None,
        help=(
            "designated off-host address; overrides $QTS_OFFHOST_HOST. "
            "There is no default: the drill fails closed when it is not configured."
        ),
    )
    parser.add_argument("--ssh-user", default=os.getenv("QTS_OFFHOST_USER", "alex"))
    parser.add_argument(
        "--expected-hostname", default=os.getenv("QTS_OFFHOST_HOSTNAME", "my-server")
    )
    parser.add_argument(
        "--host-key-alias",
        default=None,
        help=(
            "known_hosts identity used for the connection (ssh HostKeyAlias); "
            "overrides $QTS_OFFHOST_HOST_KEY_ALIAS. Host-key checking stays enabled."
        ),
    )
    parser.add_argument("--remote-dir", default=os.getenv("QTS_OFFHOST_DIR", "qts-backups"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--interval-seconds", type=int, default=120)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence/offhost",
    )
    args = parser.parse_args()
    try:
        args.remote_host = resolve_remote_host(args.remote_host)
    except RemoteTargetNotConfigured as exc:
        print(
            json.dumps({"error": "REMOTE_HOST_NOT_CONFIGURED", "detail": str(exc)}),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    args.host_key_alias = resolve_host_key_alias(args.host_key_alias)
    evidence = asyncio.run(run(args))
    print(json.dumps({"passed": evidence.get("passed"), "run_id": evidence.get("run_id")}))
    raise SystemExit(0 if evidence.get("passed") else 1)


if __name__ == "__main__":
    main()
