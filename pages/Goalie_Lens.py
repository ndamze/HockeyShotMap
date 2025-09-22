
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import date, timedelta

try:
    from _shared import (
        fetch_shots_dataframe, list_goalies, list_teams, summarize_goalie
    )
except Exception:
    st.error("Missing _shared utilities. Make sure _shared.py is present alongside this page.")
    st.stop()

st.set_page_config(page_title="Goalie Lens", page_icon="🥅", layout="wide")
st.title("Goalie Lens")

with st.sidebar:
    st.subheader("Filters")
    today = date.today()
    start = st.date_input("Start date", today - timedelta(days=30))
    end = st.date_input("End date", today)
    if start > end:
        start, end = end, start
    df = fetch_shots_dataframe(start, end)
    team_filter = st.selectbox("Team (optional)", ["All"] + list_teams(df))
    goalies = list_goalies(df, None if team_filter=="All" else team_filter)
    if not goalies:
        st.info("No goalies found for the filters.")
        st.stop()
    names = [g[1] for g in goalies]
    sel_name = st.selectbox("Goalie", names)
    gid = dict(goalies)[sel_name]

subset = df[df["goalieId"]==gid].copy()
subset_sog = subset[subset["isSOG"]]

# KPIs
k1,k2,k3,k4 = st.columns(4)
shots_faced = int(len(subset_sog))
goals_against = int(subset_sog["isGoal"].sum())
saves = shots_faced - goals_against
sv_pct = saves / max(1, shots_faced)
xga = float(subset_sog["xG"].sum())

k1.metric("Shots Faced", f"{shots_faced:,}")
k2.metric("Goals Against", f"{goals_against:,}")
k3.metric("SV%", f"{sv_pct:.1%}")
k4.metric("xGA (sum xG faced)", f"{xga:.2f}")

st.markdown("---")

# Location maps
st.subheader(f"Shot Map Faced – {sel_name}")
if shots_faced >= 1:
    scat = px.scatter(subset_sog, x="x", y="y",
                      color=subset_sog["isGoal"].map({True:"Goal Against", False:"Save"}),
                      symbol=subset_sog["shotType"],
                      hover_data=["team","shooterName","date","period","periodTime","distance","angle"],
                      title="Shots Faced (normalized toward +x)")
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    scat.update_layout(height=550, legend_title_text="Outcome")
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No shots faced to show.")

# Distance buckets
st.subheader("Performance by Distance")
bins = [0,10,20,30,40,60,1000]
labels = ["0–10","10–20","20–30","30–40","40–60","60+"]
tmp = subset_sog.copy()
tmp["distBucket"] = pd.cut(tmp["distance"], bins=bins, labels=labels, include_lowest=True)
bucket = tmp.groupby("distBucket").agg(
    Faced=("isSOG","count"),
    GA=("isGoal","sum"),
    Saves=("isSOG", lambda s: int(s.count()) - int(tmp.loc[s.index, "isGoal"].sum()))
).reset_index()
bucket["SV%"] = (bucket["Saves"] / bucket["Faced"]).replace([np.inf,-np.inf], np.nan)
st.dataframe(bucket, use_container_width=True)

bar = px.bar(bucket, x="distBucket", y=["Faced","GA","Saves"], barmode="group", title="By Distance")
st.plotly_chart(bar, use_container_width=True)

# Heatmap of goals against
st.subheader("Goals Against Heatmap")
ga = subset_sog[subset_sog["isGoal"]]
if len(ga) >= 1:
    hm = px.density_heatmap(ga, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Goals Against Density")
    hm.update_yaxes(scaleanchor="x", scaleratio=1)
    hm.update_layout(height=500)
    st.plotly_chart(hm, use_container_width=True)
else:
    st.info("No goals against in range.")
