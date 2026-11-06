#!/usr/bin/env python3
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import vpngate_public as mod


def row(host="vpn.example", port=443, country="Japan", code="JP"):
    cfg = f"client\nproto tcp\nremote {host} {port}\n"
    return {"host": host, "ip": "198.51.100.10", "country": country,
            "country_code": code, "config_b64": base64.b64encode(cfg.encode()).decode()}


class MockCheckerTests(unittest.TestCase):
    def setUp(self):
        self.nodes = mod.to_nodes([row()])
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = mod.PUBLIC_DIR
        mod.PUBLIC_DIR = Path(self.tmp.name)

    def tearDown(self):
        mod.PUBLIC_DIR = self.old_dir
        self.tmp.cleanup()

    def test_success_uses_worker_entry_not_vpngate_ip(self):
        with patch.object(mod, "CHECK_WORKER", "https://check.invalid/check"), patch.object(mod, "urlopen") as opened:
            response = opened.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps({"success": True, "responseTime": 12}).encode()
            results = mod.check_nodes(self.nodes)
        data, text = mod.build_outputs(results, 1, True)
        self.assertTrue(text.startswith("edgetunnel.cryptoa13z.workers.dev:443#"))
        self.assertNotIn("198.51.100.10:443#", text)
        self.assertEqual(data["status"], "verified")

    def test_checker_receives_public_vpngate_auth(self):
        with patch.object(mod, "CHECK_WORKER", "https://check.invalid/check"), patch.object(mod, "urlopen") as opened:
            response = opened.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"success":true}'
            mod.check_one(self.nodes[0])
            request = opened.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["sstp"], [f"vpn:vpn@{self.nodes[0]['host']}:{self.nodes[0]['port']}"])

    def test_all_failure_refuses_write(self):
        with patch.object(mod, "CHECK_WORKER", "https://check.invalid/check"), patch.object(mod, "urlopen") as opened:
            response = opened.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"success":false,"error":"mock failure"}'
            results = mod.check_nodes(self.nodes)
        self.assertFalse(any(n.get("success") is True for n in results))
        with self.assertRaises(RuntimeError):
            mod.write_public(*mod.build_outputs(results, 1, True))


if __name__ == "__main__":
    unittest.main()
