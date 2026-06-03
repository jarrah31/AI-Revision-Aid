"""Tests for the multi-response detect-and-store service."""
import json
import backend.services.multi_response_service as mrs


def _stub_detect(monkeypatch, results):
    """Stub detect_multiple_response_batch to return `results` aligned to input."""
    def _fn(questions, subject):
        return results, {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}
    monkeypatch.setattr(mrs, "detect_multiple_response_batch", _fn)


def test_detect_and_store_structures_past_paper_questions(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Which two? a b c", answer_text="a; b")
    db_conn.execute("UPDATE questions SET question_source = 'past_paper' WHERE id = ?", (q1,))
    db_conn.commit()

    _stub_detect(monkeypatch, [
        {"select_count": 2, "stem": "Which two?",
         "options": [{"text": "a", "is_correct": True},
                     {"text": "b", "is_correct": True},
                     {"text": "c", "is_correct": False}]},
    ])

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 1

    row = db_conn.execute("SELECT question_text, options_json FROM questions WHERE id = ?", (q1,)).fetchone()
    assert row["question_text"] == "Which two?"
    data = json.loads(row["options_json"])
    assert data["select_count"] == 2
    assert {o["text"] for o in data["options"] if o["is_correct"]} == {"a", "b"}


def test_detect_and_store_leaves_normal_questions_untouched(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Define osmosis.", answer_text="...")
    db_conn.execute("UPDATE questions SET question_source = 'past_paper' WHERE id = ?", (q1,))
    db_conn.commit()

    _stub_detect(monkeypatch, [None])

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 0
    row = db_conn.execute("SELECT question_text, options_json FROM questions WHERE id = ?", (q1,)).fetchone()
    assert row["question_text"] == "Define osmosis."
    assert row["options_json"] is None


def test_detect_and_store_ignores_ai_generated_questions(
    db_conn, regular_user, make_subject, make_batch, make_question, monkeypatch
):
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    make_question(bid, uid, sid, question_text="KO question", answer_text="...")  # default ai_generated

    called = {"n": 0}
    def _fn(questions, subject):
        called["n"] += 1
        return [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    monkeypatch.setattr(mrs, "detect_multiple_response_batch", _fn)

    updated = mrs.detect_and_store_multi_response(bid, "Science", uid, db_conn)
    assert updated == 0
    assert called["n"] == 0  # no past-paper questions → no AI call
