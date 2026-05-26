"""
Integration tests — HTTP endpoints via TestClient.
All 12 scenarios from the original manual test, now in pytest.
"""
import datetime
import pytest
from trading_api_kit.models import User
from trading_api_kit.auth import hash_password


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_me_no_token(client):
    r = client.get("/auth/me")
    assert r.status_code in (401, 403)


def test_login_unknown_user(client):
    r = client.post("/auth/login", json={"email": "nobody@x.com", "password": "wrong"})
    assert r.status_code == 401


def test_login_valid_and_me(client, seeded_user):
    email, password = seeded_user

    # Login
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token

    # /auth/me with token
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_me_forged_token(client):
    r = client.get("/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 401


def test_login_wrong_password(client, seeded_user):
    email, _ = seeded_user
    r = client.post("/auth/login", json={"email": email, "password": "WRONG"})
    assert r.status_code == 401


# ── Admin ─────────────────────────────────────────────────────────────────────

def test_admin_wrong_secret(client):
    r = client.get("/admin/users", headers={"X-Admin-Secret": "wrong"})
    assert r.status_code == 403


def test_admin_list_users(client, admin_secret, seeded_user):
    email, _ = seeded_user
    r = client.get("/admin/users", headers={"X-Admin-Secret": admin_secret})
    assert r.status_code == 200
    emails = [u["email"] for u in r.json()]
    assert email in emails


def test_admin_create_user(client, admin_secret):
    r = client.post(
        "/admin/users",
        headers={"X-Admin-Secret": admin_secret},
        json={"email": "created@kit.com", "name": "Created", "password": "cpass"},
    )
    assert r.status_code == 201
    assert r.json()["email"] == "created@kit.com"


def test_admin_duplicate_email(client, admin_secret):
    client.post(
        "/admin/users",
        headers={"X-Admin-Secret": admin_secret},
        json={"email": "dup@kit.com", "password": "x"},
    )
    r = client.post(
        "/admin/users",
        headers={"X-Admin-Secret": admin_secret},
        json={"email": "dup@kit.com", "password": "x"},
    )
    assert r.status_code == 409


def test_admin_change_password_and_login(client, admin_secret):
    # Create user
    client.post(
        "/admin/users",
        headers={"X-Admin-Secret": admin_secret},
        json={"email": "pwchange@kit.com", "password": "original"},
    )
    users = client.get("/admin/users", headers={"X-Admin-Secret": admin_secret}).json()
    uid = next(u["id"] for u in users if u["email"] == "pwchange@kit.com")

    # Change password
    r = client.patch(
        f"/admin/users/{uid}/password",
        headers={"X-Admin-Secret": admin_secret},
        json={"password": "changed!"},
    )
    assert r.status_code == 200

    # Old password fails
    r = client.post("/auth/login", json={"email": "pwchange@kit.com", "password": "original"})
    assert r.status_code == 401

    # New password works
    r = client.post("/auth/login", json={"email": "pwchange@kit.com", "password": "changed!"})
    assert r.status_code == 200


def test_admin_delete_user(client, admin_secret):
    # Create a throwaway user
    client.post(
        "/admin/users",
        headers={"X-Admin-Secret": admin_secret},
        json={"email": "todelete@kit.com", "password": "bye"},
    )
    users = client.get("/admin/users", headers={"X-Admin-Secret": admin_secret}).json()
    uid = next(u["id"] for u in users if u["email"] == "todelete@kit.com")

    # Delete
    r = client.delete(f"/admin/users/{uid}", headers={"X-Admin-Secret": admin_secret})
    assert r.status_code == 200

    # Login now fails
    r = client.post("/auth/login", json={"email": "todelete@kit.com", "password": "bye"})
    assert r.status_code == 401


def test_admin_delete_nonexistent(client, admin_secret):
    r = client.delete(
        "/admin/users/00000000-does-not-exist",
        headers={"X-Admin-Secret": admin_secret},
    )
    assert r.status_code == 404
