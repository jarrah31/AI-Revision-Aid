"""Tests for /api/questions endpoints."""
import pytest


# ── List / filter ─────────────────────────────────────────────────────────────

def test_list_questions_paginated(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    for i in range(5):
        make_question(bid, uid, sid, question_text=f"Q{i}?", answer_text=f"A{i}.")

    r = client.get("/api/questions?page=1&limit=3", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert "questions" in data
    assert "total" in data
    assert data["total"] == 5
    assert len(data["questions"]) == 3
    assert data["page"] == 1
    assert data["limit"] == 3


def test_list_questions_filter_by_subject(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid1 = make_subject("Biology")
    sid2 = make_subject("Chemistry")
    bid1 = make_batch(uid, sid1)
    bid2 = make_batch(uid, sid2)
    make_question(bid1, uid, sid1)
    make_question(bid2, uid, sid2, question_text="Chem Q?", answer_text="Chem A.")

    r = client.get(f"/api/questions?subject_id={sid1}", headers=user_headers)
    data = r.json()
    assert data["total"] == 1
    assert data["questions"][0]["subject_id"] == sid1


def test_list_questions_filter_by_approved(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, approved=1)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.", approved=0)

    r = client.get("/api/questions?approved=0", headers=user_headers)
    data = r.json()
    assert data["total"] == 1
    assert data["questions"][0]["approved"] == 0


def test_list_questions_user_isolation(
    client, regular_user, second_user, make_subject, make_batch, make_question, db_conn
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()
    bid1 = make_batch(uid1, sid)
    bid2 = make_batch(uid2, sid)
    make_question(bid1, uid1, sid)
    make_question(bid2, uid2, sid, question_text="User2 Q?", answer_text="User2 A.")

    r = client.get("/api/questions", headers={"Authorization": f"Bearer {token1}"})
    data = r.json()
    assert data["total"] == 1
    assert data["questions"][0]["user_id"] == uid1


# ── Get single question ───────────────────────────────────────────────────────

def test_get_question(client, user_headers, regular_user, make_subject, make_batch, make_question):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.get(f"/api/questions/{qid}", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["id"] == qid


def test_get_question_other_user_returns_404(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid)
    qid = make_question(bid, uid2, sid)

    uid1, token1 = regular_user
    r = client.get(f"/api/questions/{qid}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

def test_update_question_text(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.put(
        f"/api/questions/{qid}",
        json={"question_text": "Updated question?", "answer_text": "Updated answer."},
        headers=user_headers,
    )
    assert r.status_code == 200

    q = client.get(f"/api/questions/{qid}", headers=user_headers).json()
    assert q["question_text"] == "Updated question?"
    assert q["answer_text"] == "Updated answer."


def test_update_question_no_fields_returns_400(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.put(f"/api/questions/{qid}", json={}, headers=user_headers)
    assert r.status_code == 400


# ── Approve ───────────────────────────────────────────────────────────────────

def test_approve_single_question(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid, approved=0)

    r = client.post(f"/api/questions/{qid}/approve", headers=user_headers)
    assert r.status_code == 200

    row = db_conn.execute("SELECT approved FROM questions WHERE id = ?", (qid,)).fetchone()
    assert row["approved"] == 1


def test_approve_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid1 = make_question(bid, uid, sid, approved=0)
    qid2 = make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.", approved=0)
    make_question(bid, uid, sid, question_text="Q3?", answer_text="A3.", approved=1)

    r = client.post(f"/api/questions/approve-batch?batch_id={bid}", headers=user_headers)
    assert r.status_code == 200
    assert "2" in r.json()["message"]

    for qid in (qid1, qid2):
        row = db_conn.execute("SELECT approved FROM questions WHERE id = ?", (qid,)).fetchone()
        assert row["approved"] == 1


def test_approve_page(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid_p1 = make_question(bid, uid, sid, approved=0, page_number=1)
    qid_p2 = make_question(bid, uid, sid, question_text="P2Q?", answer_text="P2A.",
                           approved=0, page_number=2)

    r = client.post(
        f"/api/questions/approve-page?batch_id={bid}&page_number=1",
        headers=user_headers,
    )
    assert r.status_code == 200
    assert "1" in r.json()["message"]

    row_p1 = db_conn.execute("SELECT approved FROM questions WHERE id = ?", (qid_p1,)).fetchone()
    row_p2 = db_conn.execute("SELECT approved FROM questions WHERE id = ?", (qid_p2,)).fetchone()
    assert row_p1["approved"] == 1
    assert row_p2["approved"] == 0  # page 2 was not approved


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_question(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)

    r = client.delete(f"/api/questions/{qid}", headers=user_headers)
    assert r.status_code == 200

    row = db_conn.execute("SELECT id FROM questions WHERE id = ?", (qid,)).fetchone()
    assert row is None


def test_delete_question_other_user_returns_404(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid)
    qid = make_question(bid, uid2, sid)

    uid1, token1 = regular_user
    r = client.delete(f"/api/questions/{qid}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ── Export ────────────────────────────────────────────────────────────────────

def test_export_questions_returns_approved_only(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, approved=1)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.", approved=0)

    r = client.get("/api/questions/export", headers=user_headers)
    assert r.status_code == 200
    questions = r.json()
    assert len(questions) == 1
    assert questions[0]["approved"] == 1
