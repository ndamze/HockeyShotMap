from app.components.rink_plot import base_rink

def test_base_rink_builds():
    fig = base_rink()
    assert fig is not None
