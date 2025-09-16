import pandas as pd
import httpx
import streamlit as st
from datetime import date as _date, timedelta
from io import StringIO

# ---- Rink plot import with dual-path fallback ----
try:
    from app.components.rink_plot import base_rink, add_shots
except ModuleNotFoundError:
    from components.rink_plot import base_rink, add_shots

st.set_page_config(page_title="NHL Shot Tracker", layout="wide")
st.title("NHL Shot Tracker")

STATS_BASE = "https://statsapi.web.nhl.com/api/v1"
SITE_BASE = "https://api-web.nhle.com/v1"

# ---------- Parsers (StatsAPI + GameCenter fallback) ----------
STATS_SHOT_EVENTS = {"Shot", "Missed Shot", "Goal"}
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


def _gc_team_maps(feed: dict) -> dict[int, str]:
    """Build team id -> abbrev map from GameCenter feed."""
    id_to_abbrev: dict[int, str] = {}
    for key in ("homeTeam", "awayTeam"):
        team = feed.get(key)
        if isinstance(team, dict):
            tid = team.get("id") or team.get("teamId")
            ab = team.get("abbrev") or team.get("triCode") or team.get("abbreviation")
            if isinstance(tid, int) and isinstance(ab, str):
                id_to_abbrev[tid] = ab
    teams = feed.get("teams")
    if isinstance(teams, dict):
        for side in ("home", "away"):
            team = teams.get(side)
            if isinstance(team, dict):
                tid = team.get("id") or team.get("teamId")
                ab = team.get("abbrev") or team.get("triCode") or team.get("abbreviation")
                if isinstance(tid, int) and isinstance(ab, str):
                    id_to_abbrev[tid] = ab
    return id_to_abbrev


def _infer_strength_from_skaters(det: dict) -> str:
    """Derive 5v5/PP/PK from skater counts when explicit strength is missing."""
    hs = det.get("homeSkaters")
    as_ = det.get("awaySkaters")
    if isinstance(hs, int) and isinstance(as_, int):
        if hs == 5 and as_ == 5:
            return "5v5"
        if hs > as_:
            return "PP" if hs - as_ >= 1 else "EV"
        if as_ > hs:
            return "PK" if as_ - hs >= 1 else "EV"
        return "EV"
    return "Unknown"


def _rows_from_gamecenter(feed: dict) -> list[dict]:
    rows: list[dict] = []

    # 1) Normalize plays to a flat list (handles list/dict/by-period shapes)
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
        extend_from_candidate(plays_root)  # if it already has "plays"
    elif isinstance(plays_root, list):
        extend_from_candidate(plays_root)

    # 2) Build team id->abbrev map
    id_to_abbrev = _gc_team_maps(feed)

    # 3) Convert to rows
    for p in plays or []:
        ev_key = (p.get("typeDescKey") or p.get("typeCode") or "").lower()
        if ev_key not in GC_SHOT_EVENTS:
            continue

        det = p.get("details") or {}
        x, y = det.get("xCoord"), det.get("yCoord")
        if x is None or y is None:
            continue

        # Team abbrev
        team_raw = det.get("eventOwnerTeamAbbrev") or det.get("eventOwnerTeamId")
        if isinstance(team_raw, int):
            team = id_to_abbrev.get(team_raw) or str(team_raw)
        else:
            team = team_raw

        # Shooter name fallback strategy
        shooter = det.get("shootingPlayerName") or det.get("scoringPlayerName")
        if not shooter:
            plist = p.get("players") or []
            for pl in plist:
                shooter = pl.get("playerName") or pl.get("fullName")
                if shooter:
                    break
        if not shooter:
            pid = det.get("shootingPlayerId") or det.get("scoringPlayerId")
            if pid is not None:
                plist = p.get("players") or []
                for pl in plist:
                    if pl.get("playerId") == pid:
                        shooter = (
                            pl.get("playerName")
                            or (pl.get("firstName") and pl.get("lastName") and f"{pl['firstName']} {pl['lastName']}")
                        )
                        if shooter:
                            break
        shooter = shooter or "Unknown"

        pd_desc = p.get("periodDescriptor") or {}
        period = pd_desc.get("number")
        period_time = p.get("timeInPeriod") or p.get("timeRemaining") or None

        # Strength normalization/derivation
        raw_strength = (det.get("strength") or "").lower()
        if raw_strength in {"ev", "even"}:
            strength = "5v5"
        elif raw_strength in {"pp", "power play"}:
            strength = "PP"
        elif raw_strength in {"sh", "penalty kill"}:
            strength = "PK"
        elif raw_strength:
            strength = raw_strength.upper()
        else:
            strength = _infer_strength_from_skaters(det)

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


def _shots_from_feed(feed: dict) -> pd.DataFrame:
    rows = _rows_from_statsapi(feed)
    source = "StatsAPI"
    if not rows:
        rows = _rows_from_gamecenter(feed)
        source = "GameCenter"
    df = pd.DataFrame(
        rows,
        columns=["gamePk", "period", "periodTime", "event", "team", "player", "x", "y", "strength", "is_goal"],
    )
    df.attrs["parser_source"] = source
    return df


# ---------- Exact-date schedule (StatsAPI preferred) ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    """Return game PKs for exact date `d` (StatsAPI first; site API fallback filtered)."""
    wanted = d.isoformat()

    # Preferred: StatsAPI schedule
    try:
        url = f"{STATS_BASE}/schedule?date={wanted}"
        with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
        pks = []
        for day in data.get("dates", []):
            for g in day.get("games", []):
                pk = g.get("gamePk")
                if pk:
                    pks.append(int(pk))
        if pks:
            return sorted(set(pks))
    except Exception:
        pass

    # Fallback: site schedule (may include a week) -> filter to day
    try:
        url = f"{SITE_BASE}/schedule/{wanted}"
        with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
            r = c.get(url)
            r.raise_for_status()
            sched = r.json()
        games_iter = []
        if isinstance(sched.get("gameWeek"), list):
            for wk in sched["gameWeek"]:
                games_iter.extend(wk.get("games", []))
        elif isinstance(sched.get("games"), list):
            games_iter = sched["games"]

        def date_of(game: dict) -> str | None:
            for key in ("startTimeUTC", "startTime", "gameDate", "startTimeLocal"):
                v = game.get(key)
                if isinstance(v, str) and len(v) >= 10:
                    return v[:10]
            return None

        pks = []
        for g in games_iter:
            if date_of(g) == wanted:
                pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                if pk:
                    pks.append(int(pk))
        return sorted(set(pks))
    except Exception:
        return []


# ---------- Fetch shots (day / range), de-dupe & clean ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str, int]:
    """Return (shots_df, source_label, games_count) for a given date."""
    pks = fetch_game_pks_for_date(d)
    games_count = len(pks)
    if not pks:
        return pd.DataFrame(), "no games", 0

    frames = []
    used_sources = set()
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
            df = _shots_from_feed(feed)
            if not df.empty:
                frames.append(df)
                used_sources.add(df.attrs.get("parser_source", "Unknown"))

    if not frames:
        return pd.DataFrame(), "no shots", games_count

    shots = pd.concat(frames, ignore_index=True)

    # De-dupe & clip
    key_cols = ["gamePk", "period", "periodTime", "team", "player", "x", "y", "event"]
    shots = shots.drop_duplicates(subset=[c for c in key_cols if c in shots.columns])
    shots["x"] = shots["x"].clip(-100, 100)
    shots["y"] = shots["y"].clip(-43, 43)

    label = "/".join(sorted(used_sources)) if used_sources else "Unknown"
    return shots, label, games_count


@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_between(start: _date, end: _date) -> tuple[pd.DataFrame, str, int]:
    """Fetch and combine shots for an inclusive date range."""
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
        return pd.DataFrame(), "no data", games_total
    out = pd.concat(all_frames, ignore_index=True)
    return out, "/".join(sorted(s for s in used if s and s not in {"no games", "no shots"})), games_total


# ---------- Sidebar: date / range + filters ----------
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

    if mode == "Single day":
        default = preset[1] if preset and preset[0] == "single" else _date.today()
        picked = st.date_input("Date", value=default, max_value=_date.today())
        fetch = st.button("Retrieve Data")
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
        fetch = st.button("Retrieve Data")

# ---------- Load data ----------
parser_label = None
games_count = 0

# No demo mixing after user fetch; demo only before any fetch
if "data_df" not in st.session_state:
    st.session_state["data_df"] = None
    st.session_state["data_dates"] = None
    st.session_state["games_count"] = 0

if fetch:
    with st.spinner("Fetching NHL data..."):
        if mode == "Single day":
            df_live, parser_label, games_count = fetch_shots_for_date(picked)
        else:
            df_live, parser_label, games_count = fetch_shots_between(start_date, end_date)

        if df_live.empty:
            st.info("No data for the selected date(s).")
            df = pd.DataFrame(columns=["gamePk", "period", "periodTime", "event", "team", "player", "x", "y", "strength", "is_goal"])
            st.session_state["data_df"] = df
            st.session_state["data_dates"] = (picked if mode == "Single day" else (start_date, end_date))
            st.session_state["games_count"] = 0
            parser_label = None
        else:
            df = df_live
            st.session_state["data_df"] = df
            st.session_state["data_dates"] = (picked if mode == "Single day" else (start_date, end_date))
            st.session_state["games_count"] = games_count
else:
    df = st.session_state.get("data_df")
    games_count = st.session_state.get("games_count", 0)
    if df is None:
        df = pd.read_csv("data/curated/demo_shots.csv")

# ---------- Summary ----------
with left:
    st.subheader("Summary")
    total_shots = int(df.shape[0]) if not df.empty else 0
    total_goals = int(df["is_goal"].sum()) if "is_goal" in df else 0
    uniq_players = df["player"].nunique() if "player" in df else 0
    uniq_teams = df["team"].nunique() if "team" in df else 0
    st.metric("Games", games_count)
    st.metric("Shots", total_shots)
    st.metric("Goals", total_goals)
    st.metric("Players w/ shots", uniq_players)
    st.metric("Teams", uniq_teams)
    if parser_label:
        st.caption(f"Parsed via: {parser_label}")

# ---------- Filters ----------
with left:
    player_opts = ["All"] + (sorted(df["player"].dropna().unique().tolist()) if "player" in df else [])
    team_opts = sorted([t for t in df["team"].dropna().unique().tolist()]) if "team" in df else []
    selected_player = st.selectbox("Player", options=player_opts)
    selected_teams = st.multiselect("Teams", options=team_opts, default=team_opts)
    strength_opts = ["All"] + (sorted(df["strength"].dropna().unique().tolist()) if "strength" in df else [])
    selected_strength = st.selectbox("Game state", options=strength_opts)
    goals_only = st.checkbox("Show only goals", value=False)

# Apply filters
mask = pd.Series(True, index=df.index)
if selected_player != "All" and "player" in df:
    mask &= df["player"] == selected_player
if selected_teams and "team" in df:
    mask &= df["team"].isin(selected_teams)
if selected_strength != "All" and "strength" in df:
    mask &= df["strength"] == selected_strength
if goals_only and "is_goal" in df:
    mask &= df["is_goal"] == 1

filtered = df[mask].copy()

# ---------- Plot ----------
with right:
    # build hover text "Player (TEAM)" for traces where lengths match
    if not filtered.empty and {"player", "team"}.issubset(filtered.columns):
        hover_texts = filtered.apply(lambda r: f"{r['player']} ({r['team']})", axis=1).tolist()
    else:
        hover_texts = None

    fig = base_rink()
    fig = add_shots(fig, filtered)

    # Safely update hover to show only player + team
    if hover_texts:
        for tr in getattr(fig, "data", []):
            if getattr(tr, "type", "") == "scatter" and hasattr(tr, "x") and len(getattr(tr, "x", [])) == len(hover_texts):
                tr.update(text=hover_texts, hovertemplate="%{text}")

    st.plotly_chart(fig, use_container_width=True)

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
        filtered.groupby(["player", "team", "strength"], dropna=False)
        .agg(shots=("player", "size"), goals=("is_goal", "sum"))
        .reset_index()
        .sort_values(["shots", "goals"], ascending=[False, False])
    )
    st.dataframe(summary, use_container_width=True)

    # export summary CSV
    sum_buf = StringIO()
    summary.to_csv(sum_buf, index=False)
    st.download_button(
        "Download range summary CSV",
        data=sum_buf.getvalue(),
        file_name="range_summary.csv",
        mime="text/csv",
    )

st.caption(
    "Source: NHL Stats/GameCenter APIs • "
    f"Rows: {len(filtered)} • Teams: {len(selected_teams) if selected_teams else 0} • "
    f"Filters: player={selected_player}, strength={selected_strength}, goals_only={goals_only}"
)
