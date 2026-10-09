from app.auth.sessions import session_token_hash


def test_session_token_hash_is_stable_and_not_plaintext():
    token = "an opaque random bearer value"
    result = session_token_hash(token)
    assert result == session_token_hash(token)
    assert token not in result
    assert len(result) == 64
