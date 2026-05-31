from __future__ import annotations

import argparse
import json
from typing import Any

from pipeline.decision.npm_backfill import (
    NpmRegistryClient,
    download_count,
    github_key_from_repository,
    repository_url_from_metadata,
)


def summarize_npm_result(
    *,
    package: str,
    period: str,
    metadata: dict[str, Any],
    downloads: dict[str, Any],
) -> dict[str, Any]:
    repository_url = repository_url_from_metadata(metadata)
    github_key = github_key_from_repository(repository_url)
    return {
        "ok": True,
        "package": package,
        "period": period,
        "downloads": download_count(downloads),
        "has_repository": bool(repository_url),
        "github_key": github_key,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one bounded npm registry/download smoke call."
    )
    parser.add_argument("--package", default="react")
    parser.add_argument("--period", default="last-day")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)

    client = NpmRegistryClient(timeout=args.timeout)
    try:
        metadata = client.package_metadata(args.package)
        downloads = client.downloads(args.package, args.period)
        summary = summarize_npm_result(
            package=args.package,
            period=args.period,
            metadata=metadata,
            downloads=downloads,
        )
    except Exception as exc:
        summary = {
            "ok": False,
            "package": args.package,
            "period": args.period,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
