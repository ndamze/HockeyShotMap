"""
_adapter: reuse the repo's existing loader so pages never fetch directly.

It tries to find a function in your repo that returns a *shot-level DataFrame*
for a date range, then normalizes columns for the new pages.

Discovery rules:
- Scans common modules under src/ and app/ for functions whose names look like
  "fetch_*shots*" or "get_*shots*", accepting (start_date, end_date).
- You can hardwire the exact function via HSM_LOADER="module:function".
  Example: HSM_LOADER="src.nhl:fetch_shots_dataframe"

If nothing is found, it raises with a clear message (so you can tell me the path).
"""

from __future__ import annotations
import os, sys, importlib, inspect, types
from typing import Optional, Callable, Tuple, List
import pandas as pd
import numpy as np
import math

# Make repo root importable: .../HockeyShotMap/app/pages -> add .../HockeyShotMap
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)

# ----------------- helpers to normalize -----------------
def _normalize_xy(x, y):
    if x is None or y is None:
        return (np.nan, np.nan)
    return (-x, -y) if x < 0 else (x, y)

def _goal_distance_angle(x, y):
    if pd.isna(x) or pd.isna(y):
        return (np.nan, np.nan)
    dx = 89.0 - float(x)
    dy = abs(float(y))
    dist = (dx*dx + dy*dy) ** 0.5
    ang = 90.0 if dx == 0 else abs(math.degrees(math.atan2(dy, dx)))
    return (dist, ang)

def _danger(dist, ang):
    if pd.isna(dist): return "unknown"
    if dist <= 25 and ang <= 30: return "high"
    if dist <= 40 and ang <= 45: return "medium"
    return "low"

def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    need_soft = [
        "x","y","eventType","team","period","periodTime","strength",
        "shooterId","shooterName","goalieId","goalieName","isGoal",
        "xG","danger","gameDate"
    ]
    for c in need_soft:
        if c not in df.columns:
            df[c] = pd.NA

    # normalize coords (only once)
    if "x" in df.columns and "y" in df.columns:
        xy = df[["x","y"]].apply(lambda r: _normalize_xy(r["x"], r["y"]), axis=1, result_type="expand")
        df[["x","y"]] = xy.values

    # distance & angle
    if "distance" not in df.columns or "angle" not in df.columns:
        da = df[["x","y"]].apply(lambda r: _goal_distance_angle(r["x"], r["y"]), axis=1, result_type="expand")
        df[["distance","angle"]] = da.values

    # danger
    if "danger" not in df.columns:
        df["danger"] = df[["distance","angle"]].apply(lambda r: _danger(r["distance"], r["angle"]), axis=1)

    # isSOG
    if "eventType" in df.columns:
        df["isSOG"] = df["eventType"].isin(["SHOT","GOAL"])
    else:
        df["isSOG"] = False

    # isGoal
    if "isGoal" not in df.columns:
        df["isGoal"] = (df.get("eventType","") == "GOAL")

    # date
    if "gameDate" in df.columns:
        df["date"] = pd.to_datetime(df["gameDate"], errors="coerce").dt.date
    else:
        df["date"] = pd.NaT

    # order nicely (missing cols will be created above anyway)
    order = ["gameDate","date","team","eventType","period","periodTime","strength",
             "x","y","distance","angle","danger","xG",
             "shooterId","shooterName","goalieId","goalieName","isSOG","isGoal"]
    for c in order:
        if c not in df.columns: df[c] = pd.NA
    return df[order]

# ----------------- loader discovery -----------------
def _import_from_str(spec: str) -> Optional[Callable]:
    """spec like 'module.sub:func'"""
    try:
        mod_name, func_name = spec.split(":")
        m = importlib.import_module(mod_name)
        f = getattr(m, func_name, None)
        return f if callable(f) else None
    except Exception:
        return None

def _signature_ok(f: Callable) -> bool:
    try:
        sig = inspect.signature(f)
        params = list(sig.parameters.values())
        # accept (start, end) or (**kwargs) style
        return len(params) >= 2 or any(p.kind == p.VAR_KEYWORD for p in params)
    except Exception:
        return False

def _looks_like_shot_fetcher(name: str) -> bool:
    n = name.lower()
    return ("shot" in n) and (n.startswith("fetch_") or n.startswith("get_"))

def _try_candidates() -> Optional[Callable]:
    # 1) Honor explicit override
    env = os.environ.get("HSM_LOADER")
    if env:
        f = _import_from_str(env)
        if f and _signature_ok(f): return f

    # 2) Likely modules to scan (add more if needed)
    mods = [
        "src.nhl", "src.data", "src.pipeline", "src.api", "src",
        "app.main_data", "app.data", "app",
    ]
    for mod in mods:
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        for name, obj in inspect.getmembers(m, inspect.isfunction):
            if _looks_like_shot_fetcher(name) and _signature_ok(obj):
                return obj

    # 3) Deep scan under src.* submodules
    try:
        pkg = importlib.import_module("src")
        for name, obj in inspect.getmembers(pkg, inspect.ismodule):
            try:
                for fn_name, fn in inspect.getmembers(obj, inspect.isfunction):
                    if _looks_like_shot_fetcher(fn_name) and _signature_ok(fn):
                        return fn
            except Exception:
                continue
    except Exception:
        pass

    return None

_LOADER = _try_candidates()

# ----------------- public API used by pages -----------------
def fetch_shots_dataframe(start_date, end_date) -> pd.DataFrame:
    """
    First choice: call the repo’s real loader.
    If not found, raise with a helpful message so you can set HSM_LOADER or tell us the path.
    """
    if _LOADER is None:
        raise ImportError(
            "Could not find a repo loader. Set HSM_LOADER='module.sub:func' in Streamlit "
            "secrets (TOML), e.g. HSM_LOADER = \"src.nhl:fetch_shots_dataframe\"; "
            "or tell me the function path and I’ll hardwire it."
        )
    df = _LOADER(start_date, end_date)
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"Loader {_LOADER} returned {type(df)}, expected pandas.DataFrame")
    return _ensure_schema(df)
