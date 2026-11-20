#!/usr/bin/env python3
"""Build a minimal, server-safe Mihomo config from the active FlClash profile.

The script reads the active FlClash ``config.yaml`` plus the SQLite selection
metadata in memory.  It resolves the currently selected proxy group, copies only
the proxy definitions in that group, normalizes TLS verification, strips
GUI/desktop-only fields, and writes a container-ready config plus a 0600 proxy
URL file.

It never prints proxy names, server addresses, UUIDs, passwords, or
fingerprints.  Output is limited to counts, hashes, and reason codes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - operator-facing error
    raise SystemExit("PyYAML is required to build the config") from exc

VLESS_ALLOWED = {
    "name",
    "type",
    "server",
    "port",
    "uuid",
    "network",
    "tls",
    "servername",
    "flow",
    "client-fingerprint",
    "reality-opts",
    "ws-opts",
    "grpc-opts",
    "skip-cert-verify",
    "udp",
}
HYSTERIA2_ALLOWED = {
    "name",
    "type",
    "server",
    "port",
    "ports",
    "password",
    "sni",
    "skip-cert-verify",
    "fingerprint",
    "obfs",
    "obfs-password",
    "up",
    "down",
    "alpn",
    "udp",
}

PRIVATE_DIRECT_RULES = [
    "DOMAIN-SUFFIX,localhost,DIRECT",
    "DOMAIN-SUFFIX,local,DIRECT",
    "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
    "IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
    "IP-CIDR6,::1/128,DIRECT,no-resolve",
    "IP-CIDR6,fc00::/7,DIRECT,no-resolve",
    "IP-CIDR6,fe80::/10,DIRECT,no-resolve",
]

TARGET_PROXY_RULES = [
    "DOMAIN-SUFFIX,github.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,githubusercontent.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,githubassets.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,ghcr.io,QTS-EGRESS",
    "DOMAIN,auth.docker.io,QTS-EGRESS",
    "DOMAIN,registry-1.docker.io,QTS-EGRESS",
    "DOMAIN-SUFFIX,docker.io,QTS-EGRESS",
    "DOMAIN-SUFFIX,docker.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,cloudflare.docker.com,QTS-EGRESS",
    "DOMAIN,demo-api.binance.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,demo-stream.binance.com,QTS-EGRESS",
    "DOMAIN-SUFFIX,gstatic.com,QTS-EGRESS",
]

DNS_NAMESERVERS = ["223.5.5.5", "119.29.29.29", "1.1.1.1"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:12]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"source config is not a mapping: {path}")
    return data


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def active_profile_id(source_dir: Path) -> int | None:
    prefs_path = source_dir / "shared_preferences.json"
    if not prefs_path.exists():
        return None
    prefs = load_json(prefs_path)
    raw = prefs.get("flutter.config") if isinstance(prefs, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None
    profile_id = config.get("currentProfileId") if isinstance(config, dict) else None
    if isinstance(profile_id, int):
        return profile_id
    return None


def load_selected_map(source_dir: Path, profile_id: int | None) -> dict[str, Any]:
    db_path = source_dir / "database.sqlite"
    if not db_path.exists():
        return {}
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        row = None
        if profile_id is not None:
            row = con.execute(
                "select selected_map from profiles where id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            row = con.execute(
                "select selected_map from profiles order by \"order\" limit 1"
            ).fetchone()
        raw = row["selected_map"] if row else None
        con.close()
    except sqlite3.Error:
        return {}
    if not raw:
        return {}
    try:
        selected = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return selected if isinstance(selected, dict) else {}


def resolve_candidates(
    active: dict[str, Any], selected_map: dict[str, Any]
) -> tuple[list[str], str, str]:
    proxies = {
        proxy.get("name"): proxy
        for proxy in (active.get("proxies") or [])
        if isinstance(proxy, dict) and proxy.get("name")
    }
    groups = {
        group.get("name"): group
        for group in (active.get("proxy-groups") or [])
        if isinstance(group, dict) and group.get("name")
    }

    def expand(value: Any) -> list[str]:
        name = value.get("name") if isinstance(value, dict) else value
        group = groups.get(name)
        if isinstance(group, dict):
            return [
                member
                for member in (group.get("proxies") or [])
                if member in proxies
            ]
        if name in proxies:
            return [name]
        return []

    for key, value in selected_map.items():
        group = groups.get(key)
        if not isinstance(group, dict):
            continue
        names = expand(value)
        if names:
            return names, short_hash(str(key)), short_hash(str(value))

    # Fallback: prefer a select group whose first nested member is a group.
    for group in groups.values():
        if group.get("type") != "select":
            continue
        for member in group.get("proxies") or []:
            if member in groups:
                names = [
                    item
                    for item in (groups[member].get("proxies") or [])
                    if item in proxies
                ]
                if names:
                    return names, short_hash(str(group.get("name"))), short_hash(str(member))

    # Last resort: first select group's proxy members.
    for group in groups.values():
        if group.get("type") != "select":
            continue
        names = [m for m in (group.get("proxies") or []) if m in proxies]
        if names:
            return names, short_hash(str(group.get("name"))), ""

    raise SystemExit("no usable proxy selection found in active FlClash profile")


def sanitize_proxy(proxy: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    protocol = proxy.get("type")
    if protocol == "vless":
        out = {key: proxy[key] for key in VLESS_ALLOWED if key in proxy}
        for required in ("server", "port", "uuid"):
            if not out.get(required):
                return None, "missing_" + required
        reality = out.get("reality-opts")
        if reality is not None:
            if not isinstance(reality, dict) or not reality.get("public-key"):
                return None, "reality_missing_public_key"
        out["skip-cert-verify"] = False
        return out, None
    if protocol == "hysteria2":
        out = {key: proxy[key] for key in HYSTERIA2_ALLOWED if key in proxy}
        for required in ("server", "password"):
            if not out.get(required):
                return None, "missing_" + required
        if not out.get("port") and not out.get("ports"):
            return None, "missing_port"
        # ``mport`` is a FlClash-specific duplicate of the mihomo ``ports``
        # port-hopping field; drop it so only the upstream field remains.
        out.pop("mport", None)
        if not out.get("fingerprint"):
            return None, "hysteria2_without_certificate_pin"
        # Keep the certificate pin, but never disable TLS verification.
        out["skip-cert-verify"] = False
        return out, None
    return None, "unsupported_protocol"


def build_rules(original_rules: list[Any], group_names: set[str]) -> list[str]:
    rules: list[str] = list(PRIVATE_DIRECT_RULES) + list(TARGET_PROXY_RULES)
    for rule in original_rules:
        if not isinstance(rule, str) or not rule.strip():
            continue
        parts = [part.strip() for part in rule.split(",")]
        if not parts:
            continue
        if parts[0] == "MATCH":
            if len(parts) >= 2 and parts[1] in group_names:
                parts[1] = "QTS-EGRESS"
            rules.append(",".join(parts))
            continue
        if len(parts) >= 3 and parts[2] in group_names:
            parts[2] = "QTS-EGRESS"
        rules.append(",".join(parts))
    if not any(rule.startswith("MATCH,") for rule in rules):
        rules.append("MATCH,QTS-EGRESS")
    return rules


def build_config(
    active: dict[str, Any],
    selected_map: dict[str, Any],
    auth_user: str,
    auth_password: str,
    health_url: str,
    excluded_protocols: set[str],
    max_candidates: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    candidate_names, selection_key_hash, selection_value_hash = resolve_candidates(
        active, selected_map
    )
    proxy_by_name = {
        proxy.get("name"): proxy
        for proxy in (active.get("proxies") or [])
        if isinstance(proxy, dict) and proxy.get("name")
    }

    sanitized: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    removed_mport = 0
    normalized_skip_cert = 0
    seen: set[str] = set()
    for name in candidate_names:
        if name in seen:
            continue
        seen.add(name)
        proxy = proxy_by_name.get(name)
        if not isinstance(proxy, dict):
            rejection_counts["missing_definition"] += 1
            continue
        protocol = proxy.get("type")
        if protocol in excluded_protocols:
            rejection_counts["excluded_" + str(protocol)] += 1
            continue
        clean, reason = sanitize_proxy(proxy)
        if clean is None:
            rejection_counts[str(reason)] += 1
            continue
        if "mport" in proxy:
            removed_mport += 1
        if proxy.get("skip-cert-verify") is True:
            normalized_skip_cert += 1
        sanitized.append(clean)
        if len(sanitized) >= max_candidates:
            break

    if not sanitized:
        raise SystemExit("no compatible proxy candidates after sanitization")

    names = [proxy["name"] for proxy in sanitized]
    protocol_counts = Counter(proxy["type"] for proxy in sanitized)
    original_groups = {
        group.get("name")
        for group in (active.get("proxy-groups") or [])
        if isinstance(group, dict)
    }
    rules = build_rules(active.get("rules") or [], original_groups)

    config: dict[str, Any] = {
        "mixed-port": 7890,
        "allow-lan": True,
        "bind-address": "*",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "authentication": [f"{auth_user}:{auth_password}"],
        "profile": {"store-selected": True, "store-fake-ip": False},
        "geodata-mode": True,
        "geo-auto-update": False,
        "geodata-loader": "memconservative",
        "unified-delay": True,
        "tcp-concurrent": True,
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "normal",
            "nameserver": list(DNS_NAMESERVERS),
            "default-nameserver": list(DNS_NAMESERVERS),
            "proxy-server-nameserver": list(DNS_NAMESERVERS),
            "respect-rules": False,
            "use-hosts": False,
            "use-system-hosts": False,
        },
        "proxies": sanitized,
        "proxy-groups": [
            {
                "name": "QTS-AUTO",
                "type": "url-test",
                "proxies": names,
                "url": health_url,
                "interval": 300,
                "tolerance": 50,
                "lazy": False,
            },
            {
                "name": "QTS-EGRESS",
                "type": "select",
                "proxies": ["QTS-AUTO"],
            },
        ],
        "rules": rules,
    }

    # Preserve the global client fingerprint if the source profile used one.
    global_fingerprint = active.get("global-client-fingerprint")
    if isinstance(global_fingerprint, str) and global_fingerprint.strip():
        config["global-client-fingerprint"] = global_fingerprint.strip()

    dumped = yaml.safe_dump(
        config,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    metadata = {
        "generated_utc": utc_now(),
        "selection_key_sha256_12": selection_key_hash,
        "selection_value_sha256_12": selection_value_hash,
        "candidate_count": len(sanitized),
        "protocol_counts": dict(protocol_counts),
        "source_candidate_count": len(candidate_names),
        "rejections": dict(rejection_counts),
        "removed_mport": removed_mport,
        "normalized_skip_cert": normalized_skip_cert,
        "rule_count": len(rules),
        "config_sha256": sha256_bytes(dumped.encode("utf-8")),
    }
    return config, metadata, dumped


def write_text(path: Path, text: str, mode: int = 0o600) -> None:
    # Write bytes so Windows text-mode newline translation cannot make the
    # recorded config SHA-256 differ from the bytes that reach the server.
    path.write_bytes(text.encode("utf-8"))
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_source = Path(
        os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    ) / "com.follow" / "clash"
    parser.add_argument("--source-dir", type=Path, default=default_source)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--auth-user", default="qts")
    parser.add_argument(
        "--auth-password",
        default=None,
        help="explicit password; generated with secrets.token_urlsafe when omitted",
    )
    parser.add_argument(
        "--health-url",
        default="https://www.gstatic.com/generate_204",
    )
    parser.add_argument(
        "--exclude-protocol",
        action="append",
        default=[],
        choices=["vless", "hysteria2"],
    )
    parser.add_argument("--max-candidates", type=int, default=9)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_dir: Path = args.source_dir
    output_dir: Path = args.output_dir
    config_path = source_dir / "config.yaml"
    if not config_path.exists():
        raise SystemExit(f"active config not found: {config_path}")
    if args.max_candidates < 1:
        raise SystemExit("--max-candidates must be >= 1")

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(output_dir, 0o700)
    except OSError:
        pass

    active = load_yaml(config_path)
    profile_id = active_profile_id(source_dir)
    selected_map = load_selected_map(source_dir, profile_id)
    password = args.auth_password or secrets.token_urlsafe(24)
    if ":" in password:
        raise SystemExit("auth password must not contain ':'")

    config, metadata, dumped = build_config(
        active=active,
        selected_map=selected_map,
        auth_user=args.auth_user,
        auth_password=password,
        health_url=args.health_url,
        excluded_protocols=set(args.exclude_protocol),
        max_candidates=args.max_candidates,
    )
    metadata["source_profile_id"] = profile_id

    config_file = output_dir / "config.yaml"
    proxy_file = output_dir / "proxy-urls.txt"
    curl_file = output_dir / "curl-proxy.conf"
    metadata_file = output_dir / "build-metadata.json"

    write_text(config_file, dumped, 0o600)
    write_text(
        proxy_file,
        "host=http://{user}:{password}@127.0.0.1:17890\n"
        "container=http://{user}:{password}@egress:7890\n".format(
            user=args.auth_user, password=password
        ),
        0o600,
    )
    write_text(
        curl_file,
        'proxy = "http://{user}:{password}@127.0.0.1:17890"\n'
        "silent\nshow-error\n".format(user=args.auth_user, password=password),
        0o600,
    )
    write_text(
        metadata_file,
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        0o600,
    )

    # Fail closed if a forbidden exposure key slips back in.
    forbidden = {
        "external-controller",
        "external-controller-unix",
        "external-controller-pipe",
        "external-ui",
        "tun",
        "sniffer",
        "listeners",
        "tunnels",
        "redir-port",
        "tproxy-port",
        "iptables",
        "interface-name",
        "routing-mark",
        "find-process-mode",
    }
    leaked = sorted(forbidden & set(config))
    if leaked:
        raise SystemExit("forbidden keys in generated config: " + ", ".join(leaked))
    if config.get("allow-lan") is not True or config.get("bind-address") != "*":
        raise SystemExit("generated config did not enable container-local LAN binding")
    if not config.get("authentication"):
        raise SystemExit("generated config is missing proxy authentication")
    if any(p.get("skip-cert-verify") is True for p in config["proxies"]):
        raise SystemExit("generated config still disables TLS verification")
    if config["rules"][0:1] != [PRIVATE_DIRECT_RULES[0]]:
        raise SystemExit("private DIRECT rules were not placed first")
    if not any(rule.startswith("DOMAIN-SUFFIX,github.com,QTS-EGRESS") for rule in config["rules"]):
        raise SystemExit("explicit GitHub proxy rule missing")

    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
