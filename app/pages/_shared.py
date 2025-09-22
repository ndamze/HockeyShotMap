
"""
_shared.py — shared utilities for HockeyShotMap (robust networking + offline fallback).

Changes vs prior version:
- Uses a global Requests session with retries & User-Agent.
- Gracefully handles ConnectionError by returning an empty DataFrame (so pages render).
- Optional offline fallback: if a file exists at DATA_FALLBACK (CSV or Parquet), it will be loaded.
  Set via env var HSM_FALLBACK (default: "./data/sample_shots.parquet").
"""

import math
import functools
from typing import List, Tuple, Optional

import pandas as pd
import numpy as np

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

NHL_API = "https://statsapi.web.nhl.com/api/v1"

# Optional local fallback (CSV/Parquet) if network is unavailable
DATA_FALLBACK = os.environ.get("HSM_FALLBACK", "./data/sample_shots.parquet")

def _session() -> requests.Session:
    s = requests.Session()
    # Retry on common transient errors
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "HockeyShotMap/1.0 (+https://github.com/SparkerData/HockeyShotMap)"
    })
    return s

def _to_date_str(d):
    if isinstance(d, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(d).date().isoformat()
    if hasattr(d, "strftime"):
        return d.strftime("%Y-%m-%d")
    return str(d)

def _normalize_xy(x, y):
    if x is None or y is None:
        return (np.nan, np.nan)
    if x < 0:
        return (-x, -y)
    return (x, y)

def _goal_distance_angle(x, y):
    if isinstance(x, (float, int)) and isinstance(y, (float, int)):
        x = float(x); y = float(y)
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
    if np.isnan(dist):
        return "unknown"
    if dist <= 25 and angle <= 30:
        return "high"
    if dist <= 40 and angle <= 45:
        return "medium"
    return "low"

def _simple_xg(dist, angle, shot_type: Optional[str] = None):
    if np.isnan(dist) or np.isnan(angle):
        return np.nan
    b0, b1, b2 = -2.7, -0.07, -0.015
    type_adj = {
        "Tip-In": 0.35, "Deflected": 0.25, "Wrap-around": 0.15,
        "Backhand": 0.05, "Wrist Shot": 0.00, "Snap Shot": 0.02,
        "Slap Shot": -0.05, "Unknown": 0.0
    }
    t_adj = type_adj.get(shot_type or "Unknown", 0.0)
    z = b0 + b1 * dist + b2 * angle + t_adj
    return 1.0 / (1.0 + math.exp(-z))

def _extract_event_row(play: dict, game_info: dict) -> Optional[dict]:
    et = play.get("result", {}).get("eventTypeId")
    if et not in ("SHOT", "GOAL", "MISSED_SHOT", "BLOCKED_SHOT"):
        return None

    coords = play.get("coordinates", {}) or {}
    x, y = coords.get("x"), coords.get("y")
    if x is None or y is None:
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
    strength = (play.get("about", {}).get("strength") or {}).get("code")
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

def _safe_get_json(url: str) -> Optional[dict]:
    """GET JSON with retries and friendly error handling."""
    try:
        resp = _session().get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return None

def fetch_games_between(start_date: str, end_date: str) -> List[int]:
    start_date = _to_date_str(start_date)
    end_date = _to_date_str(end_date)
    url = f"{NHL_API}/schedule?startDate={start_date}&endDate={end_date}"
    data = _safe_get_json(url)
    if not data:
        return []
    game_ids = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            game_ids.append(g["gamePk"])
    return game_ids

def fetch_game_feed(game_pk: int) -> Optional[dict]:
    url = f"{NHL_API}/game/{game_pk}/feed/live"
    return _safe_get_json(url)

@functools.lru_cache(maxsize=64)
def fetch_shots_dataframe(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Returns a tidy DataFrame of shot-like events (or an empty df if offline).
    If DATA_FALLBACK exists, attempts to load it when offline.
    """
    start_date = _to_date_str(start_date)
    end_date = _to_date_str(end_date)

    rows = []
    game_ids = fetch_games_between(start_date, end_date)
    if game_ids:
        for gid in game_ids:
            feed = fetch_game_feed(gid)
            if not feed:
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
        # Try offline fallback if available
        try:
            if os.path.exists(DATA_FALLBACK):
                if DATA_FALLBACK.endswith(".parquet"):
                    df = pd.read_parquet(DATA_FALLBACK)
                else:
                    df = pd.read_csv(DATA_FALLBACK)
                # Ensure expected columns exist; if not, coerce
                for c in cols:
                    if c not in df.columns:
                        df[c] = np.nan
                df = df[cols]
            else:
                df = pd.DataFrame(columns=cols)
        except Exception:
            df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(rows)[cols]

    # Derived fields
    if "gameDate" in df.columns and not df["gameDate"].isna().all():
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
        "xG": float(s["xG"].sum() if "xG" in s else 0.0),
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
        "xGA (sum xG faced)": float(sog["xG"].sum() if "xG" in sog else 0.0),
    }
