import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="Goalie Lens", page_icon="🥅", layout="wide")
st.title("Goalie Lens")

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

    s = df if team == "All" or "team" not in df else df[df["team"] == team]
    if "goalieId" not in s or "goalieName" not in s:
        st.warning("This dataset doesn’t include goalie identifiers.")
        st.stop()

    pairs = s.dropna(subset=["goalieId","goalieName"]).apply(
        lambda r: (int(r["goalieId"]), str(r["goalieName"])), axis=1
    ).drop_duplicates().tolist()
    if not pairs:
        st.info("No goalies found for the filters."); st.stop()

    names = sorted({name for _, name in pairs})
    sel_name = st.selectbox("Goalie", names)
    gid = next(pid for pid, name in pairs if name == sel_name)

subset = df[df["goalieId"] == gid].copy() if "goalieId" in df else pd.DataFrame()
subset_sog = subset[subset.get("isSOG", False)] if not subset.empty else subset

# KPIs
c1,c2,c3,c4 = st.columns(4)
shots_faced = int(len(subset_sog))
goals_against = int(subset_sog["isGoal"].sum()) if "isGoal" in subset_sog else 0
saves = shots_faced - goals_against
sv_pct = saves / max(1, shots_faced)
xga = float(subset_sog["xG"].sum()) if "xG" in subset_sog else 0.0
c1.metric("Shots Faced", f"{shots_faced:,}")
c2.metric("Goals Against", f"{goals_against:,}")
c3.metric("SV%", f"{sv_pct:.1%}")
c4.metric("xGA (sum xG faced)", f"{xga:.2f}")
st.markdown("---")

# Shot map
st.subheader(f"Shot Map Faced – {sel_name}")
if shots_faced >= 1 and "x" in subset_sog and "y" in subset_sog:
    scat = px.scatter(subset_sog, x="x", y="y",
                      color=subset_sog["isGoal"].map({True:"Goal Against", False:"Save"}) if "isGoal" in subset_sog else None,
                      symbol=subset_sog["shotType"] if "shotType" in subset_sog else None,
                      hover_data=[c for c in ["team","shooterName","date","period","periodTime","distance","angle"]
                                  if c in subset_sog.columns],
                      title="Shots Faced (normalized toward +x)")
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No shots faced (or no coordinates) to show.")

# Distance buckets
if "distance" in subset_sog:
    st.subheader("Performance by Distance")
    bins = [0,10,20,30,40,60,1000]
    labels = ["0–10","10–20","20–30","30–40","40–60","60+"]
    tmp = subset_sog.copy()
    tmp["distBucket"] = pd.cut(tmp["distance"], bins=bins, labels=labels, include_lowest=True)
    bucket = tmp.groupby("distBucket", dropna=False).agg(
        Faced=("distance","count"),
        GA=("isGoal","sum") if "isGoal" in tmp else ("distance","count"),
    ).reset_index()
    bucket["Saves"] = bucket["Faced"] - bucket["GA"]
    bucket["SV%"] = (bucket["Saves"] / bucket["Faced"]).replace([float("inf"), float("-inf")], pd.NA)
    st.dataframe(bucket, use_container_width=True)
    bar = px.bar(bucket, x="distBucket", y=["Faced","GA","Saves"], barmode="group", title="By Distance")
    st.plotly_chart(bar, use_container_width=True)

# Goals-against heatmap
if "x" in subset_sog and "y" in subset_sog and "isGoal" in subset_sog:
    st.subheader("Goals Against Heatmap")
    ga = subset_sog[subset_sog["isGoal"]]
    if len(ga) >= 1:
        hm = px.density_heatmap(ga, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Goals Against Density")
        hm.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(hm, use_container_width=True)
    else:
        st.info("No goals against in range.")
