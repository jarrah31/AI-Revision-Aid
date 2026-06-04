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


def test_history_includes_past_paper_count(client, db_conn):
    """GET /api/costs/history returns past_paper_count per batch."""
    uid, token = _insert_user(db_conn, "covuser")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid, batch_type="knowledge_organiser")
    # 2 AI-generated + 1 past-paper question in this batch
    for src in ("ai_generated", "ai_generated", "past_paper"):
        db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved, question_source)
               VALUES (?, ?, ?, 1, 'q', 'a', 1, ?)""",
            (bid, uid, sid, src),
        )
    db_conn.commit()

    resp = client.get("/api/costs/history",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    batch = next(b for b in resp.json() if b["id"] == bid)
    assert batch["question_count"] == 3
    assert batch["past_paper_count"] == 1


def test_history_cost_derived_from_api_usage(client, db_conn):
    """The history cost_usd reflects the sum of api_usage rows, even when the
    stored upload_batches.cost_usd cache is stale/incomplete."""
    uid, token = _insert_user(db_conn, "derivecost")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid)
    # Stored cache says 0.01, but api_usage totals 0.03 (e.g. a process that
    # logged usage without updating the cache, like multi-response detection).
    db_conn.execute("UPDATE upload_batches SET cost_usd = 0.01 WHERE id = ?", (bid,))
    for ct, cost in (("qa_extraction", 0.02), ("multi_response_detection", 0.01)):
        db_conn.execute(
            """INSERT INTO api_usage
               (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
               VALUES (?, ?, ?, 10, 5, ?, 'claude-sonnet-4-6')""",
            (uid, bid, ct, cost),
        )
    db_conn.commit()

    resp = client.get("/api/costs/history",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    batch = next(b for b in resp.json() if b["id"] == bid)
    assert abs(batch["cost_usd"] - 0.03) < 1e-9


def _add_usage(db, uid, bid, call_type, model, in_tok, out_tok, cost):
    db.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (uid, bid, call_type, in_tok, out_tok, cost, model),
    )
    db.commit()


def test_batch_breakdown_groups_by_process_and_model(client, db_conn):
    """GET /api/costs/batch/{id} groups api_usage by (call_type, model) with
    per-row token/cost sums, ordered by cost desc, plus batch totals."""
    uid, token = _insert_user(db_conn, "bduser")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid)
    # Two extraction calls on sonnet + one matching call on haiku.
    _add_usage(db_conn, uid, bid, "qa_extraction", "claude-sonnet-4-6", 1000, 200, 0.01)
    _add_usage(db_conn, uid, bid, "qa_extraction", "claude-sonnet-4-6", 500, 100, 0.005)
    _add_usage(db_conn, uid, bid, "ko_matching", "claude-haiku-4-5", 300, 50, 0.0009)

    resp = client.get(f"/api/costs/batch/{bid}",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["batch_id"] == bid
    assert abs(data["total_cost_usd"] - 0.0159) < 1e-9
    assert data["total_input_tokens"] == 1800

    bd = data["breakdown"]
    assert len(bd) == 2                         # the two extraction rows collapse
    top = bd[0]
    assert top["call_type"] == "qa_extraction"  # highest cost first
    assert top["model"] == "claude-sonnet-4-6"
    assert top["call_count"] == 2
    assert top["input_tokens"] == 1500
    assert abs(top["cost_usd"] - 0.015) < 1e-9


def test_batch_breakdown_null_model_bucketed_unknown(client, db_conn):
    """Legacy rows with NULL model are reported as 'unknown'."""
    uid, token = _insert_user(db_conn, "bduser2")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid)
    db_conn.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, 'qa_extraction', 10, 5, 0.001)""",
        (uid, bid),
    )
    db_conn.commit()

    resp = client.get(f"/api/costs/batch/{bid}",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["breakdown"][0]["model"] == "unknown"


def test_batch_breakdown_404_for_other_users_batch(client, db_conn):
    """A batch owned by another user returns 404."""
    owner_id, _ = _insert_user(db_conn, "bdowner")
    _, other_token = _insert_user(db_conn, "bdother")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, owner_id, sid)

    resp = client.get(f"/api/costs/batch/{bid}",
                      headers={"Authorization": f"Bearer {other_token}"})
    assert resp.status_code == 404


def test_batch_breakdown_empty_when_no_usage(client, db_conn):
    """A batch with no api_usage rows returns zero totals and empty breakdown."""
    uid, token = _insert_user(db_conn, "bduser3")
    sid = _make_subject(db_conn)
    bid = _make_batch(db_conn, uid, sid)

    resp = client.get(f"/api/costs/batch/{bid}",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cost_usd"] == 0
    assert data["breakdown"] == []
