"""Tests for /api/admin endpoints."""
import pytest


# ── Access control ────────────────────────────────────────────────────────────

def test_non_admin_cannot_access_admin_routes(client, user_headers):
    r = client.get("/api/admin/users", headers=user_headers)
    assert r.status_code == 403


def test_unauthenticated_cannot_access_admin_routes(client):
    r = client.get("/api/admin/users")
    assert r.status_code in (401, 403)


# ── System stats ──────────────────────────────────────────────────────────────

def test_stats_returns_expected_keys(client, admin_headers):
    r = client.get("/api/admin/stats", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    for key in ("total_users", "total_questions", "approved_questions",
                "total_batches", "total_quiz_sessions", "total_quiz_answers",
                "total_cost_usd"):
        assert key in data


def test_stats_counts_are_correct(
    client, admin_headers, admin_user, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, approved=1)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.", approved=0)

    r = client.get("/api/admin/stats", headers=admin_headers)
    data = r.json()
    assert data["total_users"] == 2       # admin + regular
    assert data["total_questions"] == 2
    assert data["approved_questions"] == 1


# ── User management ───────────────────────────────────────────────────────────

def test_list_users(client, admin_headers, admin_user, regular_user):
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200
    users = r.json()
    assert len(users) == 2
    usernames = {u["username"] for u in users}
    assert "admin_u" in usernames
    assert "student1" in usernames


def test_list_users_includes_question_count(
    client, admin_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.")

    r = client.get("/api/admin/users", headers=admin_headers)
    student = next(u for u in r.json() if u["username"] == "student1")
    assert student["question_count"] == 2


def test_update_user(client, admin_headers, regular_user, db_conn):
    uid, _ = regular_user
    r = client.put(
        f"/api/admin/users/{uid}",
        json={"display_name": "Updated Name", "year_group": 12},
        headers=admin_headers,
    )
    assert r.status_code == 200

    row = db_conn.execute(
        "SELECT display_name, year_group FROM users WHERE id = ?", (uid,)
    ).fetchone()
    assert row["display_name"] == "Updated Name"
    assert row["year_group"] == 12


def test_update_user_promote_to_admin(client, admin_headers, regular_user, db_conn):
    uid, _ = regular_user
    r = client.put(
        f"/api/admin/users/{uid}",
        json={"is_admin": 1},
        headers=admin_headers,
    )
    assert r.status_code == 200

    row = db_conn.execute("SELECT is_admin FROM users WHERE id = ?", (uid,)).fetchone()
    assert row["is_admin"] == 1


def test_update_user_not_found(client, admin_headers):
    r = client.put("/api/admin/users/9999", json={"display_name": "X"}, headers=admin_headers)
    assert r.status_code == 404


def test_delete_user(client, admin_headers, regular_user, db_conn):
    uid, _ = regular_user
    r = client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 200

    row = db_conn.execute("SELECT id FROM users WHERE id = ?", (uid,)).fetchone()
    assert row is None


def test_cannot_delete_self(client, admin_headers, admin_user):
    uid, _ = admin_user
    r = client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 400


# ── Batch / content management ────────────────────────────────────────────────

def test_list_all_batches(
    client, admin_headers, regular_user, make_subject, make_batch
):
    uid, _ = regular_user
    sid = make_subject()
    make_batch(uid, sid)
    make_batch(uid, sid, filename="second.pdf")

    r = client.get("/api/admin/batches", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_get_batch_questions(
    client, admin_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)

    r = client.get(f"/api/admin/batches/{bid}/questions", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert "batch" in data
    assert "questions" in data
    assert len(data["questions"]) == 1


def test_admin_update_question(
    client, admin_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.put(
        f"/api/admin/questions/{qid}",
        json={"question_text": "Admin edited Q?", "answer_text": "Admin A."},
        headers=admin_headers,
    )
    assert r.status_code == 200

    row = db_conn.execute(
        "SELECT question_text FROM questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["question_text"] == "Admin edited Q?"


def test_admin_delete_question(
    client, admin_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.delete(f"/api/admin/questions/{qid}", headers=admin_headers)
    assert r.status_code == 200

    row = db_conn.execute("SELECT id FROM questions WHERE id = ?", (qid,)).fetchone()
    assert row is None


def test_admin_delete_batch(
    client, admin_headers, regular_user, make_subject, make_batch, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)

    r = client.delete(f"/api/admin/batches/{bid}", headers=admin_headers)
    assert r.status_code == 200

    row = db_conn.execute("SELECT id FROM upload_batches WHERE id = ?", (bid,)).fetchone()
    assert row is None


def test_admin_toggle_sharing(
    client, admin_headers, regular_user, make_subject, make_batch, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid, is_shared=0)

    # Toggle on
    r = client.put(f"/api/admin/batches/{bid}/sharing", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["is_shared"] is True

    # Toggle off
    r2 = client.put(f"/api/admin/batches/{bid}/sharing", headers=admin_headers)
    assert r2.json()["is_shared"] is False


# ── Settings management ───────────────────────────────────────────────────────

def test_get_settings_returns_keys(client, admin_headers):
    r = client.get("/api/admin/settings", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert "jwt_secret" in data
    assert "anthropic_api_key" in data


def test_get_settings_masks_api_key(client, admin_headers):
    """The API key value should be returned masked."""
    r = client.get("/api/admin/settings", headers=admin_headers)
    assert r.status_code == 200
    # masked field should contain bullet characters
    masked = r.json()["anthropic_api_key"]["masked"]
    assert "•" in masked or len(masked) == 0


def test_update_setting_anthropic_key(client, admin_headers, db_conn):
    r = client.put(
        "/api/admin/settings/anthropic_api_key",
        json={"value": "sk-ant-testkey12345"},
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["key"] == "anthropic_api_key"

    row = db_conn.execute(
        "SELECT value FROM settings WHERE key = 'anthropic_api_key'"
    ).fetchone()
    assert row["value"] == "sk-ant-testkey12345"


def test_update_setting_jwt_secret(client, admin_headers, db_conn):
    new_secret = "a" * 64
    r = client.put(
        "/api/admin/settings/jwt_secret",
        json={"value": new_secret},
        headers=admin_headers,
    )
    assert r.status_code == 200

    row = db_conn.execute(
        "SELECT value FROM settings WHERE key = 'jwt_secret'"
    ).fetchone()
    assert row["value"] == new_secret


def test_update_unknown_setting_returns_400(client, admin_headers):
    r = client.put(
        "/api/admin/settings/evil_key",
        json={"value": "something"},
        headers=admin_headers,
    )
    assert r.status_code == 400


def test_update_setting_empty_value_returns_400(client, admin_headers):
    r = client.put(
        "/api/admin/settings/anthropic_api_key",
        json={"value": "   "},
        headers=admin_headers,
    )
    assert r.status_code == 400
