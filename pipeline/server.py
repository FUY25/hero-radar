#!/usr/bin/env python3
"""Local backend for Hero Radar.

This is intentionally small: serve the generated dashboard, expose the current
config, allow a reviewed config write, and optionally trigger a manual run.
Cron is deliberately out of scope for now.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pipeline" / "config.json"
DASHBOARD_PATH = ROOT / "data" / "exports" / "dashboard.html"
PYTHON = sys.executable or "python3"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def json_response(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, body: str, *, status: int = 200, content_type: str = "text/html") -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(encoded)


def read_request_json(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def safe_config_payload(payload: Any) -> dict[str, Any]:
    config = payload.get("config") if isinstance(payload, dict) and "config" in payload else payload
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    required = {"github_trending", "github_search", "hn", "apify"}
    missing = sorted(key for key in required if key not in config)
    if missing:
        raise ValueError(f"config missing required key(s): {', '.join(missing)}")
    return config


class HeroRadarHandler(BaseHTTPRequestHandler):
    server_version = "HeroRadarLocal/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if self.path in {"/", "/dashboard", "/dashboard.html"}:
            if not DASHBOARD_PATH.exists():
                text_response(self, "dashboard.html not found. Run `python3 pipeline/run_pipeline.py --export-only` first.", status=404, content_type="text/plain")
                return
            text_response(self, DASHBOARD_PATH.read_text())
            return
        if self.path == "/api/config":
            json_response(
                self,
                {
                    "config": read_json(CONFIG_PATH),
                    "meta": {
                        "config_path": str(CONFIG_PATH),
                        "default_schedule": "24h",
                        "cron_enabled": False,
                        "takes_effect": "next pipeline run",
                    },
                },
            )
            return
        if self.path == "/api/health":
            json_response(self, {"ok": True, "dashboard_exists": DASHBOARD_PATH.exists(), "config_path": str(CONFIG_PATH)})
            return
        text_response(self, "Not found", status=404, content_type="text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/config":
            try:
                payload = read_request_json(self)
                config = safe_config_payload(payload)
                timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = CONFIG_PATH.with_suffix(f".json.{timestamp}.bak")
                shutil.copy2(CONFIG_PATH, backup)
                CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
                json_response(self, {"ok": True, "config_path": str(CONFIG_PATH), "backup_path": str(backup), "takes_effect": "next pipeline run"})
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)
            return

        if self.path == "/api/run":
            try:
                payload = read_request_json(self)
                only = payload.get("only") if isinstance(payload, dict) else None
                cmd = [PYTHON, str(ROOT / "pipeline" / "run_pipeline.py")]
                if only:
                    if not isinstance(only, list) or not all(isinstance(item, str) for item in only):
                        raise ValueError("only must be a list of adapter names")
                    cmd.extend(["--only", ",".join(only)])
                result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False)
                json_response(
                    self,
                    {
                        "ok": result.returncode == 0,
                        "returncode": result.returncode,
                        "stdout": result.stdout[-4000:],
                        "stderr": result.stderr[-8000:],
                        "dashboard": str(DASHBOARD_PATH),
                    },
                    status=200 if result.returncode == 0 else 500,
                )
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400)
            return

        text_response(self, "Not found", status=404, content_type="text/plain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), HeroRadarHandler)
    print(f"Hero Radar local server: http://{args.host}:{args.port}/", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
