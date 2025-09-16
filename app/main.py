import pandas as pd
import httpx
import streamlit as st
from datetime import date as _date

# ---- Rink plot import with dual-path fallback ----
try:
    from app.components.rink_plot import base_rink, add_shots  # when repo root on PYTHONPATH
except ModuleNotFoundError:
    from components.rink_plot import base_rink, add_shots       # when running inside app/

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
        )  # <-- closes dict + append
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

    # Some responses group plays by period
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
                "event": (
                    "Goal"
                    if ev_key == "goal"
                    else ("Shot" if ev_key == "shot-on-goal" else "Missed Shot")
                ),
                "team": team,
                "player": shooter,
                "x": float(x),
                "y": float(y),
                "strength": strength,
                "is_goal": 1 if ev_key == "goal" else 0,
            }
        )  # <-- closes dict + append
    return rows


def _shots_from_feed(feed: dict) -> pd.DataFrame:
    rows = _rows_from_statsapi(feed)
    source = "StatsAPI"
    if not rows:
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
    df.attrs["parser_source"] = source
    return df


# ---------- Live data fetchers (cached) ----------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    url = f"{SITE_BASE}/schedule/{d.isoformat()}"
    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        resp = c.get(url)
        resp.raise_for_status()
        sched = resp.json()

    pks: list[int] = []
    if "gameWeek" in sched:
        for week in sched["gameWeek"]:
            for g in week.get("games", []):
                pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                if pk:
                    pks.append(int(pk))
    elif "games" in sched:
        for g in sched["games"]:
            pk = g.get("id") or g.get("gamePk") or g.get("gameId")
            if pk:
                pks.append(int(pk))
    return sorted(set(pks))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str]:
    try:
        pks = fetch_game_pks_for_date(d)
    except Exception as e:
        return pd.DataFrame(), f"schedule error: {e}"

    if not pks:
        return pd.DataFrame(), "no games"

    all_frames: list[pd.DataFrame] = []
    used_sources: set[str] = set()
    with httpx.Client(timeout=20.0, headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"}, trust_env=True) as c:
        for pk in pks:
            # Try StatsAPI first, fallback to GameCenter
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
                all_frames.append(df)
                used_sources.add(df.attrs.get("parser_source", "Unknown"))

    if not all_frames:
        return pd.DataFrame(), "no shots"

    shots = pd.concat(all_frames, ignore_index=True)
    label = "/".join(sorted(used_sources)) if used_sources else "Unknown"
    return shots, label


# ---------- Sidebar: source & filters ----------
left, right = st.columns([1, 3])

with left:
    st.subheader("Data source")
    live_mode = st.toggle("Live mode (NHL)", value=False)
    picked = st.date_input(
        "Date",
        value=_date.today(),
        max_value=_date.today(),
        help="Select day to fetch games",
    )
    fetch_btn = st.button("Fetch live shots for date") if live_mode else None

# Load data (live or demo)
parser_source = None
if live_mode and (
    fetch_btn
    or "live_cached_df" not in st.session_state
    or st.session_state.get("live_date") != picked
):
    with st.spinner("Fetching live data..."):
        df_live, source = fetch_shots_for_date(picked)
        if df_live.empty:
            st.warning(f"No live shots for {picked} ({source}). Showing demo data instead.")
            df = pd.read_csv("data/curated/demo_shots.csv")
        else:
            df = df_live
            parser_source = source
            st.session_state["live_cached_df"] = df
            st.session_state["live_date"] = picked
else:
    if live_mode and st.session_state.get("live_cached_df") is not None and st.session_state.get("live_date") == picked:
        df = st.session_state["live_cached_df"]
        parser_source = df.attrs.get("parser_source") or parser_source
    else:
        df = pd.read_csv("data/curated/demo_shots.csv")

# Parser info (if any)
if parser_source:
    st.sidebar.info(f"Data parsed using **{parser_source}**")

# ---------- Filters ----------
with left:
    player = st.selectbox("Player", options=["All"] + sorted(df["player"].dropna().unique().tolist()))
    strength = st.selectbox("Game state", options=["All"] + sorted(df["strength"].dropna().unique().tolist()))
    goals_only = st.checkbox("Show only goals", value=False)

mask = pd.Series(True, index=df.index)
if player != "All":
    mask &= df["player"] == player
if strength != "All":
    mask &= df["strength"] == strength
if goals_only:
    mask &= df["is_goal"] == 1

filtered = df[mask].copy()

# ---------- Plot ----------
with right:
    fig = base_rink()
    fig = add_shots(fig, filtered)
    st.plotly_chart(fig, use_container_width=True)

st.caption(f"Rows: {len(filtered)} | Source: {'LIVE' if live_mode and parser_source else 'DEMO'}")
