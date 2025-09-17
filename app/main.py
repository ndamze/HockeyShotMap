from __future__ import annotations

# ===== Imports =====
import httpx
import numpy as np
import pandas as pd
import streamlit as st
from datetime import date as _date, timedelta

# Plotly is used by the rink component (we import the base figure from your component)
try:
    # when running from repo root
    from app.components.rink_plot import base_rink
except ModuleNotFoundError:
    try:
        # when Streamlit sets CWD to app/
        from components.rink_plot import base_rink
    except ModuleNotFoundError:
        # last resort: minimal inline fallback if the component cannot be imported
        import plotly.graph_objects as go

        def base_rink():
            fig = go.Figure()
            fig.update_xaxes(range=[-100, 100], visible=False)
            fig.update_yaxes(range=[-42.5, 42.5], scaleanchor="x", scaleratio=1, visible=False)
            return fig

# ===== Constants =====
STATS_BASE = "https://statsapi.web.nhl.com/api/v1"
SITE_BASE = "https://api-web.nhle.com/v1"

REQUIRED_COLS = [
    "gamePk", "period", "periodTime", "event", "team",
    "player", "x", "y", "strength", "is_goal", "matchup"
]

STATS_SHOT_EVENTS = {"Shot", "Missed Shot", "Goal"}


# ===== Helpers / Normalization =====
def _norm_name_value(v) -> str | None:
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, dict):
        # common containers { "fullName": "..." } or {"first":"...","last":"..."}
        for k in ("fullName", "name", "displayName"):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
    return None


def _full_name(first, last, fallback: str | None = None) -> str:
    f = (first or "").strip() if isinstance(first, str) else ""
    l = (last or "").strip() if isinstance(last, str) else ""
    if f or l:
        return f"{f} {l}".strip()
    return fallback or "Unknown"


def _normalize_strength_label(label: str | None) -> str:
    if not label:
        return "Unknown"
    l = str(label).strip().lower()
    mapping = {"even": "5v5", "ev": "5v5", "power play": "PP", "pp": "PP", "short handed": "PK", "sh": "PK"}
    if l in mapping:
        return mapping[l]
    l = l.replace("on", "v").replace("-", "").replace(" ", "")
    return l.upper()


# ======== Parsers ========
def _rows_from_statsapi(feed: dict) -> tuple[list[dict], str, str | None]:
    """
    StatsAPI format: /game/{pk}/feed/live
    Returns (rows, source_label, matchup)
    """
    rows: list[dict] = []
    game_pk = feed.get("gamePk")
    game_data = feed.get("gameData", {})
    live = (feed.get("liveData") or {}).get("plays") or {}
    plays = live.get("allPlays") or []

    # matchup like "CAR @ DET"
    teams = game_data.get("teams") or {}
    away = teams.get("away", {}).get("abbreviation") or teams.get("away", {}).get("triCode")
    home = teams.get("home", {}).get("abbreviation") or teams.get("home", {}).get("triCode")
    matchup = None
    if away and home:
        matchup = f"{away} @ {home}"

    for p in plays:
        event = (p.get("result") or {}).get("event") or ""
        if event not in STATS_SHOT_EVENTS:
            continue

        coords = p.get("coordinates") or {}
        x = coords.get("x")
        y = coords.get("y")
        if x is None or y is None:
            continue

        team_abbr = None
        t = p.get("team") or {}
        for k in ("triCode", "abbreviation", "name", "teamName"):
            if isinstance(t.get(k), str) and t[k].strip():
                team_abbr = t[k].strip()
                break

        players = p.get("players") or []
        shooter = None
        for pl in players:
            role = (pl.get("playerType") or pl.get("type") or "").lower()
            if role in {"shooter", "scorer"}:
                shooter = (_norm_name_value(pl.get("player")) or
                           _full_name(pl.get("player", {}).get("firstName"),
                                      pl.get("player", {}).get("lastName")))
                break
        if not shooter and players:
            first = players[0].get("player", {}).get("firstName")
            last = players[0].get("player", {}).get("lastName")
            shooter = _full_name(first, last, "Unknown")

        period = (p.get("about") or {}).get("period")
        period_time = (p.get("about") or {}).get("periodTime")
        strength = _normalize_strength_label((p.get("result") or {}).get("strength", {}).get("name"))
        is_goal = 1 if event == "Goal" else 0

        rows.append({
            "gamePk": int(game_pk) if game_pk else None,
            "period": period,
            "periodTime": period_time,
            "event": event,
            "team": team_abbr,
            "player": shooter or "Unknown",
            "x": float(x), "y": float(y),
            "strength": strength,
            "is_goal": is_goal,
            "matchup": matchup,
        })
    return rows, "StatsAPI", matchup


def _rows_from_gamecenter(feed: dict) -> tuple[list[dict], str, str | None]:
    """
    GameCenter format: /gamecenter/{pk}/play-by-play
    Returns (rows, source_label, matchup)
    """
    rows: list[dict] = []
    game_pk = feed.get("id") or feed.get("gamePk") or feed.get("gameId")
    # matchup from top-level if present
    away = (feed.get("awayTeam") or {}).get("abbrev") or (feed.get("awayTeam") or {}).get("triCode")
    home = (feed.get("homeTeam") or {}).get("abbrev") or (feed.get("homeTeam") or {}).get("triCode")
    matchup = f"{away} @ {home}" if away and home else None

    # roster mapping to resolve names if needed
    roster: dict[int, str] = {}
    for side in ("homeTeam", "awayTeam"):
        tm = feed.get(side) or {}
        for pl in (tm.get("roster") or []):
            pid = pl.get("id") or pl.get("playerId")
            name = (_norm_name_value(pl.get("name")) or
                    _full_name(pl.get("firstName"), pl.get("lastName"), "Unknown"))
            if isinstance(pid, int):
                roster[pid] = name

    # plays can be under "plays" or "playsByPeriod"
    plays = []
    if isinstance(feed.get("plays"), list):
        plays = feed["plays"]
    elif isinstance(feed.get("playsByPeriod"), list):
        for pr in feed["playsByPeriod"]:
            plays.extend(pr.get("plays", []))

    for p in plays:
        det = p.get("details") or p.get("eventDetails") or {}
        ekey = (det.get("typeDescKey") or det.get("eventTypeId") or det.get("typeCode") or "").lower()
        if ekey not in {"shot-on-goal", "missed-shot", "goal", "shot", "missed_shot"}:
            continue

        loc = p.get("coordinates") or p.get("details", {}).get("shotLocation") or {}
        x = loc.get("xCoord") if "xCoord" in loc else loc.get("x")
        y = loc.get("yCoord") if "yCoord" in loc else loc.get("y")
        if x is None or y is None:
            continue

        team_abbr = None
        tid = (p.get("team", {}) or {}).get("id") or det.get("eventOwnerTeamId")
        # attempt to resolve team abbrev from feed
        for side in ("homeTeam", "awayTeam"):
            tm = feed.get(side) or {}
            if tm.get("id") == tid:
                team_abbr = tm.get("abbrev") or tm.get("triCode")
                break

        shooter = (_norm_name_value(det.get("shootingPlayerName")) or
                   _norm_name_value(det.get("scoringPlayerName")))
        if not shooter:
            pid = det.get("shootingPlayerId") or det.get("scoringPlayerId") or det.get("playerId")
            if isinstance(pid, int):
                shooter = roster.get(pid, "Unknown")
        shooter = shooter or "Unknown"

        period = (p.get("periodDescriptor") or {}).get("number") or p.get("periodNumber")
        period_time = p.get("timeInPeriod") or p.get("timeRemaining") or (p.get("time") or None)

        # crude strength inference if provided
        strength = _normalize_strength_label(det.get("strength") or det.get("situationCode") or "EV")
        is_goal = 1 if ekey in {"goal"} else 0
        event = "Goal" if is_goal else ("Shot" if "shot" in ekey else "Shot")

        rows.append({
            "gamePk": int(game_pk) if game_pk else None,
            "period": period,
            "periodTime": period_time,
            "event": event,
            "team": team_abbr,
            "player": shooter,
            "x": float(x), "y": float(y),
            "strength": strength,
            "is_goal": is_goal,
            "matchup": matchup,
        })
    return rows, "GameCenter", matchup


def _shots_from_feed(feed: dict) -> tuple[pd.DataFrame, str, str | None]:
    # Detect which parser to use
    rows: list[dict]
    source: str
    matchup: str | None
    if "liveData" in feed or "gameData" in feed:
        rows, source, matchup = _rows_from_statsapi(feed)
    else:
        rows, source, matchup = _rows_from_gamecenter(feed)

    if not rows:
        return _empty_df(), "no shots", matchup
    df = pd.DataFrame(rows, columns=[
        "gamePk", "period", "periodTime", "event", "team",
        "player", "x", "y", "strength", "is_goal", "matchup"
    ])
    df.attrs["parser_source"] = source
    return df, source, matchup


def _empty_df() -> pd.DataFrame:
    df = pd.DataFrame(columns=REQUIRED_COLS)
    df["x"] = df["x"].astype("float64")
    df["y"] = df["y"].astype("float64")
    df["is_goal"] = df["is_goal"].astype("int64")
    return df


# ======== Exact-date schedule (robust / UTC-safe) ========
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_pks_for_date(d: _date) -> list[int]:
    """
    Return all gamePk values for calendar date d.
    IMPORTANT: Do NOT re-filter by gameDate (UTC drift can hide games).
    """
    wanted = d.isoformat()
    headers = {"User-Agent": "SparkerData-HockeyShotMap/1.0"}

    def _pks_from_stats_json(data: dict) -> list[int]:
        pks: list[int] = []
        for day in data.get("dates", []) or []:
            for g in day.get("games", []) or []:
                pk = g.get("gamePk")
                if pk:
                    pks.append(int(pk))
        return pks

    # Preferred: StatsAPI ?date=
    try:
        url = f"{STATS_BASE}/schedule?date={wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            pks = _pks_from_stats_json(r.json())
        if pks:
            return sorted(set(pks))
    except Exception:
        pass

    # Fallback: StatsAPI range (same day)
    try:
        url = f"{STATS_BASE}/schedule?startDate={wanted}&endDate={wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            pks = _pks_from_stats_json(r.json())
        if pks:
            return sorted(set(pks))
    except Exception:
        pass

    # GameCenter schedule fallback (already date-scoped)
    try:
        url = f"{SITE_BASE}/schedule/{wanted}"
        with httpx.Client(timeout=20.0, headers=headers, trust_env=True) as c:
            r = c.get(url); r.raise_for_status()
            sched = r.json()

        def _game_iter(s):
            if isinstance(s.get("gameWeek"), list):
                for wk in s["gameWeek"]:
                    for g in wk.get("games", []) or []:
                        yield g
            for g in s.get("games", []) or []:
                yield g

        pks: list[int] = []
        for g in _game_iter(sched):
            pk = g.get("id") or g.get("gamePk") or g.get("gameId")
            if pk:
                pks.append(int(pk))
        return sorted(set(pks))
    except Exception:
        return []


# ======== Fetch shots (day / range), de-dupe, label matchup ========
@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_for_date(d: _date) -> tuple[pd.DataFrame, str, int]:
    """
    Fetch all shots/goals for a single calendar date `d`.
    Returns (DataFrame, parser_label, games_count).
    """
    pks = fetch_game_pks_for_date(d)
    games_count = len(pks)
    if not pks:
        return _empty_df(), "no games", 0

    frames: list[pd.DataFrame] = []
    used_sources: set[str] = set()
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

            df_one, source, matchup = _shots_from_feed(feed)
            if matchup:
                matchup_map[int(pk)] = matchup
            if not df_one.empty:
                frames.append(df_one.assign(gamePk=int(pk)))
                used_sources.add(source)

    if not frames:
        return _empty_df(), "no shots", games_count

    out = pd.concat(frames, ignore_index=True)

    # Fill matchup if column is present but empty
    if "matchup" in out.columns:
        out["matchup"] = out["gamePk"].map(matchup_map).fillna(out["matchup"])
    else:
        out["matchup"] = out["gamePk"].map(matchup_map)

    # Ensure required columns exist
    for col in REQUIRED_COLS:
        if col not in out.columns:
            out[col] = pd.NA

    # Drop dups
    out = out.drop_duplicates(subset=["gamePk", "period", "periodTime", "player", "x", "y", "event"])

    parser_label = "/".join(sorted(s for s in used_sources if s))
    return out[REQUIRED_COLS], parser_label or "unknown", games_count


@st.cache_data(ttl=300, show_spinner=False)
def fetch_shots_between(start: _date, end: _date) -> tuple[pd.DataFrame, str, int]:
    """
    Inclusive date range fetch. Concatenates day results.
    """
    all_frames: list[pd.DataFrame] = []
    used_labels: set[str] = set()
    games_total = 0

    day = start
    while day <= end:
        df, label, games_count = fetch_shots_for_date(day)
        games_total += games_count
        if not df.empty:
            all_frames.append(df.assign(source_date=str(day)))
            used_labels.add(label)
        day += timedelta(days=1)

    if not all_frames:
        return _empty_df(), "no data", games_total

    out = pd.concat(all_frames, ignore_index=True)
    if "source_date" not in out.columns:
        out["source_date"] = pd.NA

    return out[REQUIRED_COLS + ["source_date"]], "/".join(sorted(used_labels)), games_total


# =========================
# UI
# =========================
st.set_page_config(page_title="NHL Shot Tracker", layout="wide")
st.title("🏒 NHL Shot Tracker")

left, right = st.columns([0.38, 0.62])

with left:
    mode = st.radio("Mode", ["Single day", "Date range"], horizontal=True)
    preset = st.session_state.get("preset")

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
        players = df[["player", "team"]].dropna().drop_duplicates()
        for _, row in players.iterrows():
            label_to_player[f"{row['team'] or 'UNK'} — {row['player']}"] = row["player"]
    selected_players = st.multiselect("Players", options=sorted(label_to_player.keys()))
    selected_player_values = [label_to_player[k] for k in selected_players] if selected_players else None

    goals_only = st.checkbox("Goals only", value=False)

    # Metrics
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rows", len(df))
    c2.metric("Games", games_count)
    c3.metric("Parser", parser_label or "—")
    uniq_players = df["player"].nunique() if not df.empty else 0
    uniq_teams = df["team"].nunique() if not df.empty else 0
    c4.metric("Players", uniq_players)
    c5.metric("Teams", uniq_teams)

    if st.session_state.get("parser_label"):
        st.caption(f"Parsed via: {st.session_state['parser_label']}")

# ---------- Plot ----------
with right:
    fig = base_rink()

    # --- Add NHL rink lines (center red + two blue) ---
    fig.add_shape(
        type="line",
        x0=0, x1=0, y0=-42.5, y1=42.5,
        line=dict(color="red", width=3),
        layer="below"
    )
    for x in (25, -25):
        fig.add_shape(
            type="line",
            x0=x, x1=x, y0=-42.5, y1=42.5,
            line=dict(color="blue", width=3),
            layer="below"
        )

    # --- Goal creases (semi-circles in light blue) ---
    crease_radius = 6
    crease_color = "rgba(173, 216, 230, 0.4)"  # light blue with transparency

    theta = np.linspace(-np.pi/2, np.pi/2, 50)

    # Left crease (goal near x = -89 ft)
    x_left = -89 + crease_radius * np.cos(theta)
    y_left = 0 + crease_radius * np.sin(theta)
    import plotly.graph_objects as go
    fig.add_trace(go.Scatter(
        x=x_left, y=y_left,
        fill="toself", mode="lines",
        line=dict(color="rgba(0,0,0,0)"),
        fillcolor=crease_color,
        showlegend=False,
        hoverinfo="skip",
        opacity=0.4,
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
        opacity=0.4,
    ))

    # Apply player / goals-only filters
    filtered = df.copy()
    if selected_player_values:
        filtered = filtered[filtered["player"].isin(selected_player_values)]
    if goals_only:
        filtered = filtered[filtered["is_goal"] == 1]

    # Scatter shots
    if not filtered.empty:
        # color goals differently by size/opacity to avoid adding a colorscale
        sizes = np.where(filtered["is_goal"].astype(int) == 1, 9, 7)
        opac = np.where(filtered["is_goal"].astype(int) == 1, 0.95, 0.7)
        fig.add_trace(go.Scatter(
            x=filtered["x"], y=filtered["y"], mode="markers",
            marker=dict(size=sizes),
            opacity=opac,
            text=filtered["player"],
            hovertemplate="Player: %{text}<br>x: %{x}, y: %{y}<extra></extra>",
            showlegend=False,
        ))

    st.plotly_chart(fig, use_container_width=True, height=520)

# ---------- Table ----------
with left:
    filtered = df.copy()
    if selected_player_values:
        filtered = filtered[filtered["player"].isin(selected_player_values)]
    if goals_only:
        filtered = filtered[filtered["is_goal"] == 1]

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
