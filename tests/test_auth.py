"""Tests for /api/auth endpoints."""
import pytest

SIGNUP_URL = "/api/auth/signup"
LOGIN_URL = "/api/auth/login"
ME_URL = "/api/auth/me"
PROFILE_URL = "/api/auth/profile"
CHANGE_PW_URL = "/api/auth/change-password"

ALICE = {
    "username": "alice",
    "password": "pass1234",
    "display_name": "Alice Smith",
    "year_group": 10,
}
BOB = {
    "username": "bob",
    "password": "pass5678",
    "display_name": "Bob Jones",
    "year_group": 11,
}


# ── Signup ────────────────────────────────────────────────────────────────────

def test_signup_first_user_becomes_admin(client):
    r = client.post(SIGNUP_URL, json=ALICE)
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    user = data["user"]
    assert user["is_admin"] is True
    assert user["username"] == "alice"
    assert user["year_group"] == 10


def test_signup_second_user_is_not_admin(client):
    client.post(SIGNUP_URL, json=ALICE)
    r = client.post(SIGNUP_URL, json=BOB)
    assert r.status_code == 200
    assert r.json()["user"]["is_admin"] is False


def test_signup_duplicate_username_rejected(client):
    client.post(SIGNUP_URL, json=ALICE)
    r = client.post(SIGNUP_URL, json=ALICE)
    assert r.status_code == 400
    assert "taken" in r.json()["detail"].lower()


def test_signup_short_password_rejected(client):
    payload = {**ALICE, "password": "abc"}
    r = client.post(SIGNUP_URL, json=payload)
    assert r.status_code == 422  # Pydantic validation


def test_signup_invalid_year_group_rejected(client):
    payload = {**ALICE, "year_group": 6}  # min is 7
    r = client.post(SIGNUP_URL, json=payload)
    assert r.status_code == 422


# ── Login ─────────────────────────────────────────────────────────────────────

def test_login_success_returns_token(client):
    client.post(SIGNUP_URL, json=ALICE)
    r = client.post(LOGIN_URL, json={"username": "alice", "password": "pass1234"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["user"]["username"] == "alice"


def test_login_wrong_password_rejected(client):
    client.post(SIGNUP_URL, json=ALICE)
    r = client.post(LOGIN_URL, json={"username": "alice", "password": "wrong!"})
    assert r.status_code == 401


def test_login_unknown_user_rejected(client):
    r = client.post(LOGIN_URL, json={"username": "nobody", "password": "pass"})
    assert r.status_code == 401


# ── /me ───────────────────────────────────────────────────────────────────────

def test_get_me_returns_current_user(client):
    signup_r = client.post(SIGNUP_URL, json=ALICE)
    token = signup_r.json()["token"]
    r = client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    me = r.json()
    assert me["username"] == "alice"
    assert me["display_name"] == "Alice Smith"
    assert me["year_group"] == 10


def test_get_me_no_token_is_rejected(client):
    r = client.get(ME_URL)
    assert r.status_code in (401, 403)


def test_get_me_invalid_token_returns_403(client):
    r = client.get(ME_URL, headers={"Authorization": "Bearer not.a.valid.token"})
    assert r.status_code == 401


# ── Profile update ────────────────────────────────────────────────────────────

def test_update_profile_display_name_and_year(client):
    signup_r = client.post(SIGNUP_URL, json=ALICE)
    token = signup_r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.put(PROFILE_URL, json={"display_name": "Alicia", "year_group": 12}, headers=headers)
    assert r.status_code == 200

    me = client.get(ME_URL, headers=headers).json()
    assert me["display_name"] == "Alicia"
    assert me["year_group"] == 12


def test_update_profile_no_fields_returns_400(client):
    signup_r = client.post(SIGNUP_URL, json=ALICE)
    token = signup_r.json()["token"]
    r = client.put(PROFILE_URL, json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


# ── Change password ───────────────────────────────────────────────────────────

def test_change_password_success(client):
    signup_r = client.post(SIGNUP_URL, json=ALICE)
    token = signup_r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        CHANGE_PW_URL,
        json={"current_password": "pass1234", "new_password": "newpass5678"},
        headers=headers,
    )
    assert r.status_code == 200

    # Old password should no longer work
    assert client.post(LOGIN_URL, json={"username": "alice", "password": "pass1234"}).status_code == 401
    # New password should work
    assert client.post(LOGIN_URL, json={"username": "alice", "password": "newpass5678"}).status_code == 200


def test_change_password_wrong_current_rejected(client):
    signup_r = client.post(SIGNUP_URL, json=ALICE)
    token = signup_r.json()["token"]
    r = client.post(
        CHANGE_PW_URL,
        json={"current_password": "wrong!", "new_password": "newpass5678"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
