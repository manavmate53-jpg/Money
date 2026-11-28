#!/usr/bin/env python3
"""
Instant GitHub Actions Dispatcher (`repository_dispatch`).
Eliminates GitHub free-tier cron delays (6-10h lag) by firing instant dispatch events
from local macOS launchd or zero-cost external webhooks (e.g., cron-job.org).

Usage:
  GITHUB_TOKEN=ghp_... python scripts/trigger_dispatch.py --event run-hourly-desk
  python scripts/trigger_dispatch.py --emit-launchd
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "manavmate53-jpg/Money"
VALID_EVENTS = ("run-hourly-desk", "run-macro-desk", "run-council-desk")
DESK_TO_EVENT = {
    "hourly": "run-hourly-desk",
    "macro": "run-macro-desk",
    "council": "run-council-desk",
}


def fire_dispatch(
    event_type: str,
    repo: str = DEFAULT_REPO,
    token: str = "",
    dry_run: bool = False,
) -> dict:
    """Posts a `repository_dispatch` event to GitHub's REST API."""
    if event_type not in VALID_EVENTS:
        raise ValueError(f"Unsupported event_type '{event_type}'. Choose from {VALID_EVENTS}.")

    url = f"https://api.github.com/repos/{repo}/dispatches"
    payload = {
        "event_type": event_type,
        "client_payload": {"triggered_by": "scripts/trigger_dispatch.py"},
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "url": url, "payload": payload}

    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN (or GH_TOKEN) environment variable.")

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Money-Dispatch-Trigger/1.0",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": resp.status in (200, 204), "status": resp.status, "event_type": event_type}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "error": detail}


def emit_launchd_plist(repo: str = DEFAULT_REPO) -> str:
    """Generates a ready-to-install macOS launchd plist for exact hourly instant dispatch."""
    script_abs = os.path.abspath(__file__)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.money.hourly.dispatch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>{script_abs}</string>
    <string>--event</string>
    <string>run-hourly-desk</string>
    <string>--repo</string>
    <string>{repo}</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
"""


def main():
    parser = argparse.ArgumentParser(description="Fire GitHub Actions repository_dispatch events")
    parser.add_argument("--event", choices=list(VALID_EVENTS), default=None)
    parser.add_argument("--desk", choices=list(DESK_TO_EVENT.keys()), default=None, help="Alias: hourly | macro | council")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--emit-launchd", "--print-launchd-plist", dest="emit_launchd", action="store_true", help="Print macOS launchd plist and exit")
    args = parser.parse_args()

    event_type = args.event or (DESK_TO_EVENT[args.desk] if args.desk else "run-hourly-desk")

    if args.emit_launchd:
        print(emit_launchd_plist(args.repo))
        return

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    res = fire_dispatch(event_type, repo=args.repo, token=token, dry_run=args.dry_run)
    print(json.dumps(res, indent=2))
    if not res.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
