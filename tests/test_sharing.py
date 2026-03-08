"""Tests for /api/shared endpoints."""
import pytest


# ── List shared batches ───────────────────────────────────────────────────────

def test_list_shared_returns_same_year_only(
    client, regular_user, second_user, other_year_user,
    make_subject, make_batch, make_question, db_conn
):
    """Students only see shared batches from peers in the same year group."""
    uid1, token1 = regular_user      # Year 10
    uid2, _ = second_user            # Year 10
    uid3, _ = other_year_user        # Year 11

    sid = make_subject()

    # uid2 (year 10) shares a batch
    bid_same = make_batch(uid2, sid, is_shared=1, status="completed")
    make_question(bid_same, uid2, sid, approved=1)

    # uid3 (year 11) shares a batch
    bid_other = make_batch(uid3, sid, is_shared=1, status="completed")
    make_question(bid_other, uid3, sid, approved=1)

    r = client.get("/api/shared", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 200
    batch_ids = {b["id"] for b in r.json()}
    assert bid_same in batch_ids
    assert bid_other not in batch_ids


def test_list_shared_excludes_own_batches(
    client, regular_user, second_user, make_subject, make_batch
):
    """Students should not see their own shared batches in the list."""
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()

    # uid1 shares their own batch
    bid_own = make_batch(uid1, sid, is_shared=1, status="completed")
    # uid2 shares a batch
    bid_other = make_batch(uid2, sid, is_shared=1, status="completed")

    r = client.get("/api/shared", headers={"Authorization": f"Bearer {token1}"})
    batch_ids = {b["id"] for b in r.json()}
    assert bid_own not in batch_ids
    assert bid_other in batch_ids


def test_list_shared_excludes_private_batches(
    client, regular_user, second_user, make_subject, make_batch
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()

    make_batch(uid2, sid, is_shared=0, status="completed")  # private

    r = client.get("/api/shared", headers={"Authorization": f"Bearer {token1}"})
    assert r.json() == []


def test_list_shared_excludes_imported_batches(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    """source_batch_id IS NOT NULL → imported, should not appear in the list."""
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()

    original_bid = make_batch(uid2, sid, is_shared=1, status="completed")
    make_question(original_bid, uid2, sid, approved=1)

    # Simulate uid1 importing the batch
    imported_bid = make_batch(
        uid1, sid, is_shared=1, status="completed", source_batch_id=original_bid
    )

    r = client.get("/api/shared", headers={"Authorization": f"Bearer {token1}"})
    batch_ids = {b["id"] for b in r.json()}
    assert imported_bid not in batch_ids  # imported ≠ original shared
    assert original_bid in batch_ids


# ── Preview shared batch ──────────────────────────────────────────────────────

def test_preview_shared_batch(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid, is_shared=1, status="completed")
    qid = make_question(bid, uid2, sid, approved=1)

    r = client.get(f"/api/shared/{bid}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 200
    data = r.json()
    assert "batch" in data
    assert "questions" in data
    assert len(data["questions"]) == 1
    assert data["questions"][0]["id"] == qid


def test_preview_private_batch_returns_404(
    client, regular_user, second_user, make_subject, make_batch
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid, is_shared=0, status="completed")

    r = client.get(f"/api/shared/{bid}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


def test_preview_other_year_batch_returns_404(
    client, regular_user, other_year_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user      # Year 10
    uid3, _ = other_year_user        # Year 11
    sid = make_subject()
    bid = make_batch(uid3, sid, is_shared=1, status="completed")
    make_question(bid, uid3, sid, approved=1)

    r = client.get(f"/api/shared/{bid}", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


# ── Import shared batch ───────────────────────────────────────────────────────

def test_import_shared_batch(
    client, regular_user, second_user, make_subject, make_batch, make_question, db_conn
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid, is_shared=1, status="completed")
    qid = make_question(bid, uid2, sid, question_text="Shared Q?", answer_text="Shared A.", approved=1)

    r = client.post(f"/api/shared/{bid}/import", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 200
    data = r.json()
    assert "batch_id" in data
    assert data["batch_id"] != bid  # new batch created

    # Verify the new batch belongs to uid1
    new_bid = data["batch_id"]
    batch_row = db_conn.execute(
        "SELECT user_id, source_batch_id FROM upload_batches WHERE id = ?", (new_bid,)
    ).fetchone()
    assert batch_row["user_id"] == uid1
    assert batch_row["source_batch_id"] == bid

    # Verify the question was copied
    copied_q = db_conn.execute(
        "SELECT * FROM questions WHERE batch_id = ? AND user_id = ?", (new_bid, uid1)
    ).fetchone()
    assert copied_q is not None
    assert copied_q["question_text"] == "Shared Q?"
    assert copied_q["approved"] == 1


def test_import_already_imported_returns_400(
    client, regular_user, second_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user
    uid2, _ = second_user
    sid = make_subject()
    bid = make_batch(uid2, sid, is_shared=1, status="completed")
    make_question(bid, uid2, sid, approved=1)

    headers = {"Authorization": f"Bearer {token1}"}
    client.post(f"/api/shared/{bid}/import", headers=headers)

    # Second import attempt should fail
    r = client.post(f"/api/shared/{bid}/import", headers=headers)
    assert r.status_code == 400
    assert "already" in r.json()["detail"].lower()


def test_import_other_year_batch_returns_404(
    client, regular_user, other_year_user, make_subject, make_batch, make_question
):
    uid1, token1 = regular_user
    uid3, _ = other_year_user
    sid = make_subject()
    bid = make_batch(uid3, sid, is_shared=1, status="completed")
    make_question(bid, uid3, sid, approved=1)

    r = client.post(f"/api/shared/{bid}/import", headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404
