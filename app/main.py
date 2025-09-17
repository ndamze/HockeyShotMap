from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st
from datetime import date as _date, timedelta
from io import StringIO
import numpy as np
import plotly.graph_objects as go

# ---- Rink plot import with dual-path fallback ----
try:
    from app.components.rink_plot import base_rink  # when running from repo root
except ModuleNotFoundError:
    try:
        from components.rink_plot import base_rink  # when running inside app/
    except ModuleNotFoundError:
        st.error("Could not import rink_plot component. Please ensure the components directory exists.")
        st.stop()

st.set_page_config(page_title="NHL Shot Tracker", layout="wide")
st.title("NHL Shot Tracker")

STATS_BASE = "https://statsapi.web.nhl.com/api/v1"
SITE_BASE = "https://api-web.nhle.com/v1"

REQUIRED_COLS = [
    "gamePk", "period", "periodTime", "event", "team",
    "player", "x", "y", "strength", "is_goal", "matchup"
]

# ---------- Team color map (primary-ish colors) ----------
TEAM_COLORS = {
    "ANA": "#FC4C02", "ARI": "#8C2633", "BOS": "#FFB81C", "BUF": "#003087",
    "CGY": "#C8102E", "CAR": "#CC0000", "CHI": "#CF0A2C", "COL": "#6F263D",
    "CBJ": "#002654", "DAL": "#006847", "DET": "#CE1126", "EDM": "#041E42",
    "FLA": "#C8102E", "LAK": "#111111", "MIN": "#154734", "MTL": "#AF1E2D",
    "NSH": "#FFB81C", "NJD": "#CE1126", "NYI": "#F47D30", "NYR": "#0038A8",
    "OTT": "#C52032", "PHI": "#F74902", "PIT": "#FCB514", "SEA": "#99D9D9",
    "SJS": "#006D75", "STL": "#002F87", "TBL": "#002868", "TOR": "#00205B",
    "VAN": "#00205B", "VGK": "#B4975A", "WSH": "#041E42", "WPG": "#041E42",
}

# =========================
# Helpers / Normalization
# =========================

def _norm_name_value(v) -> str | None:
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, dict):
        # common GC string containers
        for k in ("default", "en", "English", "EN", "first", "last", "full"):
            if k in v and isinstance(v[k], str) and v[k].strip():
                return v[k].strip()
        parts = [str(x).strip() for x in v.values() if isinstance(x, str) and x.strip()]
        return " ".join(parts) if parts else None
    return None

def _full_name(first, last, fallback: str | None = None) -> str:
    f = _norm_name_value(first)
    l = _norm_name_value(last)
    if f or l:
        return f"{f or ''} {l or ''}".strip()
    return fallback or "Unknown"

def _normalize_strength_label(label: str | None) -> str:
    if not label:
        return "Unknown"
    l = str(label).strip().lower()
    mapping = {
        "even": "5v5", "ev": "5v5",
        "power play": "PP", "pp": "PP",
        "short handed": "PK", "sh": "PK", "penalty kill": "PK",
    }
    if l in mapping:
        return mapping[l]
    # e.g., "4-on-4", "4v4", "3v3", "5on3"
    l = l.replace("on", "v").replace("-", "").replace(" ", "")
    return l.upper()

# =========================
# StatsAPI parser
# =========================

STATS_SHOT_EVENTS = {"Shot", "Missed Shot", "Goal"}

def _rows_from_statsapi(feed: dict) -> list[dict]:
    rows: list[dict] = []
    plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", []) or []
    game_pk = (feed.get("gamePk") or
               (feed.get("gameData") or {}).get("game", {}).get("pk"))
    for p in plays:
        ev = (p.get("result", {}) or {}).get("event")
        if ev not in STATS_SHOT_EVENTS:
            continue
        coords = p.get("coordinates") or {}
        x, y = coords.get("x"), coords.get("y")
        if x is None or y is None:
            continue

        team_obj = p.get("team") or {}
        team = team_obj.get("triCode") or team_obj.get("name")

        shooter = None
        for pl in (p.get("players") or []):
            if pl.get("playerType") in ("Shooter", "Scorer"):
                shooter = (pl.get("player") or {}).get("fullName")
                break

        strength = _normalize_strength_label(((p.get("result") or {}).get("strength") or {}).get("name"))

        rows.append(
            {
                "gamePk": game_pk,
                "period": (p.get("about") or {}).get("period"),
                "periodTime": (p.get("about") or {}).get("periodTime"),
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

def _matchup_from_statsapi(feed: dict) -> str | None:
    gd = feed.get("gameData") or {}
    teams = gd.get("teams") or {}
    home = (teams.get("home") or {}).get("triCode")
    away = (teams.get("away") or {}).get("triCode")
    if home and away:
        return f"{away} @ {home}"
    return None

# =========================
# GameCenter parser
# =========================

GC_SHOT_EVENTS = {"shot-on-goal", "missed-shot", "goal"}

def _gc_team_maps(feed: dict) -> tuple[dict[int, str], int | None, int | None]:
    id_to_abbrev: dict[int, str] = {}
    home_id = None
    away_id = None
    if isinstance(feed.get("homeTeam"), dict):
        ht = feed["homeTeam"]
        home_id = ht.get("id") or ht.get("teamId")
        ab = ht.get("abbrev") or ht.get("triCode") or ht.get("abbreviation")
        if isinstance(home_id, int) and isinstance(ab, str):
            id_to_abbrev[home_id] = ab
    if isinstance(feed.get("awayTeam"), dict):
        at = feed["awayTeam"]
        away_id = at.get("id") or at.get("teamId")
        ab = at.get("abbrev") or at.get("triCode") or at.get("abbreviation")
        if isinstance(away_id, int) and isinstance(ab, str):
            id_to_abbrev[away_id] = ab
    teams = feed.get("teams")
    if isinstance(teams, dict):
        for side in ("home", "away"):
            tm = teams.get(side)
            if isinstance(tm, dict):
                tid = tm.get("id") or tm.get("teamId")
                ab = tm.get("abbrev") or tm.get("triCode") or tm.get("abbreviation")
                if isinstance(tid, int) and isinstance(ab, str):
                    id_to_abbrev[tid] = ab
                if side == "home" and home_id is None:
                    home_id = tid
                if side == "away" and away_id is None:
                    away_id = tid
    return id_to_abbrev, home_id, away_id

def _gc_roster_map(feed: dict) -> dict[int, str]:
    roster: dict[int, str] = {}
    for spot in feed.get("rosterSpots") or []:
        if not isinstance(spot, dict):
            continue
        pid = spot.get("playerId")
        fn, ln = spot.get("firstName"), spot.get("lastName")
        full = _full_name(fn, ln)
        if isinstance(pid, int) and full and full != "Unknown":
            roster[pid] = full
    for team_key in ("homeTeam", "awayTeam"):
        team = feed.get(team_key)
        if not isinstance(team, dict):
            continue
        for subkey in ("sroster", "roster", "lineup"):
            arr = team.get(subkey)
            if not isinstance(arr, list):
                continue
            for item in arr:
                if not isinstance(item, dict):
                    continue
                pid = item.get("playerId") or item.get("id")
                full = (_norm_name_value(item.get("playerName"))
                        or _full_name(item.get("firstName"), item.get("lastName")))
                if isinstance(pid, int) and full and full != "Unknown":
                    roster[pid] = full
    return roster

def _infer_strength_from_skaters(det: dict, owner_team_id: int | None, home_id: int | None, away_id: int | None) -> str:
    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    hs = _to_int(det.get("homeSkaters"))
    as_ = _to_int(det.get("awaySkaters"))
    if hs is None or as_ is None:
        sc = (det.get("situationCode") or "").lower()
        nums = []
        cur = ""
        for ch in sc:
            if ch.isdigit():
                cur += ch
            elif cur:
                nums.append(int(cur))
                cur = ""
        if cur:
            nums.append(int(cur))
        if len(nums) == 2:
            hs, as_ = nums[0], nums[1]
    if hs is None or as_ is None:
        return "Unknown"
    if hs == 5 and as_ == 5:
        return "5v5"
    if owner_team_id and home_id and away_id:
        owner_is_home = owner_team_id == home_id
        owner_skaters = hs if owner_is_home else as_
        other_skaters = as_ if owner_is_home else hs
        if owner_skaters > other_skaters:
            return "PP"
        if owner_skaters < other_skaters:
            return "PK"
        return "EV"
    if hs > as_:
        return "PP"
    if as_ > hs:
        return "PK"
    return "EV"

def _rows_from_gamecenter(feed: dict) -> list[dict]:
    rows: list[dict] = []

    # plays normalization
    plays_root = feed.get("plays", [])
    plays: list[dict] = []

    def extend_from_candidate(cand):
        nonlocal plays
        if cand is None:
            return
        if isinstance(cand, list):
            if cand and isinstance(cand[0], dict) and "plays" in cand[0]:
                for block in cand:
                    plays.extend(block.get("plays") or [])
            else:
                plays.extend(cand)
        elif isinstance(cand, dict):
            plays.extend(cand.get("plays", []))

    if isinstance(plays_root, dict):
        extend_from_candidate(plays_root.get("all"))
        extend_from_candidate(plays_root.get("byPeriod"))
        extend_from_candidate(plays_root.get("currentPlay"))
        extend_from_candidate(plays_root)
    elif isinstance(plays_root, list):
        extend_from_candidate(plays_root)

    id_to_abbrev, home_id, away_id = _gc_team_maps(feed)
    roster = _gc_roster_map(feed)

    for p in plays or []:
        ev_key = (p.get("typeDescKey") or p.get("typeCode") or "").lower()
        if ev_key not in GC_SHOT_EVENTS:
            continue

        det = p.get("details") or {}
        x, y = det.get("xCoord"), det.get("yCoord")
        if x is None or y is None:
            continue

        team_raw = det.get("eventOwnerTeamAbbrev") or det.get("eventOwnerTeamId")
        if isinstance(team_raw, int):
            team = id_to_abbrev.get(team_raw) or str(team_raw)
            owner_team_id = team_raw
        else:
            team = team_raw
            owner_team_id = det.get("eventOwnerTeamId") if isinstance(det.get("eventOwnerTeamId"), int) else None

        shooter = (_norm_name_value(det.get("shootingPlayerName")) or
                   _norm_name_value(det.get("scoringPlayerName")))
        if not shooter:
            for pl in (p.get("players") or []):
                role = (pl.get("typeDescKey") or pl.get("typeCode") or "").lower()
                if role in {"shooter", "scorer"}:
                    shooter = (_norm_name_value(pl.get("playerName")) or
                               _full_name(pl.get("firstName"), pl.get("lastName")))
                    if shooter:
                        break
        if not shooter:
            pid = det.get("shootingPlayerId") or det.get("scoringPlayerId") or det.get("playerId")
            if pid is not None:
                for pl in (p.get("players") or []):
                    if pl.get("playerId") == pid:
                        shooter = (_norm_name_value(pl.get("playerName")) or
                                   _full_name(pl.get("firstName"), pl.get("lastName")))
                        if shooter:
                            break
                if not shooter:
                    shooter = roster.get(pid)
        shooter = shooter or "Unknown"

        pd_desc = p.get("periodDescriptor") or {}
        period = pd_desc.get("number")
        period_time = p.get("timeInPeriod") or p.get("timeRemaining") or None

        raw_strength = _norm_name_value(det.get("strength"))
        if raw_strength:
            strength = _normalize_strength_label(raw_strength)
        else:
            strength = _infer_strength_from_skaters(det, owner_team_id, home_id, away_id)

        rows.append(
            {
                "gamePk": feed.get("id") or feed.get("gameId"),
                "period": period,
                "periodTime": period_time,
                "event": ("Goal" if ev_key == "goal" else ("Shot" if ev_key == "shot-on-goal" else "Missed Shot")),
                "team": team,
                "player": shooter,
                "x": float(x),
                "y": float(y),
                "strength": strength,
                "is_goal": 1 if ev_key == "goal" else 0,
            }
        )
    return rows

def _matchup_from_gamecenter(feed: dict) -> str | None:
    def _abbr(d, keys=("abbrev", "triCode", "abbreviation")):
        if isinstance(d, dict):
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v:
                    return v
        return None

    home = _abbr(feed.get("homeTeam")) or _abbr((feed.get("teams") or {}).get("home"))
    away = _abbr(feed.get("awayTeam")) or _abbr((feed.get("teams") or {}).get("away"))
    if home and away:
        return f"{away} @ {home}"
    return None

# =========================
# Common utilities
# =========================

def _shots_from_feed(feed: dict) -> tuple[pd.DataFrame, str, str | None]:
    rows = _rows_from_statsapi(feed)
    matchup = None
    source = "StatsAPI"
    if rows:
        matchup = _matchup_from_statsapi(feed)
    else:
        rows = _rows_from_gamecenter(feed)
        source = "GameCenter"
        matchup = _matchup_from_gamecenter(feed)

    df = pd.DataFrame(
        rows,
        columns=["gamePk", "period", "periodTime", "event", "team", "player", "x", "y", "strength", "is_goal"],
    )
    df.attrs["parser_source"] = source
    return df, source, matchup

def _empty_df() -> pd.DataFrame:
    df = pd.DataFrame(columns=REQUIRED_COLS)
    for c in ("x", "y"):
        df[c] = pd.Series(dtype="float")
    for c in ("is_goal",):
        df[c] = pd.Series(dtype="int")
    return df

# ---------- Exact-date schedule (robust) ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    """
    Return all gamePk values for calendar date d.
    Robust: unions StatsAPI (?date and ±1-day range) + GameCenter schedule.
    Avoids UTC pitfalls and weird key variants (officialDate, startTimeUTC, etc).
    """
    wanted = d.isoformat()
    headers = {"User-Agent": "SparkerData-HockeyShotMap/1.0"}

    # ---- Helpers
    def _safe_str_date(v) -> str | None:
        # accept "2025-01-13T...", or nested {"$date": "..."}
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
        if isinstance(v, dict):
            inner = v.get("$date") or v.get("date")
            if isinstance(inner, str) and len(inner) >= 10:
                return inner[:10]
        return None

    def _is_wanted_game(game: dict) -> bool:
        # Check common date keys used across the two APIs
        keys = (
            "gameDate", "officialDate", "startTimeUTC", "startTimeLocal",
            "gameDateISO", "gameTime", "gameDateTime"
        )
        for k in keys:
            if k in game:
                s = _safe_str_date(game.get(k))
                if s == wanted:
                    return True
        # Some GameCenter "gameWeek" entries attach day at the week level
        week_day = _safe_str_date(game.get("date"))
        if week_day == wanted:
            return True
        return False

    def _collect_from_stats_json(data: dict) -> list[int]:
        pks: list[int] = []
        # Prefer exact day buckets if present
        for day in (data.get("dates") or []):
            day_key = _safe_str_date(day.get("date"))
            for g in (day.get("games") or []):
                pk = g.get("gamePk")
                if not pk:
                    continue
                # If day_key matches, accept blindly; otherwise fall back to per-game key checks
                if day_key == wanted or _is_wanted_game(g):
                    pks.append(int(pk))
        return pks

    def _collect_from_gc_sched(sched: dict) -> list[int]:
        """
        GameCenter schedule may return either:
          - {"games": [...] } (already day-scoped)
          - {"gameWeek": [{"date": "...", "games":[...]} ...]}
        """
        pks: list[int] = []

        # Direct "games" list (filter by date keys if present)
        for g in (sched.get("games") or []):
            if _is_wanted_game(g):
                pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                if pk:
                    pks.append(int(pk))

        # Weekly buckets
        for wk in (sched.get("gameWeek") or []):
            wk_date = _safe_str_date(wk.get("date"))
            for g in (wk.get("games") or []):
                if wk_date == wanted or _is_wanted_game(g):
                    pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                    if pk:
                        pks.append(int(pk))

        return pks

    # ---- Strategy: union of 3 sources
    found: set[int] = set()

    # 1) StatsAPI exact date
    try:
        url = f"{STATS_BASE}/schedule?date={wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url)
            r.raise_for_status()
            found.update(_collect_from_stats_json(r.json()))
    except Exception:
        pass

    # 2) StatsAPI ±1-day range (handles UTC drift / late starts)
    try:
        start = (d - timedelta(days=1)).isoformat()
        end = (d + timedelta(days=1)).isoformat()
        url = f"{STATS_BASE}/schedule?startDate={start}&endDate={end}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url)
            r.raise_for_status()
            found.update(_collect_from_stats_json(r.json()))
    except Exception:
        pass

    # 3) GameCenter schedule (day)
    try:
        url = f"{SITE_BASE}/schedule/{wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url)
            r.raise_for_status()
            found.update(_collect_from_gc_sched(r.json()))
    except Exception:
        pass

    return sorted(found)

# ---------- Fetch shots (day / range), add matchup, de-dupe & clean ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str, int]:
    pks = fetch_game_pks_for_date(d)
    games_count = len(pks)
    if not pks:
        return _empty_df(), "no games", 0

    frames = []
    used_sources = set()
    matchup_map: dict[int, str] = {}

    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        for pk in pks:
            feed = None
            try:
                r = c.get(f"{STATS_BASE}/game/{pk}/feed/live")
                r.raise_for_status()
                feed = r.json()
            except Exception:
                try:
                    r = c.get(f"{SITE_BASE}/gamecenter/{pk}/play-by-play")
                    r.raise_for_status()
                    feed = r.json()
                except Exception:
                    feed = None
            if not feed:
                continue
            df_part, source, matchup = _shots_from_feed(feed)
            if matchup:
                matchup_map[int(pk)] = matchup
            if not df_part.empty:
                frames.append(df_part.assign(gamePk=int(pk)))
                used_sources.add(source)

    if not frames:
        return _empty_df(), "no shots", games_count

    shots = pd.concat(frames, ignore_index=True)

    # De-dupe & clip
    key_cols = ["gamePk", "period", "periodTime", "team", "player", "x", "y", "event"]
    shots = shots.drop_duplicates(subset=[c for c in key_cols if c in shots.columns])
    shots["x"] = shots["x"].clip(-100, 100)
    shots["y"] = shots["y"].clip(-43, 43)

    # Add matchup column
    shots["matchup"] = shots["gamePk"].map(lambda pk: matchup_map.get(int(pk)) if pd.notna(pk) else None)

    # Ensure all required columns exist
    for col in REQUIRED_COLS:
        if col not in shots.columns:
            shots[col] = pd.NA

    label = "/".join(sorted(used_sources)) if used_sources else "Unknown"
    return shots[REQUIRED_COLS], label, games_count

@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_between(start: _date, end: _date) -> tuple[pd.DataFrame, str, int]:
    all_frames = []
    used = set()
    games_total = 0
    day = start
    while day <= end:
        df, label, games_count = fetch_shots_for_date(day)
        games_total += games_count
        if not df.empty:
            all_frames.append(df.assign(source_date=str(day)))
            used.update((label or "Unknown").split("/"))
        day += timedelta(days=1)
    if not all_frames:
        return _empty_df(), "no data", games_total
    out = pd.concat(all_frames, ignore_index=True)
    if "source_date" not in out.columns:
        out["source_date"] = pd.NA
    return out[REQUIRED_COLS + ["source_date"]], "/".join(sorted(s for s in used if s and s not in {"no games", "no shots"})), games_total

# =========================
# UI
# =========================

left, right = st.columns([1, 3])

with left:
    st.subheader("Date selection")

    # Quick presets
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        if st.button("Today"):
            st.session_state["_preset"] = ("single", _date.today(), _date.today())
    with col_p2:
        if st.button("Yesterday"):
            y = _date.today() - timedelta(days=1)
            st.session_state["_preset"] = ("single", y, y)
    with col_p3:
        if st.button("Last 7 days"):
            end = _date.today()
            start = end - timedelta(days=6)
            st.session_state["_preset"] = ("range", start, end)

    mode = st.radio("Mode", ["Single day", "Date range"], horizontal=True)
    preset = st.session_state.pop("_preset", None)

    fetch_click = False  # ensure variable always exists

    # --- Cache buster ---
    if st.button("Force refresh (clear cache)"):
        st.cache_data.clear()
        st.rerun()

    if mode == "Single day":
        default = preset[1] if preset and preset[0] == "single" else _date.today()
        picked = st.date_input("Date", value=default, max_value=_date.today())
        fetch_click = st.button("Retrieve Data")
    else:
        if preset and preset[0] == "range":
            default_start, default_end = preset[1], preset[2]
        else:
            default_start, default_end = _date.today(), _date.today()
        picked_range = st.date_input("Date range", value=(default_start, default_end), max_value=_date.today())
        if isinstance(picked_range, tuple) and len(picked_range) == 2:
            start_date, end_date = picked_range
        else:
            start_date, end_date = _date.today(), _date.today()
        fetch_click = st.button("Retrieve Data")

# --- Session state bootstrapping (API-only; default = today) ---
if "initialized" not in st.session_state:
    st.session_state["initialized"] = True
    st.session_state["data_df"] = _empty_df()
    st.session_state["data_dates"] = _date.today()
    st.session_state["games_count"] = 0
    df_live, parser_label_boot, games_boot = fetch_shots_for_date(_date.today())
    st.session_state["data_df"] = df_live
    st.session_state["games_count"] = games_boot
    st.session_state["parser_label"] = parser_label_boot if not df_live.empty else None

# ---------- Load (refetch if button clicked) ----------
parser_label = st.session_state.get("parser_label")
games_count = st.session_state.get("games_count", 0)
df = st.session_state.get("data_df", _empty_df()).copy()

if fetch_click:
    with st.spinner("Fetching NHL data..."):
        if mode == "Single day":
            df_live, parser_label, games_count = fetch_shots_for_date(picked)
            st.session_state["data_dates"] = picked
        else:
            df_live, parser_label, games_count = fetch_shots_between(start_date, end_date)
            st.session_state["data_dates"] = (start_date, end_date)

        st.session_state["data_df"] = df_live
        st.session_state["games_count"] = games_count
        st.session_state["parser_label"] = None if df_live.empty else parser_label
        df = df_live.copy()

# Ensure df has required columns even if empty
for col in REQUIRED_COLS:
    if col not in df.columns:
        df[col] = pd.NA

# ---------- Filters ----------
with left:
    # Player multiselect grouped by team in the label (TEAM — Player)
    label_to_player: dict[str, str] = {}
    if not df.empty:
        players = df[["player", "team"]].dropna().drop_duplicates().sort_values(["team", "player"])
        label_to_player = {f"{row.team} — {row.player}": row.player for row in players.itertuples(index=False)}
        player_opts = list(label_to_player.keys())
    else:
        player_opts = []

    selected_player_labels = st.multiselect("Players", options=player_opts, default=[])
    selected_players = {label_to_player[lbl] for lbl in selected_player_labels} if selected_player_labels else None

    # Matchup filter only in single-day mode
    matchup_opts = []
    if isinstance(st.session_state.get("data_dates"), _date) or (mode == "Single day"):
        if "matchup" in df and not df["matchup"].dropna().empty:
            matchup_opts = sorted(df["matchup"].dropna().unique().tolist())
    selected_matchups = st.multiselect("Matchup (AWAY @ HOME)", options=matchup_opts, default=matchup_opts)

    goals_only = st.checkbox("Show only goals", value=False)

# Apply filters (guard empties)
mask = pd.Series(True, index=df.index)
if selected_players and "player" in df.columns:
    mask &= df["player"].isin(selected_players)
if selected_matchups and "matchup" in df.columns:
    mask &= df["matchup"].isin(selected_matchups)
if goals_only and "is_goal" in df.columns:
    mask &= df["is_goal"] == 1

filtered = df[mask].copy()
# Ensure required columns exist on filtered, too
for col in REQUIRED_COLS:
    if col not in filtered.columns:
        filtered[col] = pd.NA

# ---------- Summary (filtered only) ----------
with left:
    st.subheader("Summary")
    c1, c2, c3, c4, c5 = st.columns(5)

    df_used = filtered  # always use filtered data

    games_filtered = int(df_used["gamePk"].nunique()) if "gamePk" in df_used else 0
    total_shots = int(df_used.shape[0]) if not df_used.empty else 0
    total_goals = int(df_used["is_goal"].sum()) if "is_goal" in df_used else 0
    uniq_players = df_used["player"].nunique() if "player" in df_used else 0
    uniq_teams = df_used["team"].nunique() if "team" in df_used else 0

    c1.metric("Games", games_filtered)
    c2.metric("Shots", total_shots)
    c3.metric("Goals", total_goals)
    c4.metric("Players", uniq_players)
    c5.metric("Teams", uniq_teams)

    if st.session_state.get("parser_label"):
        st.caption(f"Parsed via: {st.session_state['parser_label']}")

# ---------- Plot ----------
with right:
    fig = base_rink()

    # --- Accurate NHL center line + blue lines (1 ft wide) ---
    RINK_Y_MIN, RINK_Y_MAX = -42.5, 42.5
    LINE_HALF_FT = 0.5  # 1 ft total

    # Center red line: x in [-0.5, 0.5]
    fig.add_shape(
        type="rect",
        x0=-LINE_HALF_FT, x1=LINE_HALF_FT,
        y0=RINK_Y_MIN, y1=RINK_Y_MAX,
        line=dict(width=0),
        fillcolor="red",
        layer="above",
    )

    # Blue lines: inside edges at ±25 ft, 1 ft thick -> [-26,-25] and [25,26]
    for x0, x1 in [(-26.0, -25.0), (25.0, 26.0)]:
        fig.add_shape(
            type="rect",
            x0=x0, x1=x1,
            y0=RINK_Y_MIN, y1=RINK_Y_MAX,
            line=dict(width=0),
            fillcolor="blue",
            layer="above",
        )

    # --- End-zone faceoff circles (red outline) ---
    ez_r = 15.0
    ez_centers = [(-69, 22), (-69, -22), (69, 22), (69, -22)]
    for cx, cy in ez_centers:
        fig.add_shape(
            type="circle",
            x0=cx - ez_r, x1=cx + ez_r,
            y0=cy - ez_r, y1=cy + ez_r,
            line=dict(color="red", width=2),
            fillcolor="rgba(0,0,0,0)",
            layer="above",
        )

    # --- Faceoff dots (end-zone = red, neutral-zone = blue, center-ice = blue) ---
    DOT_R = 1.0  # ~2 ft diameter

    # End-zone red dots (at the circle centers)
    for cx, cy in ez_centers:
        fig.add_shape(
            type="circle",
            x0=cx - DOT_R, x1=cx + DOT_R,
            y0=cy - DOT_R, y1=cy + DOT_R,
            line=dict(width=0),
            fillcolor="red",
            layer="above",
        )

    # Neutral-zone blue dots (NO circles in NHL spec)
    nz_spots = [(-20, 22), (-20, -22), (20, 22), (20, -22)]
    for cx, cy in nz_spots:
        fig.add_shape(
            type="circle",
            x0=cx - DOT_R, x1=cx + DOT_R,
            y0=cy - DOT_R, y1=cy + DOT_R,
            line=dict(width=0),
            fillcolor="blue",
            layer="above",
        )

    # Center-ice big blue circle + blue dot
    center_r = 15.0
    fig.add_shape(
        type="circle",
        x0=-center_r, x1=center_r,
        y0=-center_r, y1=center_r,
        line=dict(color="blue", width=2),
        fillcolor="rgba(0,0,0,0)",
        layer="above",
    )
    fig.add_shape(
        type="circle",
        x0=-DOT_R, x1=DOT_R,
        y0=-DOT_R, y1=DOT_R,
        line=dict(width=0),
        fillcolor="blue",
        layer="above",
    )

    # --- End-zone hash marks (vertical, above/below circles) ---
    # NHL-style: short vertical red ticks above & below each end-zone circle,
    # positioned slightly outside the circle ring. Neutral-zone ticks remain horizontal.
    HASH_LEN = 2.0    # ft (tick length)
    EZ_GAP_X = 5.5    # ft left/right of circle center for tick columns
    EZ_OUT   = 1.0    # ft outside the circle edge (vertical offset from ring)
    NZ_GAP_Y = 1.8    # ft above/below the NZ dots
    NZ_OFF_X = 4.0    # ft left/right from NZ dot center

    def _v_tick(x_center: float, y_center: float, color: str):
        """Draw a short VERTICAL tick centered at (x_center, y_center)."""
        fig.add_shape(
            type="line",
            x0=x_center, y0=y_center - HASH_LEN / 2,
            x1=x_center, y1=y_center + HASH_LEN / 2,
            line=dict(color=color, width=2),
            layer="above",
        )

    def _h_tick(x_center: float, y_center: float, color: str):
        """Draw a short HORIZONTAL tick centered at (x_center, y_center)."""
        fig.add_shape(
            type="line",
            x0=x_center - HASH_LEN / 2, y0=y_center,
            x1=x_center + HASH_LEN / 2, y1=y_center,
            line=dict(color=color, width=2),
            layer="above",
        )

    # End-zone ticks (red): top/bottom of the circle, two columns (left/right of center)
    for cx, cy in ez_centers:
        y_top = cy + ez_r + EZ_OUT   # just outside the circle ring
        y_bot = cy - ez_r - EZ_OUT
        x_left  = cx - EZ_GAP_X
        x_right = cx + EZ_GAP_X

        _v_tick(x_left,  y_top, "red")
        _v_tick(x_left,  y_bot, "red")
        _v_tick(x_right, y_top, "red")
        _v_tick(x_right, y_bot, "red")
    
    # --- Goal creases (semi-circles; darker) ---
    crease_radius = 6
    crease_color = "rgba(25, 118, 210, 0.55)"  # darker blue
    theta = np.linspace(-np.pi / 2, np.pi / 2, 50)

    # Left crease (goal near x = -89 ft)
    x_left = -89 + crease_radius * np.cos(theta)
    y_left = 0 + crease_radius * np.sin(theta)
    fig.add_trace(go.Scatter(
        x=x_left, y=y_left,
        fill="toself", mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        fillcolor=crease_color,
        showlegend=False,
        hoverinfo="skip",
        opacity=0.7,
    ))

    # Right crease (goal near x = +89 ft)
    x_right = 89 - crease_radius * np.cos(theta)
    y_right = 0 + crease_radius * np.sin(theta)
    fig.add_trace(go.Scatter(
        x=x_right, y=y_right,
        fill="toself", mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        fillcolor=crease_color,
        showlegend=False,
        hoverinfo="skip",
        opacity=0.7,
    ))

    # --- Rounded white rink surface under the lines ---
    left_x, right_x = -100, 100
    bottom_y, top_y = -42.5, 42.5
    r = 28.0
    k = 0.5522847498
    path = (
        f"M {left_x+r},{bottom_y} "
        f"L {right_x-r},{bottom_y} "
        f"C {right_x-r + k*r},{bottom_y} {right_x},{bottom_y + r - k*r} {right_x},{bottom_y + r} "
        f"L {right_x},{top_y - r} "
        f"C {right_x},{top_y - r + k*r} {right_x - r + k*r},{top_y} {right_x - r},{top_y} "
        f"L {left_x + r},{top_y} "
        f"C {left_x + r - k*r},{top_y} {left_x},{top_y - r + k*r} {left_x},{top_y - r} "
        f"L {left_x},{bottom_y + r} "
        f"C {left_x},{bottom_y + r - k*r} {left_x + r - k*r},{bottom_y} {left_x + r},{bottom_y} Z"
    )
    fig.add_shape(type="path", path=path, fillcolor="white", line=dict(width=0), layer="below")

    # Arena background
    ARENA_BG = "#E9ECEF"
    fig.update_layout(
        plot_bgcolor=ARENA_BG, paper_bgcolor=ARENA_BG,
        margin=dict(l=10, r=10, t=20, b=10), height=520,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(color="black"), bgcolor="rgba(0,0,0,0)", borderwidth=0
        ),
        hoverlabel=dict(font=dict(color="white"), bgcolor="rgba(0,0,0,0.7)"),
    )

    # ---- Scatter data & render ----
    if not filtered.empty:
        # Hover text helper
        def _hover_row(r):
            name = r.get("player") or "Unknown"
            team = r.get("team") or ""
            period = r.get("period")
            try:
                pnum = int(period) if pd.notna(period) else None
            except Exception:
                pnum = None
            ptime = r.get("periodTime") or ""
            when = f"P{pnum} {ptime}".strip() if pnum else ptime

            label = f"{name} ({team})"
            if when:
                label += f"<br>{when}"
            if int(r.get("is_goal", 0) or 0) == 1:
                stg = r.get("strength")
                if isinstance(stg, str) and stg and stg != "Unknown":
                    label += f" — {stg}"
            return label

        # Separate goals vs non-goals
        non_goals = filtered[filtered["is_goal"] != 1] if "is_goal" in filtered else filtered
        goals = filtered[filtered["is_goal"] == 1] if "is_goal" in filtered else filtered.iloc[0:0]

        if not non_goals.empty:
            fig.add_trace(go.Scatter(
                x=non_goals.get("x", []), y=non_goals.get("y", []),
                mode="markers",
                marker=dict(
                    color=[TEAM_COLORS.get(t, "#888888")
                           for t in non_goals.get("team", pd.Series([""] * len(non_goals))).fillna("")],
                    size=7, opacity=0.8,
                    line=dict(color="black", width=0.8),
                ),
                text=[_hover_row(r) for _, r in non_goals.iterrows()],
                hovertemplate="%{text}<extra></extra>",
                name="Shots",
            ))

        if not goals.empty:
            fig.add_trace(go.Scatter(
                x=goals.get("x", []), y=goals.get("y", []),
                mode="markers",
                marker=dict(
                    color=[TEAM_COLORS.get(t, "#888888")
                           for t in goals.get("team", pd.Series([""] * len(goals))).fillna("")],
                    size=9, opacity=0.95, symbol="star",
                    line=dict(color="black", width=1.0),
                ),
                text=[_hover_row(r) for _, r in goals.iterrows()],
                hovertemplate="%{text}<extra></extra>",
                name="Goals",
            ))

        st.plotly_chart(fig, use_container_width=True)
    else:
        # Still show the rink even if there’s no data
        st.plotly_chart(fig, use_container_width=True)
        st.info("No data for the selected date(s).")

# ---------- Export ----------
with left:
    st.subheader("Export")
    if not filtered.empty:
        csv_buf = StringIO()
        filtered.to_csv(csv_buf, index=False)
        st.download_button(
            "Download filtered shots CSV",
            data=csv_buf.getvalue(),
            file_name="shots_filtered.csv",
            mime="text/csv",
        )
    else:
        st.caption("No filtered rows to export.")

# ---------- Optional: summarized table for ranges (compact) ----------
if not filtered.empty and isinstance(st.session_state.get("data_dates"), tuple):
    st.subheader("Player summary (selected range)")
    summary = (
        filtered.groupby(["player", "team"], dropna=False)
        .agg(shots=("player", "size"), goals=("is_goal", "sum"))
        .reset_index()
        .sort_values(["shots", "goals"], ascending=[False, False])
    )
    st.dataframe(summary, use_container_width=True, height=260)

# Compact footer caption
st.caption(
    f"Rows: {len(filtered)} • "
    f"Filters → players: {'custom' if selected_players else 'All'}, "
    f"goals_only: {bool(goals_only)}"
)
