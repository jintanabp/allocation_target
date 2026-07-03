#!/usr/bin/env python3
"""Smoke test หลัง deploy — health + unittest ชุดหลัก"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _fetch_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json

        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Post-deploy smoke test")
    ap.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--skip-http", action="store_true")
    args = ap.parse_args()
    base = str(args.base_url).rstrip("/")
    failed = 0

    if not args.skip_http:
        try:
            health = _fetch_json(f"{base}/health")
            print(f"health: {health.get('status')} build={((health.get('build') or {}).get('version'))}")
            if health.get("status") != "ok":
                print("health status not ok", file=sys.stderr)
                failed += 1
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"health check failed: {e}", file=sys.stderr)
            failed += 1

    print("running unittest…")
    rc = subprocess.call(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=REPO,
    )
    if rc != 0:
        failed += 1

    if failed:
        print("smoke_deploy: FAILED", file=sys.stderr)
        return 1
    print("smoke_deploy: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
