import sys, os, traceback
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta

# --- import repo loader via adapter ---
try:
    from _shared_repo import fetch_shots_dataframe
    def list_players(df, team=None):
        s = df[df["team"] == team] if team else df
        s = s.dropna(subset=["shooterId","shooterName"])
        return sorted({(int(r["shooterId"]), str(r["shooterName"])) for _, r in s.iterrows()}, key=lambda x:x[1])
    def list_teams(df): 
        return sorted([t for t in df["team"].dropna().unique().tolist() if t])
except Exception as e:
    st.error("Could not import the repo data adapter (_shared_repo.py).")
    st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))
    st.stop()

st.set_page_config(page_title="Player Pages", page_icon="🧊", layout="wide")
st.title("Player Pages")

with st.sidebar:
    st.subheader("Filters")
    today = date.today()
    start = st.date_input("Start date", today - timedelta(days=30))
    end = st.date_input("End date", today)
    if start > end:
        start, end = end, start
    df = fetch_shots_dataframe(start, end)
    team = st.selectbox("Team (optional)", ["All"] + list_teams(df))
    players = list_players(df, None if team=="All" else team)
    if not players:
        st.info("No players found for the filters.")
        st.stop()
    names = [p[1] for p in players]
    sel_name = st.selectbox("Player", names)
    pid = dict(players)[sel_name]

subset = df[df["shooterId"] == pid]

# KPIs
k1,k2,k3,k4 = st.columns(4)
shots = int(subset["isSOG"].sum())
goals = int(subset["isGoal"].sum())
sh_pct = goals/max(1,shots)
xg = float(subset["xG"].sum()) if "xG" in subset else 0.0

k1.metric("Shots", f"{shots:,}")
k2.metric("Goals", f"{goals:,}")
k3.metric("Sh%", f"{sh_pct:.1%}")
k4.metric("xG (sum)", f"{xg:.2f}")

st.markdown("---")

# Shot map
st.subheader(f"Shot Map – {sel_name}")
if shots >= 1:
    scat = px.scatter(subset, x="x", y="y",
                      color=subset["isGoal"].map({True:"Goal", False:"Shot"}),
                      symbol=subset["shotType"],
                      hover_data=["team","date","period","periodTime","shotType","distance","angle"],
                      title="Shots (normalized toward +x)")
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No shots to show.")

# Shot type breakdown
st.subheader("Shot Type Breakdown")
shot_types = (
    subset[subset["isSOG"]]
    .groupby("shotType").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index()
    .sort_values("Shots", ascending=False)
)
st.dataframe(shot_types, use_container_width=True)
bar = px.bar(shot_types, x="shotType", y=["Shots","Goals"], barmode="group", title="By Shot Type")
st.plotly_chart(bar, use_container_width=True)

# Danger breakdown
st.subheader("Danger Zones")
dang = (
    subset[subset["isSOG"]]
    .groupby("danger").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index()
    .sort_values("danger")
)
dzbar = px.bar(dang, x="danger", y=["Shots","Goals"], barmode="group", title="By Danger")
st.plotly_chart(dzbar, use_container_width=True)

# Trend
st.subheader("Trend")
trend = (
    subset[subset["isSOG"]]
    .groupby("date").agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"), xG=("xG","sum"))
    .reset_index().sort_values("date")
)
line = px.line(trend, x="date", y=["Shots","Goals","xG"], title="Game-by-Game")
st.plotly_chart(line, use_container_width=True)
