#!/usr/bin/env python3
"""Bootstrap a season's worth of data (placeholder)."""
import argparse, pandas as pd, numpy as np
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2023)
    ap.add_argument("--teams", nargs="*", default=["CAR", "TOR", "BOS"])  # not used in demo
    args = ap.parse_args()

    Path("data/curated").mkdir(parents=True, exist_ok=True)
    # For demo, synthesize a small set of shots
    rng = np.random.default_rng(7)
    n = 150
    df = pd.DataFrame({
        "x": rng.uniform(-85, 89, size=n),
        "y": rng.uniform(-42, 42, size=n),
        "player": rng.choice(["Aho", "Matthews", "Pastrnak", "Svechnikov"], size=n),
        "strength": rng.choice(["5v5", "PP", "PK"], size=n, p=[0.7, 0.2, 0.1]),
        "is_goal": rng.integers(0, 2, size=n)
    })
    df.to_csv("data/curated/demo_shots.csv", index=False)
    print("Wrote data/curated/demo_shots.csv (synthetic)")

if __name__ == "__main__":
    main()
