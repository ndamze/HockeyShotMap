from __future__ import annotations

import httpx
import pandas as pd
import streamlit as st
from datetime import date as _date, timedelta
from io import StringIO
import numpy as np
import plotly.graph_objects as go
import re

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

def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _parse_mmss(s: str | None) -> int:
    """'MM:SS' -> seconds (0 if bad)."""
    if not isinstance(s, str):
        return 0
    s = s.strip()
    if not s or ":" not in s:
        return 0
    m, ss = s.split(":", 1)
    return max(0, _safe_int(m) * 60 + _safe_int(ss))

def _game_seconds(period: int | None, period_time: str | None) -> int:
    """Absolute game seconds since 0:00 of P1."""
    p = _safe_int(period) or 1
    return (p - 1) * 20 * 60 + _parse_mmss(period_time)

def _norm_strength_final(label: str | None) -> str:
    """
    Normalize to: 5v5, PP, PK, 4v4, 3v3, 6v5, 5v6, etc.
    Unknown/missing values *do not* collapse to 5v5 here.
    """
    if not label:
        return "Unknown"
    t = str(label).strip()
    if t == "":
        return "Unknown"
    l = t.lower().replace("on", "v").replace("-", "").replace(" ", "")
    l = l.replace("x", "v").replace("vs", "v")
    if l in {"ev", "even", "evenstrength"}:
        return "5v5"
    if l in {"pp", "ppg", "powerplay", "powerplayadvantage"}:
        return "PP"
    if l in {"pk", "sh", "shg", "shorthanded", "penaltykill"}:
        return "PK"
    m = re.match(r"^(\d+)v(\d+)$", l)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a == b:
            return f"{a}v{b}"
        # we don't know the shooter side here; keep numeric
        return f"{a}v{b}"
    if l in {"unknown", "unk", "n/a", "na", "null", "none"}:
        return "Unknown"
    return l.upper()

# =========================
# StatsAPI → Manpower simulation
# =========================

def _home_away_codes(feed: dict) -> tuple[str | None, str | None]:
    gd = feed.get("gameData") or {}
    teams = gd.get("teams") or {}
    home = (teams.get("home") or {}).get("triCode")
    away = (teams.get("away") or {}).get("triCode")
    return home, away

def _rows_from_statsapi_with_manpower(feed: dict) -> tuple[list[dict], str | None]:
    """
    Build shots/misses/goals and compute strength from a simulated penalty timeline.
    Implements: active minors/majors + 'minor ends on PP goal'.
    Returns (rows, matchup).
    """
    rows: list[dict] = []
    plays = (feed.get("liveData") or {}).get("plays", {}) or {}
    all_plays = plays.get("allPlays") or []
    if not all_plays:
        return rows, None

    home_code, away_code = _home_away_codes(feed)
    matchup = None
    if home_code and away_code:
        matchup = f"{away_code} @ {home_code}"

    # Active penalties (segments) at current time
    # Each segment: {"end": int_seconds, "type": "minor"/"major"}
    active_home: list[dict] = []
    active_away: list[dict] = []
    # Pending segments (for double minors' second half etc.)
    pending_home: list[dict] = []
    pending_away: list[dict] = []

    def _expire_segments(t: int):
        nonlocal active_home, active_away
        active_home = [seg for seg in active_home if seg["end"] > t]
        active_away = [seg for seg in active_away if seg["end"] > t]

    def _activate_pending(t: int):
        """Move any pending segments whose start <= t to active."""
        def move(pending, active):
            keep = []
            for seg in pending:
                if seg["start"] <= t:
                    active.append({"end": seg["end"], "type": seg["type"]})
                else:
                    keep.append(seg)
            return keep
        nonlocal pending_home, pending_away, active_home, active_away
        pending_home = move(pending_home, active_home)
        pending_away = move(pending_away, active_away)

    def _counts_now() -> tuple[int, int]:
        """Return (home_skaters, away_skaters) from active segments."""
        def minus_five(active_list):
            # majors and minors both reduce skaters
            return max(3, 5 - len(active_list))
        return minus_five(active_home), minus_five(active_away)

    def _is_minor(seg: dict) -> bool:
        return seg.get("type") == "minor"

    def _cancel_one_minor_for(team: str, t: int):
        """On a PP goal: remove the minor against the short-handed team with smallest remaining time."""
        target = active_home if team == "HOME" else active_away
        minors = [(i, seg) for i, seg in enumerate(target) if _is_minor(seg)]
        if not minors:
            return
        # earliest to expire (smallest remaining)
        idx, _seg = min(minors, key=lambda kv: kv[1]["end"])
        del target[idx]

    # Iterate plays in chronological order
    # Build rows for shots/misses/goals, and update penalty state as we go.
    for p in all_plays:
        about = p.get("about") or {}
        period = about.get("period")
        period_time = about.get("periodTime")
        t = _game_seconds(period, period_time)

        # First update penalty state to time t
        _expire_segments(t)
        _activate_pending(t)

        result = p.get("result") or {}
        event = (result.get("eventTypeId") or result.get("event") or "").upper()

        # Penalties: start segments
        if event == "PENALTY":
            sev = (result.get("penaltySeverity") or "").title()  # 'Minor', 'Major', 'Match', 'Misconduct', etc
            mins = _safe_int(result.get("penaltyMinutes")) or 0

            # Determine penalized side (StatsAPI: the 'team' on the play is the penalized team)
            team_obj = p.get("team") or {}
            tri = team_obj.get("triCode") or team_obj.get("name")
            penalized_side = "HOME" if tri and home_code and tri == home_code else (
                             "AWAY" if tri and away_code and tri == away_code else None)

            # Only penalties that reduce manpower:
            # Minor (2), Double-minor (4), Major (5), Match (5)
            reduces = False
            segments: list[tuple[int,int,str]] = []  # (start,end,type)
            if sev == "Minor" and mins in (2, 4):
                reduces = True
                if mins == 2:
                    segments.append((t, t + 120, "minor"))
                else:  # 4 -> two consecutive minors
                    segments.append((t, t + 120, "minor"))
                    segments.append((t + 120, t + 240, "minor"))
            elif sev in ("Major", "Match") and mins >= 5:
                reduces = True
                segments.append((t, t + mins * 60, "major"))
            # Misconducts (10) do not reduce manpower

            if reduces and penalized_side:
                if penalized_side == "HOME":
                    # Activate the first segment now; queue the second (if any) to pending
                    if segments:
                        first = segments[0]
                        active_home.append({"end": first[1], "type": first[2]})
                    for seg in segments[1:]:
                        pending_home.append({"start": seg[0], "end": seg[1], "type": seg[2]})
                else:
                    if segments:
                        first = segments[0]
                        active_away.append({"end": first[1], "type": first[2]})
                    for seg in segments[1:]:
                        pending_away.append({"start": seg[0], "end": seg[1], "type": seg[2]})

            # Done processing penalty; continue to next play
            continue

        # Build shot/miss/goal rows
        if event in {"SHOT", "MISSED_SHOT", "GOAL"}:
            coords = p.get("coordinates") or {}
            x, y = coords.get("x"), coords.get("y")
            if x is None or y is None:
                # keep only events with coordinates (for plotting)
                pass
            team_obj = p.get("team") or {}
            tri = team_obj.get("triCode") or team_obj.get("name") or ""
            shooter = None
            for pl in (p.get("players") or []):
                if pl.get("playerType") in ("Shooter", "Scorer"):
                    shooter = (pl.get("player") or {}).get("fullName")
                    break
            shooter = shooter or "Unknown"

            # Counts before goal cancellation
            home_cnt, away_cnt = _counts_now()
            # Label relative to the SHOOTING side
            shooter_side = "HOME" if tri and home_code and tri == home_code else (
                           "AWAY" if tri and away_code and tri == away_code else None)

            if home_cnt == away_cnt:
                label = f"{home_cnt}v{away_cnt}"
            else:
                if shooter_side == "HOME":
                    label = "PP" if home_cnt > away_cnt else "PK"
                elif shooter_side == "AWAY":
                    label = "PP" if away_cnt > home_cnt else "PK"
                else:
                    # If we can't tell, just keep numeric
                    label = f"{home_cnt}v{away_cnt}"

            rows.append(
                {
                    "gamePk": feed.get("gamePk") or (feed.get("gameData") or {}).get("game", {}).get("pk"),
                    "period": period,
                    "periodTime": period_time,
                    "event": "Goal" if event == "GOAL" else ("Shot" if event == "SHOT" else "Missed Shot"),
                    "team": tri,
                    "player": shooter,
                    "x": float(x) if x is not None else None,
                    "y": float(y) if y is not None else None,
                    "strength": _norm_strength_final(label),
                    "is_goal": 1 if event == "GOAL" else 0,
                }
            )

            # If this was a goal on the power play, cancel one opponent MINOR (not major)
            if event == "GOAL" and home_cnt != away_cnt and shooter_side in {"HOME", "AWAY"}:
                short_side = "AWAY" if home_cnt > away_cnt else "HOME"
                # Only cancel if the short-handed side has an active MINOR
                _cancel_one_minor_for(short_side, t)

            continue

        # For all other events, nothing to record — but still keep the penalty clock updated
        # (handled at the start of loop)

    return rows, matchup

# =========================
# GameCenter schedule helper (we keep your robust union)
# =========================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    wanted = d.isoformat()
    headers = {"User-Agent": "SparkerData-HockeyShotMap/1.0"}

    def _safe_str_date(v) -> str | None:
        if isinstance(v, str) and len(v) >= 10:
            return v[:10]
        if isinstance(v, dict):
            inner = v.get("$date") or v.get("date")
            if isinstance(inner, str) and len(inner) >= 10:
                return inner[:10]
        return None

    def _is_wanted_game(game: dict) -> bool:
        keys = ("gameDate","officialDate","startTimeUTC","startTimeLocal","gameDateISO","gameTime","gameDateTime")
        for k in keys:
            if k in game:
                s = _safe_str_date(game.get(k))
                if s == wanted:
                    return True
        week_day = _safe_str_date(game.get("date"))
        if week_day == wanted:
            return True
        return False

    def _collect_from_stats_json(data: dict) -> list[int]:
        pks: list[int] = []
        for day in (data.get("dates") or []):
            day_key = _safe_str_date(day.get("date"))
            for g in (day.get("games") or []):
                pk = g.get("gamePk")
                if not pk:
                    continue
                if day_key == wanted or _is_wanted_game(g):
                    pks.append(int(pk))
        return pks

    def _collect_from_gc_sched(sched: dict) -> list[int]:
        pks: list[int] = []
        for g in (sched.get("games") or []):
            if _is_wanted_game(g):
                pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                if pk:
                    pks.append(int(pk))
        for wk in (sched.get("gameWeek") or []):
            wk_date = _safe_str_date(wk.get("date"))
            for g in (wk.get("games") or []):
                if wk_date == wanted or _is_wanted_game(g):
                    pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                    if pk:
                        pks.append(int(pk))
        return pks

    found: set[int] = set()
    try:
        url = f"{STATS_BASE}/schedule?date={wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            found.update(_collect_from_stats_json(r.json()))
    except Exception:
        pass
    try:
        start = (d - timedelta(days=1)).isoformat()
        end = (d + timedelta(days=1)).isoformat()
        url = f"{STATS_BASE}/schedule?startDate={start}&endDate={end}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            found.update(_collect_from_stats_json(r.json()))
    except Exception:
        pass
    try:
        url = f"{SITE_BASE}/schedule/{wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            found.update(_collect_from_gc_sched(r.json()))
    except Exception:
        pass

    return sorted(found)

# ---------- Fetch shots (day / range) using StatsAPI + timeline ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str, int]:
    pks = fetch_game_pks_for_date(d)
    games_count = len(pks)
    if not pks:
        return pd.DataFrame(columns=REQUIRED_COLS), "no games", 0

    frames = []
    matchup_map: dict[int, str] = {}
    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        for pk in pks:
            try:
                r = c.get(f"{STATS_BASE}/game/{pk}/feed/live")
                r.raise_for_status()
                feed = r.json()
            except Exception:
                continue

            rows, matchup = _rows_from_statsapi_with_manpower(feed)
            if matchup:
                matchup_map[int(pk)] = matchup
            if not rows:
                continue
            df = pd.DataFrame(rows)
            # Enforce schema/order and clip coords
            for col in REQUIRED_COLS:
                if col not in df.columns:
                    df[col] = pd.NA
            df["x"] = df["x"].clip(-100, 100)
            df["y"] = df["y"].clip(-43, 43)
            # Override gamePk (if feed lacked it)
            df["gamePk"] = int(pk)
            frames.append(df[REQUIRED_COLS])

    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLS), "no shots", games_count

    shots = pd.concat(frames, ignore_index=True)
    # De-dupe
    key_cols = ["gamePk", "period", "periodTime", "team", "player", "x", "y", "event"]
    shots = shots.drop_duplicates(subset=[c for c in key_cols if c in shots.columns])

    # Add matchup
    shots["matchup"] = shots["gamePk"].map(lambda pk: matchup_map.get(int(pk)) if pd.notna(pk) else None)

    return shots[REQUIRED_COLS], "StatsAPI+Timeline", games_count

@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_between(start: _date, end: _date) -> tuple[pd.DataFrame, str, int]:
    all_frames = []
    games_total = 0
    day = start
    while day <= end:
        df, label, games_count = fetch_shots_for_date(day)
        games_total += games_count
        if not df.empty:
            all_frames.append(df.assign(source_date=str(day)))
        day += timedelta(days=1)
    if not all_frames:
        return pd.DataFrame(columns=REQUIRED_COLS + ["source_date"]), "no data", games_total
    out = pd.concat(all_frames, ignore_index=True)
    if "source_date" not in out.columns:
        out["source_date"] = pd.NA
    return out[REQUIRED_COLS + ["source_date"]], "StatsAPI+Timeline", games_total

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

    fetch_click = False

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

# --- Session state bootstrapping (default = today) ---
if "initialized" not in st.session_state:
    st.session_state["initialized"] = True
    st.session_state["data_df"] = pd.DataFrame(columns=REQUIRED_COLS)
    st.session_state["data_dates"] = _date.today()
    st.session_state["games_count"] = 0
    df_live, parser_label_boot, games_boot = fetch_shots_for_date(_date.today())
    st.session_state["data_df"] = df_live
    st.session_state["games_count"] = games_boot
    st.session_state["parser_label"] = parser_label_boot if not df_live.empty else None
    # share to other pages
    st.session_state["shots_df"] = df_live
    st.session_state["selected_start_date"] = _date.today()
    st.session_state["selected_end_date"] = _date.today()

# ---------- Load (refetch if button clicked) ----------
parser_label = st.session_state.get("parser_label")
games_count = st.session_state.get("games_count", 0)
df = st.session_state.get("data_df", pd.DataFrame(columns=REQUIRED_COLS)).copy()

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
        # share
        st.session_state["shots_df"] = df_live
        if mode == "Single day":
            st.session_state["selected_start_date"] = picked
            st.session_state["selected_end_date"] = picked
        else:
            st.session_state["selected_start_date"] = start_date
            st.session_state["selected_end_date"] = end_date

# Ensure df has required columns even if empty
for col in REQUIRED_COLS:
    if col not in df.columns:
        df[col] = pd.NA

# ---------- Filters ----------
with left:
    label_to_player: dict[str, str] = {}
    if not df.empty:
        players = df[["player", "team"]].dropna().drop_duplicates().sort_values(["team", "player"])
        label_to_player = {f"{row.team} — {row.player}": row.player for row in players.itertuples(index=False)}
        player_opts = list(label_to_player.keys())
    else:
        player_opts = []

    selected_player_labels = st.multiselect("Players", options=player_opts, default=[])
    selected_players = {label_to_player[lbl] for lbl in selected_player_labels} if selected_player_labels else None

    matchup_opts = []
    if isinstance(st.session_state.get("data_dates"), _date) or (mode == "Single day"):
        if "matchup" in df and not df["matchup"].dropna().empty:
            matchup_opts = sorted(df["matchup"].dropna().unique().tolist())
    selected_matchups = st.multiselect("Matchup (AWAY @ HOME)", options=matchup_opts, default=matchup_opts)

    goals_only = st.checkbox("Show only goals", value=False)

mask = pd.Series(True, index=df.index)
if selected_players and "player" in df.columns:
    mask &= df["player"].isin(selected_players)
if selected_matchups and "matchup" in df.columns:
    mask &= df["matchup"].isin(selected_matchups)
if goals_only and "is_goal" in df.columns:
    mask &= df["is_goal"] == 1

filtered = df[mask].copy()
for col in REQUIRED_COLS:
    if col not in filtered.columns:
        filtered[col] = pd.NA

# ---------- Summary ----------
with left:
    st.subheader("Summary")
    c1, c2, c3, c4, c5 = st.columns(5)

    df_used = filtered

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

    # Rounded white rink underlay
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

    # Center & blue lines
    RINK_Y_MIN, RINK_Y_MAX = -42.5, 42.5
    LINE_HALF_FT = 0.5
    fig.add_shape(type="rect", x0=-LINE_HALF_FT, x1=LINE_HALF_FT, y0=RINK_Y_MIN, y1=RINK_Y_MAX, line=dict(width=0), fillcolor="red", layer="below")
    for x0, x1 in [(-26.0, -25.0), (25.0, 26.0)]:
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=RINK_Y_MIN, y1=RINK_Y_MAX, line=dict(width=0), fillcolor="blue", layer="below")

    # Goal lines
    GOAL_X = 89.0
    GOAL_HALF_THICK = 0.167 / 2
    GOAL_Y_EXTENT = 36.0
    for gx in (-GOAL_X, GOAL_X):
        fig.add_shape(type="rect", x0=gx - GOAL_HALF_THICK, x1=gx + GOAL_HALF_THICK, y0=-GOAL_Y_EXTENT, y1=GOAL_Y
