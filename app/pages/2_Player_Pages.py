import streamlit as st
import pandas as pd
import plotly.express as px
import math
from datetime import date, timedelta

st.set_page_config(page_title="Player Pages", page_icon="🧊", layout="wide")
st.title("Player Pages")

# ---- pull the already-fetched data from session ----
def _find_df() -> pd.DataFrame | None:
    keys = ["shots_df","df_shots","data_df","df","shots","data_shots","shots_dataframe"]
    for k in keys:
        if k in st.session_state and isinstance(st.session_state[k], pd.DataFrame):
            return st.session_state[k]
    return None

def _harmonize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # isSOG / isGoal from your schema
    if "isSOG" not in out.columns:
        out["isSOG"] = out.get("event", "").isin(["Shot","Goal"])
    if "isGoal" not in out.columns:
        if "is_goal" in out.columns:
            out["isGoal"] = out["is_goal"].astype(bool)
        else:
            out["isGoal"] = out.get("event", "") == "Goal"

    # date (if you fetched a range, you already store source_date)
    if "date" not in out.columns:
        if "source_date" in out.columns:
            out["date"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date
        elif "gameDate" in out.columns:
            out["date"] = pd.to_datetime(out["gameDate"], errors="coerce").dt.date

    # strength default
    if "strength" not in out.columns:
        out["strength"] = "EV"

    # distance / angle / danger from (x,y)
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

    # Ensure booleans
    out["isSOG"] = out["isSOG"].astype(bool)
    out["isGoal"] = out["isGoal"].astype(bool)
    return out

df_raw = _find_df()
if df_raw is None or df_raw.empty:
    st.warning("No data found in session. Go to **Home**, fetch data, then return here.")
    st.stop()

df = _harmonize(df_raw)

# ---- sidebar (show current date range from Home) ----
with st.sidebar:
    st.subheader("Filters")
    start = st.session_state.get("selected_start_date", date.today() - timedelta(days=30))
    end   = st.session_state.get("selected_end_date", date.today())
    st.date_input("Start date (display only)", start, disabled=True)
    st.date_input("End date (display only)", end, disabled=True)

    teams = ["All"] + (sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else [])
    team = st.selectbox("Team (optional)", teams)

    # Build player list from the 'player' column (no numeric IDs required)
    s = df[df["team"] == team] if team != "All" and "team" in df else df
    if "player" not in s or s["player"].dropna().empty:
        st.info("No players found for the filters."); st.stop()
    names = sorted(s["player"].dropna().unique().tolist())
    sel_name = st.selectbox("Player", names)

subset = df[df["player"] == sel_name].copy()

# ---- KPIs ----
c1,c2,c3,c4 = st.columns(4)
shots = int(subset["isSOG"].sum())
goals = int(subset["isGoal"].sum())
sh_pct = goals / max(1, shots)
xg = float(subset["xG"].sum()) if "xG" in subset.columns else 0.0
c1.metric("Shots", f"{shots:,}")
c2.metric("Goals", f"{goals:,}")
c3.metric("Sh%", f"{sh_pct:.1%}")
c4.metric("xG (sum)", f"{xg:.2f}")
st.markdown("---")

# ---- Shot map ----
st.subheader(f"Shot Map – {sel_name}")
if shots >= 1 and {"x","y"}.issubset(subset.columns):
    scat = px.scatter(
        subset, x="x", y="y",
        color=subset["isGoal"].map({True:"Goal", False:"Shot"}),
        hover_data=[c for c in ["team","date","period","periodTime","strength","distance","angle"] if c in subset.columns],
        title="Shots (normalized toward +x)"
    )
    scat.update_yaxes(scaleanchor="x", scaleratio=1)
    st.plotly_chart(scat, use_container_width=True)
else:
    st.info("No shots (or no coordinates) to show.")

# ---- By Strength ----
st.subheader("By Strength")
st_tbl = (
    subset.groupby("strength", dropna=False)
    .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
    .reset_index().sort_values("strength", na_position="last")
)
st.dataframe(st_tbl, use_container_width=True)
st_bar = px.bar(st_tbl, x="strength", y=["Shots","Goals"], barmode="group", title="Shots & Goals by Strength")
st.plotly_chart(st_bar, use_container_width=True)

# ---- Danger buckets ----
if "danger" in subset.columns:
    st.subheader("Danger Zones")
    dz = (
        subset.groupby("danger", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index().sort_values("danger", na_position="last")
    )
    dzbar = px.bar(dz, x="danger", y=["Shots","Goals"], barmode="group", title="By Danger")
    st.plotly_chart(dzbar, use_container_width=True)

# ---- Trend ----
if "date" in subset.columns:
    st.subheader("Trend")
    trend = (
        subset.groupby("date", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index().sort_values("date")
    )
    line = px.line(trend, x="date", y=["Shots","Goals"], title="Game-by-Game")
    st.plotly_chart(line, use_container_width=True)
