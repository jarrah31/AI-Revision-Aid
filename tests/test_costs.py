import sqlite3
import pytest
from tests.conftest import _insert_user


def _make_subject(db, name="Biology"):
    cur = db.execute("INSERT INTO subjects (name, icon, color) VALUES (?, '🧬', '#000')", (name,))
    db.commit()
    return cur.lastrowid


def _make_category(db, subject_id, name="Cells"):
    cur = db.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    )
    db.commit()
    return cur.lastrowid


def _make_subcategory(db, category_id, name="Mitosis"):
    cur = db.execute(
        "INSERT INTO subcategories (category_id, name) VALUES (?, ?)", (category_id, name)
    )
    db.commit()
    return cur.lastrowid


def _make_batch(db, user_id, subject_id, category_id=None, subcategory_id=None,
                batch_type="knowledge_organiser", source_type="pdf",
                is_handwritten=0, tier=None):
    cur = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, subcategory_id, filename, pdf_path,
            page_start, page_end, status, batch_type, source_type, is_handwritten, tier)
           VALUES (?, ?, ?, ?, 'test.pdf', 'batch_1.pdf', 1, 5, 'completed', ?, ?, ?, ?)""",
        (user_id, subject_id, category_id, subcategory_id,
         batch_type, source_type, is_handwritten, tier),
    )
    db.commit()
    return cur.lastrowid


def test_history_returns_new_fields(client, db_conn):
    """GET /costs/history returns batch_type, source_type, is_handwritten, tier,
    category_name, and subcategory_name."""
    uid, token = _insert_user(db_conn, "histuser")
    sid = _make_subject(db_conn)
    cat_id = _make_category(db_conn, sid)
    sub_id = _make_subcategory(db_conn, cat_id)
    _make_batch(
        db_conn, uid, sid,
        category_id=cat_id, subcategory_id=sub_id,
        batch_type="past_paper", source_type="pdf",
        is_handwritten=0, tier="Foundation",
    )

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    b = data[0]
    assert b["batch_type"] == "past_paper"
    assert b["source_type"] == "pdf"
    assert b["is_handwritten"] == 0
    assert b["tier"] == "Foundation"
    assert b["category_name"] == "Cells"
    assert b["subcategory_name"] == "Mitosis"


def test_history_nulls_when_no_category(client, db_conn):
    """category_name and subcategory_name are None when not set."""
    uid, token = _insert_user(db_conn, "histuser2")
    sid = _make_subject(db_conn, "Physics")
    _make_batch(db_conn, uid, sid)

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    b = resp.json()[0]
    assert b["category_name"] is None
    assert b["subcategory_name"] is None
    assert b["batch_type"] == "knowledge_organiser"
    assert b["source_type"] == "pdf"
    assert b["tier"] is None


def test_history_handwritten_flag(client, db_conn):
    """is_handwritten=1 is returned correctly."""
    uid, token = _insert_user(db_conn, "histuser3")
    sid = _make_subject(db_conn, "Art")
    _make_batch(db_conn, uid, sid, source_type="images", is_handwritten=1)

    resp = client.get(
        "/costs/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    b = resp.json()[0]
    assert b["source_type"] == "images"
    assert b["is_handwritten"] == 1
