"""Tests for /api/categories endpoints."""
import pytest


def test_create_category_returns_201(client, user_headers, make_subject):
    sid = make_subject("Biology")
    r = client.post(
        "/api/categories",
        json={"subject_id": sid, "name": "Cell Biology"},
        headers=user_headers,
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Cell Biology"
    assert data["subject_id"] == sid
    assert "id" in data


def test_list_categories_for_subject(client, user_headers, make_subject, db_conn):
    sid = make_subject("Biology")
    db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (sid, "Cell Biology")
    )
    db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (sid, "Genetics")
    )
    db_conn.commit()

    r = client.get(f"/api/categories?subject_id={sid}", headers=user_headers)
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert names == {"Cell Biology", "Genetics"}


def test_list_categories_requires_subject_id(client, user_headers):
    r = client.get("/api/categories", headers=user_headers)
    assert r.status_code == 422  # missing required query param


def test_create_duplicate_category_returns_400(client, user_headers, make_subject):
    sid = make_subject("Biology")
    client.post(
        "/api/categories",
        json={"subject_id": sid, "name": "Cell Biology"},
        headers=user_headers,
    )
    r = client.post(
        "/api/categories",
        json={"subject_id": sid, "name": "Cell Biology"},
        headers=user_headers,
    )
    assert r.status_code == 400


def test_assign_page_category(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid, page_number=1)

    # Create a category
    cat_r = client.post(
        "/api/categories",
        json={"subject_id": sid, "name": "Genetics"},
        headers=user_headers,
    )
    cat_id = cat_r.json()["id"]

    r = client.post(
        "/api/categories/assign-page",
        json={"batch_id": bid, "page_number": 1, "category_id": cat_id},
        headers=user_headers,
    )
    assert r.status_code == 200

    # Verify the question was assigned the category
    row = db_conn.execute(
        "SELECT category_id FROM questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["category_id"] == cat_id


def test_assign_page_category_clear(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """Passing category_id=None should clear the category."""
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid, page_number=1)

    r = client.post(
        "/api/categories/assign-page",
        json={"batch_id": bid, "page_number": 1, "category_id": None},
        headers=user_headers,
    )
    assert r.status_code == 200

    row = db_conn.execute(
        "SELECT category_id FROM questions WHERE id = ?", (qid,)
    ).fetchone()
    assert row["category_id"] is None
