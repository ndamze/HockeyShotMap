
import sys, os, importlib, traceback
import streamlit as st

# Ensure this pages directory is importable (so `_shared.py` in the same folder can be imported)
_PAGES_DIR = os.path.dirname(__file__)
if _PAGES_DIR not in sys.path:
    sys.path.append(_PAGES_DIR)

try:
    _shared = importlib.import_module("_shared")
    fetch_shots_dataframe = _shared.fetch_shots_dataframe
    # Optional helpers (some pages won't use all of these)
    list_teams       = getattr(_shared, "list_teams", None)
    list_players     = getattr(_shared, "list_players", None)
    list_goalies     = getattr(_shared, "list_goalies", None)
    summarize_team   = getattr(_shared, "summarize_team", None)
    summarize_player = getattr(_shared, "summarize_player", None)
    summarize_goalie = getattr(_shared, "summarize_goalie", None)
except Exception as e:
    st.error("Failed to import `_shared.py`. See details below:")
    st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    st.stop()

import pandas as pd
import numpy as np
import plotly.express as px
from datetime import date, timedelta

st.set_page_config(page_title="Team Overview", page_icon="🏒", layout="wide")
st.title("Team Overview")

with st.sidebar:
    st.subheader("Filters")
    today = date.today()
    start = st.date_input("Start date", today - timedelta(days=7))
    end = st.date_input("End date", today)
    if start > end:
        st.warning("Start date is after end date. Adjusting.")
        start, end = end, start
    df = fetch_shots_dataframe(start, end)
    if list_teams is None:
        st.error("`list_teams` missing in _shared.py")
        st.stop()
    teams = ["— select —"] + list_teams(df)
    team = st.selectbox("Team", teams)

if team == "— select —" or not team:
    st.info("Select a team to view overview.")
    st.stop()

team_df = df[df["team"]==team]

# KPI row
col1, col2, col3, col4 = st.columns(4)
shots = int(team_df["isSOG"].sum())
goals = int(team_df["isGoal"].sum())
sh_pct = (goals / shots) if shots else 0.0
xg = float(team_df["xG"].sum()) if "xG" in team_df else 0.0

col1.metric("Shots (SOG)", f"{shots:,}")
col2.metric("Goals", f"{goals:,}")
col3.metric("Shooting %", f"{sh_pct:.1%}")
col4.metric("xG (sum)", f"{xg:.2f}")

st.markdown("---")

# Strength breakdown table
st.subheader("By Strength")
if summarize_team is None:
    st.warning("`summarize_team` not found in _shared.py; showing raw table.")
    strength_tbl = (team_df.groupby("strength")
                    .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
                    .reset_index())
else:
    strength_tbl = summarize_team(df, team)
st.dataframe(strength_tbl, use_container_width=True)

# Heatmap (shot density)
st.subheader("Shot Density Heatmap (All shots, normalized attacking)")
heat_df = team_df[team_df["isSOG"]].copy()
if len(heat_df) >= 5:
    fig = px.density_heatmap(
        heat_df, x="x", y="y", nbinsx=40, nbinsy=20,
        histfunc="count", title="Shot Density",
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    fig.update_layout(height=500)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough shots to render heatmap.")

# Danger breakdown
st.subheader("Danger Breakdown")
dang = (team_df[team_df["isSOG"]]
        .groupby("danger").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
        .reset_index()
        .sort_values(["danger"]))
bar = px.bar(dang, x="danger", y=["Shots","Goals"], barmode="group", title="Shots & Goals by Danger")
st.plotly_chart(bar, use_container_width=True)

# Trend by game/date
st.subheader("Trend")
trend = (team_df[team_df["isSOG"]]
         .groupby("date").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
         .reset_index()
         .sort_values("date"))
line = px.line(trend, x="date", y=["Shots","Goals","xG"], title="Game-by-Game")
st.plotly_chart(line, use_container_width=True)
