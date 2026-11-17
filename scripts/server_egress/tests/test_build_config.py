import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_config  # noqa: E402


def write_source(root: Path) -> None:
    source = {
        "proxies": [
            {
                "name": "vless-a",
                "type": "vless",
                "server": "vless.example.invalid",
                "port": 443,
                "uuid": "00000000-0000-0000-0000-000000000001",
                "tls": True,
                "servername": "vless.example.invalid",
                "client-fingerprint": "chrome",
                "flow": "xtls-rprx-vision",
                "reality-opts": {
                    "public-key": "example-public-key",
                    "short-id": "0000",
                },
                "skip-cert-verify": False,
                "udp": True,
            },
            {
                "name": "hy2-a",
                "type": "hysteria2",
                "server": "hy2.example.invalid",
                "port": 443,
                "ports": "20000-30000",
                "mport": "20000-30000",
                "password": "example-password",
                "sni": "hy2.example.invalid",
                "fingerprint": "a" * 64,
                "skip-cert-verify": True,
                "udp": True,
            },
        ],
        "proxy-groups": [
            {
                "name": "SELECT",
                "type": "select",
                "proxies": ["AUTO", "DIRECT"],
            },
            {
                "name": "AUTO",
                "type": "url-test",
                "proxies": ["vless-a", "hy2-a"],
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
            },
        ],
        "rules": [
            "DOMAIN-SUFFIX,example.com,SELECT",
            "GEOIP,CN,DIRECT",
            "MATCH,SELECT",
        ],
    }
    (root / "config.yaml").write_text(
        build_config.yaml.safe_dump(source, sort_keys=False),
        encoding="utf-8",
    )
    (root / "shared_preferences.json").write_text(
        json.dumps(
            {
                "flutter.config": json.dumps(
                    {"currentProfileId": 7}
                )
            }
        ),
        encoding="utf-8",
    )
    con = sqlite3.connect(root / "database.sqlite")
    con.execute(
        'create table profiles (id integer, selected_map text, "order" integer)'
    )
    con.execute(
        "insert into profiles values (?, ?, ?)",
        (7, json.dumps({"SELECT": {"name": "AUTO"}}), 0),
    )
    con.commit()
    con.close()


class BuildConfigTests(unittest.TestCase):
    def test_generates_minimal_safe_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_source(root)
            active = build_config.load_yaml(root / "config.yaml")
            selected = build_config.load_selected_map(
                root, build_config.active_profile_id(root)
            )
            config, metadata, dumped = build_config.build_config(
                active=active,
                selected_map=selected,
                auth_user="qts",
                auth_password="example-token",
                health_url="https://www.gstatic.com/generate_204",
                excluded_protocols=set(),
                max_candidates=9,
            )

        self.assertEqual(len(config["proxies"]), 2)
        self.assertEqual(metadata["protocol_counts"], {"hysteria2": 1, "vless": 1})
        self.assertNotIn("external-controller", config)
        self.assertNotIn("tun", config)
        self.assertNotIn("sniffer", config)
        self.assertTrue(config["allow-lan"])
        self.assertEqual(config["dns"]["enhanced-mode"], "normal")
        self.assertEqual(config["authentication"], ["qts:example-token"])
        self.assertEqual(config["proxy-groups"][0]["name"], "QTS-AUTO")
        self.assertEqual(config["proxy-groups"][1]["proxies"], ["QTS-AUTO"])
        self.assertEqual(config["rules"][0], build_config.PRIVATE_DIRECT_RULES[0])
        self.assertIn(
            "DOMAIN-SUFFIX,github.com,QTS-EGRESS", config["rules"]
        )
        self.assertEqual(config["rules"][-1], "MATCH,QTS-EGRESS")
        self.assertTrue(dumped.startswith("mixed-port: 7890"))
        for proxy in config["proxies"]:
            self.assertFalse(proxy["skip-cert-verify"])
            self.assertNotIn("mport", proxy)
        self.assertEqual(metadata["removed_mport"], 1)
        self.assertEqual(metadata["normalized_skip_cert"], 1)

    def test_hysteria2_without_pin_is_rejected(self) -> None:
        clean, reason = build_config.sanitize_proxy(
            {
                "name": "hy2-no-pin",
                "type": "hysteria2",
                "server": "hy2.example.invalid",
                "port": 443,
                "password": "example",
                "sni": "hy2.example.invalid",
                "skip-cert-verify": True,
            }
        )
        self.assertIsNone(clean)
        self.assertEqual(reason, "hysteria2_without_certificate_pin")


if __name__ == "__main__":
    unittest.main()
