import duckdb
from pathlib import Path
import pandas as pd

def connect(db_path: str | Path = "data/curated/shots.duckdb"):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))

def write_table(df: pd.DataFrame, name: str, db_path: str | Path = "data/curated/shots.duckdb"):
    con = connect(db_path)
    con.register("df", df)
    con.execute(f"CREATE TABLE IF NOT EXISTS {name} AS SELECT * FROM df")
    con.close()
