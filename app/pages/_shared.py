# --- at top of _shared.py (near your imports) ---
import os
import math
import functools
from typing import List, Tuple, Optional
import pandas as pd
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

NHL_API = "https://statsapi.web.nhl.com/api/v1"

# OFFLINE switch: set HSM_OFFLINE="1" in Streamlit Secrets to force local data.
OFFLINE = os.environ.get("HSM_OFFLINE", "0") == "1"

# Optional local fallback file (CSV or Parquet)
DATA_FALLBACK = os.environ.get("HSM_FALLBACK", "./app/data/sample_shots.parquet")

def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=0.3,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "HEAD"]),
                  raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "HockeyShotMap/1.0 (+https://github.com/SparkerData/HockeyShotMap)"})
    return s

def _safe_get_json(url: str) -> Optional[dict]:
    try:
        resp = _session().get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return None

# Helper for pages to know the data source
def data_source_label(df: Optional[pd.DataFrame]) -> str:
    if df is not None and getattr(df, "_hsm_source", None):
        return df._hsm_source
    return "unknown"

# --- your other helpers (normalize, angle, xG, _extract_event_row) stay the same ---

@functools.lru_cache(maxsize=64)
def fetch_shots_dataframe(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Returns a tidy DataFrame of shot-like events.
    Sources:
      - OFFLINE mode -> local fallback (CSV/Parquet)
      - ONLINE with API -> live data
      - If API fails -> local fallback if present, else empty
    """
    # If OFFLINE is set, skip network entirely
    if OFFLINE:
        df = _load_fallback()
        df._hsm_source = "offline-fallback"
        return df

    # Try live API
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

    if rows:
        df = _finalize_df(pd.DataFrame(rows))
        df._hsm_source = "live-api"
        return df

    # API failed or returned nothing → fallback
    df = _load_fallback()
    df._hsm_source = "offline-fallback"
    return df

def _finalize_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "gamePk","gameDate","eventType","team","period","periodTime","strength",
        "x","y","distance","angle","shotType","shooterId","shooterName",
        "goalieId","goalieName","isGoal","danger","xG"
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    df = df[cols]
    if "gameDate" in df and not df["gameDate"].isna().all():
        df["date"] = pd.to_datetime(df["gameDate"]).dt.date
    else:
        df["date"] = pd.NaT
    df["isSOG"] = df["eventType"].isin(["SHOT", "GOAL"])
    return df

def _load_fallback() -> pd.DataFrame:
    try:
        if os.path.exists(DATA_FALLBACK):
            if DATA_FALLBACK.lower().endswith(".parquet"):
                df = pd.read_parquet(DATA_FALLBACK)
            else:
                df = pd.read_csv(DATA_FALLBACK)
            return _finalize_df(df)
    except Exception:
        pass
    # No fallback file → return empty schema
    return _finalize_df(pd.DataFrame([]))

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
