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


def _make_pp_batch_with_questions(db_conn, user_id, subject_id, texts,
                                  exam_board="AQA", exam_year=2023, paper_number="Paper 1"):
    """Insert a past_paper batch (with exam metadata) + its questions. Returns [pp_ids]."""
    cur = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end, status,
            batch_type, exam_board, exam_year, paper_number)
           VALUES (?, ?, 'pp.pdf', 'pp.pdf', 1, 2, 'completed', 'past_paper', ?, ?, ?)""",
        (user_id, subject_id, exam_board, exam_year, paper_number),
    )
    pp_batch = cur.lastrowid
    pp_ids = []
    for t in texts:
        c = db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text, answer_text,
                approved, question_source)
               VALUES (?, ?, ?, 1, ?, 'ans', 1, 'past_paper')""",
            (pp_batch, user_id, subject_id, t),
        )
        pp_ids.append(c.lastrowid)
    db_conn.commit()
    return pp_ids


def _make_ko_question(db_conn, batch_id, user_id, subject_id, text="KO q"):
    c = db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source)
           VALUES (?, ?, ?, 1, ?, 'ko ans', 0, 'ai_generated')""",
        (batch_id, user_id, subject_id, text),
    )
    db_conn.commit()
    return c.lastrowid


def test_blend_keeps_multiple_matches(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(
        db_conn, user_id, sid, ["pp A", "pp B", "pp C"])

    # give the KO question a category so we can assert inheritance
    cat_id = db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, 'Cells')", (sid,)
    ).lastrowid
    db_conn.execute("UPDATE questions SET category_id = ? WHERE id = ?", (cat_id, ko_q))
    db_conn.commit()

    def fake_match(ko_list, pp_list):
        return [{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids]
    monkeypatch.setattr(upload, "match_ko_to_past_papers", fake_match)

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    rows = db_conn.execute(
        "SELECT question_text, question_source, question_source_detail "
        "FROM questions WHERE batch_id = ? ORDER BY id", (ko_batch,)
    ).fetchall()
    assert len(rows) == 3                       # 1 replaced + 2 inserted
    assert all(r["question_source"] == "past_paper" for r in rows)
    assert all(r["question_source_detail"] == "AQA 2023 Paper 1" for r in rows)
    assert {r["question_text"] for r in rows} == {"pp A", "pp B", "pp C"}

    cats = db_conn.execute(
        "SELECT DISTINCT category_id FROM questions WHERE batch_id = ?", (ko_batch,)
    ).fetchall()
    assert [c["category_id"] for c in cats] == [cat_id]


def test_blend_caps_at_three(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(
        db_conn, user_id, sid, ["a", "b", "c", "d", "e"])

    monkeypatch.setattr(upload, "match_ko_to_past_papers",
        lambda k, p: [{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    n = db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id = ?", (ko_batch,)
    ).fetchone()["c"]
    assert n == 3   # capped


def test_blend_single_match_is_replace_only(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(db_conn, user_id, sid, ["only one"])

    monkeypatch.setattr(upload, "match_ko_to_past_papers",
        lambda k, p: [{"ko_question_id": ko_q, "past_paper_question_id": pp_ids[0]}])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    rows = db_conn.execute(
        "SELECT id, question_text, question_source FROM questions WHERE batch_id = ?",
        (ko_batch,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == ko_q                 # same row, replaced in place
    assert rows[0]["question_source"] == "past_paper"
    assert rows[0]["question_text"] == "only one"


def test_blend_dedupes_pp_across_ko_questions(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_a = _make_ko_question(db_conn, ko_batch, user_id, sid, "KO A")
    ko_b = _make_ko_question(db_conn, ko_batch, user_id, sid, "KO B")
    pp_ids = _make_pp_batch_with_questions(db_conn, user_id, sid, ["shared pp"])

    # Both KO questions claim the same single past-paper question.
    monkeypatch.setattr(upload, "match_ko_to_past_papers", lambda k, p: [
        {"ko_question_id": ko_a, "past_paper_question_id": pp_ids[0]},
        {"ko_question_id": ko_b, "past_paper_question_id": pp_ids[0]},
    ])

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    pp_in_ko = db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id = ? AND question_source = 'past_paper'",
        (ko_batch,)
    ).fetchone()["c"]
    assert pp_in_ko == 1   # used once; the second KO question keeps its AI question


def test_matching_prompt_supports_multiple_and_formats():
    from backend.prompts.matching import MATCHING_PROMPT
    # Format-string integrity: both placeholders must survive and no stray braces.
    rendered = MATCHING_PROMPT.format(ko_list="[]", pp_list="[]")
    assert "[]" in rendered
    # Must instruct multiple matches per KO point, capped at 3.
    assert "up to 3" in MATCHING_PROMPT
    lowered = MATCHING_PROMPT.lower()
    assert "different way" in lowered
