import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="Team Overview", page_icon="🏒", layout="wide")
st.title("Team Overview")

# ---- Pull the already-fetched data from session ----
def _find_df() -> pd.DataFrame | None:
    keys = [
        "shots_df", "df_shots", "df", "shots", "data_shots", "shots_dataframe"
    ]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

df = _find_df()
if df is None or df.empty:
    st.warning(
        "No data found in session. Open the **Home** page, fetch data (pick your dates), "
        "and then return here. These pages reuse the exact data the main page loads."
    )
    st.stop()

# ---- Sidebar filters use the existing date range if available, otherwise default UI dates ----
with st.sidebar:
    st.subheader("Filters")
    # The main page may have stored these; if not, use last 7 days visually (does not re-fetch)
    start = st.session_state.get("selected_start_date", date.today() - timedelta(days=7))
    end   = st.session_state.get("selected_end_date", date.today())
    st.date_input("Start date (display only)", start, disabled=True)
    st.date_input("End date (display only)", end, disabled=True)

    teams = sorted([t for t in df["team"].dropna().unique().tolist() if t])
    team = st.selectbox("Team", ["— select —"] + teams)

if team == "— select —" or not team:
    st.info("Select a team to view overview.")
    st.stop()

team_df = df[df["team"] == team]

# KPI row
c1,c2,c3,c4 = st.columns(4)
shots = int(team_df["isSOG"].sum()) if "isSOG" in team_df else 0
goals = int(team_df["isGoal"].sum()) if "isGoal" in team_df else 0
sh_pct = (goals / shots) if shots else 0.0
xg = float(team_df["xG"].sum()) if "xG" in team_df else 0.0
c1.metric("Shots (SOG)", f"{shots:,}")
c2.metric("Goals", f"{goals:,}")
c3.metric("Shooting %", f"{sh_pct:.1%}")
c4.metric("xG (sum)", f"{xg:.2f}")
st.markdown("---")

# Strength table
st.subheader("By Strength")
strength_cols = {"isSOG":"sum", "isGoal":"sum"}
if "xG" in team_df: strength_cols["xG"] = "sum"
strength_tbl = (
    team_df.groupby("strength", dropna=False)
    .agg(**{ "Shots":("isSOG","sum"), "Goals":("isGoal","sum"), **({"xG":("xG","sum")} if "xG" in team_df else {}) })
    .reset_index()
)
st.dataframe(strength_tbl, use_container_width=True)

# Heatmap
st.subheader("Shot Density Heatmap (All shots, normalized attacking)")
if "x" in team_df and "y" in team_df:
    heat_df = team_df[team_df.get("isSOG", True)].copy()
    if len(heat_df) >= 5:
        fig = px.density_heatmap(heat_df, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Shot Density")
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough shots to render heatmap.")
else:
    st.info("This dataset doesn't include shot coordinates (x,y).")

# Danger breakdown
if "danger" in team_df:
    st.subheader("Danger Breakdown")
    dang = (
        team_df[team_df.get("isSOG", True)]
        .groupby("danger", dropna=False)
        .agg(Shots=("isSOG","sum") if "isSOG" in team_df else ("team","count"),
             Goals=("isGoal","sum") if "isGoal" in team_df else ("team","count"),
             **({"xG":("xG","sum")} if "xG" in team_df else {}))
        .reset_index()
        .sort_values("danger", na_position="last")
    )
    bar = px.bar(dang, x="danger", y=[c for c in ["Shots","Goals"] if c in dang] + (["xG"] if "xG" in dang else []),
                 barmode="group", title="Shots/Goals by Danger")
    st.plotly_chart(bar, use_container_width=True)

# Trend
if "date" in team_df:
    st.subheader("Trend")
    trend = (
        team_df[team_df.get("isSOG", True)]
        .groupby("date", dropna=False)
        .agg(Shots=("isSOG","sum") if "isSOG" in team_df else ("team","count"),
             Goals=("isGoal","sum") if "isGoal" in team_df else ("team","count"),
             **({"xG":("xG","sum")} if "xG" in team_df else {}))
        .reset_index().sort_values("date")
    )
    y_cols = [c for c in ["Shots","Goals","xG"] if c in trend]
    if y_cols:
        line = px.line(trend, x="date", y=y_cols, title="Game-by-Game")
        st.plotly_chart(line, use_container_width=True)
