import pandas as pd

def export_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False)
