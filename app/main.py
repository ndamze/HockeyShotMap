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

st.set_page_config(page_title="Hockey Shot Maps", layout="wide")
st.title("Hockey Shot Heatmaps")

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


def _rows_from_gamecenter(feed: dict) -> list[dict]:
    rows: list[dict] = []

    # 1) Normalize `plays` into a flat list of play dicts, regardless of shape
    plays_root = feed.get("plays", [])
    plays: list[dict] = []

    def extend_from_candidate(cand):
        nonlocal plays
        if cand is None:
            return
        if isinstance(cand, list):
            if cand and isinstance(cand[0], dict) and "plays" in cand[0]:
                # [{"plays":[...]}, {"plays":[...]}]  (by-period blocks)
                for block in cand:
                    plays.extend(block.get("plays") or [])
            else:
                # direct list of plays
                plays.extend(cand)
        elif isinstance(cand, dict):
            # rare: single block with a "plays" key
            plays.extend(cand.get("plays", []))

    if isinstance(plays_root, dict):
        extend_from_candidate(plays_root.get("all"))
        extend_from_candidate(plays_root.get("byPeriod"))
        extend_from_candidate(plays_root.get("currentPlay"))
        extend_from_candidate(plays_root)  # if it already has "plays"
    elif isinstance(plays_root, list):
        extend_from_candidate(plays_root)
    else:
        plays = []

    if not isinstance(plays, list):
        plays = []

    # 2) Convert GC plays to our rows
    for p in plays:
        ev_key = (p.get("typeDescKey") or p.get("typeCode") or "").lower()
        if ev_key not in {"shot-on-goal", "missed-shot", "goal"}:
            continue

        det = p.get("details") or {}
        x, y = det.get("xCoord"), det.get("yCoord")
        if x is None or y is None:
            continue

        team = det.get("eventOwnerTeamAbbrev") or det.get("eventOwnerTeamId") or None

        # --- improved shooter name fallback ---
        shooter = det.get("shootingPlayerName") or det.get("scoringPlayerName")
        if not shooter:
            plist = p.get("players") or []
            for pl in plist:
                shooter = pl.get("playerName") or pl.get("fullName")
                if shooter:
                    break
        shooter = shooter or "Unknown"
        # --- end fallback ---

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
                "event": (
                    "Goal" if ev_key == "goal"
                    else ("Shot" if ev_key == "shot-on-goal" else "Missed Shot")
                ),
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


# ---------- Live data fetchers (cached) ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    """
    Return game PKs for the *exact* date `d`, not the whole week.
    """
    url = f"{SITE_BASE}/schedule/{d.isoformat()}"
    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        resp = c.get(url)
        resp.raise_for_status()
        sched = resp.json()

    wanted = d.isoformat()
    pks: list[int] = []

    def date_of(game: dict) -> str | None:
        # Try common fields, fall back to parsing from others
        for key in ("startTimeUTC", "startTime", "gameDate", "startTimeLocal"):
            val = game.get(key)
            if isinstance(val, str) and len(val) >= 10:
                return val[:10]
        return None

    # Shapes seen: {"gameWeek":[{"games":[...]} ...]} OR {"games":[...]}
    games_iter = []
    if isinstance(sched.get("gameWeek"), list):
        for wk in sched["gameWeek"]:
            games_iter.extend(wk.get("games", []))
    elif isinstance(sched.get("games"), list):
        games_iter = sched["games"]

    for g in games_iter:
        gdate = date_of(g)
        if gdate == wanted:
            pk = g.get("id") or g.get("gamePk") or g.get("gameId")
            if pk is not None:
                pks.append(int(pk))

    return sorted(set(pks))



@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str, int]:
    """Return (shots_df, source_label, games_count) for a given date."""
    try:
        pks = fetch_game_pks_for_date(d)
    except Exception as e:
        return pd.DataFrame(), f"schedule error: {e}", 0

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

    if "_preset" in st.session_state:
        preset = st.session_state.pop("_preset")
    else:
        preset = None

    if mode == "Single day":
        default = preset[1] if preset and preset[0] == "single" else _date.today()
        picked = st.date_input("Date", value=default, max_value=_date.today())
        fetch = st.button("Fetch day")
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
        fetch = st.button("Fetch range")

# ---------- Load data ----------
parser_label = None
games_count = 0

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
            st.warning("No shots for the selected date(s). Falling back to demo data.")
            df = pd.read_csv("data/curated/demo_shots.csv")
            st.session_state["data_df"] = df
            st.session_state["data_dates"] = None
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
    # Player options from data (shooters in selected date(s) or demo)
    player_opts = ["All"] + (sorted(df["player"].dropna().unique().tolist()) if "player" in df else [])
    # Team multiselect
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
    fig = base_rink()
    fig = add_shots(fig, filtered)
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
    f"Rows: {len(filtered)} • "
    f"Teams: {len(selected_teams) if selected_teams else 0} • "
    f"Filters: player={selected_player}, strength={selected_strength}, goals_only={goals_only}"
)
