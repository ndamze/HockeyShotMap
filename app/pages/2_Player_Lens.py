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

st.set_page_config(page_title="Player Lens", page_icon="🧊", layout="wide")
st.title("Player Lens")

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
            if d <= 25 and a <= 30: return "low" if False else "high"  # placeholder, will set below
            if d <= 40 and a <= 45: return "medium"
            return "low"
        # corrected explicitly:
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

    teams = ["All"] + (sorted([t for t in df["team"].dropna().unique().tolist() if t]) if "team" in df else [])
    team = st.selectbox("Team (optional)", teams)

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

# ---- Shot map on rink (hover: Team, Date, Period, Period Time, Strength) ----
st.subheader(f"Shot Map – {sel_name}")
if shots >= 1 and {"x","y"}.issubset(subset.columns):
    fig = base_rink()
    # Build customdata for clean hover
    cd = subset.reindex(columns=["team","date","period","periodTime","strength"]).fillna("")
    fig.add_trace(go.Scatter(
        x=subset["x"], y=subset["y"],
        mode="markers",
        marker=dict(size=8, opacity=0.9, line=dict(color="black", width=0.8)),
        # color by isGoal: draw two layers to keep hover simple
        name="Shots",
        customdata=cd.values,
        hovertemplate=(
            "Team: %{customdata[0]}<br>"
            "Date: %{customdata[1]}<br>"
            "Period: %{customdata[2]}<br>"
            "Period Time: %{customdata[3]}<br>"
            "Strength: %{customdata[4]}<extra></extra>"
        )
    ))
    # overlay stars for goals
    goals_df = subset[subset["isGoal"]]
    if not goals_df.empty:
        cdg = goals_df.reindex(columns=["team","date","period","periodTime","strength"]).fillna("")
        fig.add_trace(go.Scatter(
            x=goals_df["x"], y=goals_df["y"],
            mode="markers",
            marker=dict(size=10, opacity=0.98, symbol="star", line=dict(color="black", width=1.0)),
            name="Goals",
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
    st.info("No shots (or no coordinates) to show.")

# ---- By Strength ----
st.subheader("By Strength")
st_tbl = (
    subset.groupby("strength", dropna=False)
    .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
    .reset_index().sort_values("strength", na_position="last")
)
st.dataframe(st_tbl, use_container_width=True)
fig_s = go.Figure()
fig_s.add_bar(x=st_tbl["strength"], y=st_tbl["Shots"], name="Shots")
fig_s.add_bar(x=st_tbl["strength"], y=st_tbl["Goals"], name="Goals")
fig_s.update_layout(barmode="group", title="Shots & Goals by Strength")
st.plotly_chart(fig_s, use_container_width=True)

# ---- Danger (Low → Medium → High) ----
if "danger" in subset.columns:
    st.subheader("Danger Zones")
    tmp = subset.copy()
    tmp["danger"] = tmp["danger"].astype(str).str.lower()
    cats = pd.api.types.CategoricalDtype(["low","medium","high"], ordered=True)
    tmp["danger"] = tmp["danger"].astype(cats)
    dz = (
        tmp.groupby("danger", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index().sort_values("danger")
    )
    dz["danger"] = dz["danger"].astype(str).str.capitalize()
    fig_d = go.Figure()
    fig_d.add_bar(x=dz["danger"], y=dz["Shots"], name="Shots")
    fig_d.add_bar(x=dz["danger"], y=dz["Goals"], name="Goals")
    fig_d.update_layout(barmode="group", title="By Danger")
    st.plotly_chart(fig_d, use_container_width=True)

# ---- Trend ----
if "date" in subset.columns:
    st.subheader("Trend")
    trend = (
        subset.groupby("date", dropna=False)
        .agg(Shots=("isSOG","sum"), Goals=("isGoal","sum"))
        .reset_index().sort_values("date")
    )
    fig_t = go.Figure()
    fig_t.add_scatter(x=trend["date"], y=trend["Shots"], name="Shots", mode="lines")
    fig_t.add_scatter(x=trend["date"], y=trend["Goals"], name="Goals", mode="lines")
    st.plotly_chart(fig_t, use_container_width=True)
