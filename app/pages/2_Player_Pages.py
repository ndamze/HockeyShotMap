import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="Player Pages", page_icon="🧊", layout="wide")
st.title("Player Pages")

def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

df = _find_df()
if df is None or df.empty:
    st.warning("No data found in session. Go to **Home**, fetch data, then return here.")
    st.stop()

with st.sidebar:
    st.subheader("Filters")
    start = st.session_state.get("selected_start_date", date.today() - timedelta(days=30))
    end   = st.session_state.get("selected_end_date", date.today())
    st.date_input("Start date (display only)", start, disabled=True)
    st.date_input("End date (display only)", end, disabled=True)

    teams = ["All"] + sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else ["All"]
    team = st.selectbox("Team (optional)", teams)

    s = df[df["team"] == team] if team != "All" and "team" in df else df
    if "shooterId" not in s or "shooterName" not in s:
        st.warning("This dataset doesn’t include shooter identifiers.")
        st.stop()

    pairs = s.dropna(subset=["shooterId","shooterName"]).apply(
        lambda r: (int(r["shooterId"]), str(r["shooterName"])), axis=1
    ).drop_duplicates().tolist()
    if not pairs:
        st.info("No players found for the filters."); st.stop()

    names = sorted({name for _, name in pairs})
    sel_name = st.selectbox("Player", names)
    pid = next(pid for pid, name in pairs if name == sel_name)

subset = df[df["shooterId"] == pid] if "shooterId" in df else pd.DataFrame()

# KPIs
c1,c2,c3,c4 = st.columns(4)
shots = int(subset["isSOG"].sum()) if "isSOG" in subset else 0
goals = int(subset["isGoal"].sum()) if "isGoal" in subset else 0
sh_pct = goals / max(1, shots)
xg = float(subset["xG"].sum()) if "xG" in subset else 0.0
c1.metric("Shots", f"{shots:,}")
c2.metric("Goals", f"{goals:,}")
c3.metric("Sh%", f"{sh_pct:.1%}")
c4.metric("xG (sum)", f"{xg:.2f}")
st.markdown("---")

# Shot map
st.subheader(f"Shot Map – {sel_name}")
if shots >= 1 and "x" in subset and "y" in subset:
    scat = px.scatter(subset, x="x", y="y",
                      color=subset["isGoal"].map({True:"Goal", False:"Shot"}) if "isGoal" in subset else None,
                      symbol=subset["shotType"] if "shotType" in subset else None,
                      hover_data=[c for c in ["team","date","period","periodTime","shotType","distance","angle"]
                                  if c in subset.columns],
                      title="Shots (normalized toward +x)")
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No shots (or no coordinates) to show.")

# Shot types
if "shotType" in subset:
    st.subheader("Shot Type Breakdown")
    shot_types = (
        subset[subset.get("isSOG", True)]
        .groupby("shotType", dropna=False)
        .agg(Shots=("isSOG","sum") if "isSOG" in subset else ("shotType","count"),
             Goals=("isGoal","sum") if "isGoal" in subset else ("shotType","count"),
             **({"xG":("xG","sum")} if "xG" in subset else {}))
        .reset_index().sort_values("Shots" if "isSOG" in subset else "shotType", ascending=False)
    )
    st.dataframe(shot_types, use_container_width=True)
    ycols = [c for c in ["Shots","Goals"] if c in shot_types] + (["xG"] if "xG" in shot_types else [])
    if ycols:
        bar = px.bar(shot_types, x="shotType", y=ycols, barmode="group", title="By Shot Type")
        st.plotly_chart(bar, use_container_width=True)

# Danger
if "danger" in subset:
    st.subheader("Danger Zones")
    dang = (
        subset[subset.get("isSOG", True)]
        .groupby("danger", dropna=False)
        .agg(Shots=("isSOG","sum") if "isSOG" in subset else ("danger","count"),
             Goals=("isGoal","sum") if "isGoal" in subset else ("danger","count"),
             **({"xG":("xG","sum")} if "xG" in subset else {}))
        .reset_index().sort_values("danger", na_position="last")
    )
    ycols = [c for c in ["Shots","Goals"] if c in dang] + (["xG"] if "xG" in dang else [])
    if ycols:
        dzbar = px.bar(dang, x="danger", y=ycols, barmode="group", title="By Danger")
        st.plotly_chart(dzbar, use_container_width=True)

# Trend
if "date" in subset:
    st.subheader("Trend")
    trend = (
        subset[subset.get("isSOG", True)]
        .groupby("date", dropna=False)
        .agg(Shots=("isSOG","sum") if "isSOG" in subset else ("date","count"),
             Goals=("isGoal","sum") if "isGoal" in subset else ("date","count"),
             **({"xG":("xG","sum")} if "xG" in subset else {}))
        .reset_index().sort_values("date")
    )
    ycols = [c for c in ["Shots","Goals","xG"] if c in trend]
    if ycols:
        line = px.line(trend, x="date", y=ycols, title="Game-by-Game")
        st.plotly_chart(line, use_container_width=True)
