from app.auth.passwords import hash_password, verify_password
from app.auth.accounts import normalize_username
import pytest


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert "correct horse battery staple" not in first
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong", first)


def test_malformed_hash_fails_closed():
    assert not verify_password("anything", "")
    assert not verify_password("anything", "scrypt$invalid")


def test_account_name_and_password_policy():
    assert normalize_username(" Alice_2 ") == "alice_2"
    with pytest.raises(ValueError, match="INVALID_USERNAME"):
        normalize_username("a")
    with pytest.raises(ValueError, match="PASSWORD_TOO_SHORT"):
        hash_password("short")
