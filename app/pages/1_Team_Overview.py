import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta

# --- Rink plot import with dual-path fallback (same as Home) ---
try:
    from app.components.rink_plot import base_rink  # when running from repo root
except ModuleNotFoundError:
    try:
        from components.rink_plot import base_rink  # when running inside app/
    except ModuleNotFoundError:
        st.error("Could not import rink_plot component. Please ensure the components directory exists.")
        st.stop()

st.set_page_config(page_title="Team Overview", page_icon="🏒", layout="wide")
st.title("Team Overview")

# ---- pull the already-fetched data from session ----
def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","data_df","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

def _norm_strength(s) -> str:
    """
    Normalize any incoming strength label to: 5v5, PP, PK, 4v4, 3v3, 6v5, etc.
    Unknown/missing values are treated as 5v5 (so you never see UNKNOWN).
    """
    import re
    if s is None:
        return "5v5"
    t = str(s).strip()
    if t == "":
        return "5v5"

    l = t.lower()
    # squash variants: "4-on-4", "4 on 4", "4v4", "4 vs 4"
    l = l.replace("on", "v").replace("-", "").replace(" ", "")
    l = l.replace("vs", "v")

    # Unknown-ish -> 5v5
    if l in {"unknown", "unk", "n/a", "na", "null", "none"}:
        return "5v5"

    # Common words
    if l in {"ev", "even", "evenstrength"}:
        return "5v5"
    if l in {"pp", "ppg", "powerplay", "powerplayadvantage"}:
        return "PP"
    if l in {"pk", "sh", "shg", "shorthanded", "penaltykill"}:
        return "PK"

    # Numeric patterns
    m = re.match(r"^(\d+)v(\d+)$", l)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        # classify numeric advantages commonly seen as PP/PK
        if a == b:
            return f"{a}v{b}"  # keep 4v4, 3v3 as-is
        if a > b:
            return "PP"
        if a < b:
            return "PK"

    # Specific numeric advantages seen in feeds
    if l in {"5v4", "5v3", "4v3", "6v5", "6v4"}:
        return "PP"
    if l in {"4v5", "3v5", "3v4", "5v6", "4v6"}:
        return "PK"

    # Last resort: uppercase known-looking tokens; otherwise default 5v5
    return "5v5" if l in {"", "ev"} else l.upper()

def _harmonize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # isSOG / isGoal from your schema
    if "isSOG" not in out.columns:
        out["isSOG"] = out.get("event", "").isin(["Shot","Goal"])
    if "isGoal" not in out.columns:
        if "is_goal" in out.columns:
            out["isGoal"] = out["is_goal"].astype(bool)
        else:
            out["isGoal"] = out.get("event", "") == "Goal"

    # date (if you fetched a range, you already store source_date)
    if "date" not in out.columns:
        if "source_date" in out.columns:
            out["date"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date
        elif "gameDate" in out.columns:
            out["date"] = pd.to_datetime(out["gameDate"], errors="coerce").dt.date

    # strength normalized
    if "strength" in out.columns:
        out["strength"] = out["strength"].map(_norm_strength)
    else:
        out["strength"] = "5v5"

    # Ensure boolean dtype
    out["isSOG"] = out["isSOG"].astype(bool)
    out["isGoal"] = out["isGoal"].astype(bool)

    return out

df_raw = _find_df()
if df_raw is None or df_raw.empty:
    st.warning(
        "No data found in session. Open the **Home** page, fetch data (pick your dates), "
        "and then return here. These pages reuse the exact data the main page loads."
    )
    st.stop()

df = _harmonize(df_raw)

# ---- sidebar (display the same date range loaded on Home) ----
with st.sidebar:
    st.subheader("Filters")
    start = st.session_state.get("selected_start_date", date.today() - timedelta(days=7))
    end   = st.session_state.get("selected_end_date", date.today())
    st.date_input("Start date (display only)", start, disabled=True)
    st.date_input("End date (display only)", end, disabled=True)

    teams = sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else []
    team = st.selectbox("Team", ["— select —"] + teams)

if team == "— select —" or not team:
    st.info("Select a team to view overview.")
    st.stop()

team_df = df[df["team"] == team].copy()

# ---- KPIs ----
c1,c2,c3,c4 = st.columns(4)
shots = int(team_df["isSOG"].sum()) if "isSOG" in team_df else int(len(team_df))
goals = int(team_df["isGoal"].sum()) if "isGoal" in team_df else int((team_df.get("event","") == "Goal").sum())
sh_pct = (goals / shots) if shots else 0.0
xg = float(team_df["xG"].sum()) if "xG" in team_df else 0.0
c1.metric("Shots (SOG)", f"{shots:,}")
c2.metric("Goals", f"{goals:,}")
c3.metric("Shooting %", f"{sh_pct:.1%}")
c4.metric("xG (sum)", f"{xg:.2f}")

st.markdown("---")

# ---- By Strength table ----
st.subheader("By Strength")
agg_spec = {"Shots":("isSOG","sum"), "Goals":("isGoal","sum")}
if "xG" in team_df: agg_spec["xG"] = ("xG","sum")
strength_tbl = (
    team_df.groupby("strength", dropna=False)
    .agg(**agg_spec)
    .reset_index()
    .sort_values("strength", na_position="last")
)
st.dataframe(strength_tbl, use_container_width=True)

# ---- Shot Density heatmap on the rink ----
st.subheader("Shot Density Heatmap (All shots, normalized attacking)")
if {"x","y"}.issubset(team_df.columns):
    heat_df = team_df[team_df["isSOG"]].copy()
    if len(heat_df) >= 5:
        fig = base_rink()
        fig.add_trace(go.Histogram2d(
            x=heat_df["x"], y=heat_df["y"],
            nbinsx=40, nbinsy=20, opacity=0.85, showscale=True
        ))
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough shots to render heatmap.")
else:
    st.info("This dataset doesn't include shot coordinates (x,y).")

# ---- Danger breakdown (Low → Medium → High) ----
if "danger" in team_df.columns:
    st.subheader("Danger Breakdown")
    # normalize to lower-case labels, then order
    dtmp = team_df.copy()
    dtmp["danger"] = dtmp["danger"].astype(str).str.lower()
    cats = pd.api.types.CategoricalDtype(["low","medium","high"], ordered=True)
    dtmp["danger"] = dtmp["danger"].astype(cats)

    dang = (
        dtmp[dtmp["isSOG"]]
        .groupby("danger", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index()
        .sort_values("danger")
    )
    dang["danger"] = dang["danger"].astype(str).str.capitalize()
    fig_d = go.Figure()
    fig_d.add_bar(x=dang["danger"], y=dang["Shots"], name="Shots")
    fig_d.add_bar(x=dang["danger"], y=dang["Goals"], name="Goals")
    fig_d.update_layout(barmode="group", title="Shots & Goals by Danger")
    st.plotly_chart(fig_d, use_container_width=True)

# ---- Trend ----
if "date" in team_df.columns:
    st.subheader("Trend")
    trend = (
        team_df[team_df["isSOG"]]
        .groupby("date", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index()
        .sort_values("date")
    )
    fig_t = go.Figure()
    fig_t.add_scatter(x=trend["date"], y=trend["Shots"], name="Shots", mode="lines")
    fig_t.add_scatter(x=trend["date"], y=trend["Goals"], name="Goals", mode="lines")
    st.plotly_chart(fig_t, use_container_width=True)
