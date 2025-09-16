import streamlit as st
import pandas as pd
import subprocess, sys
from pathlib import Path
from app.components.rink_plot import base_rink, add_shots

st.set_page_config(page_title="Hockey Shot Maps", layout="wide")
st.title("Hockey Shot Heatmaps")

# Sidebar and data controls
left, right = st.columns([1, 3])
with left:
    st.subheader("Data source")
    live = st.toggle("Live mode (today's games)", value=False)

    if live and st.button("Fetch live shots now"):
        # Run the ingest script to grab today's data
        result = subprocess.run(
            [sys.executable, "scripts/ingest_live.py"],
            capture_output=True,
            text=True,
        )
        st.code(result.stdout or "(no stdout)")
        if result.stderr:
            st.error(result.stderr)

    # Pick which CSV to read based on mode
    data_path = Path(
        "data/curated/shots_latest.csv" if live else "data/curated/demo_shots.csv"
    )

# Load data
if data_path.exists():
    df = pd.read_csv(data_path)
else:
    df = pd.DataFrame(columns=["x", "y", "player", "strength", "is_goal"])

# Show parser source if available
parser_source = getattr(df, "attrs", {}).get("parser_source")
if parser_source:
    st.sidebar.info(f"Data parsed using **{parser_source}**")

# Sidebar filters
with left:
    player = st.selectbox(
        "Player", options=["All"] + sorted(df["player"].dropna().unique().tolist())
    )
    strength = st.selectbox(
        "Game state", options=["All"] + sorted(df["strength"].dropna().unique().tolist())
    )
    view_goals = st.checkbox("Show only goals", value=False)

# Apply filters
mask = pd.Series(True, index=df.index)
if player != "All":
    mask &= df["player"] == player
if strength != "All":
    mask &= df["strength"] == strength
if view_goals:
    mask &= df["is_goal"] == 1

filtered = df[mask].copy()

# Plot
with right:
    fig = base_rink()
    fig = add_shots(fig, filtered)
    st.plotly_chart(fig, use_container_width=True)

st.caption(f"Rows: {len(filtered)} (source: {'live' if live else 'demo'})")
