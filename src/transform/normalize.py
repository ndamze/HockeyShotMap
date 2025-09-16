import numpy as np
import pandas as pd

GOAL_X, GOAL_Y = 89.0, 0.0  # normalized right-attack goal center

def shot_distance(x: float, y: float) -> float:
    return float(np.hypot(GOAL_X - x, GOAL_Y - y))

def shot_angle(x: float, y: float) -> float:
    dy = y - GOAL_Y
    dx = GOAL_X - x
    return float(np.degrees(np.arctan2(abs(dy), dx)))

def normalize_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    # Placeholder: assume input already in right-attack frame.
    return df.copy()
