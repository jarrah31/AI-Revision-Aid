"""Tests for past-paper upload processing: matcher corpus + figure capture."""
import sqlite3

import backend.routers.upload as upload


def _set_past_paper(db_conn, batch_id):
    """Flip a batch's type to past_paper (make_batch defaults to knowledge_organiser)."""
    db_conn.execute(
        "UPDATE upload_batches SET batch_type = 'past_paper' WHERE id = ?", (batch_id,)
    )
    db_conn.commit()


def test_matcher_uses_full_corpus_not_just_100(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """The matcher must consider every past-paper question, not just the newest 100."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()

    ko_batch = make_batch(user_id, subject_id)   # default batch_type = knowledge_organiser
    pp_batch = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, pp_batch)

    # One AI-generated KO question
    db_conn.execute(
        """INSERT INTO questions (batch_id, user_id, subject_id, page_number,
           question_text, answer_text, question_source)
           VALUES (?, ?, ?, 1, 'KO q', 'KO a', 'ai_generated')""",
        (ko_batch, user_id, subject_id),
    )
    # 150 past-paper questions
    for i in range(150):
        db_conn.execute(
            """INSERT INTO questions (batch_id, user_id, subject_id, page_number,
               question_text, answer_text, question_source)
               VALUES (?, ?, ?, 1, ?, ?, 'past_paper')""",
            (pp_batch, user_id, subject_id, f"PP q{i}", f"PP a{i}"),
        )
    db_conn.commit()

    captured = {}

    def fake_match(ko_list, pp_list):
        captured["pp_count"] = len(pp_list)
        return []

    monkeypatch.setattr(upload, "match_ko_to_past_papers", fake_match)

    upload._match_and_replace_with_past_papers(ko_batch, user_id, subject_id, db_conn)

    assert "pp_count" in captured, "matcher was never called"
    assert captured["pp_count"] == 150
