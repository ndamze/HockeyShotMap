import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta

st.set_page_config(page_title="Team Overview", page_icon="🏒", layout="wide")
st.title("Team Overview")

# ---- pull the already-fetched data from session ----
def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","data_df","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

def _harmonize(df: pd.DataFrame) -> pd.DataFrame:
    """Make sure the columns our charts expect exist, mapping from your main page schema."""
    out = df.copy()

    # strength
    if "strength" not in out.columns:
        out["strength"] = "EV"

    # isSOG (shot on goal or goal) from 'event'
    if "isSOG" not in out.columns:
        if "event" in out.columns:
            out["isSOG"] = out["event"].isin(["Shot","Goal"])
        else:
            out["isSOG"] = False

    # isGoal from 'is_goal' or 'event'
    if "isGoal" not in out.columns:
        if "is_goal" in out.columns:
            out["isGoal"] = out["is_goal"].astype(bool)
        elif "event" in out.columns:
            out["isGoal"] = out["event"] == "Goal"
        else:
            out["isGoal"] = False

    # date from 'source_date' or 'gameDate'
    if "date" not in out.columns:
        if "source_date" in out.columns:
            out["date"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date
        elif "gameDate" in out.columns:
            out["date"] = pd.to_datetime(out["gameDate"], errors="coerce").dt.date

    # ensure boolean dtype for sums
    for col in ("isSOG","isGoal"):
        if col in out.columns:
            out[col] = out[col].astype(bool)

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

# ---- By Strength table (only aggregate columns that exist) ----
st.subheader("By Strength")
agg_spec = {}
if "isSOG" in team_df: agg_spec["Shots"] = ("isSOG","sum")
else:                   agg_spec["Shots"] = ("team","size")
if "isGoal" in team_df: agg_spec["Goals"] = ("isGoal","sum")
if "xG" in team_df:     agg_spec["xG"]    = ("xG","sum")

strength_tbl = (
    team_df.groupby("strength", dropna=False)
    .agg(**agg_spec)
    .reset_index()
    .sort_values("strength", na_position="last")
)
st.dataframe(strength_tbl, use_container_width=True)

# ---- Shot Density heatmap ----
st.subheader("Shot Density Heatmap (All shots, normalized attacking)")
if {"x","y"}.issubset(team_df.columns):
    heat_df = team_df[team_df.get("isSOG", True)].copy()
    if len(heat_df) >= 5:
        fig = px.density_heatmap(heat_df, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Shot Density")
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough shots to render heatmap.")
else:
    st.info("This dataset doesn't include shot coordinates (x,y).")

# ---- Danger breakdown (only if 'danger' exists) ----
if "danger" in team_df.columns:
    st.subheader("Danger Breakdown")
    dang_agg = {}
    if "isSOG" in team_df: dang_agg["Shots"] = ("isSOG","sum")
    if "isGoal" in team_df: dang_agg["Goals"] = ("isGoal","sum")
    if "xG" in team_df:     dang_agg["xG"]    = ("xG","sum")

    if dang_agg:
        dang = (
            team_df[team_df.get("isSOG", True)]
            .groupby("danger", dropna=False)
            .agg(**dang_agg)
            .reset_index()
            .sort_values("danger", na_position="last")
        )
        ycols = [c for c in ["Shots","Goals","xG"] if c in dang]
        if ycols:
            bar = px.bar(dang, x="danger", y=ycols, barmode="group", title="Shots & Goals by Danger")
            st.plotly_chart(bar, use_container_width=True)

# ---- Trend (only if 'date' exists) ----
if "date" in team_df.columns:
    st.subheader("Trend")
    trend_agg = {}
    if "isSOG" in team_df: trend_agg["Shots"] = ("isSOG","sum")
    else:                  trend_agg["Shots"] = ("team","size")
    if "isGoal" in team_df: trend_agg["Goals"] = ("isGoal","sum")
    if "xG" in team_df:     trend_agg["xG"]    = ("xG","sum")

    trend = (
        team_df[team_df.get("isSOG", True)]
        .groupby("date", dropna=False)
        .agg(**trend_agg)
        .reset_index()
        .sort_values("date")
    )
    y_cols = [c for c in ["Shots","Goals","xG"] if c in trend]
    if y_cols:
        line = px.line(trend, x="date", y=y_cols, title="Game-by-Game")
        st.plotly_chart(line, use_container_width=True)
