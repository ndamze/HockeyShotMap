"""
_shared.py — shared utilities for HockeyShotMap Streamlit pages.

What’s new here:
- Defensive imports with clear error messages (so a missing dependency doesn’t look
  like a missing file).
- No side effects at import time (no network calls or heavy work).
- All helpers are pure functions; caching only inside fetch_shots_dataframe().
"""

# -----------------------
# Safe, explicit imports
# -----------------------
try:
    import math
    import functools
    from typing import List, Tuple, Dict, Optional
except Exception as e:
    raise RuntimeError(
        "Failed to import Python stdlib modules in _shared.py. "
        "Your Python environment might be corrupted."
    ) from e

try:
    import pandas as pd
    import numpy as np
except Exception as e:
    raise ImportError(
        "Failed to import pandas/numpy in _shared.py. "
        "Install them in this environment:\n\n    pip install pandas numpy"
    ) from e

try:
    import requests
except Exception as e:
    raise ImportError(
        "Failed to import requests in _shared.py. "
        "Install it with:\n\n    pip install requests"
    ) from e


# -----------------------
# Constants & helpers
# -----------------------
NHL_API = "https://statsapi.web.nhl.com/api/v1"

def _to_date_str(d):
    if isinstance(d, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(d).date().isoformat()
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)


# -----------------------
# Data fetchers
# -----------------------
def fetch_games_between(start_date: str, end_date: str) -> List[int]:
    """
    Return list of NHL gamePk between start_date and end_date inclusive.
    Uses the public NHL StatsAPI schedule endpoint.
    """
    start_date = _to_date_str(start_date)
    end_date = _to_date_str(end_date)
    url = f"{NHL_API}/schedule?startDate={start_date}&endDate={end_date}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    game_ids = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            game_ids.append(g["gamePk"])
    return game_ids


def fetch_game_feed(game_pk: int) -> dict:
    """
    Return the full game feed (play-by-play) for a given gamePk.
    """
    url = f"{NHL_API}/game/{game_pk}/feed/live"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


# -----------------------
# Geometry & models
# -----------------------
def _normalize_xy(x, y):
    """
    Flip coordinates so that all shots are toward +x (opponent goal approx at +89).
    Works even if x/y are None by returning NaNs.
    """
    if x is None or y is None:
        return (np.nan, np.nan)
    if x < 0:
        return (-x, -y)
    return (x, y)


def _goal_distance_angle(x, y):
    """
    Compute distance and angle (degrees) to attacking goal line after normalization.

    - Rink is approximately 200x85; attacking goal line ~ x=89 in NHL StatsAPI coords.
    - Angle is 0 straight ahead on the centerline, 90 at the boards (when dx==0).
    """
    if isinstance(x, (float, int)) and isinstance(y, (float, int)):
        x = float(x)
        y = float(y)
    else:
        return (np.nan, np.nan)
    if np.isnan(x) or np.isnan(y):
        return (np.nan, np.nan)
    dx = 89.0 - x
    dy = abs(y)
    dist = float(math.hypot(dx, dy))
    angle = float(math.degrees(math.atan2(dy, dx))) if dx != 0 else 90.0
    return (dist, angle)


def _danger_zone(dist, angle):
    """
    Simple danger classification:
      - high:   <=25 ft and <=30°
      - medium: <=40 ft and <=45°
      - low:    otherwise
    """
    if np.isnan(dist):
        return "unknown"
    if dist <= 25 and angle <= 30:
        return "high"
    if dist <= 40 and angle <= 45:
        return "medium"
    return "low"


def _simple_xg(dist, angle, shot_type: Optional[str] = None):
    """
    Lightweight logistic expected-goals approximation using distance & angle.
    Coefficients are heuristic. Replace with your trained model if available.
    """
    if np.isnan(dist) or np.isnan(angle):
        return np.nan
    # logistic: 1 / (1 + exp(-(b0 + b1*dist + b2*angle + type_adj)))
    b0, b1, b2 = -2.7, -0.07, -0.015
    type_adj = {
        "Tip-In": 0.35, "Deflected": 0.25, "Wrap-around": 0.15,
        "Backhand": 0.05, "Wrist Shot": 0.00, "Snap Shot": 0.02,
        "Slap Shot": -0.05, "Unknown": 0.0
    }
    t_adj = type_adj.get(shot_type or "Unknown", 0.0)
    z = b0 + b1 * dist + b2 * angle + t_adj
    return 1.0 / (1.0 + math.exp(-z))


# -----------------------
# Event parsing
# -----------------------
def _extract_event_row(play: dict, game_info: dict) -> Optional[dict]:
    """
    Convert a single play dict from the StatsAPI into a tidy row with:
    normalized x/y, distance, angle, danger, xG, shooter/goalie IDs, etc.
    """
    et = play.get("result", {}).get("eventTypeId")
    if et not in ("SHOT", "GOAL", "MISSED_SHOT", "BLOCKED_SHOT"):
        return None

    coords = play.get("coordinates", {}) or {}
    x, y = coords.get("x"), coords.get("y")
    if x is None or y is None:
        # keep the row for totals even if no coordinates
        x, y = (np.nan, np.nan)
    x, y = _normalize_xy(x, y)
    dist, ang = _goal_distance_angle(x, y)

    players = play.get("players", []) or []
    shooter_id, shooter_name = None, None
    goalie_id, goalie_name = None, None
    shot_type = play.get("result", {}).get("secondaryType")

    for p in players:
        ptype = p.get("playerType")
        if ptype in ("Shooter", "Scorer"):
            shooter_id = p["player"]["id"]
            shooter_name = p["player"]["fullName"]
        if ptype == "Goalie":
            goalie_id = p["player"]["id"]
            goalie_name = p["player"]["fullName"]

    team_name = (play.get("team") or {}).get("name")
    strength = (play.get("about", {}).get("strength") or {}).get("code")  # None for non-goal events
    period = play.get("about", {}).get("period")
    period_time = play.get("about", {}).get("periodTime")

    is_goal = (et == "GOAL")
    is_shot_on_goal = (et in ("SHOT", "GOAL"))

    row = {
        "gamePk": game_info["gamePk"],
        "gameDate": game_info.get("gameDate"),
        "eventType": et,
        "team": team_name,
        "period": period,
        "periodTime": period_time,
        "strength": strength if strength else ("EVEN" if is_shot_on_goal else "ALL"),
        "x": x, "y": y,
        "distance": dist, "angle": ang,
        "shotType": shot_type or "Unknown",
        "shooterId": shooter_id, "shooterName": shooter_name,
        "goalieId": goalie_id, "goalieName": goalie_name,
        "isGoal": is_goal,
    }
    row["danger"] = _danger_zone(dist, ang)
    row["xG"] = _simple_xg(dist, ang, row["shotType"])
    return row


# -----------------------
# Public API (used by pages)
# -----------------------
@functools.lru_cache(maxsize=64)
def fetch_shots_dataframe(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Fetch all shot-like events between dates and return a tidy DataFrame.

    Columns:
      gamePk, gameDate, date, eventType, team, period, periodTime, strength,
      x, y, distance, angle, shotType, shooterId, shooterName,
      goalieId, goalieName, isGoal, isSOG, danger, xG
    """
    start_date = _to_date_str(start_date)
    end_date = _to_date_str(end_date)

    game_ids = fetch_games_between(start_date, end_date)
    rows = []
    for gid in game_ids:
        try:
            feed = fetch_game_feed(gid)
        except Exception:
            # Skip games that fail to fetch (network hiccup, etc.)
            continue

        game_info = {
            "gamePk": gid,
            "gameDate": (feed.get("gameData", {}).get("datetime", {}) or {}).get("dateTime")
        }
        all_plays = (feed.get("liveData", {}).get("plays", {}) or {}).get("allPlays", [])
        for play in all_plays:
            row = _extract_event_row(play, game_info)
            if row:
                rows.append(row)

    cols = [
        "gamePk","gameDate","eventType","team","period","periodTime","strength",
        "x","y","distance","angle","shotType","shooterId","shooterName",
        "goalieId","goalieName","isGoal","danger","xG"
    ]
    if not rows:
        df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(rows)[cols]

    # Derived fields
    if "gameDate" in df.columns:
        df["date"] = pd.to_datetime(df["gameDate"]).dt.date
    else:
        df["date"] = pd.NaT
    df["isSOG"] = df["eventType"].isin(["SHOT", "GOAL"])
    return df


def list_teams(df: pd.DataFrame) -> List[str]:
    vals = sorted([t for t in df.get("team", pd.Series([], dtype=object)).dropna().unique().tolist() if t])
    return vals


def list_players(df: pd.DataFrame, team: Optional[str] = None) -> List[Tuple[int, str]]:
    s = df
    if team:
        s = s[s["team"] == team]
    s = s.dropna(subset=["shooterId", "shooterName"])
    pairs = {(int(r["shooterId"]), str(r["shooterName"])) for _, r in s.iterrows()}
    return sorted(pairs, key=lambda x: x[1])


def list_goalies(df: pd.DataFrame, team: Optional[str] = None) -> List[Tuple[int, str]]:
    s = df.dropna(subset=["goalieId", "goalieName"])
    if team:
        s = s[s["team"] == team]
    pairs = {(int(r["goalieId"]), str(r["goalieName"])) for _, r in s.iterrows()}
    return sorted(pairs, key=lambda x: x[1])


def summarize_team(df: pd.DataFrame, team: str) -> pd.DataFrame:
    s = df[df["team"] == team]
    if s.empty:
        return pd.DataFrame(columns=["strength", "Shots", "Goals", "xG", "Sh%"])
    agg = (
        s.groupby("strength")
        .agg(Shots=("isSOG", "sum"), Goals=("isGoal", "sum"), xG=("xG", "sum"))
        .reset_index()
    )
    agg["Sh%"] = (agg["Goals"] / agg["Shots"]).replace([np.inf, -np.inf], np.nan)
    return agg.sort_values("strength")


def summarize_player(df: pd.DataFrame, shooter_id: int) -> dict:
    s = df[df["shooterId"] == shooter_id]
    shots = int(s["isSOG"].sum())
    goals = int(s["isGoal"].sum())
    return {
        "Shots": shots,
        "Goals": goals,
        "xG": float(s["xG"].sum()),
        "Sh%": float(goals / max(1, shots)),
    }


def summarize_goalie(df: pd.DataFrame, goalie_id: int) -> dict:
    faced = df[df["goalieId"] == goalie_id]
    sog = faced[faced["isSOG"]]
    ga = faced[faced["isGoal"]]
    saves = int(len(sog) - len(ga))
    sv_pct = saves / max(1, len(sog))
    return {
        "Shots Faced": int(len(sog)),
        "Goals Against": int(len(ga)),
        "Saves": saves,
        "SV%": float(sv_pct),
        "xGA (sum xG faced)": float(sog["xG"].sum()),
    }
