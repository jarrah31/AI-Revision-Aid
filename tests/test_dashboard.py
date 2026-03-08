"""Tests for /api/dashboard endpoints."""
import pytest
from datetime import date


# ── Summary ───────────────────────────────────────────────────────────────────

def test_dashboard_summary_empty(client, user_headers):
    r = client.get("/api/dashboard/summary", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_questions"] == 0
    assert data["total_sessions"] == 0
    assert data["due_today"] == 0
    assert data["accuracy"] == 0
    assert data["subjects"] == []
    assert data["pending_review_batches"] == []


def test_dashboard_summary_with_data(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_srs_card
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid, approved=1)
    make_srs_card(uid, qid, next_review_date=date.today().isoformat())  # due today

    r = client.get("/api/dashboard/summary", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_questions"] == 1
    assert data["due_today"] == 1
    assert len(data["subjects"]) == 1
    assert data["subjects"][0]["question_count"] == 1


def test_dashboard_summary_pending_review_batches(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    """Completed batches with unapproved questions should appear in pending_review_batches."""
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid, status="completed")
    make_question(bid, uid, sid, approved=0)

    r = client.get("/api/dashboard/summary", headers=user_headers)
    data = r.json()
    assert len(data["pending_review_batches"]) == 1
    assert data["pending_review_batches"][0]["id"] == bid
    assert data["pending_review_batches"][0]["pending_count"] == 1


def test_dashboard_summary_accuracy(
    client, user_headers, regular_user, make_subject, make_batch,
    make_question, make_srs_card, db_conn
):
    """Accuracy = correct_answers / total_answers * 100."""
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    make_srs_card(uid, qid, next_review_date="2000-01-01")

    # Start a session and submit 4 answers: 3 correct, 1 incorrect
    session_r = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id = session_r.json()["session_id"]

    for q in [5, 5, 5, 1]:  # quality 5=correct, 1=incorrect
        client.post(
            f"/api/quiz/{session_id}/answer",
            json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": q},
            headers=user_headers,
        )

    r = client.get("/api/dashboard/summary", headers=user_headers)
    data = r.json()
    assert data["total_answers"] == 4
    assert data["correct_answers"] == 3
    assert data["accuracy"] == 75


def test_dashboard_summary_user_isolation(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()

    bid1 = make_batch(uid1, sid)
    bid2 = make_batch(uid2, sid)
    make_question(bid1, uid1, sid)
    # uid2 has 3 questions
    for i in range(3):
        make_question(bid2, uid2, sid, question_text=f"Q{i}?", answer_text=f"A{i}.")

    r = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token1}"})
    data = r.json()
    assert data["total_questions"] == 1  # only uid1's questions


# ── Due cards ─────────────────────────────────────────────────────────────────

def test_due_cards_returns_by_subject(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_srs_card
):
    uid, _ = regular_user
    sid1 = make_subject("Biology")
    sid2 = make_subject("Chemistry")
    bid1 = make_batch(uid, sid1)
    bid2 = make_batch(uid, sid2)
    qid1 = make_question(bid1, uid, sid1)
    qid2 = make_question(bid2, uid, sid2, question_text="Chem Q?", answer_text="Chem A.")

    make_srs_card(uid, qid1, next_review_date=date.today().isoformat())
    make_srs_card(uid, qid2, next_review_date=date.today().isoformat())

    r = client.get("/api/dashboard/due-cards", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    subject_names = {item["subject_name"] for item in data}
    assert {"Biology", "Chemistry"} == subject_names


def test_due_cards_empty_when_none_due(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_srs_card
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    make_srs_card(uid, qid, next_review_date="2099-01-01")  # far in the future

    r = client.get("/api/dashboard/due-cards", headers=user_headers)
    assert r.json() == []


# ── History ───────────────────────────────────────────────────────────────────

def test_quiz_history_returns_sessions(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)
    make_question(bid, uid, sid, question_text="Q2?", answer_text="A2.")

    # Create two quiz sessions
    session_r1 = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id1 = session_r1.json()["session_id"]
    client.post(f"/api/quiz/{session_id1}/complete", headers=user_headers)

    session_r2 = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id2 = session_r2.json()["session_id"]
    client.post(f"/api/quiz/{session_id2}/complete", headers=user_headers)

    r = client.get("/api/dashboard/history", headers=user_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_quiz_history_empty(client, user_headers):
    r = client.get("/api/dashboard/history", headers=user_headers)
    assert r.status_code == 200
    assert r.json() == []


# ── Subject stats ─────────────────────────────────────────────────────────────

def test_subject_stats(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)

    r = client.get(f"/api/dashboard/subject/{sid}", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_questions"] == 1
    assert data["total_answers"] == 0
    assert data["accuracy"] == 0
    assert isinstance(data["weakest_questions"], list)


def test_subject_stats_with_answers(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_srs_card
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    make_srs_card(uid, qid, next_review_date="2000-01-01")

    session_r = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id = session_r.json()["session_id"]
    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 5},
        headers=user_headers,
    )

    r = client.get(f"/api/dashboard/subject/{sid}", headers=user_headers)
    data = r.json()
    assert data["total_answers"] == 1
    assert data["correct_answers"] == 1
    assert data["accuracy"] == 100
