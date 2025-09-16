#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys

import pandas as pd
import httpx

from src.ingest.client import NHLClient
from src.ingest.fetch import game_pks_today
from src.transform.plays_to_shots import shots_from_feed
from src.transform.features import engineer
from src.transform.normalize import normalize_coordinates


def _fallback_demo(out_path: Path) -> None:
    # Write a tiny synthetic dataset so the app still runs.
    demo = pd.DataFrame(
        {
            "x": [-70, -30, 10, 40, 75, 60, -55, 20, 85, -80],
            "y": [0, 10, -15, 20, -5, 30, -25, 5, 0, 12],
            "player": [
                "Aho",
                "Aho",
                "Matthews",
                "Pastrnak",
                "Svechnikov",
                "Aho",
                "Matthews",
                "Pastrnak",
                "Svechnikov",
                "Aho",
            ],
            "strength": ["5v5", "PP", "5v5", "5v5", "PK", "5v5", "PP", "5v5", "5v5", "PK"],
            "is_goal": [0, 1, 0, 0, 1, 0, 0, 1, 1, 0],
        }
    )
    demo.to_csv(out_path, index=False)
    print(f"[fallback] Wrote demo data to {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest NHL shots for a given day (default: today).")
    ap.add_argument("--date", type=str, help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    target_day = date.today()
    if args.date:
        try:
            target_day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"[error] --date must be YYYY-MM-DD (got: {args.date})")
            sys.exit(2)

    out_dir = Path("data/curated")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "shots_latest.csv"

    client = NHLClient()

    try:
        pks = game_pks_today(client, target_day)
    except httpx.HTTPError as e:
        print(f"[network] Could not fetch schedule for {target_day}: {e}")
        _fallback_demo(out_path)
        return

    if not pks:
        print(f"[info] No games found on {target_day}. Using fallback demo data.")
        _fallback_demo(out_path)
        return

    frames = []
    for pk in pks:
        try:
            feed = client.game_feed_live(pk)
        except httpx.HTTPError as e:
            print(f"[network] Failed to fetch game {pk}: {e}")
            continue
        df = shots_from_feed(feed)
        if not df.empty:
            frames.append(df)

    if not frames:
        print(f"[info] Found {len(pks)} games but no shot events with coordinates. Using fallback demo data.")
        _fallback_demo(out_path)
        return

    shots = pd.concat(frames, ignore_index=True)
    shots = normalize_coordinates(shots)
    shots = engineer(shots)

    shots.to_csv(out_path, index=False)
    print(f"[ok] Wrote {out_path} with {len(shots)} rows from {len(pks)} game(s) on {target_day}.")


if __name__ == "__main__":
    main()
