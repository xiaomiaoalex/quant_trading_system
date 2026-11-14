"""Phase-5 off-host (SSH tunnel -> S3) independent verification.

Expectations are derived from the phase-5 contract (13.16), the designated-host
work order and hand calculation, not from the engineer's green evidence.  The
tests fall in three groups:

* target identity: a loopback tunnel endpoint must never be recorded as the
  off-host target, and the DR gate must reject a disguised loopback / a tunnel
  whose host key was not strictly verified;
* transport plumbing: the S3 client must normalise metadata key casing and
  stream the dump body, and the drill script must keep BatchMode + strict host
  key checking and send LF-only shell payloads;
* evidence integrity: recompute RPO gaps, per-object readback digests, restore
  facts/RTO and the DR gate before/after from the raw recorded numbers.

Nothing here talks to the network, PostgreSQL, Docker or the designated host.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_GLOB = "artifacts/foundation/phase5/evidence/offhost/*/offhost-backup-restore.json"
DIGEST = "a" * 64
REMOTE_HOST = "192.168.3.4"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _s3_config():
    from trader.ops.s3 import S3Config

    return S3Config(
        endpoint_url="http://127.0.0.1:19000",
        bucket="qts-p5-offhost-independent",
        prefix="phase5/independent",
        access_key="placeholder-access",
        secret_key="placeholder-secret",
    )


def _tunnel_config(off_host: bool = True, **overrides):
    from trader.ops.backup import BackupConfig

    fields = dict(
        dsn="postgresql://placeholder/placeholder",
        output_dir=Path("unused"),
        interval_seconds=120,
        s3=_s3_config(),
        off_host_attested=off_host,
        attested_by="user-designated alex@192.168.3.4",
        target_kind="s3_compatible",
        transport="ssh_tunnel",
        remote_host=REMOTE_HOST,
        ssh_host_key_verified=True,
    )
    fields.update(overrides)
    return BackupConfig(**fields)


def _evidence(now_ms: int, target: dict, **restore_overrides):
    restore = {
        "drill_id": "independent",
        "started_at_ms": now_ms - 50_000,
        "completed_at_ms": now_ms - 40_000,
        "rto_seconds": 10,
        "backup_sha256": DIGEST,
        "verified": True,
        "source_database": {"host": "127.0.0.1", "database": "qts_src"},
        "target_database": {"host": "127.0.0.1", "database": "qts_rst"},
        "checks": {"facts_match": True, "invariants_ok": True},
    }
    restore.update(restore_overrides)
    return {
        "schema_version": 1,
        "backup": {
            "completed_at_ms": now_ms - 60_000,
            "cadence_seconds": 120,
            "sha256": DIGEST,
            "object_key": "phase5/independent/qts.dump",
            "target": target,
        },
        "restore": restore,
    }


def _drill_module():
    path = ROOT / "scripts" / "drill_offhost_backup.py"
    if not path.is_file():
        pytest.skip("drill_offhost_backup.py is not present in this checkout")
    spec = importlib.util.spec_from_file_location("drill_offhost_backup", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 5.6 - backup target identity for an SSH tunnel
# --------------------------------------------------------------------------- #


def test_5_6_a_tunnel_records_the_remote_host_not_the_loopback_endpoint():
    identity = _tunnel_config().target_identity()

    assert identity["transport"] == "ssh_tunnel"
    assert identity["remote_host"] == REMOTE_HOST
    # The S3 client endpoint is a local forward; it must stay visible as such
    # instead of being promoted to the off-host target.
    assert identity["tunnel_endpoint_host"] == "127.0.0.1"
    assert identity["endpoint_host"] == REMOTE_HOST
    assert identity["endpoint_host"] != identity["tunnel_endpoint_host"]
    assert identity["off_host"] is True
    assert identity["identity_verified"] is True
    assert identity["kind"] == "s3_compatible"


def test_5_6_a_tunnel_without_strict_host_key_checking_is_not_verified():
    identity = _tunnel_config(ssh_host_key_verified=False).target_identity()

    assert identity["transport"] == "ssh_tunnel"
    assert identity["ssh_host_key_verified"] is False
    assert identity["identity_verified"] is False


def test_5_6_an_unknown_transport_is_refused_at_configuration_time():
    from trader.ops.backup import BackupError

    with pytest.raises(BackupError, match="BACKUP_TRANSPORT_INVALID"):
        _tunnel_config(transport="carrier_pigeon")


def test_5_6_a_direct_target_never_claims_a_remote_host():
    identity = _tunnel_config(transport="direct", remote_host="").target_identity()

    assert identity["transport"] == "direct"
    assert "remote_host" not in identity
    assert identity["endpoint_host"] == "127.0.0.1"


# --------------------------------------------------------------------------- #
# 5.6/5.7 - DR gate target identity counterexamples
# --------------------------------------------------------------------------- #


def _gate(evidence, now_ms):
    from trader.ops.dr_evidence import evaluate_dr_evidence

    return evaluate_dr_evidence(evidence, now_ms=now_ms)


def test_5_6_a_complete_tunnel_evidence_passes_and_exposes_the_transport():
    now = 1_800_000_000_000
    target = _tunnel_config().target_identity()

    result = _gate(_evidence(now, target), now)

    assert result.ready is True, result.reasons
    assert result.reasons == ()
    assert result.details["target_transport"] == "ssh_tunnel"
    assert result.details["target_remote_host"] == REMOTE_HOST
    assert result.details["target_endpoint_host"] == REMOTE_HOST
    assert result.details["target_kind"] == "s3_compatible"


def test_5_6_a_tunnel_without_a_remote_host_fails_closed():
    now = 1_800_000_000_000
    target = _tunnel_config(remote_host="").target_identity()

    # The loopback URL survives as endpoint_host, so the gate must use the
    # transport-specific identity check rather than the URL alone.
    assert target["endpoint_host"] == "127.0.0.1"
    result = _gate(_evidence(now, target), now)

    assert result.ready is False
    assert "DR_TARGET_NOT_VERIFIED_OFF_HOST" in result.reasons


def test_5_6_a_loopback_remote_host_cannot_be_relabelled_as_off_host():
    now = 1_800_000_000_000
    # A dishonest config can still produce a "verified looking" identity: the
    # enforcement point is the gate, which must refuse a loopback remote host.
    target = _tunnel_config(remote_host="127.0.0.1").target_identity()

    assert target["off_host"] is True
    assert target["identity_verified"] is True
    result = _gate(_evidence(now, target), now)

    assert result.ready is False
    assert "DR_TARGET_NOT_VERIFIED_OFF_HOST" in result.reasons


@pytest.mark.parametrize("loopback", ["127.0.0.1", "127.0.1.9", "localhost", "LOCALHOST"])
def test_5_6_every_loopback_spelling_is_rejected(loopback):
    now = 1_800_000_000_000
    target = _tunnel_config(remote_host=loopback).target_identity()

    result = _gate(_evidence(now, target), now)

    assert result.ready is False
    assert "DR_TARGET_NOT_VERIFIED_OFF_HOST" in result.reasons


def test_5_6_a_tunnel_with_an_unverified_host_key_fails_closed():
    now = 1_800_000_000_000
    target = _tunnel_config(ssh_host_key_verified=False).target_identity()

    result = _gate(_evidence(now, target), now)

    assert result.ready is False
    assert "DR_TARGET_NOT_VERIFIED_OFF_HOST" in result.reasons


def test_5_6_an_emulator_relabelled_as_a_tunnel_is_still_rejected():
    now = 1_800_000_000_000
    target = _tunnel_config(target_kind="local_emulator").target_identity()

    result = _gate(_evidence(now, target), now)

    assert result.details["target_kind"] == "local_emulator"
    assert result.ready is False
    assert "DR_TARGET_NOT_VERIFIED_OFF_HOST" in result.reasons


def test_5_6_a_direct_transport_must_point_at_a_non_local_endpoint():
    now = 1_800_000_000_000
    local = _tunnel_config(transport="direct", remote_host="").target_identity()
    assert _gate(_evidence(now, local), now).ready is False

    from trader.ops.s3 import S3Config

    remote_s3 = S3Config(
        endpoint_url="https://203.0.113.10:9000",
        bucket="qts-p5-offhost-independent",
        prefix="phase5/independent",
        access_key="placeholder-access",
        secret_key="placeholder-secret",
    )
    remote = _tunnel_config(transport="direct", remote_host="", s3=remote_s3).target_identity()

    result = _gate(_evidence(now, remote), now)
    assert result.ready is True, result.reasons
    assert result.details["target_transport"] == "direct"
    assert result.details["target_endpoint_host"] == "203.0.113.10"


def test_5_7_a_contradictory_restore_check_keeps_the_gate_closed():
    now = 1_800_000_000_000
    target = _tunnel_config().target_identity()

    evidence = _evidence(now, target, checks={"facts_match": False, "invariants_ok": True})
    result = _gate(evidence, now)

    assert result.ready is False
    assert "DR_RESTORE_FACTS_UNVERIFIED" in result.reasons


# --------------------------------------------------------------------------- #
# 5.6 - S3 metadata casing and streaming upload
# --------------------------------------------------------------------------- #


class _FakeS3Client:
    def __init__(self, metadata: dict, body: bytes = b""):
        self.metadata = metadata
        self.body = body
        self.put: dict = {}

    def head_object(self, *, Bucket: str, Key: str):  # noqa: N803 - boto shape
        return {
            "ContentLength": len(self.body),
            "Metadata": self.metadata,
            "ETag": '"abc123"',
        }

    def put_object(self, **kwargs):
        body = kwargs.get("Body")
        # Reading here proves the upload streams an open handle, not a buffer
        # that the caller already read into memory.
        self.put = {**kwargs, "body_bytes": body.read() if hasattr(body, "read") else None}
        return {"ETag": '"abc123"'}


def test_5_6_the_s3_client_lowercases_mixed_case_metadata_keys(tmp_path):
    from trader.ops.s3 import S3Target

    target = S3Target(_s3_config())
    target._client = _FakeS3Client({"Sha256": DIGEST, "Run-Id": "run-1"}, b"payload")

    head = target.head("phase5/independent/qts.dump")

    assert head["metadata"] == {"sha256": DIGEST, "run-id": "run-1"}
    assert head["content_length"] == len(b"payload")
    assert head["etag"] == "abc123"


def test_5_6_the_s3_client_streams_the_dump_body(tmp_path):
    from trader.ops.s3 import S3Target

    dump = tmp_path / "qts.dump"
    dump.write_bytes(b"streamed-dump-body")
    client = _FakeS3Client({})
    target = S3Target(_s3_config())
    target._client = client

    target.upload(dump, "phase5/independent/qts.dump", {"sha256": DIGEST})

    assert client.put["body_bytes"] == b"streamed-dump-body"
    assert client.put["Metadata"] == {"sha256": DIGEST}
    assert client.put["Key"] == "phase5/independent/qts.dump"
    assert client.put["Bucket"] == "qts-p5-offhost-independent"


# --------------------------------------------------------------------------- #
# 5.6/5.7 - drill script contract (SSH hardening, probe parsing, readback)
# --------------------------------------------------------------------------- #


def test_5_6_the_drill_ssh_keeps_batch_mode_and_strict_host_key_checking(monkeypatch):
    module = _drill_module()
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input_bytes")
        return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(module, "_run", fake_run)
    module._ssh("alex@192.168.3.4", "set -eu\nprintf ok\n")

    argv = captured["argv"]
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "ConnectTimeout=15" in argv
    assert argv[-3:] == ["alex@192.168.3.4", "sh", "-s"]


def test_5_6_the_drill_sends_lf_only_shell_payloads(monkeypatch):
    module = _drill_module()
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["input"] = kwargs.get("input_bytes")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module, "_run", fake_run)
    module._ssh("alex@192.168.3.4", "set -eu\nprintf 'hostname='; hostname\n")

    payload = captured["input"]
    assert isinstance(payload, bytes)
    assert b"\r" not in payload, "text-mode CRLF translation breaks remote sh"
    assert payload.endswith(b"\n")


def test_5_6_the_remote_probe_parser_reads_key_value_lines(monkeypatch):
    module = _drill_module()
    monkeypatch.setattr(
        module,
        "_ssh_checked",
        lambda *a, **k: "hostname=my-server\nuser=alex\nqts_backups=exists\nssh_ok=1\n",
    )

    probe = module._remote_probe("alex@192.168.3.4")

    assert probe["hostname"] == "my-server"
    assert probe["user"] == "alex"
    assert probe["qts_backups"] == "exists"


def test_5_6_remote_readback_recomputes_each_object_digest(monkeypatch):
    module = _drill_module()
    good = b"remote-object-body"
    key = "phase5/independent/good.dump"
    bad_key = "phase5/independent/tampered.dump"

    class FakeClient:
        def list_objects_v2(self, *, Bucket, Prefix):  # noqa: N803 - boto shape
            return {"Contents": [{"Key": key}, {"Key": bad_key}]}

        def head_object(self, *, Bucket, Key):  # noqa: N803
            return {"ETag": '"e"', "Metadata": {"Sha256": "ignored"}}

        def get_object(self, *, Bucket, Key):  # noqa: N803
            body = good if Key == key else good + b"-tampered"
            return {"Body": io.BytesIO(body)}

    monkeypatch.setattr(module, "_s3_client", lambda _config: FakeClient())
    expected = {
        key: hashlib.sha256(good).hexdigest(),
        bad_key: hashlib.sha256(b"other").hexdigest(),
    }

    result = module._remote_verify_objects(_s3_config(), expected)

    by_key = {item["key"]: item for item in result["objects"]}
    assert by_key[key]["digest_matches_backup"] is True
    assert by_key[bad_key]["digest_matches_backup"] is False
    assert result["all_digests_match"] is False


# --------------------------------------------------------------------------- #
# 5.6/5.7 - integrity of the recorded off-host evidence
# --------------------------------------------------------------------------- #


def _latest_evidence() -> tuple[Path, dict]:
    candidates = sorted(
        ROOT.glob(EVIDENCE_GLOB), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not candidates:
        pytest.skip("no off-host evidence run is present in this checkout")
    path = candidates[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_5_6_recorded_rpo_gaps_are_within_five_minutes():
    _path, evidence = _latest_evidence()
    runs = [run for run in evidence["backup_runs"] if run.get("status") == "succeeded"]

    assert len(runs) >= 2, "RPO needs at least two completed backups"
    stamps = sorted(int(run["completed_at_ms"]) for run in runs)
    gaps = [(later - earlier) // 1000 for earlier, later in zip(stamps, stamps[1:])]

    assert gaps == evidence["rpo"]["gaps_seconds"], (gaps, evidence["rpo"]["gaps_seconds"])
    assert max(gaps) <= 300
    assert evidence["rpo"]["rpo_seconds"] == 300
    assert evidence["rpo"]["rpo_satisfied"] is True
    assert evidence["rpo"]["successful_backups"] == len(runs)


def test_5_6_every_recorded_target_is_really_off_host():
    _path, evidence = _latest_evidence()
    targets = [run["target"] for run in evidence["backup_runs"] if run.get("status") == "succeeded"]

    assert targets, "no backup target recorded"
    for target in targets:
        assert target["transport"] == "ssh_tunnel"
        assert target["remote_host"] == evidence["remote_destination"].split("@")[-1]
        assert target["ssh_host_key_verified"] is True
        assert target["off_host"] is True
        assert target["identity_verified"] is True
        assert target["kind"] != "local_emulator"
        # The client endpoint is the tunnel; the recorded target is the host.
        assert target["tunnel_endpoint_host"] == "127.0.0.1"
        assert target["endpoint_host"] == target["remote_host"]
        assert not target["endpoint_host"].startswith("127.")
        assert target["attested_by"]


def test_5_6_recorded_receipts_and_remote_readback_agree():
    _path, evidence = _latest_evidence()
    objects = {item["key"]: item for item in evidence["remote_verification"]["objects"]}

    assert evidence["remote_verification"]["all_digests_match"] is True
    runs = [run for run in evidence["backup_runs"] if run.get("status") == "succeeded"]
    assert len(objects) == len(runs)
    for run in runs:
        digest = run["sha256"]
        assert len(digest) == 64
        assert run["receipt"]["head_metadata_sha256"] == digest
        assert run["receipt"]["readback_sha256"] == digest
        assert run["receipt"]["head_content_length"] == run["size_bytes"]
        remote = objects[run["object_key"]]
        assert remote["sha256"] == digest
        assert remote["size"] == run["size_bytes"]
        assert remote["digest_matches_backup"] is True
        # Metadata casing from a real S3 server is lower-case after transport.
        assert remote["metadata"]["sha256"] == digest


def test_5_7_recorded_restore_proves_facts_and_measured_rto():
    _path, evidence = _latest_evidence()
    restore = evidence["restore"]

    assert restore["verified"] is True
    assert restore["checks"]["facts_match"] is True
    assert restore["checks"]["invariants_ok"] is True
    assert restore["rto_seconds"] == (restore["completed_at_ms"] - restore["started_at_ms"]) // 1000
    assert restore["rto_seconds"] <= 3600
    # The restore target must be a separate database from the recorded source.
    source_dbs = {
        item["metadata"].get("source-database")
        for item in evidence["remote_verification"]["objects"]
    }
    assert source_dbs and None not in source_dbs
    assert restore["target_database"]["database"] not in source_dbs
    source = evidence["source_facts"]
    assert set(source["counts"]) == {
        "trading_accounts",
        "trading_commands",
        "trading_executions",
        "trading_ledger",
        "trading_events",
        "trading_outbox",
    }


def test_5_7_the_gate_rejects_before_and_accepts_after():
    _path, evidence = _latest_evidence()

    before = evidence["dr_gate_before"]
    after = evidence["dr_gate_after"]
    assert before["ready"] is False
    assert before["reasons"]
    assert after["ready"] is True
    assert after["reasons"] == []
    assert after["details"]["target_transport"] == "ssh_tunnel"
    assert after["details"]["target_remote_host"] == evidence["remote_destination"].split("@")[-1]
    assert after["backup_age_seconds"] is not None and after["backup_age_seconds"] <= 300
    assert after["rto_seconds"] is not None and after["rto_seconds"] <= 3600
