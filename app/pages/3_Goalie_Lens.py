import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import math
from datetime import date, timedelta

# --- Rink plot import with dual-path fallback ---
try:
    from app.components.rink_plot import base_rink
except ModuleNotFoundError:
    try:
        from components.rink_plot import base_rink
    except ModuleNotFoundError:
        st.error("Could not import rink_plot component. Please ensure the components directory exists.")
        st.stop()

st.set_page_config(page_title="Goalie Lens", page_icon="🥅", layout="wide")
st.title("Goalie Lens")

def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","data_df","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

def _norm_strength(s: str | None) -> str:
    if s is None:
        return "5v5"
    t = str(s).strip().lower().replace("on","v").replace("-","").replace(" ", "")
    if t in {"ev","even"}: return "5v5"
    if t in {"pp","powerplay","powerplayadvantage"}: return "PP"
    if t in {"pk","sh","shorthanded","penaltykill"}: return "PK"
    if "v" in t:
        parts = t.split("v")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}v{parts[1]}"
    return t.upper() if t else "5v5"

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
    if "strength" in out.columns:
        out["strength"] = out["strength"].map(_norm_strength)
    else:
        out["strength"] = "5v5"

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
        out["danger"] = out[["distance","angle"]].apply(
            lambda r: ("high" if (r["distance"] <= 25 and r["angle"] <= 30)
                       else ("medium" if (r["distance"] <= 40 and r["angle"] <= 45) else "low")),
            axis=1
        )

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

    has_goalie = (
        ("goalieName" in df and df["goalieName"].notna().any()) or
        ("goalieId" in df and df["goalieId"].notna().any())
    )

    if has_goalie:
        s = df.dropna(subset=["goalieName"]) if "goalieName" in df else df.dropna(subset=["goalieId"])
        choices = (sorted(s["goalieName"].unique().tolist()) if "goalieName" in s
                   else sorted(s["goalieId"].unique().tolist()))
        sel = st.selectbox("Goalie", choices)
        subset = (df[df["goalieName"] == sel].copy() if "goalieName" in s else df[df["goalieId"] == sel].copy())
        goalie_label = sel if isinstance(sel, str) else str(sel)
        mode = "goalie"
    else:
        teams = sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else []
        if not teams:
            st.info("No team identifiers found."); st.stop()
        team_sel = st.selectbox("Team (defending aggregate)", teams)
        if "matchup" in df.columns and "team" in df.columns:
            mask = df["isSOG"] & df["matchup"].fillna("").str.contains(team_sel) & (df["team"] != team_sel)
            subset = df[mask].copy()
        else:
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

# ---- Shot Map Faced on rink (hover trimmed) ----
st.subheader(f"Shot Map Faced – {goalie_label}")
if {"x","y"}.issubset(subset_sog.columns):
    fig = base_rink()
    cd = subset_sog.reindex(columns=["team","date","period","periodTime","strength"]).fillna("")
    # saves and goals as separate traces
    saves_df = subset_sog[~subset_sog["isGoal"]]
    goals_df = subset_sog[ subset_sog["isGoal"]]

    if not saves_df.empty:
        cds = saves_df.reindex(columns=["team","date","period","periodTime","strength"]).fillna("")
        fig.add_trace(go.Scatter(
            x=saves_df["x"], y=saves_df["y"],
            mode="markers",
            marker=dict(size=8, opacity=0.9, line=dict(color="black", width=0.8)),
            name="Saves",
            customdata=cds.values,
            hovertemplate=(
                "Team: %{customdata[0]}<br>"
                "Date: %{customdata[1]}<br>"
                "Period: %{customdata[2]}<br>"
                "Period Time: %{customdata[3]}<br>"
                "Strength: %{customdata[4]}<extra></extra>"
            )
        ))
    if not goals_df.empty:
        cdg = goals_df.reindex(columns=["team","date","period","periodTime","strength"]).fillna("")
        fig.add_trace(go.Scatter(
            x=goals_df["x"], y=goals_df["y"],
            mode="markers",
            marker=dict(size=10, opacity=0.98, symbol="star", line=dict(color="black", width=1.0)),
            name="Goals Against",
            customdata=cdg.values,
            hovertemplate=(
                "Team: %{customdata[0]}<br>"
                "Date: %{customdata[1]}<br>"
                "Period: %{customdata[2]}<br>"
                "Period Time: %{customdata[3]}<br>"
                "Strength: %{customdata[4]}<extra></extra>"
            )
        ))
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(fig, use_container_width=True)
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
    fig_b = go.Figure()
    fig_b.add_bar(x=bucket["distBucket"], y=bucket["Faced"], name="Faced")
    fig_b.add_bar(x=bucket["distBucket"], y=bucket["GA"], name="GA")
    fig_b.add_bar(x=bucket["distBucket"], y=bucket["Saves"], name="Saves")
    fig_b.update_layout(barmode="group", title="By Distance")
    st.plotly_chart(fig_b, use_container_width=True)

# ---- Goals Against heatmap on rink ----
st.subheader("Goals Against Heatmap")
ga = subset_sog[subset_sog["isGoal"]] if "isGoal" in subset_sog.columns else pd.DataFrame()
if not ga.empty and {"x","y"}.issubset(ga.columns):
    fig_h = base_rink()
    fig_h.add_trace(go.Histogram2d(
        x=ga["x"], y=ga["y"],
        nbinsx=40, nbinsy=20, opacity=0.85, showscale=True
    ))
    fig_h.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(fig_h, use_container_width=True)
else:
    st.info("No goals against in range.")
