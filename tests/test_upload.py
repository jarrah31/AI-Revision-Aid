"""Tests for past-paper upload processing: matcher corpus + figure capture."""
import sqlite3

import backend.routers.upload as upload

# Stub usage dict returned by mocked match_ko_to_past_papers (signature now
# returns (matches, usage)).
_MATCH_USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": "claude-haiku-4-5"}


def test_normalise_ref_preserves_legacy_behaviour():
    f = upload._normalise_ref
    assert f("1 (a)") == "1a"
    assert f("2b.") == "2b"
    assert f("Question 3") == "3"
    assert f("") == ""
    assert f(None) is None


def test_normalise_ref_strips_zero_padding_so_qp_and_ms_join():
    """Real failure observed on AQA JUN22: a model zero-pads the question paper
    ('02.1') but not the mark scheme ('2.1'). After normalisation these MUST
    collapse to the same key, or the answer silently fails to attach."""
    f = upload._normalise_ref
    assert f("02.1") == f("2.1") == "21"
    assert f("04.6") == f("4.6") == "46"
    assert f("01.1") == f("1.1") == "11"
    # zero-padding with a letter sub-part
    assert f("01a") == f("1a") == "1a"
    # multi-digit question numbers keep their internal digits
    assert f("10.1") == "101"


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
        return [], _MATCH_USAGE

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


def test_past_paper_question_with_null_answer_stored_as_empty(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """Regression: a question-paper question with an explicit null answer (no mark
    scheme yet) must store answer_text as '' rather than crashing the NOT NULL
    constraint and dropping the page's questions."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _set_past_paper(db_conn, batch_id)

    extraction_result = {
        "page_type": "questions",
        "questions": [
            {"question_ref": "5a", "question": "State two functions of the liver.",
             "answer": None, "type": None, "difficulty": None,
             "related_image_index": None},
        ],
        "answers": [],
        "images": [],
    }
    usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0, "model": "claude-haiku-4-5"}
    monkeypatch.setattr(upload, "extract_qa_from_past_paper", lambda b64, subj: (extraction_result, usage))
    monkeypatch.setattr(upload, "render_page_to_png", lambda path, n: b"fakepng")
    monkeypatch.setattr(upload, "save_full_page_image", lambda *a, **kw: "full.png")

    upload.process_batch(
        batch_id=batch_id, pdf_path="ignored.pdf", subject_name="Biology",
        subject_id=subject_id, user_id=user_id, page_start=1, page_end=1,
        batch_type="past_paper",
    )

    conn = sqlite3.connect(str(isolated_db))
    conn.row_factory = sqlite3.Row
    q = conn.execute("SELECT * FROM questions WHERE batch_id = ?", (batch_id,)).fetchone()
    err = conn.execute("SELECT error_message FROM upload_batches WHERE id = ?", (batch_id,)).fetchone()
    conn.close()

    assert q is not None                       # the question was stored, not dropped
    assert q["answer_text"] == ""              # null coerced to empty string
    assert q["question_text"] == "State two functions of the liver."
    assert q["question_type"] == "factual"     # null type falls back to default
    assert q["difficulty"] == 1                # null difficulty falls back to default
    assert err["error_message"] is None        # no NOT NULL crash recorded


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
        return [{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids], _MATCH_USAGE
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
        lambda k, p: ([{"ko_question_id": ko_q, "past_paper_question_id": pid} for pid in pp_ids], _MATCH_USAGE))

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
        lambda k, p: ([{"ko_question_id": ko_q, "past_paper_question_id": pp_ids[0]}], _MATCH_USAGE))

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
    monkeypatch.setattr(upload, "match_ko_to_past_papers", lambda k, p: ([
        {"ko_question_id": ko_a, "past_paper_question_id": pp_ids[0]},
        {"ko_question_id": ko_b, "past_paper_question_id": pp_ids[0]},
    ], _MATCH_USAGE))

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    pp_in_ko = db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id = ? AND question_source = 'past_paper'",
        (ko_batch,)
    ).fetchone()["c"]
    assert pp_in_ko == 1   # used once; the second KO question keeps its AI question


def test_blend_logs_matching_cost(
    isolated_db, db_conn, regular_user, make_subject, make_batch, monkeypatch
):
    """The matcher's API call is recorded against the batch as a 'ko_matching'
    api_usage row (with its model) and added to the batch cost."""
    monkeypatch.setattr(upload, "DB_PATH", isolated_db)
    user_id, _ = regular_user
    sid = make_subject()
    ko_batch = make_batch(user_id, sid)
    ko_q = _make_ko_question(db_conn, ko_batch, user_id, sid)
    pp_ids = _make_pp_batch_with_questions(db_conn, user_id, sid, ["only one"])

    usage = {"input_tokens": 120, "output_tokens": 30, "cost_usd": 0.0042, "model": "claude-haiku-4-5"}
    monkeypatch.setattr(upload, "match_ko_to_past_papers",
        lambda k, p: ([{"ko_question_id": ko_q, "past_paper_question_id": pp_ids[0]}], usage))

    upload._match_and_replace_with_past_papers(ko_batch, user_id, sid, db_conn)

    row = db_conn.execute(
        "SELECT model, input_tokens, output_tokens, cost_usd FROM api_usage "
        "WHERE batch_id = ? AND call_type = 'ko_matching'", (ko_batch,)
    ).fetchone()
    assert row is not None
    assert row["model"] == "claude-haiku-4-5"
    assert row["input_tokens"] == 120
    assert abs(row["cost_usd"] - 0.0042) < 1e-9


def test_matching_prompt_supports_multiple_and_formats():
    from backend.prompts.matching import MATCHING_PROMPT
    # Format-string integrity: both placeholders must survive and no stray braces.
    rendered = MATCHING_PROMPT.format(ko_list="[]", pp_list="[]")
    assert "[]" in rendered
    # Must instruct multiple matches per KO point, capped at 3.
    assert "up to 3" in MATCHING_PROMPT
    lowered = MATCHING_PROMPT.lower()
    assert "different way" in lowered


def _df(fid, paper_type, paper_number, tier, board="AQA", year=2024, filename=None):
    """Build a detected-file dict shaped like paper_detection_files rows."""
    return {
        "id": fid, "status": "detected", "paper_type": paper_type,
        "exam_board": board, "exam_year": year,
        "paper_number": paper_number, "tier": tier, "filename": filename,
    }


def test_year_from_filename():
    f = upload._year_from_filename
    assert f("Biology-AQA-84611F-QP-JUN22.PDF") == 2022
    assert f("Biology-AQA-84611F-MS-JUN2022.PDF") == 2022     # 4-digit preferred
    assert f("Chemistry-OCR-Paper2-NOV23.pdf") == 2023
    assert f("Maths-Summer-24-Higher.pdf") == 2024
    assert f("8461_2023_paper1.pdf") == 2023
    assert f("no-year-here.pdf") is None
    assert f(None) is None


def test_compute_matches_pairs_when_qp_year_only_in_filename():
    """Regression: QP cover omits the year (exam_year=None) but the filename says
    JUN22; the MS cover states 2022. They must still pair via the filename year."""
    files = [
        _df(1, "question_paper", "Paper 1F", "Foundation",
            year=None, filename="Biology-AQA-84611F-QP-JUN22.PDF"),
        _df(2, "mark_scheme", "Paper 1", "Foundation",
            year=2022, filename="Biology-AQA-84611F-MS-JUN22.PDF"),
    ]
    matches = upload._compute_matches(files)
    paired = [m for m in matches if m["match_group"] is not None]
    assert len(paired) == 1
    assert paired[0]["qp_id"] == 1
    assert paired[0]["ms_id"] == 2
    assert paired[0]["exam_year"] == 2022    # filename-derived year surfaced for display


def test_compute_matches_filename_year_keeps_years_separate():
    """Filename-derived years must not collapse different years together."""
    files = [
        _df(1, "question_paper", "Paper 1", "Foundation",
            year=None, filename="Bio-QP-JUN22.PDF"),
        _df(2, "mark_scheme", "Paper 1", "Foundation",
            year=2022, filename="Bio-MS-JUN22.PDF"),
        _df(3, "question_paper", "Paper 1", "Foundation",
            year=None, filename="Bio-QP-JUN23.PDF"),
        _df(4, "mark_scheme", "Paper 1", "Foundation",
            year=2023, filename="Bio-MS-JUN23.PDF"),
    ]
    matches = upload._compute_matches(files)
    paired = [m for m in matches if m["match_group"] is not None]
    by_qp = {m["qp_id"]: m["ms_id"] for m in paired}
    assert by_qp[1] == 2     # 2022 QP -> 2022 MS
    assert by_qp[3] == 4     # 2023 QP -> 2023 MS


def test_compute_matches_pairs_qp_1F_with_ms_paper_1():
    """Regression: QP cover yields paper_number 'Paper 1F' (tier folded in) while
    the MS cover yields 'Paper 1'. They are the same paper and must pair."""
    files = [
        _df(1, "question_paper", "Paper 1F", "Foundation"),
        _df(2, "mark_scheme", "Paper 1", "Foundation"),
    ]
    matches = upload._compute_matches(files)
    paired = [m for m in matches if m["match_group"] is not None]
    assert len(paired) == 1
    assert paired[0]["qp_id"] == 1
    assert paired[0]["ms_id"] == 2          # MS must be matched, not skipped


def test_compute_matches_keeps_foundation_and_higher_separate():
    """Tier-letter normalisation must NOT collapse Foundation and Higher papers."""
    files = [
        _df(1, "question_paper", "Paper 1F", "Foundation"),
        _df(2, "mark_scheme", "Paper 1", "Foundation"),
        _df(3, "question_paper", "Paper 1H", "Higher"),
        _df(4, "mark_scheme", "Paper 1", "Higher"),
    ]
    matches = upload._compute_matches(files)
    paired = [m for m in matches if m["match_group"] is not None]
    assert len(paired) == 2
    by_qp = {m["qp_id"]: m["ms_id"] for m in paired}
    assert by_qp[1] == 2   # Foundation QP -> Foundation MS
    assert by_qp[3] == 4   # Higher QP -> Higher MS
