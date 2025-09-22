
# Shared utilities for HockeyShotMap add-on pages
import math
import functools
from typing import List, Tuple, Dict, Optional
import pandas as pd
import numpy as np
import requests

NHL_API = "https://statsapi.web.nhl.com/api/v1"

def _to_date_str(d):
    if isinstance(d, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(d).date().isoformat()
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)

def fetch_games_between(start_date: str, end_date: str) -> List[int]:
    """Return list of gamePk between start_date and end_date inclusive (regular + playoffs)."""
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
    url = f"{NHL_API}/game/{game_pk}/feed/live"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()

def _normalize_xy(x, y):
    """Flip coordinates so that all shots are toward +x (opponent goal at +89)."""
    if x is None or y is None:
        return (np.nan, np.nan)
    if x < 0:
        return (-x, -y)
    return (x, y)

def _goal_distance_angle(x, y):
    """Compute distance and angle to attacking goal line (x=89 after normalization)."""
    if np.isnan(x) or np.isnan(y):
        return (np.nan, np.nan)
    dx = 89 - x  # goal line approx at x=89
    dy = abs(y)
    dist = float(math.hypot(dx, dy))
    angle = float(math.degrees(math.atan2(dy, dx))) if dx != 0 else 90.0
    return (dist, angle)

def _danger_zone(dist, angle):
    """Heuristic danger classification commonly used by analysts."""
    if np.isnan(dist):
        return "unknown"
    if dist <= 25 and angle <= 30:
        return "high"
    if dist <= 40 and angle <= 45:
        return "medium"
    return "low"

def _simple_xg(dist, angle, shot_type: Optional[str]=None):
    """Lightweight logistic xG approximation using distance & angle.
    Coefficients are heuristic; replace with your model if available.
    """
    if np.isnan(dist) or np.isnan(angle):
        return np.nan
    # logistic: 1 / (1 + exp(-(b0 + b1*dist + b2*angle)))
    b0, b1, b2 = -2.7, -0.07, -0.015
    # shot type adjustment (very light)
    type_adj = {
        "Tip-In": 0.35, "Deflected": 0.25, "Wrap-around": 0.15,
        "Backhand": 0.05, "Wrist Shot": 0.00, "Snap Shot": 0.02,
        "Slap Shot": -0.05, "Unknown": 0.0
    }
    t_adj = type_adj.get(shot_type or "Unknown", 0.0)
    z = b0 + b1*dist + b2*angle + t_adj
    return 1.0 / (1.0 + math.exp(-z))

def _extract_event_row(play: dict, game_info: dict) -> Optional[dict]:
    et = play.get("result", {}).get("eventTypeId")
    if et not in ("SHOT", "GOAL", "MISSED_SHOT", "BLOCKED_SHOT"):
        return None
    coords = play.get("coordinates", {})
    x, y = coords.get("x"), coords.get("y")
    if x is None or y is None:
        # keep for totals even if we cannot plot
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
    strength = (play.get("about", {}).get("strength") or {}).get("code")  # may be None for non-goals
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

@functools.lru_cache(maxsize=64)
def fetch_shots_dataframe(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch all shot-like events between dates and return a tidy DataFrame."""
    start_date = _to_date_str(start_date)
    end_date = _to_date_str(end_date)

    game_ids = fetch_games_between(start_date, end_date)
    rows = []
    for gid in game_ids:
        try:
            feed = fetch_game_feed(gid)
        except Exception:
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
    if not rows:
        return pd.DataFrame(columns=[
            "gamePk","gameDate","eventType","team","period","periodTime","strength",
            "x","y","distance","angle","shotType","shooterId","shooterName","goalieId","goalieName","isGoal","danger","xG"
        ])
    df = pd.DataFrame(rows)
    # Derive helper fields
    df["date"] = pd.to_datetime(df["gameDate"]).dt.date
    df["isSOG"] = df["eventType"].isin(["SHOT","GOAL"])
    return df

def list_teams(df: pd.DataFrame) -> List[str]:
    vals = sorted([t for t in df["team"].dropna().unique().tolist() if t])
    return vals

def list_players(df: pd.DataFrame, team: Optional[str]=None) -> List[Tuple[int,str]]:
    s = df
    if team:
        s = s[s["team"]==team]
    s = s.dropna(subset=["shooterId","shooterName"])
    pairs = sorted({(int(r["shooterId"]), str(r["shooterName"])) for _,r in s.iterrows()}, key=lambda x:x[1])
    return pairs

def list_goalies(df: pd.DataFrame, team: Optional[str]=None) -> List[Tuple[int,str]]:
    s = df.dropna(subset=["goalieId","goalieName"])
    if team:
        s = s[s["team"]==team]
    pairs = sorted({(int(r["goalieId"]), str(r["goalieName"])) for _,r in s.iterrows()}, key=lambda x:x[1])
    return pairs

def summarize_team(df: pd.DataFrame, team: str) -> pd.DataFrame:
    s = df[df["team"]==team]
    agg = s.groupby("strength").agg(
        Shots=("isSOG","sum"),
        Goals=("isGoal","sum"),
        xG=("xG","sum")
    ).reset_index()
    agg["Sh%"] = (agg["Goals"] / agg["Shots"]).replace([np.inf, -np.inf], np.nan)
    return agg.sort_values("strength")

def summarize_player(df: pd.DataFrame, shooter_id: int) -> dict:
    s = df[df["shooterId"]==shooter_id]
    out = {
        "Shots": int(s["isSOG"].sum()),
        "Goals": int(s["isGoal"].sum()),
        "xG": float(s["xG"].sum()),
        "Sh%": float((s["isGoal"].sum() / max(1, s["isSOG"].sum())))
    }
    return out

def summarize_goalie(df: pd.DataFrame, goalie_id: int) -> dict:
    faced = df[df["goalieId"]==goalie_id]
    sog = faced[faced["isSOG"]]
    ga = faced[faced["isGoal"]]
    saves = int(len(sog) - len(ga))
    sv_pct = saves / max(1, len(sog))
    xga = float(ga["xG"].sum()) + float(sog[~sog["isGoal"]]["xG"].sum())  # total expected vs faced
    out = {
        "Shots Faced": int(len(sog)),
        "Goals Against": int(len(ga)),
        "Saves": saves,
        "SV%": float(sv_pct),
        "xGA (sum xG faced)": float(sog["xG"].sum()),
    }
    return out
