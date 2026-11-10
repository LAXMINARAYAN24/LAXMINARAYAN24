#!/usr/bin/env python3
"""Write data/contributions.json (the last 365 days of GitHub contributions).

    GH_TOKEN=... python scripts/fetch_contributions.py
    python scripts/fetch_contributions.py --demo     # fake data, for previewing only

The render script only reads the JSON file, so you can also produce it any other way:
    {"login": "...", "total": 123, "days": [{"date": "2026-01-31", "count": 4}, ...]}
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(login: str, token: str) -> dict:
    today = dt.datetime.now(dt.timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0)
    start = (today - dt.timedelta(days=364)).replace(hour=0, minute=0, second=0)
    body = json.dumps({
        "query": QUERY,
        "variables": {"login": login, "from": start.isoformat(), "to": today.isoformat()},
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors") or not payload.get("data", {}).get("user"):
        raise SystemExit(f"GraphQL error: {payload.get('errors') or 'user not found'}")
    cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": d["date"], "count": d["contributionCount"]}
        for week in cal["weeks"] for d in week["contributionDays"]
    ]
    days.sort(key=lambda d: d["date"])
    return {"login": login, "total": cal["totalContributions"], "days": days}


def demo(login: str) -> dict:
    rng = random.Random(7)
    end = dt.date.today()
    days, streak_left = [], 0
    for i in range(364, -1, -1):
        d = end - dt.timedelta(days=i)
        if streak_left == 0 and rng.random() < 0.05:
            streak_left = rng.randint(3, 12)
        busy = streak_left > 0
        streak_left = max(0, streak_left - 1)
        p = (0.85 if busy else 0.22) * (0.6 if d.weekday() >= 5 else 1.0)
        count = rng.choice([1, 1, 2, 3, 4, 6, 9]) if rng.random() < p else 0
        days.append({"date": d.isoformat(), "count": count})
    return {"login": login, "demo": True, "total": sum(d["count"] for d in days), "days": days}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default=None)
    ap.add_argument("--out", default=str(ROOT / "data" / "contributions.json"))
    ap.add_argument("--demo", action="store_true", help="write fake data (marked demo) for previews")
    args = ap.parse_args()

    profile = json.loads((ROOT / "data" / "profile.json").read_text(encoding="utf-8"))
    login = args.login or profile["login"]
    if args.demo:
        data = demo(login)
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GH_TOKEN (or GITHUB_TOKEN), or pass --demo.")
        data = fetch(login, token)
    Path(args.out).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {args.out}: {len(data['days'])} days, {data['total']} contributions"
          + (" (DEMO DATA)" if data.get("demo") else ""))


if __name__ == "__main__":
    main()
