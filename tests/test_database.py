"""Schema migration tests."""
from backend import database
from tests.conftest import _insert_user


def test_questions_has_options_json_column(db_conn):
    cols = [r["name"] for r in db_conn.execute("PRAGMA table_info(questions)").fetchall()]
    assert "options_json" in cols


def _subject(db, name="Biology"):
    return db.execute(
        "INSERT INTO subjects (name, icon, color) VALUES (?, '🧬', '#000')", (name,)
    ).lastrowid


def _category(db, subject_id, name):
    return db.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    ).lastrowid


def _paper(db, user_id, subject_id, category_id=None):
    return db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'b.pdf', 1, 2, 'completed', 'past_paper')""",
        (user_id, subject_id, category_id),
    ).lastrowid


def _q(db, batch_id, user_id, subject_id, category_id):
    db.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, category_id, page_number, question_text,
            answer_text, approved, question_source)
           VALUES (?, ?, ?, ?, 1, 'q', 'a', 1, 'past_paper')""",
        (batch_id, user_id, subject_id, category_id),
    )


def test_backfill_sets_batch_category_to_most_common(db_conn):
    uid, _ = _insert_user(db_conn, "bf")
    sid = _subject(db_conn)
    cells = _category(db_conn, sid, "Cells")
    genes = _category(db_conn, sid, "Genetics")
    bid = _paper(db_conn, uid, sid, category_id=None)
    _q(db_conn, bid, uid, sid, cells)
    _q(db_conn, bid, uid, sid, cells)
    _q(db_conn, bid, uid, sid, genes)
    _q(db_conn, bid, uid, sid, None)
    db_conn.commit()

    database.backfill_past_paper_categories(db_conn)
    db_conn.commit()

    row = db_conn.execute("SELECT category_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert row["category_id"] == cells


def test_backfill_idempotent_and_skips_untagged(db_conn):
    uid, _ = _insert_user(db_conn, "bf2")
    sid = _subject(db_conn, "Physics")
    cat = _category(db_conn, sid, "Forces")
    set_bid = _paper(db_conn, uid, sid, category_id=cat)
    other = _category(db_conn, sid, "Energy")
    _q(db_conn, set_bid, uid, sid, other)
    null_bid = _paper(db_conn, uid, sid, category_id=None)
    _q(db_conn, null_bid, uid, sid, None)
    db_conn.commit()

    database.backfill_past_paper_categories(db_conn)
    database.backfill_past_paper_categories(db_conn)  # idempotent
    db_conn.commit()

    assert db_conn.execute(
        "SELECT category_id FROM upload_batches WHERE id=?", (set_bid,)
    ).fetchone()["category_id"] == cat
    assert db_conn.execute(
        "SELECT category_id FROM upload_batches WHERE id=?", (null_bid,)
    ).fetchone()["category_id"] is None
