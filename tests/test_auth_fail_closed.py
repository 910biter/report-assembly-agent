from app.auth import MULTIUSER_READY


def test_multiuser_release_gate_is_open_after_isolation_tests():
    assert MULTIUSER_READY is True
