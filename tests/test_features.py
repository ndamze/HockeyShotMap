import pandas as pd
from src.transform.features import engineer

def test_engineer_adds_columns():
    df = pd.DataFrame({"x": [10.0], "y": [0.0]})
    out = engineer(df)
    assert {"dist", "angle"}.issubset(out.columns)
