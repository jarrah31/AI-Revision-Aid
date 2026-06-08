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


def test_count_source_buckets(
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
    assert count("ai_generated") == 2                    # pure KO (AI) questions
    assert count("past_paper") == 2                      # standalone only (NOT blended)
    # Blended = the whole blended KO booklet: 2 AI (uncovered) + 1 exam = 3.
    assert count("blended") == 3
    assert count("past_paper", "blended") == 5           # standalone + whole booklet
    assert count("ai_generated", "blended") == 3         # AI is a subset of the booklet


def test_blended_mode_exam_only(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """blended_mode='exam_only' returns just the matched exam questions from a
    blended booklet, not its uncovered-knowledge AI questions."""
    uid, _ = regular_user
    sid = make_subject()
    _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    def count(mode):
        r = client.get(
            f"/api/quiz/count?subject_id={sid}&question_sources=blended&blended_mode={mode}",
            headers=user_headers,
        )
        assert r.status_code == 200
        return r.json()["count"]

    assert count("mixed") == 3          # 2 AI + 1 exam (whole booklet)
    assert count("exam_only") == 1      # just the exam match

    # start honours it too
    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "question_sources": ["blended"],
                          "blended_mode": "exam_only", "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    assert texts == {"blended exam"}


def test_count_blended_zero_logs_diagnostic(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn, caplog
):
    """When a blended selection returns 0, a diagnostic must log WHY — e.g. the
    booklet's questions are unapproved (excluded by approved=1) — so remote logs
    can pinpoint the cause instead of silently returning 'None available'."""
    import logging
    uid, _ = regular_user
    sid = make_subject()
    # A blended booklet (KO batch with a past_paper row) whose questions are all
    # UNAPPROVED → invisible to every quiz query, including blended.
    ko = make_batch(uid, sid, filename="unapproved.pdf")
    make_question(ko, uid, sid, question_source="ai_generated", approved=0)
    make_question(ko, uid, sid, question_source="past_paper", approved=0)

    with caplog.at_level(logging.INFO, logger="backend.routers.quiz"):
        r = client.get(f"/api/quiz/count?subject_id={sid}&question_sources=blended",
                       headers=user_headers)
    assert r.status_code == 200
    assert r.json()["count"] == 0
    diag = [rec.getMessage() for rec in caplog.records
            if rec.name == "backend.routers.quiz"]
    assert any("0 results" in m for m in diag), diag
    # The diagnostic surfaces that matching rows exist but are unapproved.
    assert any("total=2" in m and "approved=0" in m for m in diag), diag


def test_count_blended_nonzero_no_diagnostic(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn, caplog
):
    """The diagnostic only fires on an empty result — a healthy blended count is silent."""
    import logging
    uid, _ = regular_user
    sid = make_subject()
    _blend_fixture(db_conn, uid, sid, make_batch, make_question)
    with caplog.at_level(logging.INFO, logger="backend.routers.quiz"):
        r = client.get(f"/api/quiz/count?subject_id={sid}&question_sources=blended",
                       headers=user_headers)
    assert r.status_code == 200
    assert r.json()["count"] == 3
    assert not [rec for rec in caplog.records if rec.name == "backend.routers.quiz"]


def test_blended_excludes_unblended_ko_batches(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """A KO booklet with NO matched exam questions is not 'blended'."""
    uid, _ = regular_user
    sid = make_subject()
    plain_ko = make_batch(uid, sid, filename="plain.pdf")     # KO, never blended
    make_question(plain_ko, uid, sid, question_source="ai_generated")
    make_question(plain_ko, uid, sid, question_source="ai_generated")

    r = client.get(f"/api/quiz/count?subject_id={sid}&question_sources=blended",
                   headers=user_headers)
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_start_quiz_blended_includes_ai_and_exam(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "question_sources": ["blended"], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    # The booklet's exam match AND its uncovered-knowledge AI questions, but NOT
    # the standalone past papers.
    assert texts == {"blended exam", "KO 1", "KO 2"}


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


# ── In-quiz source provenance pills ──────────────────────────────────────────

def _make_exam_batch(db, uid, sid, make_batch, filename, board="AQA",
                     year=2023, paper="Paper 2", tier="Higher"):
    b = make_batch(uid, sid, filename=filename)
    db.execute(
        "UPDATE upload_batches SET batch_type='past_paper', exam_board=?, "
        "exam_year=?, paper_number=?, tier=? WHERE id=?",
        (board, year, paper, tier, b),
    )
    db.commit()
    return b


def test_start_quiz_attaches_provenance(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    pp = _make_exam_batch(db_conn, uid, sid, make_batch, "AQA-Bio-P2H.pdf")
    make_question(pp, uid, sid, question_source="past_paper", question_text="exam q")
    ko = make_batch(uid, sid, filename="booklet.pdf")
    make_question(ko, uid, sid, question_source="ai_generated", question_text="ai q")

    r = client.post("/api/quiz/start", json={"subject_id": sid, "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    by_text = {q["question_text"]: q["provenance"] for q in r.json()["questions"]}

    assert by_text["ai q"]["source"] == "ai_generated"
    assert "filename" not in by_text["ai q"]

    ex = by_text["exam q"]
    assert ex["source"] == "past_paper"
    assert ex["paper_number"] == "Paper 2"
    assert ex["tier"] == "Higher"
    assert ex["filename"] == "AQA-Bio-P2H.pdf"
    assert ex["exam_board"] == "AQA"
    assert ex["exam_year"] == 2023


def test_start_quiz_provenance_marks_verified_mark_scheme(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """A past-paper question whose answer came from an uploaded mark scheme
    (answer_from_mark_scheme=1) is flagged mark_scheme_verified for the badge;
    an AI-inferred one is not."""
    uid, _ = regular_user
    sid = make_subject()
    pp = _make_exam_batch(db_conn, uid, sid, make_batch, "AQA-Bio-P2H.pdf")
    verified = make_question(pp, uid, sid, question_source="past_paper",
                             question_text="verified exam q")
    make_question(pp, uid, sid, question_source="past_paper",
                  question_text="inferred exam q")
    db_conn.execute(
        "UPDATE questions SET answer_from_mark_scheme = 1 WHERE id = ?", (verified,)
    )
    db_conn.commit()

    r = client.post("/api/quiz/start", json={"subject_id": sid, "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    by_text = {q["question_text"]: q["provenance"] for q in r.json()["questions"]}

    assert by_text["verified exam q"]["mark_scheme_verified"] is True
    assert by_text["inferred exam q"]["mark_scheme_verified"] is False


def test_start_quiz_provenance_blended_uses_source_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """A blended question's pills come from its source exam batch, not the KO
    booklet it physically lives in."""
    uid, _ = regular_user
    sid = make_subject()
    exam = _make_exam_batch(db_conn, uid, sid, make_batch, "exam-paper1F.pdf",
                            paper="Paper 1", tier="Foundation")
    ko = make_batch(uid, sid, filename="booklet.pdf")
    db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source, source_batch_id)
           VALUES (?, ?, ?, 1, 'blended exam q', 'a', 1, 'past_paper', ?)""",
        (ko, uid, sid, exam),
    )
    db_conn.commit()

    r = client.post("/api/quiz/start", json={"subject_id": sid, "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    prov = next(q["provenance"] for q in r.json()["questions"]
                if q["question_text"] == "blended exam q")
    assert prov["source"] == "past_paper"
    assert prov["filename"] == "exam-paper1F.pdf"     # the EXAM batch, not booklet.pdf
    assert prov["paper_number"] == "Paper 1"
    assert prov["tier"] == "Foundation"


def test_provenance_includes_ko_grounding(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """A grounded blended question exposes reasoning + crop URL in provenance;
    a plain past-paper question without grounding does not."""
    uid, _ = regular_user
    sid = make_subject()
    b = make_batch(uid, sid, filename="pp.pdf")
    _set_batch_type(db_conn, b, "past_paper")   # past_paper source filter needs this
    qid = make_question(b, uid, sid, question_source="past_paper")
    db_conn.execute(
        "UPDATE questions SET ko_grounding_reasoning = ?, ko_crop_filename = ? WHERE id = ?",
        ("Organ is defined here.", "batch_1/page_1_kocrop_1_2.png", qid))
    db_conn.commit()

    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "question_sources": ["past_paper"], "count": 5},
                    headers=user_headers)
    assert r.status_code == 200
    q = next(x for x in r.json()["questions"] if x["id"] == qid)
    assert q["provenance"]["ko_reasoning"] == "Organ is defined here."
    assert q["provenance"]["ko_crop_url"] == "/images/batch_1/page_1_kocrop_1_2.png"


# ── Batch-id filter (quiz count scoped to specific uploads) ───────────────────

def test_batch_filter_helper():
    from backend.routers.quiz import _batch_filter
    assert _batch_filter(None) == (None, [])
    assert _batch_filter([]) == (None, [])
    assert _batch_filter([5]) == ("q.batch_id IN (?)", [5])
    assert _batch_filter([5, 9]) == ("q.batch_id IN (?,?)", [5, 9])


def test_count_filters_by_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    uid, _ = regular_user
    sid = make_subject()
    ko, pp = _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    def count(*batch_ids, sources=()):
        qs = "".join(f"&batch_ids={b}" for b in batch_ids)
        qs += "".join(f"&question_sources={s}" for s in sources)
        r = client.get(f"/api/quiz/count?subject_id={sid}{qs}", headers=user_headers)
        assert r.status_code == 200
        return r.json()["count"]

    assert count() == 5                         # no batch filter = everything
    assert count(ko) == 3                        # the KO booklet's 2 AI + 1 blended
    assert count(pp) == 2                        # standalone past-paper batch only
    assert count(ko, pp) == 5                    # union of both
    # composes with the source filter: the KO booklet's matched exam question only
    r = client.get(
        f"/api/quiz/count?subject_id={sid}&batch_ids={ko}"
        f"&question_sources=blended&blended_mode=exam_only",
        headers=user_headers,
    )
    assert r.json()["count"] == 1


def test_start_quiz_filters_by_batch(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn
):
    """Starting a quiz with batch_ids draws questions only from those uploads."""
    uid, _ = regular_user
    sid = make_subject()
    ko, pp = _blend_fixture(db_conn, uid, sid, make_batch, make_question)

    # Restrict to the standalone past-paper batch → only its two questions.
    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "batch_ids": [pp], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    assert texts == {"standalone 1", "standalone 2"}

    # Restrict to the KO booklet → its 3 questions, none from the standalone batch.
    r = client.post("/api/quiz/start",
                    json={"subject_id": sid, "batch_ids": [ko], "count": 20},
                    headers=user_headers)
    assert r.status_code == 200
    texts = {q["question_text"] for q in r.json()["questions"]}
    assert "standalone 1" not in texts and "standalone 2" not in texts
    assert "blended exam" in texts
