import plotly.graph_objects as go

RINK_W, RINK_H = 200, 85  # normalized feet; drawing is schematic

def base_rink():
    fig = go.Figure()
    fig.update_xaxes(range=[-100, 100], visible=False)
    fig.update_yaxes(range=[-42.5, 42.5], scaleanchor="x", scaleratio=1, visible=False)
    # (lines and creases are added by app/main.py)
    return fig

def add_shots(fig, df):
    if df.empty:
        return fig
    fig.add_scatter(
        x=df["x"], y=df["y"], mode="markers",
        opacity=0.7, marker=dict(size=7),
        text=df.get("player", None)
    )
    return fig
