import streamlit as st
import pandas as pd
import plotly.express as px
import math
from datetime import date, timedelta

st.set_page_config(page_title="Goalie Lens", page_icon="🥅", layout="wide")
st.title("Goalie Lens")

def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","data_df","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

def _harmonize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "isSOG" not in out.columns:
        out["isSOG"] = out.get("event", "").isin(["Shot","Goal"])
    if "isGoal" not in out.columns:
        if "is_goal" in out.columns:
            out["isGoal"] = out["is_goal"].astype(bool)
        else:
            out["isGoal"] = out.get("event", "") == "Goal"
    if "date" not in out.columns:
        if "source_date" in out.columns:
            out["date"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date
        elif "gameDate" in out.columns:
            out["date"] = pd.to_datetime(out["gameDate"], errors="coerce").dt.date
    if "strength" not in out.columns:
        out["strength"] = "EV"

    # distance / angle / danger for maps
    def _dist_ang(x, y):
        try:
            x = float(x); y = float(y)
        except Exception:
            return (math.nan, math.nan)
        dx = 89.0 - x
        dy = abs(y)
        dist = (dx*dx + dy*dy) ** 0.5
        ang = 90.0 if dx == 0 else abs(math.degrees(math.atan2(dy, dx)))
        return (dist, ang)

    if "distance" not in out.columns or "angle" not in out.columns:
        da = out[["x","y"]].apply(lambda r: _dist_ang(r.get("x"), r.get("y")), axis=1, result_type="expand")
        out[["distance","angle"]] = da.values

    if "danger" not in out.columns:
        def _danger(d, a):
            if pd.isna(d): return "unknown"
            if d <= 25 and a <= 30: return "high"
            if d <= 40 and a <= 45: return "medium"
            return "low"
        out["danger"] = out[["distance","angle"]].apply(lambda r: _danger(r["distance"], r["angle"]), axis=1)

    out["isSOG"] = out["isSOG"].astype(bool)
    out["isGoal"] = out["isGoal"].astype(bool)
    return out

df_raw = _find_df()
if df_raw is None or df_raw.empty:
    st.warning("No data found in session. Go to **Home**, fetch data, then return here.")
    st.stop()

df = _harmonize(df_raw)

# ---- sidebar ----
with st.sidebar:
    st.subheader("Filters")
    start = st.session_state.get("selected_start_date", date.today() - timedelta(days=30))
    end   = st.session_state.get("selected_end_date", date.today())
    st.date_input("Start date (display only)", start, disabled=True)
    st.date_input("End date (display only)", end, disabled=True)

    # If the dataset doesn't have goalie identifiers, fall back to TEAM-level goalie lens
    has_goalie = (
        ("goalieId" in df and df["goalieId"].notna().any()) or
        ("goalieName" in df and df["goalieName"].notna().any())
    )

    if has_goalie:
        # (If you later add goalieName/Id to your main df, this path will kick in automatically)
        s = df.dropna(subset=["goalieName"]) if "goalieName" in df else df.dropna(subset=["goalieId"])
        unique_goalies = sorted(s["goalieName"].unique().tolist()) if "goalieName" in s else sorted(s["goalieId"].unique().tolist())
        sel = st.selectbox("Goalie", unique_goalies)
        if "goalieName" in s:
            subset = df[df["goalieName"] == sel].copy()
            goalie_label = sel
        else:
            subset = df[df["goalieId"] == sel].copy()
            goalie_label = str(sel)
        mode = "goalie"
    else:
        teams = sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else []
        if not teams:
            st.info("No team identifiers found."); st.stop()
        team_sel = st.selectbox("Team (defending aggregate)", teams)
        # All shots on goal where selected team is defending: matchup contains team, shooter != team
        if "matchup" in df.columns and "team" in df.columns:
            mask = df["isSOG"] & df["matchup"].fillna("").str.contains(team_sel) & (df["team"] != team_sel)
            subset = df[mask].copy()
        else:
            # Fallback: just take all SOG not by that team (works for single-game views)
            subset = df[(df["isSOG"]) & (df["team"] != team_sel)].copy()
        goalie_label = f"{team_sel} (aggregate)"
        mode = "team"

if subset.empty:
    st.info("No shots faced for the current selection."); st.stop()

subset_sog = subset[subset["isSOG"]]

# ---- KPIs ----
c1,c2,c3,c4 = st.columns(4)
shots_faced = int(len(subset_sog))
goals_against = int(subset_sog["isGoal"].sum())
saves = shots_faced - goals_against
sv_pct = saves / max(1, shots_faced)
xga = float(subset_sog["xG"].sum()) if "xG" in subset_sog.columns else 0.0
c1.metric("Shots Faced", f"{shots_faced:,}")
c2.metric("Goals Against", f"{goals_against:,}")
c3.metric("SV%", f"{sv_pct:.1%}")
c4.metric("xGA (sum xG faced)", f"{xga:.2f}")
st.caption(f"View: {('Goalie ' + goalie_label) if mode=='goalie' else ('Team aggregate: ' + goalie_label)}")
st.markdown("---")

# ---- Shot map faced ----
st.subheader(f"Shot Map Faced – {goalie_label}")
if {"x","y"}.issubset(subset_sog.columns):
    scat = px.scatter(
        subset_sog, x="x", y="y",
        color=subset_sog["isGoal"].map({True:"Goal Against", False:"Save"}),
        hover_data=[c for c in ["team","player","date","period","periodTime","strength","distance","angle"] if c in subset_sog.columns],
        title="Shots Faced (normalized toward +x)"
    )
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No (x,y) coordinates available to draw the map.")

# ---- Distance buckets ----
if "distance" in subset_sog.columns:
    st.subheader("Performance by Distance")
    bins = [0,10,20,30,40,60,1000]
    labels = ["0–10","10–20","20–30","30–40","40–60","60+"]
    tmp = subset_sog.copy()
    tmp["distBucket"] = pd.cut(tmp["distance"], bins=bins, labels=labels, include_lowest=True)
    bucket = tmp.groupby("distBucket", dropna=False).agg(
        Faced=("distance","count"),
        GA=("isGoal","sum")
    ).reset_index()
    bucket["Saves"] = bucket["Faced"] - bucket["GA"]
    bucket["SV%"] = (bucket["Saves"] / bucket["Faced"]).replace([float("inf"), float("-inf")], pd.NA)
    st.dataframe(bucket, use_container_width=True)
    bar = px.bar(bucket, x="distBucket", y=["Faced","GA","Saves"], barmode="group", title="By Distance")
    st.plotly_chart(bar, use_container_width=True)

# ---- Goals-against heatmap ----
if {"x","y","isGoal"}.issubset(subset_sog.columns):
    st.subheader("Goals Against Heatmap")
    ga = subset_sog[subset_sog["isGoal"]]
    if len(ga) >= 1:
        hm = px.density_heatmap(ga, x="x", y="y", nbinsx=40, nbinsy=20, histfunc="count", title="Goals Against Density")
        hm.update_yaxes(scaleanchor="x", scaleratio=1)
        st.plotly_chart(hm, use_container_width=True)
    else:
        st.info("No goals against in range.")
