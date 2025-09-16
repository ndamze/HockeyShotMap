from src.transform.normalize import shot_distance, shot_angle

def test_distance_basic():
    assert round(shot_distance(89.0, 0.0), 3) == 0.0

def test_angle_range():
    a = shot_angle(60.0, 10.0)
    assert 0 <= a <= 90
