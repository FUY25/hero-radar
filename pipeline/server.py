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
import mimetypes
import shutil
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.dashboard_data import build_dashboard_data
from pipeline.decision.candidate_context import context_bundle_for_entity
from pipeline.decision.schema import init_decision_db

CONFIG_PATH = ROOT / "pipeline" / "config.json"
DASHBOARD_PATH = ROOT / "data" / "exports" / "dashboard.html"
DB_PATH = ROOT / "data" / "hero_radar.sqlite"
WEB_DIST_PATH = ROOT / "web" / "dist"
PYTHON = sys.executable or "python3"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def add_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def json_response(
    handler: BaseHTTPRequestHandler,
    payload: Any,
    *,
    status: int = 200,
    cors: bool = False,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if cors:
        add_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def text_response(
    handler: BaseHTTPRequestHandler,
    body: str,
    *,
    status: int = 200,
    content_type: str = "text/html",
    cors: bool = False,
) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-store")
    if cors:
        add_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(encoded)


def bytes_response(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/octet-stream",
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


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


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def connect_decision_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    init_decision_db(conn)
    return conn


def query_latest_decision_run(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        select run_id
        from decision_runs
        where status = 'ok'
        order by coalesce(completed_at, started_at) desc, started_at desc
        limit 1
        """
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        """
        select run_id
        from decision_runs
        where status = 'running'
        order by started_at desc
        limit 1
        """
    ).fetchone()
    return row[0] if row else None


def query_candidates(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    candidates = []
    for row in conn.execute(
        """
        select pc.entity_id, e.canonical_entity, e.canonical_key, pc.level, pc.fired_families_json, pc.first_trigger_at
        from potential_candidates pc
        join entities e on e.entity_id = pc.entity_id
        where pc.run_id = ?
        order by
            case pc.level when 'high_potential' then 0 when 'potential' then 1 else 2 end,
            e.canonical_entity
        """,
        (run_id,),
    ).fetchall():
        payload = {
            "entity_id": row[0],
            "canonical_entity": row[1],
            "canonical_key": row[2],
            "level": row[3],
            "fired_families": json_loads(row[4], []),
            "first_trigger_at": row[5],
        }
        payload.update(context_bundle_for_entity(conn, entity_id=payload["entity_id"], run_id=run_id))
        candidates.append(payload)

    edge_watch = []
    for row in conn.execute(
        """
        select ew.entity_id, e.canonical_entity, e.canonical_key, ew.reason_json, ew.source_refs_json, ew.status
        from edge_watch_candidates ew
        join entities e on e.entity_id = ew.entity_id
        where ew.run_id = ?
        order by e.canonical_entity
        """,
        (run_id,),
    ).fetchall():
        payload = {
            "entity_id": row[0],
            "canonical_entity": row[1],
            "canonical_key": row[2],
            "level": "edge_watch",
            "reasons": json_loads(row[3], []),
            "source_refs": json_loads(row[4], []),
            "status": row[5],
        }
        payload.update(context_bundle_for_entity(conn, entity_id=payload["entity_id"], run_id=run_id))
        edge_watch.append(payload)
    return {"run_id": run_id, "candidates": candidates, "edge_watch": edge_watch}


def query_dashboard_data_payload() -> dict[str, Any]:
    payload = build_dashboard_data(db_path=DB_PATH, config=read_json(CONFIG_PATH))
    conn = connect_decision_db()
    try:
        run_id = query_latest_decision_run(conn) or ""
        payload["candidates"] = (
            query_candidates(conn, run_id)
            if run_id
            else {"run_id": "", "candidates": [], "edge_watch": []}
        )
    finally:
        conn.close()
    return payload


def build_run_command(payload: Any) -> list[str]:
    options = payload if isinstance(payload, dict) else {}
    cmd = [PYTHON, str(ROOT / "pipeline" / "run_daily.py")]
    only = options.get("only")
    if only:
        if not isinstance(only, list) or not all(isinstance(item, str) for item in only):
            raise ValueError("only must be a list of adapter names")
        cmd.extend(["--only-source", ",".join(only)])
    if options.get("skip_sources"):
        cmd.append("--skip-sources")
    if options.get("skip_decision"):
        cmd.append("--skip-decision")
    if options.get("no_backfill") or options.get("backfill") is False:
        cmd.append("--no-backfill")

    string_options = {
        "run_id": "--run-id",
        "now": "--now",
        "llm_model": "--llm-model",
        "x_credible_handles": "--x-credible-handles",
    }
    for key, flag in string_options.items():
        value = options.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            cmd.extend([flag, value])

    integer_options = {
        "classify_hn_limit": "--classify-hn-limit",
        "classify_x_limit": "--classify-x-limit",
        "llm_concurrency": "--llm-concurrency",
        "x_stage1_batch_size": "--x-stage1-batch-size",
        "resolver_search_limit": "--resolver-search-limit",
        "resolver_research_limit": "--resolver-research-limit",
        "resolver_research_rounds": "--resolver-research-rounds",
        "enrich_readme_limit": "--enrich-readme-limit",
    }
    for key, flag in integer_options.items():
        value = options.get(key)
        if value is not None:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{key} must be an integer")
            cmd.extend([flag, str(value)])
    return cmd


def resolve_entity_lookup(conn: sqlite3.Connection, entity_or_key: str) -> str:
    if entity_or_key.startswith("entity:"):
        return entity_or_key
    row = conn.execute(
        "select entity_id from entities where canonical_key = ? limit 1",
        (entity_or_key,),
    ).fetchone()
    return row[0] if row else entity_or_key


def query_evidence(conn: sqlite3.Connection, entity_id: str, run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row[0],
            "entity_id": row[1],
            "canonical_entity": row[2],
            "alias": row[3],
            "source": row[4],
            "event_at": row[5],
            "relative_to_reference": row[6],
            "metric_name": row[7],
            "metric_value": row[8],
            "family": row[9],
            "rule_id": row[10],
            "rule_version": row[11],
            "signal_label": row[12],
            "historical_safety": row[13],
            "note": row[14],
            "raw_url_or_ref": row[15],
            "run_id": row[16],
        }
        for row in conn.execute(
            """
            select id, entity_id, canonical_entity, alias, source, event_at, relative_to_reference, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id
            from evidence_rows
            where entity_id = ? and run_id = ?
            order by event_at desc, id desc
            """,
            (entity_id, run_id),
        ).fetchall()
    ]


def query_entity(conn: sqlite3.Connection, entity_id: str, run_id: str) -> dict[str, Any]:
    entity_row = conn.execute(
        """
        select entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json
        from entities
        where entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    entity = (
        {
            "entity_id": entity_row[0],
            "canonical_entity": entity_row[1],
            "canonical_key": entity_row[2],
            "key_type": entity_row[3],
            "first_seen": entity_row[4],
            "aliases": json_loads(entity_row[5], []),
            "source_item_ids": json_loads(entity_row[6], []),
        }
        if entity_row
        else {"entity_id": entity_id}
    )
    candidate_row = conn.execute(
        """
        select level, fired_families_json, first_trigger_at
        from potential_candidates
        where entity_id = ? and run_id = ?
        """,
        (entity_id, run_id),
    ).fetchone()
    candidate = (
        {
            "entity_id": entity_id,
            "level": candidate_row[0],
            "fired_families": json_loads(candidate_row[1], []),
            "first_trigger_at": candidate_row[2],
        }
        if candidate_row
        else None
    )
    edge_row = conn.execute(
        """
        select reason_json, source_refs_json, status
        from edge_watch_candidates
        where entity_id = ? and run_id = ?
        """,
        (entity_id, run_id),
    ).fetchone()
    edge_watch = (
        {
            "entity_id": entity_id,
            "reasons": json_loads(edge_row[0], []),
            "source_refs": json_loads(edge_row[1], []),
            "status": edge_row[2],
        }
        if edge_row
        else None
    )
    backfill_jobs = [
        {
            "id": row[0],
            "source": row[1],
            "reason": row[2],
            "status": row[3],
            "requested_at": row[4],
            "completed_at": row[5],
            "result_ref": row[6],
        }
        for row in conn.execute(
            """
            select id, source, reason, status, requested_at, completed_at, result_ref
            from backfill_jobs
            where entity_id = ? and run_id = ?
            order by id
            """,
            (entity_id, run_id),
        ).fetchall()
    ]
    return {
        "run_id": run_id,
        "entity": entity,
        "candidate": candidate,
        "edge_watch": edge_watch,
        "backfill_jobs": backfill_jobs,
        "evidence": query_evidence(conn, entity_id, run_id),
        "context": context_bundle_for_entity(conn, entity_id=entity_id, run_id=run_id),
    }


def serve_web_dist(handler: BaseHTTPRequestHandler, path: str) -> bool:
    if path == "/app" or path.startswith("/app/"):
        index_path = WEB_DIST_PATH / "index.html"
        if not index_path.exists():
            text_response(
                handler,
                "web/dist/index.html not found. Run `cd web && npm run build` first.",
                status=404,
                content_type="text/plain",
            )
            return True
        text_response(handler, index_path.read_text())
        return True
    if path.startswith("/assets/"):
        rel = path.lstrip("/")
        asset_path = (WEB_DIST_PATH / rel).resolve()
        if not str(asset_path).startswith(str(WEB_DIST_PATH.resolve())) or not asset_path.is_file():
            return False
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        bytes_response(handler, asset_path.read_bytes(), content_type=content_type)
        return True
    return False


class HeroRadarHandler(BaseHTTPRequestHandler):
    server_version = "HeroRadarLocal/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")

    def do_OPTIONS(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_response(204)
            add_cors_headers(self)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if path == "/api/dashboard-data":
            json_response(self, query_dashboard_data_payload(), cors=True)
            return
        if path == "/api/candidates":
            with connect_decision_db() as conn:
                run_id = query_latest_decision_run(conn) or ""
                payload = query_candidates(conn, run_id) if run_id else {"run_id": "", "candidates": [], "edge_watch": []}
            json_response(self, payload, cors=True)
            return
        if path == "/api/evidence":
            query = parse_qs(parsed.query)
            requested_entity_id = (query.get("entity_id") or [""])[0]
            with connect_decision_db() as conn:
                run_id = query_latest_decision_run(conn) or ""
                entity_id = resolve_entity_lookup(conn, requested_entity_id) if requested_entity_id else ""
                payload = {
                    "run_id": run_id,
                    "entity_id": entity_id,
                    "evidence": query_evidence(conn, entity_id, run_id) if run_id and entity_id else [],
                }
            json_response(self, payload, cors=True)
            return
        if path.startswith("/api/entity/"):
            requested_entity_id = unquote(path.removeprefix("/api/entity/"))
            with connect_decision_db() as conn:
                run_id = query_latest_decision_run(conn) or ""
                entity_id = resolve_entity_lookup(conn, requested_entity_id)
                payload = (
                    query_entity(conn, entity_id, run_id)
                    if run_id
                    else {
                        "run_id": "",
                        "entity": {"entity_id": entity_id},
                        "candidate": None,
                        "edge_watch": None,
                        "backfill_jobs": [],
                        "evidence": [],
                    }
                )
            json_response(self, payload, cors=True)
            return
        if path == "/api/config":
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
                cors=True,
            )
            return
        if path == "/api/health":
            json_response(
                self,
                {
                    "ok": True,
                    "dashboard_exists": DASHBOARD_PATH.exists(),
                    "config_path": str(CONFIG_PATH),
                },
                cors=True,
            )
            return
        if path.startswith("/api/"):
            json_response(self, {"ok": False, "error": "Not found"}, status=404, cors=True)
            return
        if path in {"/", "/dashboard", "/dashboard.html"}:
            if not DASHBOARD_PATH.exists():
                text_response(self, "dashboard.html not found. Run `python3 pipeline/run_pipeline.py --export-only` first.", status=404, content_type="text/plain")
                return
            text_response(self, DASHBOARD_PATH.read_text())
            return
        if serve_web_dist(self, path):
            return
        text_response(self, "Not found", status=404, content_type="text/plain")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/config":
            try:
                payload = read_request_json(self)
                config = safe_config_payload(payload)
                timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = CONFIG_PATH.with_suffix(f".json.{timestamp}.bak")
                shutil.copy2(CONFIG_PATH, backup)
                CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
                json_response(self, {"ok": True, "config_path": str(CONFIG_PATH), "backup_path": str(backup), "takes_effect": "next pipeline run"}, cors=True)
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400, cors=True)
            return

        if path == "/api/run":
            try:
                payload = read_request_json(self)
                cmd = build_run_command(payload)
                result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600, check=False)
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
                    cors=True,
                )
            except Exception as exc:  # noqa: BLE001
                json_response(self, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400, cors=True)
            return

        if path.startswith("/api/"):
            json_response(self, {"ok": False, "error": "Not found"}, status=404, cors=True)
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
