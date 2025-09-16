# src/transform/plays_to_shots.py
from __future__ import annotations
import pandas as pd

# StatsAPI (legacy) labels
STATS_SHOT_EVENTS = {"Shot", "Missed Shot", "Goal"}

# GameCenter (api-web) labels
GC_SHOT_EVENTS = {"shot-on-goal", "missed-shot", "goal"}


def _rows_from_statsapi(feed: dict) -> list[dict]:
    rows: list[dict] = []
    plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", []) or []
    for p in plays:
        ev = (p.get("result", {}) or {}).get("event")
        if ev not in STATS_SHOT_EVENTS:
            continue
        coords = p.get("coordinates") or {}
        x, y = coords.get("x"), coords.get("y")
        if x is None or y is None:
            continue

        about = p.get("about") or {}
        team = (p.get("team") or {}).get("triCode") or (p.get("team") or {}).get("name")
        players = p.get("players") or []
        shooter = None
        for pl in players:
            if pl.get("playerType") in ("Shooter", "Scorer"):
                shooter = (pl.get("player") or {}).get("fullName")
                break

        strength = ((p.get("result") or {}).get("strength") or {}).get("name") or "Unknown"
        rows.append(
            {
                "gamePk": about.get("gamePk"),
                "period": about.get("period"),
                "periodTime": about.get("periodTime"),
                "event": ev,
                "team": team,
                "player": shooter or "Unknown",
                "x": float(x),
                "y": float(y),
                "strength": strength,
                "is_goal": 1 if ev == "Goal" else 0,
            }
        )
    return rows


def _rows_from_gamecenter(feed: dict) -> list[dict]:
    rows: list[dict] = []
    plays_root = feed.get("plays") or {}
    plays = (
        plays_root.get("all")
        or plays_root.get("currentPlay")
        or plays_root.get("byPeriod")
        or []
    )

    if isinstance(plays, list) and plays and isinstance(plays[0], dict) and "plays" in plays[0]:
        grouped = []
        for block in plays:
            grouped.extend(block.get("plays") or [])
        plays = grouped

    if not isinstance(plays, list):
        plays = []

    for p in plays:
        ev_key = (p.get("typeDescKey") or p.get("typeCode") or "").lower()
        if ev_key not in GC_SHOT_EVENTS:
            continue

        det = p.get("details") or {}
        x, y = det.get("xCoord"), det.get("yCoord")
        if x is None or y is None:
            continue

        team = det.get("eventOwnerTeamAbbrev") or det.get("eventOwnerTeamId") or None
        shooter = det.get("shootingPlayerName") or det.get("scoringPlayerName") or "Unknown"

        pd_desc = p.get("periodDescriptor") or {}
        period = pd_desc.get("number")
        period_time = p.get("timeInPeriod") or p.get("timeRemaining") or None

        raw_strength = (det.get("strength") or "").lower()
        strength = {
            "ev": "5v5",
            "even": "5v5",
            "pp": "PP",
            "power play": "PP",
            "sh": "PK",
            "penalty kill": "PK",
        }.get(raw_strength, raw_strength.upper() if raw_strength else "Unknown")

        rows.append(
            {
                "gamePk": feed.get("id") or feed.get("gameId"),
                "period": period,
                "periodTime": period_time,
                "event": "Goal" if ev_key == "goal" else ("Shot" if ev_key == "shot-on-goal" else "Missed Shot"),
                "team": team,
                "player": shooter,
                "x": float(x),
                "y": float(y),
                "strength": strength,
                "is_goal": 1 if ev_key == "goal" else 0,
            }
        )
    return rows


def shots_from_feed(feed: dict) -> pd.DataFrame:
    """
    Produce a normalized shots DataFrame from either the StatsAPI feed or the GameCenter feed.
    Columns: gamePk, period, periodTime, event, team, player, x, y, strength, is_goal
    """
    # Try StatsAPI first
    rows = _rows_from_statsapi(feed)
    source = "StatsAPI"

    if not rows:
        # Fallback to GameCenter (api-web)
        rows = _rows_from_gamecenter(feed)
        source = "GameCenter"

    df = pd.DataFrame(
        rows,
        columns=[
            "gamePk",
            "period",
            "periodTime",
            "event",
            "team",
            "player",
            "x",
            "y",
            "strength",
            "is_goal",
        ],
    )

    # Store parser source as attribute so Streamlit can show it
    df.attrs["parser_source"] = source
    print(f"[parser] Extracted {len(df)} shots using {source}")

    return df
