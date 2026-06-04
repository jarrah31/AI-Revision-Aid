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


def test_past_paper_upload_captures_figure(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """A past-paper diagram question should get its figure cropped and linked via image_id."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, batch_id)

    # Stub the Claude extraction: one diagram-based question + one figure region
    extraction_result = {
        "page_type": "questions",
        "questions": [
            {
                "question_ref": "1a",
                "question": "Label structure X in the diagram.",
                "answer": "Nucleus",
                "marks": 1,
                "type": "diagram-based",
                "difficulty": 1,
                "related_image_index": 0,
            }
        ],
        "answers": [],
        "images": [
            {
                "description": "Cell diagram",
                "bbox_x_pct": 10.0,
                "bbox_y_pct": 20.0,
                "bbox_w_pct": 40.0,
                "bbox_h_pct": 30.0,
            }
        ],
    }
    usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0}

    monkeypatch.setattr(
        upload, "extract_qa_from_past_paper", lambda b64, subj: (extraction_result, usage)
    )
    monkeypatch.setattr(upload, "render_page_to_png", lambda path, n: b"fakepng")
    monkeypatch.setattr(upload, "save_full_page_image", lambda *a, **kw: "full.png")
    monkeypatch.setattr(
        upload,
        "crop_image_region",
        lambda *a, **kw: ("batch_x/page_1_img_0.png", 120, 90),
    )

    upload.process_batch(
        batch_id=batch_id,
        pdf_path="ignored.pdf",
        subject_name="Biology",
        subject_id=subject_id,
        user_id=user_id,
        page_start=1,
        page_end=1,
        batch_type="past_paper",
    )

    # process_batch wrote via its own connection; read back with a fresh one
    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    images = conn.execute(
        "SELECT * FROM images WHERE batch_id = ?", (batch_id,)
    ).fetchall()
    question = conn.execute(
        "SELECT * FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert len(images) == 1
    assert images[0]["filename"] == "batch_x/page_1_img_0.png"
    assert question["image_id"] == images[0]["id"]
    assert question["question_source"] == "past_paper"


def test_past_paper_question_without_figure_has_no_image(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """A non-diagram past-paper question yields no images row and a NULL image_id."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, batch_id)

    extraction_result = {
        "page_type": "questions",
        "questions": [
            {
                "question_ref": "2a",
                "question": "Define osmosis.",
                "answer": "Net movement of water...",
                "marks": 2,
                "type": "definition",
                "difficulty": 1,
                "related_image_index": None,
            }
        ],
        "answers": [],
        "images": [],
    }
    usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0}

    monkeypatch.setattr(
        upload, "extract_qa_from_past_paper", lambda b64, subj: (extraction_result, usage)
    )
    monkeypatch.setattr(upload, "render_page_to_png", lambda path, n: b"fakepng")
    monkeypatch.setattr(upload, "save_full_page_image", lambda *a, **kw: "full.png")
    monkeypatch.setattr(
        upload, "crop_image_region", lambda *a, **kw: ("never.png", 1, 1)
    )

    upload.process_batch(
        batch_id=batch_id,
        pdf_path="ignored.pdf",
        subject_name="Biology",
        subject_id=subject_id,
        user_id=user_id,
        page_start=1,
        page_end=1,
        batch_type="past_paper",
    )

    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    images = conn.execute(
        "SELECT * FROM images WHERE batch_id = ?", (batch_id,)
    ).fetchall()
    question = conn.execute(
        "SELECT * FROM questions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    conn.close()

    assert len(images) == 0
    assert question["image_id"] is None


def test_matching_prompt_supports_multiple_and_formats():
    from backend.prompts.matching import MATCHING_PROMPT
    # Format-string integrity: both placeholders must survive and no stray braces.
    rendered = MATCHING_PROMPT.format(ko_list="[]", pp_list="[]")
    assert "[]" in rendered
    # Must instruct multiple matches per KO point, capped at 3.
    assert "up to 3" in MATCHING_PROMPT
    lowered = MATCHING_PROMPT.lower()
    assert "different way" in lowered
