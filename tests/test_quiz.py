"""Tests for /api/quiz endpoints."""
import pytest
import json as _json
from datetime import date


# ── Quiz start ────────────────────────────────────────────────────────────────

def test_start_quiz_no_cards_returns_empty(client, user_headers):
    r = client.post("/api/quiz/start", json={}, headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] is None
    assert data["questions"] == []


def test_start_quiz_with_new_cards(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid)  # No SRS card → "new card"

    r = client.post("/api/quiz/start", json={}, headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] is not None
    assert len(data["questions"]) == 1


def test_start_quiz_with_overdue_srs_cards(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_srs_card
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    make_srs_card(uid, qid, next_review_date="2000-01-01")  # far in the past

    r = client.post("/api/quiz/start", json={}, headers=user_headers)
    assert r.status_code == 200
    assert r.json()["session_id"] is not None
    assert len(r.json()["questions"]) == 1


def test_start_quiz_respects_count_limit(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    for i in range(10):
        make_question(bid, uid, sid, question_text=f"Q{i}?", answer_text=f"A{i}.")

    r = client.post("/api/quiz/start", json={"count": 3}, headers=user_headers)
    assert r.status_code == 200
    # New cards are capped at 5 per session (quiz start logic), count limits further
    assert len(r.json()["questions"]) <= 3


def test_start_quiz_filters_by_subject(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid1 = make_subject("Biology")
    sid2 = make_subject("Chemistry")
    bid1 = make_batch(uid, sid1)
    bid2 = make_batch(uid, sid2)
    make_question(bid1, uid, sid1)
    make_question(bid2, uid, sid2, question_text="Chem Q?", answer_text="Chem A.")

    r = client.post("/api/quiz/start", json={"subject_id": sid1}, headers=user_headers)
    assert r.status_code == 200
    questions = r.json()["questions"]
    assert all(q["subject_id"] == sid1 for q in questions)


def test_start_quiz_excludes_unapproved(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, approved=0)

    r = client.post("/api/quiz/start", json={}, headers=user_headers)
    assert r.status_code == 200
    assert r.json()["session_id"] is None
    assert r.json()["questions"] == []


# ── Flashcard answer ──────────────────────────────────────────────────────────

def _create_session(client, user_headers, uid, make_subject, make_batch, make_question,
                    make_srs_card=None):
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    if make_srs_card:
        make_srs_card(uid, qid, next_review_date="2000-01-01")
    quiz_r = client.post("/api/quiz/start", json={}, headers=user_headers)
    data = quiz_r.json()
    return data["session_id"], qid


def test_flashcard_answer_correct(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={
            "question_id": qid,
            "quiz_format": "flashcard",
            "quality_rating": 5,  # perfect
        },
        headers=user_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["is_correct"] is True
    assert "correct_answer" in data


def test_flashcard_answer_incorrect_quality(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 1},
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_correct"] is False


def test_flashcard_answer_creates_srs_card(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 4},
        headers=user_headers,
    )

    row = db_conn.execute(
        "SELECT * FROM srs_cards WHERE user_id = ? AND question_id = ?", (uid, qid)
    ).fetchone()
    assert row is not None
    assert row["repetitions"] == 1


# ── MCQ answer ────────────────────────────────────────────────────────────────

def test_mcq_answer_correct(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_mcq_options
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)
    make_mcq_options(qid, correct="X is Y.")

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={
            "question_id": qid,
            "quiz_format": "mcq",
            "student_answer": "X is Y.",
        },
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_correct"] is True


def test_mcq_answer_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question, make_mcq_options
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)
    make_mcq_options(qid, correct="X is Y.")

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={
            "question_id": qid,
            "quiz_format": "mcq",
            "student_answer": "Option A.",  # wrong
        },
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_correct"] is False


# ── Typed answer ──────────────────────────────────────────────────────────────

def test_typed_answer_correct(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={
            "question_id": qid,
            "quiz_format": "typed",
            "student_answer": "X is Y.",  # exactly matches answer_text
        },
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_correct"] is True


def test_typed_answer_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    r = client.post(
        f"/api/quiz/{session_id}/answer",
        json={
            "question_id": qid,
            "quiz_format": "typed",
            "student_answer": "Completely wrong answer.",
        },
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["is_correct"] is False


# ── Session management ────────────────────────────────────────────────────────

def test_answer_session_not_found(client, user_headers):
    r = client.post(
        "/api/quiz/9999/answer",
        json={"question_id": 1, "quiz_format": "flashcard", "quality_rating": 4},
        headers=user_headers,
    )
    assert r.status_code == 404


def test_complete_session(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    # Answer the question
    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 5},
        headers=user_headers,
    )

    r = client.post(f"/api/quiz/{session_id}/complete", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    assert "correct" in data
    assert "incorrect" in data


def test_get_session_with_answers(
    client, user_headers, regular_user, make_subject, make_batch, make_question
):
    uid, _ = regular_user
    session_id, qid = _create_session(client, user_headers, uid, make_subject, make_batch, make_question)

    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 4},
        headers=user_headers,
    )

    r = client.get(f"/api/quiz/{session_id}", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    assert "session" in data
    assert "answers" in data
    assert len(data["answers"]) == 1


def test_get_session_other_user_returns_404(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user
    uid2, token2 = second_user
    session_id, _ = _create_session(
        client, {"Authorization": f"Bearer {token1}"},
        uid1, make_subject, make_batch, make_question
    )

    r = client.get(f"/api/quiz/{session_id}", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 404


# ── SRS update verification ───────────────────────────────────────────────────

def test_correct_answer_updates_srs_interval(
    client, user_headers, regular_user, make_subject, make_batch,
    make_question, make_srs_card, db_conn
):
    """After a correct flashcard answer (quality >= 3), interval should increase."""
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    make_srs_card(uid, qid, next_review_date="2000-01-01", interval_days=0, repetitions=0)

    session_r = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id = session_r.json()["session_id"]

    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 5},
        headers=user_headers,
    )

    row = db_conn.execute(
        "SELECT interval_days, repetitions FROM srs_cards WHERE user_id = ? AND question_id = ?",
        (uid, qid),
    ).fetchone()
    assert row["repetitions"] == 1
    assert row["interval_days"] == 1  # First correct answer → 1 day


def test_incorrect_answer_resets_srs(
    client, user_headers, regular_user, make_subject, make_batch,
    make_question, make_srs_card, db_conn
):
    """After an incorrect flashcard answer, repetitions should reset to 0."""
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    # Card with established interval
    make_srs_card(uid, qid, next_review_date="2000-01-01", interval_days=10, repetitions=3)

    session_r = client.post("/api/quiz/start", json={}, headers=user_headers)
    session_id = session_r.json()["session_id"]

    client.post(
        f"/api/quiz/{session_id}/answer",
        json={"question_id": qid, "quiz_format": "flashcard", "quality_rating": 1},
        headers=user_headers,
    )

    row = db_conn.execute(
        "SELECT repetitions, interval_days FROM srs_cards WHERE user_id = ? AND question_id = ?",
        (uid, qid),
    ).fetchone()
    assert row["repetitions"] == 0
    assert row["interval_days"] == 1


# ── Multi-response questions ───────────────────────────────────────────────────

def _make_session_with_question(db_conn, uid, qid):
    cur = db_conn.execute(
        """INSERT INTO quiz_sessions
           (user_id, quiz_mode, total_questions, current_index)
           VALUES (?, 'mixed', 1, 0)""",
        (uid,),
    )
    db_conn.commit()
    return cur.lastrowid


def _add_multi_response(db_conn, qid, select_count, options):
    db_conn.execute(
        "UPDATE questions SET options_json = ? WHERE id = ?",
        (_json.dumps({"select_count": select_count, "options": options}), qid),
    )
    db_conn.commit()


def test_multi_response_exact_match_is_correct(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a", "b"]),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["is_correct"] is True
    assert sorted(body["correct_options"]) == ["a", "b"]


def test_multi_response_subset_is_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a"]),
    })
    assert r.json()["is_correct"] is False


def test_multi_response_superset_is_incorrect(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True},
        {"text": "c", "is_correct": False},
    ])
    sess = _make_session_with_question(db_conn, uid, qid)

    r = client.post(f"/api/quiz/{sess}/answer", headers=user_headers, json={
        "question_id": qid, "quiz_format": "multi_response",
        "student_answer": _json.dumps(["a", "b", "c"]),
    })
    assert r.json()["is_correct"] is False


def test_quiz_start_strips_option_correctness(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": False},
    ])

    r = client.post("/api/quiz/start", json={"subject_id": sid}, headers=user_headers)
    assert r.status_code == 200
    q = next(x for x in r.json()["questions"] if x["id"] == qid)
    assert q["multi_response"]["select_count"] == 2
    assert all("is_correct" not in o for o in q["multi_response"]["options"])
    assert "options_json" not in q


def test_resume_preserves_multi_response(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    qid = make_question(bid, uid, sid)
    _add_multi_response(db_conn, qid, 2, [
        {"text": "a", "is_correct": True}, {"text": "b", "is_correct": False},
    ])
    # Start a quiz so questions_json is populated (already-stripped) for this session
    start = client.post("/api/quiz/start", json={"subject_id": sid}, headers=user_headers).json()
    sess = start["session_id"]

    r = client.get(f"/api/quiz/{sess}/resume", headers=user_headers)
    assert r.status_code == 200
    q = next(x for x in r.json()["questions"] if x["id"] == qid)
    assert q["multi_response"] is not None
    assert q["multi_response"]["select_count"] == 2
    assert all("is_correct" not in o for o in q["multi_response"]["options"])


# ── Three-bucket question-source filter (KO / Past Paper / Blended) ───────────

def _set_batch_type(db, batch_id, t):
    db.execute("UPDATE upload_batches SET batch_type = ? WHERE id = ?", (t, batch_id))
    db.commit()


def _blend_fixture(db, uid, sid, make_batch, make_question):
    """KO batch with 2 pure-AI + 1 blended (past_paper-in-KO) question, plus a
    standalone past-paper batch with 2 past_paper questions. Returns the KO batch id."""
    ko = make_batch(uid, sid, filename="booklet.pdf")          # default type = knowledge_organiser
    make_question(ko, uid, sid, question_text="KO 1", question_source="ai_generated")
    make_question(ko, uid, sid, question_text="KO 2", question_source="ai_generated")
    make_question(ko, uid, sid, question_text="blended exam", question_source="past_paper")

    pp = make_batch(uid, sid, filename="exam.pdf")
    _set_batch_type(db, pp, "past_paper")
    make_question(pp, uid, sid, question_text="standalone 1", question_source="past_paper")
    make_question(pp, uid, sid, question_text="standalone 2", question_source="past_paper")
    return ko, pp


def test_count_source_buckets_are_distinct(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    def count(*sources):
        qs = "".join(f"&question_sources={s}" for s in sources)
        r = client.get(f"/api/quiz/count?subject_id={sid}{qs}", headers=user_headers)
        assert r.status_code == 200
        return r.json()["count"]

    assert count() == 5                                  # no filter = everything
    assert count("ai_generated") == 2                    # pure KO
    assert count("past_paper") == 2                      # standalone only (NOT blended)
    assert count("blended") == 1                         # exam-in-KO only
    assert count("past_paper", "blended") == 3           # all exam questions
    assert count("ai_generated", "blended") == 3


def test_start_quiz_blended_only(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "question_sources": ["blended"], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    qs = r.json()["questions"]
    assert len(qs) == 1
    assert qs[0]["question_text"] == "blended exam"


def test_sources_endpoint_lists_provenance(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    ko, pp = _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    r = client.get(f"/api/quiz/sources?subject_id={sid}", headers=user_headers)
    assert r.status_code == 200
    by_id = {row["batch_id"]: row for row in r.json()}

    assert by_id[ko]["batch_type"] == "knowledge_organiser"
    assert by_id[ko]["question_count"] == 3
    assert by_id[ko]["past_paper_count"] == 1            # the blended one
    assert by_id[pp]["batch_type"] == "past_paper"
    assert by_id[pp]["question_count"] == 2
    assert by_id[pp]["past_paper_count"] == 2


def test_sources_endpoint_excludes_unapproved(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    b = make_batch(uid, sid)
    make_question(b, uid, sid, approved=1)
    make_question(b, uid, sid, approved=0)              # should not be counted

    r = client.get(f"/api/quiz/sources?subject_id={sid}", headers=user_headers)
    assert r.status_code == 200
    assert r.json()[0]["question_count"] == 1
