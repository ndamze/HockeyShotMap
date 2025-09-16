import plotly.graph_objects as go

RINK_W, RINK_H = 200, 85  # normalized feet; drawing is schematic

def base_rink():
    fig = go.Figure()
    fig.update_xaxes(range=[-100, 100], visible=False)
    fig.update_yaxes(range=[-42.5, 42.5], scaleanchor="x", scaleratio=1, visible=False)
    # Center line
    fig.add_shape(type="line", x0=0, y0=-42.5, x1=0, y1=42.5)
    # Blue lines (approx at ±25 ft from center)
    fig.add_shape(type="line", x0=25, y0=-42.5, x1=25, y1=42.5)
    fig.add_shape(type="line", x0=-25, y0=-42.5, x1=-25, y1=42.5)
    # Goal crease area boxes (schematic)
    fig.add_shape(type="rect", x0=85, y0=-4, x1=89, y1=4)
    fig.add_shape(type="rect", x0=-89, y0=-4, x1=-85, y1=4)
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
