"""Tests for /api/subjects endpoints."""
import pytest


def test_create_subject_returns_201(client, user_headers):
    r = client.post(
        "/api/subjects",
        json={"name": "Biology", "icon": "🧬", "color": "#22c55e"},
        headers=user_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Biology"
    assert "id" in data


def test_create_subject_requires_auth(client):
    r = client.post("/api/subjects", json={"name": "Biology"})
    assert r.status_code in (401, 403)


def test_list_subjects_returns_all(client, user_headers, make_subject):
    make_subject("Biology")
    make_subject("Chemistry")
    r = client.get("/api/subjects", headers=user_headers)
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert {"Biology", "Chemistry"} <= names


def test_list_subjects_includes_question_count(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject("Biology")
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.")

    r = client.get("/api/subjects", headers=user_headers)
    subj = next(s for s in r.json() if s["name"] == "Biology")
    assert subj["question_count"] == 2


def test_update_subject_name(client, user_headers, make_subject):
    sid = make_subject("Biology")
    r = client.put(f"/api/subjects/{sid}", json={"name": "Advanced Biology"}, headers=user_headers)
    assert r.status_code == 200

    subjects = client.get("/api/subjects", headers=user_headers).json()
    names = {s["name"] for s in subjects}
    assert "Advanced Biology" in names
    assert "Biology" not in names


def test_update_subject_not_found(client, user_headers):
    r = client.put("/api/subjects/9999", json={"name": "X"}, headers=user_headers)
    assert r.status_code == 404


def test_update_subject_no_fields_returns_400(client, user_headers, make_subject):
    sid = make_subject()
    r = client.put(f"/api/subjects/{sid}", json={}, headers=user_headers)
    assert r.status_code == 400


def test_delete_subject(client, user_headers, make_subject):
    sid = make_subject("Biology")
    r = client.delete(f"/api/subjects/{sid}", headers=user_headers)
    assert r.status_code == 200

    subjects = client.get("/api/subjects", headers=user_headers).json()
    assert not any(s["name"] == "Biology" for s in subjects)


def test_delete_subject_not_found(client, user_headers):
    r = client.delete("/api/subjects/9999", headers=user_headers)
    assert r.status_code == 404


def test_create_duplicate_subject_returns_400(client, user_headers):
    client.post("/api/subjects", json={"name": "Biology"}, headers=user_headers)
    r = client.post("/api/subjects", json={"name": "Biology"}, headers=user_headers)
    assert r.status_code == 400
