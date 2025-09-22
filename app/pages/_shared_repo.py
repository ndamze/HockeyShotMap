"""
_adapter: reuse the repo's existing loader so pages never fetch directly.

If a loader isn't found automatically, this module will display a clear error
that lists candidate functions it discovered (module:function). Then you can:
  - set HSM_LOADER="module.sub:func" in Streamlit Secrets (TOML), or
  - tell me which one to pin here.

A "loader" is any function accepting (start_date, end_date) and returning
a pandas.DataFrame of shot-level events.
"""

from __future__ import annotations
import os, sys, importlib, inspect
from typing import Optional, Callable, List, Tuple
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

    # normalize coords
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

    # isSOG / isGoal
    if "eventType" in df.columns:
        df["isSOG"] = df["eventType"].isin(["SHOT","GOAL"])
    else:
        df["isSOG"] = False
    if "isGoal" not in df.columns:
        df["isGoal"] = (df.get("eventType","") == "GOAL")

    # date
    if "gameDate" in df.columns:
        df["date"] = pd.to_datetime(df["gameDate"], errors="coerce").dt.date
    else:
        df["date"] = pd.NaT

    order = ["gameDate","date","team","eventType","period","periodTime","strength",
             "x","y","distance","angle","danger","xG",
             "shooterId","shooterName","goalieId","goalieName","isSOG","isGoal"]
    for c in order:
        if c not in df.columns: df[c] = pd.NA
    return df[order]

# ----------------- discovery -----------------
def _signature_ok(f: Callable) -> bool:
    try:
        sig = inspect.signature(f)
        params = list(sig.parameters.values())
        # accept (start, end) or a kwargs-style function
        return len(params) >= 2 or any(p.kind == p.VAR_KEYWORD for p in params)
    except Exception:
        return False

def _looks_like_shot_fetcher(name: str) -> bool:
    n = name.lower()
    return ("shot" in n) and (n.startswith("fetch_") or n.startswith("get_"))

def _import_module_safe(mod_name: str):
    try:
        return importlib.import_module(mod_name)
    except Exception:
        return None

def _scan_module_for_candidates(mod_name: str) -> List[Tuple[str, Callable]]:
    m = _import_module_safe(mod_name)
    out: List[Tuple[str, Callable]] = []
    if not m:
        return out
    for fn_name, fn in inspect.getmembers(m, inspect.isfunction):
        if _looks_like_shot_fetcher(fn_name) and _signature_ok(fn):
            out.append((f"{mod_name}:{fn_name}", fn))
    return out

def _deep_scan_pkg(root_pkg: str) -> List[Tuple[str, Callable]]:
    found: List[Tuple[str, Callable]] = []
    pkg = _import_module_safe(root_pkg)
    if not pkg:
        return found
    # try direct functions on the package
    found.extend(_scan_module_for_candidates(root_pkg))
    # scan immediate submodules that are already imported/packaged
    for name, obj in inspect.getmembers(pkg):
        if inspect.ismodule(obj) and obj.__name__.startswith(root_pkg + "."):
            found.extend(_scan_module_for_candidates(obj.__name__))
            # (avoid recursive import crawling to keep it fast)
    return found

def _try_find_loader() -> Tuple[Optional[Callable], List[str]]:
    # 0) explicit override
    env = os.environ.get("HSM_LOADER")
    if env and ":" in env:
        mod, fn = env.split(":", 1)
        m = _import_module_safe(mod)
        if m:
            f = getattr(m, fn, None)
            if callable(f) and _signature_ok(f):
                return f, [env]

    # 1) scan common modules
    mods = [
        "src.nhl", "src.data", "src.pipeline", "src.api", "src",
        "app.main_data", "app.data", "app",
    ]
    candidates: List[Tuple[str, Callable]] = []
    for mod in mods:
        candidates.extend(_scan_module_for_candidates(mod))

    # 2) shallow deep-scan src.* (already-importable submodules)
    candidates.extend(_deep_scan_pkg("src"))

    f: Optional[Callable] = candidates[0][1] if candidates else None
    labels = [c[0] for c in candidates]
    return f, labels

_LOADER, _CANDIDATES = _try_find_loader()

# ----------------- public API -----------------
def fetch_shots_dataframe(start_date, end_date) -> pd.DataFrame:
    if _LOADER is None:
        # Make the error self-serve: show candidates right in the exception
        msg = [
            "Could not find a repo loader (function to fetch shot-level data).",
            "Fix it by either:",
            "  1) Setting HSM_LOADER = \"module.sub:func\" in Streamlit Secrets (TOML), OR",
            "  2) Telling us the correct function so we can hardwire it here.",
        ]
        if _CANDIDATES:
            msg.append("")
            msg.append("Discovered candidate functions you can try:")
            for c in _CANDIDATES[:20]:
                msg.append(f"  - {c}")
            if len(_CANDIDATES) > 20:
                msg.append(f"  ... and {len(_CANDIDATES)-20} more")
        raise ImportError("\n".join(msg))

    df = _LOADER(start_date, end_date)
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"Loader returned {type(df)}, expected pandas.DataFrame")
    return _ensure_schema(df)
