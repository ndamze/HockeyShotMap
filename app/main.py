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
    return max(0, (_safe_int(m) or 0) * 60 + (_safe_int(ss) or 0))

def _game_seconds(period: int | None, period_time: str | None) -> int:
    """Absolute game seconds since 0:00 of P1."""
    p = _safe_int(period) or 1
    return (p - 1) * 20 * 60 + _parse_mmss(period_time)

def _norm_strength_final(label: str | None) -> str:
    """
    Normalize to: 5v5, PP, PK, 4v4, 3v3, 6v5, 5v6, etc.
    Unknown/missing stays 'Unknown' (we do NOT collapse to 5v5).
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
        return f"{a}v{b}"
    if l in {"unknown", "unk", "n/a", "na", "null", "none"}:
        return "Unknown"
    return l.upper()

# =========================
# Team identity helpers (use IDs, not names)
# =========================

def _team_identity(feed: dict):
    """
    Returns:
      home_id, away_id, id_to_tricode (dict[int,str]), matchup (e.g., 'NYR @ BOS')
    """
    id_to_tricode: dict[int, str] = {}
    game = (feed.get("gameData") or {}).get("teams") or {}
    home = game.get("home") or {}
    away = game.get("away") or {}
    home_id = _safe_int(home.get("id"))
    away_id = _safe_int(away.get("id"))
    home_tri = (home.get("triCode") or home.get("abbreviation") or "").strip() or None
    away_tri = (away.get("triCode") or away.get("abbreviation") or "").strip() or None
    if home_id and home_tri:
        id_to_tricode[home_id] = home_tri
    if away_id and away_tri:
        id_to_tricode[away_id] = away_tri
    matchup = f"{away_tri} @ {home_tri}" if (home_tri and away_tri) else None
    return home_id, away_id, id_to_tricode, matchup

def _label_for_team_id(team_id: int | None, id_to_tri: dict[int, str], fallback_name: str | None) -> str:
    if team_id in id_to_tri:
        return id_to_tri[team_id]
    if isinstance(fallback_name, str) and fallback_name.strip():
        return fallback_name.strip()
    return "UNK"

# =========================
# StatsAPI → Manpower simulation (fixed to use team IDs)
# =========================

def _rows_from_statsapi_with_manpower(feed: dict) -> list[dict]:
    rows: list[dict] = []
    live = (feed.get("liveData") or {})
    plays = (live.get("plays") or {})
    all_plays = plays.get("allPlays") or []
    if not all_plays:
        return rows

    home_id, away_id, id_to_tri, matchup = _team_identity(feed)

    # active/pending penalties by side (HOME, AWAY)
    active_home: list[dict] = []   # each: {"end": int, "type": "minor"/"major"}
    active_away: list[dict] = []
    pending_home: list[dict] = []  # each: {"start": int, "end": int, "type": "minor"}
    pending_away: list[dict] = []

    def _expire_segments(t: int):
        nonlocal active_home, active_away
        active_home = [seg for seg in active_home if seg["end"] > t]
        active_away = [seg for seg in active_away if seg["end"] > t]

    def _activate_pending(t: int):
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
        def minus_five(active_list):
            return max(3, 5 - len(active_list))
        return minus_five(active_home), minus_five(active_away)

    def _is_minor(seg: dict) -> bool:
        return seg.get("type") == "minor"

    def _cancel_one_minor_for(side: str, t: int):
        """On a PP goal: remove the minor with least time left on that (short-handed) side."""
        target = active_home if side == "HOME" else active_away
        minors = [(i, seg) for i, seg in enumerate(target) if _is_minor(seg)]
        if not minors:
            return False
        idx, _seg = min(minors, key=lambda kv: kv[1]["end"])
        del target[idx]
        return True

    def _promote_pending_minor_immediate(side: str, t: int):
        """If a double-minor was in progress and we just ended the first half on a PP goal,
        immediately start the next minor at time t."""
        pending = pending_home if side == "HOME" else pending_away
        if not pending:
            return
        # pick the earliest pending minor
        idx = None
        earliest = None
        for i, seg in enumerate(pending):
            if seg.get("type") != "minor":
                continue
            if earliest is None or seg["start"] < earliest:
                earliest = seg["start"]; idx = i
        if idx is None:
            return
        seg = pending.pop(idx)
        dur = seg["end"] - seg["start"]
        (active_home if side == "HOME" else active_away).append({"end": t + dur, "type": "minor"})

    # Walk plays in order
    for p in all_plays:
        about = p.get("about") or {}
        period = about.get("period")
        period_time = about.get("periodTime")
        t = _game_seconds(period, period_time)

        # update penalty clocks
        _expire_segments(t)
        _activate_pending(t)

        result = p.get("result") or {}
        event = (result.get("eventTypeId") or result.get("event") or "").upper()

        # ---- PENALTY: start segments via team ID (penalized team = play.team)
        if event == "PENALTY":
            sev = (result.get("penaltySeverity") or "").title()
            mins = _safe_int(result.get("penaltyMinutes")) or 0

            team_obj = p.get("team") or {}
            team_id = _safe_int(team_obj.get("id"))
            team_name = team_obj.get("name") or ""
            if team_id is None and "triCode" in team_obj:
                # very rare, but keep a fallback (won't help for side though)
                team_name = team_obj.get("triCode") or team_name

            penalized_side = None
            if team_id is not None and (home_id is not None or away_id is not None):
                if home_id is not None and team_id == home_id:
                    penalized_side = "HOME"
                elif away_id is not None and team_id == away_id:
                    penalized_side = "AWAY"

            reduces = False
            segments: list[tuple[int,int,str]] = []
            if sev == "Minor" and mins in (2, 4):
                reduces = True
                if mins == 2:
                    segments.append((t, t + 120, "minor"))
                else:  # 4 = double minor -> two consecutive minors
                    segments.append((t, t + 120, "minor"))
                    segments.append((t + 120, t + 240, "minor"))
            elif sev in ("Major", "Match") and mins >= 5:
                reduces = True
                segments.append((t, t + mins * 60, "major"))
            # Misconducts etc. do not reduce manpower

            if reduces and penalized_side:
                if penalized_side == "HOME":
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

            continue  # done with penalty

        # ---- SHOT/MISS/GOAL
        if event in {"SHOT", "MISSED_SHOT", "GOAL"}:
            coords = p.get("coordinates") or {}
            x, y = coords.get("x"), coords.get("y")
            team_obj = p.get("team") or {}
            team_id = _safe_int(team_obj.get("id"))
            team_name = team_obj.get("name") or ""
            tri = _label_for_team_id(team_id, id_to_tri, team_name)

            # shooter name
            shooter = None
            for pl in (p.get("players") or []):
                if pl.get("playerType") in ("Shooter", "Scorer"):
                    shooter = (pl.get("player") or {}).get("fullName")
                    break
            shooter = shooter or "Unknown"

            # counts BEFORE any PP-goal cancellation
            home_cnt, away_cnt = _counts_now()

            # determine shooting side by ID
            if team_id is not None:
                shooter_side = "HOME" if (home_id is not None and team_id == home_id) else (
                               "AWAY" if (away_id is not None and team_id == away_id) else None)
            else:
                shooter_side = None

            if home_cnt == away_cnt:
                label = f"{home_cnt}v{away_cnt}"
            else:
                if shooter_side == "HOME":
                    label = "PP" if home_cnt > away_cnt else "PK"
                elif shooter_side == "AWAY":
                    label = "PP" if away_cnt > home_cnt else "PK"
                else:
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
                    "matchup": matchup,
                }
            )

            # PP goal: cancel ONE minor from short-handed side; if it was a double-minor, start next half immediately
            if event == "GOAL" and home_cnt != away_cnt and shooter_side in {"HOME", "AWAY"}:
                short_side = "AWAY" if home_cnt > away_cnt else "HOME"
                cancelled = _cancel_one_minor_for(short_side, t)
                if cancelled:
                    _promote_pending_minor_immediate(short_side, t)

            continue  # done with shot-like play

        # others: we only needed them to advance the penalty clock

    return rows

# =========================
# Game/Day fetching (schedule union kept)
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
    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        for pk in pks:
            try:
                r = c.get(f"{STATS_BASE}/game/{pk}/feed/live")
                r.raise_for_status()
                feed = r.json()
            except Exception:
                continue

            rows = _rows_from_statsapi_with_manpower(feed)
            if not rows:
                continue
            df = pd.DataFrame(rows)

            # Enforce schema/order and clip coords
            for col in REQUIRED_COLS:
                if col not in df.columns:
                    df[col] = pd.NA
            df["x"] = df["x"].clip(-100, 100)
            df["y"] = df["y"].clip(-43, 43)
            # Ensure gamePk
            df["gamePk"] = int(pk)

            frames.append(df[REQUIRED_COLS])

    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLS), "no shots", games_count

    shots = pd.concat(frames, ignore_index=True)
    # De-dupe
    key_cols = ["gamePk", "period", "periodTime", "team", "player", "x", "y", "event"]
    shots = shots.drop_duplicates(subset=[c for c in key_cols if c in shots.columns])

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
# UI (unchanged visuals)
# =========================

left, right = st.columns([1, 3])

with left:
    st.subheader("Date selection")

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
        fig.add_shape(type="rect", x0=gx - GOAL_HALF_THICK, x1=gx + GOAL_HALF_THICK, y0=-GOAL_Y_EXTENT, y1=GOAL_Y_EXTENT, line=dict(width=0), fillcolor="red", layer="below")

    # End-zone circles & dots
    ez_r = 15.0
    ez_centers = [(-69, 22), (-69, -22), (69, 22), (69, -22)]
    for cx, cy in ez_centers:
        fig.add_shape(type="circle", x0=cx - ez_r, x1=cx + ez_r, y0=cy - ez_r, y1=cy + ez_r, line=dict(color="red", width=2), fillcolor="rgba(0,0,0,0)", layer="below")
    DOT_R = 1.0
    for cx, cy in ez_centers:
        fig.add_shape(type="circle", x0=cx - DOT_R, x1=cx + DOT_R, y0=cy - DOT_R, y1=cy + DOT_R, line=dict(width=0), fillcolor="red", layer="below")
    nz_spots = [(-20, 22), (-20, -22), (20, 22), (20, -22)]
    for cx, cy in nz_spots:
        fig.add_shape(type="circle", x0=cx - DOT_R, x1=cx + DOT_R, y0=cy - DOT_R, y1=cy + DOT_R, line=dict(width=0), fillcolor="blue", layer="below")

    # Center-ice circle & dot
    center_r = 15.0
    fig.add_shape(type="circle", x0=-center_r, x1=center_r, y0=-center_r, y1=center_r, line=dict(color="blue", width=2), fillcolor="rgba(0,0,0,0)", layer="below")
    fig.add_shape(type="circle", x0=-DOT_R, x1=DOT_R, y0=-DOT_R, y1=DOT_R, line=dict(width=0), fillcolor="blue", layer="below")

    # Goal creases
    crease_radius = 6
    crease_color = "rgba(25, 118, 210, 0.55)"
    theta = np.linspace(-np.pi / 2, np.pi / 2, 50)
    x_left = -89 + crease_radius * np.cos(theta); y_left = 0 + crease_radius * np.sin(theta)
    fig.add_trace(go.Scatter(x=x_left, y=y_left, fill="toself", mode="lines", line=dict(color="rgba(0,0,0,0)"), fillcolor=crease_color, showlegend=False, hoverinfo="skip", opacity=0.7))
    x_right = 89 - crease_radius * np.cos(theta); y_right = 0 + crease_radius * np.sin(theta)
    fig.add_trace(go.Scatter(x=x_right, y=y_right, fill="toself", mode="lines", line=dict(color="rgba(0,0,0,0)"), fillcolor=crease_color, showlegend=False, hoverinfo="skip", opacity=0.7))

    # Background
    ARENA_BG = "#E9ECEF"
    fig.update_layout(
        plot_bgcolor=ARENA_BG, paper_bgcolor=ARENA_BG,
        margin=dict(l=10, r=10, t=20, b=10), height=520,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="black"), bgcolor="rgba(0,0,0,0)", borderwidth=0),
        hoverlabel=dict(font=dict(color="white"), bgcolor="rgba(0,0,0,0.7)"),
    )

    # ---- Scatter data & render ----
    if not filtered.empty:
        def _hover_row(r):
            team = r.get("team") or ""
            data_dates = st.session_state.get("data_dates")
            if "source_date" in r and isinstance(r["source_date"], str) and r["source_date"]:
                date_str = r["source_date"]
            elif isinstance(data_dates, (_date,)):
                date_str = data_dates.isoformat()
            else:
                date_str = ""
            period = r.get("period")
            try:
                pnum = int(period) if pd.notna(period) else None
            except Exception:
                pnum = None
            ptime = (r.get("periodTime") or "").strip()
            when = f"P{pnum} {ptime}".strip() if pnum else ptime
            stg = r.get("strength")
            stg = stg if isinstance(stg, str) and stg.strip() and stg.upper() != "UNKNOWN" else None
            pieces = []
            if team: pieces.append(team)
            if date_str: pieces.append(date_str)
            if when: pieces.append(when)
            if stg: pieces.append(stg)
            return "<br>".join(pieces)

        non_goals = filtered[filtered["is_goal"] != 1] if "is_goal" in filtered else filtered
        goals = filtered[filtered["is_goal"] == 1] if "is_goal" in filtered else filtered.iloc[0:0]

        if not non_goals.empty:
            fig.add_trace(go.Scatter(
                x=non_goals.get("x", []), y=non_goals.get("y", []),
                mode="markers",
                marker=dict(
                    color=[TEAM_COLORS.get(t, "#888888") for t in non_goals.get("team", pd.Series([""] * len(non_goals))).fillna("")],
                    size=7, opacity=0.8, line=dict(color="black", width=0.8),
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
                    color=[TEAM_COLORS.get(t, "#888888") for t in goals.get("team", pd.Series([""] * len(goals))).fillna("")],
                    size=9, opacity=0.95, symbol="star", line=dict(color="black", width=1.0),
                ),
                text=[_hover_row(r) for _, r in goals.iterrows()],
                hovertemplate="%{text}<extra></extra>",
                name="Goals",
            ))

        st.plotly_chart(fig, use_container_width=True)
    else:
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

# ---------- Optional: summarized table for ranges ----------
if not filtered.empty and isinstance(st.session_state.get("data_dates"), tuple):
    st.subheader("Player summary (selected range)")
    summary = (
        filtered.groupby(["player", "team"], dropna=False)
        .agg(shots=("player", "size"), goals=("is_goal", "sum"))
        .reset_index()
        .sort_values(["shots", "goals"], ascending=[False, False])
    )
    st.dataframe(summary, use_container_width=True, height=260)

# ---------- Debug (quick peek) ----------
with st.expander("Debug strength (dev only)"):
    if not df.empty and "strength" in df.columns:
        st.write(df["strength"].value_counts(dropna=False).head(50))
        st.caption("Strength computed from StatsAPI penalties using team IDs; PP goal ends a MINOR and promotes next half of a double-minor.")
# Footer
st.caption(
    f"Rows: {len(filtered)} • "
    f"Filters → players: {'custom' if (st.session_state.get('shots_df') is not None and 'players' in locals() and len(players)>0) else 'All'}, "
    f"goals_only: {bool(goals_only)}"
)
