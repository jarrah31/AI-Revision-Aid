"""Unit tests for backend.services.mcq_service.ensure_mcq_options."""
import backend.services.mcq_service as mcq_service


def _q(qid, source="ai_generated"):
    return {
        "id": qid,
        "question_text": f"Q{qid}",
        "answer_text": f"A{qid}",
        "subject_name": "Science",
        "question_source": source,
    }


def test_ensure_mcq_skips_past_paper_questions(db_conn, regular_user, monkeypatch):
    """Past-paper questions are real exam Q&A and must not trigger MCQ generation."""
    uid, _ = regular_user
    captured = {"ids": None}

    def fake_generate(questions, subject):
        captured["ids"] = [q["id"] for q in questions]
        return ([], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})

    monkeypatch.setattr(mcq_service, "generate_mcq_distractors", fake_generate)

    questions = [_q(101, "ai_generated"), _q(102, "past_paper")]
    mcq_service.ensure_mcq_options(questions, db_conn, uid)

    # Only the ai_generated question is sent to the distractor generator
    assert captured["ids"] == [101]


def test_ensure_mcq_all_past_paper_makes_no_api_call(db_conn, regular_user, monkeypatch):
    """If every question is a past paper, the generator is never called at all."""
    uid, _ = regular_user
    called = {"count": 0}

    def fake_generate(questions, subject):
        called["count"] += 1
        return ([], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})

    monkeypatch.setattr(mcq_service, "generate_mcq_distractors", fake_generate)

    questions = [_q(201, "past_paper"), _q(202, "past_paper")]
    mcq_service.ensure_mcq_options(questions, db_conn, uid)

    assert called["count"] == 0
