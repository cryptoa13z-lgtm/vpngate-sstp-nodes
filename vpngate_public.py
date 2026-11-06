#!/usr/bin/env python3
"""生成公开的 VPNGate SSTP 节点列表。

入口是公开的 Cloudflare Worker hostname；VPNGate 主机只作为 SSTP 后端。
本地默认不检测，输出状态为 unverified；GitHub Actions 必须启用检测。
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

API_URL = os.environ.get("VPNGATE_API", "https://www.vpngate.net/api/iphone/")
HTTP_TIMEOUT = min(max(int(os.environ.get("HTTP_TIMEOUT", "30")), 5), 60)
MAX_NODES = min(max(int(os.environ.get("MAX_NODES", "25")), 1), 500)
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", Path(__file__).with_name("public")))
USER_AGENT = "ipnode-public-pipeline/2.0"
ENTRY = os.environ.get("NODE_ENTRY", "edgetunnel.cryptoa13z.workers.dev:443").strip()
CHECK_WORKER = os.environ.get("CHECK_WORKER", "").strip()
CHECK_REQUIRED = os.environ.get("CHECK_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
CHECK_CONCURRENCY = min(max(int(os.environ.get("CHECK_CONCURRENCY", "2")), 1), 2)
CHECK_TIMEOUT = min(max(float(os.environ.get("CHECK_TIMEOUT", "25")), 1), 25)
PROTO_TCP = re.compile(r"^proto\s+(?:tcp|tcp4|tcp6)\b", re.I | re.M)
REMOTE = re.compile(r"^remote\s+\S+\s+(\d+)\b", re.I | re.M)


def fetch_csv() -> str:
    req = Request(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"})
    with urlopen(req, timeout=HTTP_TIMEOUT) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("upstream response exceeds 8 MiB safety limit")
    return raw.decode("utf-8", "replace")


def parse_rows(text: str) -> list[dict[str, str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    header_index = next((i for i, line in enumerate(lines) if line.lstrip("#").startswith("HostName")), None)
    if header_index is None:
        raise RuntimeError("VPNGate CSV header HostName not found")
    header = next(csv.reader(io.StringIO(lines[header_index].lstrip("#"))))
    normalized = {h.strip().lstrip("*").lower(): i for i, h in enumerate(header)}
    config_index = next((i for key, i in normalized.items() if "base64" in key), None)
    required = {"hostname", "ip", "countrylong", "countryshort"}
    if config_index is None or not required.issubset(normalized):
        raise RuntimeError("VPNGate CSV is missing required columns")
    rows = []
    for line in lines[header_index + 1:]:
        try:
            fields = next(csv.reader(io.StringIO(line)))
            row = {"host": fields[normalized["hostname"]].strip(), "ip": fields[normalized["ip"]].strip(),
                   "country": fields[normalized["countrylong"]].strip(), "country_code": fields[normalized["countryshort"]].strip(),
                   "config_b64": fields[config_index].strip()}
        except (IndexError, KeyError, csv.Error):
            continue
        if row["host"] and row["ip"]:
            rows.append(row)
    return rows


def to_nodes(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    nodes, seen = [], set()
    for row in rows:
        try:
            config = base64.b64decode(row["config_b64"], validate=False).decode("utf-8", "replace")
        except Exception:
            continue
        if not PROTO_TCP.search(config):
            continue
        match = REMOTE.search(config)
        if not match:
            continue
        port = int(match.group(1))
        host = row["host"] if row["host"].endswith(".opengw.net") else row["host"] + ".opengw.net"
        key = (host.lower(), port)
        if key in seen:
            continue
        seen.add(key)
        nodes.append({"host": host, "ip": row["ip"], "port": port, "country": row["country"],
                      "country_code": row["country_code"], "protocol": "sstp",
                      "public_endpoint": f"sstp://vpn:vpn@{host}:{port}"})
        if len(nodes) >= MAX_NODES:
            break
    return nodes


def check_one(node: dict[str, object]) -> dict[str, object]:
    out = dict(node)
    out["link"] = f"sstp://vpn:vpn@{node['host']}:{node['port']}"
    out["status"] = "failed"
    url = CHECK_WORKER.rstrip("/") + "/" + "?sstp=" + quote(f"vpn:vpn@{node['host']}:{node['port']}", safe="")
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(req, timeout=CHECK_TIMEOUT) as response:
            if response.status != 200:
                out["error"] = f"HTTP {response.status}"
                return out
            payload = json.loads(response.read(256 * 1024).decode("utf-8", "replace"))
        out["success"] = payload.get("success") is True
        out["status"] = "success" if out["success"] else "failed"
        out["latency_ms"] = payload.get("responseTime")
        out["colo"] = payload.get("colo")
        if not out["success"]:
            out["error"] = payload.get("error") or "check failed"
        if out["success"] and payload.get("exit") is not None:
            out["exit"] = payload["exit"]
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def check_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    with ThreadPoolExecutor(max_workers=CHECK_CONCURRENCY) as pool:
        futures = [pool.submit(check_one, node) for node in nodes]
        return [future.result() for future in as_completed(futures)]


def build_outputs(nodes: list[dict[str, object]], raw_count: int, checked: bool) -> tuple[dict, str]:
    available = [node for node in nodes if not checked or node.get("success") is True]
    status = "verified" if checked else "unverified"
    data = {"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": API_URL, "entry": ENTRY, "status": status,
            "stats": {"raw_rows": raw_count, "parsed_nodes": len(nodes), "published_nodes": len(available),
                      "checked": len(nodes) if checked else 0, "success": len(available) if checked else 0},
            "nodes": available}
    lines = [f"{ENTRY}#{node['country_code'] or '??'}-{i:03d}$sstp://vpn:vpn@{node['host']}:{node['port']}"
             for i, node in enumerate(available, 1)]
    return data, "\n".join(lines) + ("\n" if lines else "")


def write_public(data: dict, nodes_text: str) -> None:
    if not data["nodes"]:
        raise RuntimeError("no publishable nodes; refusing to overwrite output")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (PUBLIC_DIR / "nodes.txt").write_text(nodes_text, encoding="utf-8")
    (PUBLIC_DIR / "index.html").write_text("<!doctype html><meta charset='utf-8'><title>VPNGate SSTP nodes</title>"
        "<p>公开 VPNGate 元数据；不保证住宅、可用性、稳定性、匿名性或隐私。</p>"
        "<p><a href='data.json'>data.json</a> · <a href='nodes.txt'>nodes.txt</a></p>\n", encoding="utf-8")


def main() -> int:
    try:
        rows = parse_rows(fetch_csv())
        candidates = to_nodes(rows)
        if not candidates:
            raise RuntimeError("no TCP/SSTP nodes parsed; refusing to overwrite output")
        checked = bool(CHECK_WORKER)
        if CHECK_REQUIRED and not checked:
            raise RuntimeError("CHECK_WORKER is required")
        results = check_nodes(candidates) if checked else candidates
        if checked and not any(node.get("success") is True for node in results):
            raise RuntimeError("checker returned no success=true nodes; refusing to overwrite output")
        data, nodes_text = build_outputs(results, len(rows), checked)
        write_public(data, nodes_text)
        print(json.dumps({"raw_rows": len(rows), "candidates": len(candidates), "published_nodes": len(data["nodes"]), "status": data["status"]}))
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
