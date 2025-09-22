import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

try:
    from _shared_repo import fetch_shots_dataframe
except Exception as e:
    st.error("Could not import repo data adapter (_shared_repo.py).")
    st.exception(e); st.stop()

def list_teams(df): 
    return sorted([t for t in df["team"].dropna().unique().tolist() if t])

st.set_page_config(page_title="Team Overview", page_icon="🏒", layout="wide")
st.title("Team Overview")

with st.sidebar:
    st.subheader("Filters")
    today = date.today()
    start = st.date_input("Start date", today - timedelta(days=7))
    end = st.date_input("End date", today)
    if start > end: start, end = end, start
    df = fetch_shots_dataframe(start, end)
    teams = ["— select —"] + list_teams(df)
    team = st.selectbox("Team", teams)

if team == "— select —" or not team:
    st.info("Select a team to view overview."); st.stop()

team_df = df[df["team"] == team]

# KPIs
c1,c2,c3,c4 = st.columns(4)
shots = int(team_df["isSOG"].sum())
goals = int(team_df["isGoal"].sum())
sh_pct = (goals / shots) if shots else 0.0
xg = float(team_df["xG"].sum()) if "xG" in team_df else 0.0
c1.metric("Shots (SOG)", f"{shots:,}")
c2.metric("Goals", f"{goals:,}")
c3.metric("Shooting %", f"{sh_pct:.1%}")
c4.metric("xG (sum)", f"{xg:.2f}")
st.markdown("---")

# Strength table
st.subheader("By Strength")
strength_tbl = (
    team_df.groupby("strength")
    .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index()
)
st.dataframe(strength_tbl, use_container_width=True)

# Heatmap
st.subheader("Shot Density Heatmap (All shots, normalized attacking)")
heat_df = team_df[team_df["isSOG"]].copy()
if len(heat_df) >= 5:
    fig = px.density_heatmap(heat_df, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Shot Density")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough shots to render heatmap.")

# Danger breakdown
st.subheader("Danger Breakdown")
dang = (
    team_df[team_df["isSOG"]]
    .groupby("danger").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index().sort_values("danger")
)
bar = px.bar(dang, x="danger", y=["Shots","Goals"], barmode="group", title="Shots & Goals by Danger")
st.plotly_chart(bar, use_container_width=True)

# Trend
st.subheader("Trend")
trend = (
    team_df[team_df["isSOG"]]
    .groupby("date").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index().sort_values("date")
)
line = px.line(trend, x="date", y=["Shots","Goals","xG"], title="Game-by-Game")
st.plotly_chart(line, use_container_width=True)
