import pandas as pd
from .normalize import shot_distance, shot_angle

def engineer(df: pd.DataFrame) -> pd.DataFrame:
    shots = df.copy()
    shots["dist"] = shots.apply(lambda r: shot_distance(r.x, r.y), axis=1)
    shots["angle"] = shots.apply(lambda r: shot_angle(r.x, r.y), axis=1)
    return shots
