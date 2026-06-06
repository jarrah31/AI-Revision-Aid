import backend.routers.past_papers as past_papers
from tests.conftest import _insert_user


def _make_past_paper(db_conn, batch_id, board="AQA", year=2023, paper="Paper 1", tier="Foundation"):
    db_conn.execute(
        """UPDATE upload_batches
           SET batch_type='past_paper', exam_board=?, exam_year=?, paper_number=?, tier=?
           WHERE id=?""",
        (board, year, paper, tier, batch_id),
    )
    db_conn.commit()


def _set_source(db_conn, question_id, source="past_paper"):
    db_conn.execute(
        "UPDATE questions SET question_source=? WHERE id=?", (source, question_id)
    )
    db_conn.commit()


def _add_image(db_conn, batch_id, question_id, filename="batch_x/page_1_img_0.png"):
    cur = db_conn.execute(
        "INSERT INTO images (batch_id, page_number, filename) VALUES (?, 1, ?)",
        (batch_id, filename),
    )
    image_id = cur.lastrowid
    db_conn.execute("UPDATE questions SET image_id=? WHERE id=?", (image_id, question_id))
    db_conn.commit()
    return image_id


def _write_full_page(data_dir, batch_id, page_number=1, size=(400, 300)):
    """Create a real full-page PNG on disk where the recrop endpoint expects it."""
    from PIL import Image
    d = data_dir / "images" / f"batch_{batch_id}"
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(d / f"page_{page_number}_full.png")


def test_list_questions_filters_by_source(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    pp_q = make_question(batch_id, user_id, subject_id, question_text="PP q")
    ko_q = make_question(batch_id, user_id, subject_id, question_text="KO q")
    _set_source(db_conn, pp_q, "past_paper")
    _set_source(db_conn, ko_q, "ai_generated")

    r = client.get(
        f"/api/questions?batch_id={batch_id}&question_source=past_paper",
        headers=user_headers,
    )
    assert r.status_code == 200
    ids = [q["id"] for q in r.json()["questions"]]
    assert ids == [pp_q]


def test_update_question_persists_question_ref(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q_id = make_question(batch_id, user_id, subject_id)

    r = client.put(
        f"/api/questions/{q_id}",
        headers=user_headers,
        json={"question_ref": "1a"},
    )
    assert r.status_code == 200
    row = db_conn.execute(
        "SELECT question_ref FROM questions WHERE id=?", (q_id,)
    ).fetchone()
    assert row["question_ref"] == "1a"


def test_list_past_papers(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()

    pp_batch = make_batch(user_id, subject_id)
    _make_past_paper(db_conn, pp_batch)
    q1 = make_question(pp_batch, user_id, subject_id)
    q2 = make_question(pp_batch, user_id, subject_id)
    _set_source(db_conn, q1, "past_paper")
    _set_source(db_conn, q2, "past_paper")
    _add_image(db_conn, pp_batch, q1)  # one figure

    ko_batch = make_batch(user_id, subject_id)  # knowledge_organiser — must be excluded

    r = client.get(f"/api/past-papers?subject_id={subject_id}", headers=user_headers)
    assert r.status_code == 200
    papers = r.json()
    assert len(papers) == 1
    p = papers[0]
    assert p["id"] == pp_batch
    assert p["exam_board"] == "AQA"
    assert p["question_count"] == 2
    assert p["figure_count"] == 1


def test_delete_past_paper_cascades(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    _make_past_paper(db_conn, batch_id)
    q = make_question(batch_id, user_id, subject_id)
    _set_source(db_conn, q, "past_paper")
    _add_image(db_conn, batch_id, q)

    r = client.delete(f"/api/past-papers/{batch_id}", headers=user_headers)
    assert r.status_code == 200

    assert db_conn.execute(
        "SELECT COUNT(*) c FROM upload_batches WHERE id=?", (batch_id,)
    ).fetchone()["c"] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM questions WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"] == 0
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM images WHERE batch_id=?", (batch_id,)
    ).fetchone()["c"] == 0


def test_delete_past_paper_rejects_ko_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch
):
    user_id, _ = regular_user
    subject_id = make_subject()
    ko_batch = make_batch(user_id, subject_id)  # knowledge_organiser

    r = client.delete(f"/api/past-papers/{ko_batch}", headers=user_headers)
    assert r.status_code == 404
    assert db_conn.execute(
        "SELECT COUNT(*) c FROM upload_batches WHERE id=?", (ko_batch,)
    ).fetchone()["c"] == 1


def test_delete_past_paper_rejects_other_user(
    client, db_conn, regular_user, second_user, make_subject, make_batch
):
    owner_id, _ = regular_user
    _, other_token = second_user
    subject_id = make_subject()
    batch_id = make_batch(owner_id, subject_id)
    _make_past_paper(db_conn, batch_id)

    r = client.delete(
        f"/api/past-papers/{batch_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert r.status_code == 404


def _make_category(db_conn, subject_id, name="Cells"):
    cur = db_conn.execute(
        "INSERT INTO categories (subject_id, name) VALUES (?, ?)", (subject_id, name)
    )
    db_conn.commit()
    return cur.lastrowid


def _paper_with_questions(db_conn, uid, subject_id, category_id=None, n=2):
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'batch_1.pdf', 1, 2, 'completed', 'past_paper')""",
        (uid, subject_id, category_id),
    ).lastrowid
    qids = []
    for i in range(n):
        qids.append(db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved, question_source)
               VALUES (?, ?, ?, 1, 'q', 'a', 1, 'past_paper')""",
            (bid, uid, subject_id),
        ).lastrowid)
    db_conn.commit()
    return bid, qids


def test_patch_assigns_existing_category_and_cascades(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch1")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id, "category_id": cat},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_id"] == cat
    assert body["category_name"] == "Cells"
    b = db_conn.execute("SELECT category_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert b["category_id"] == cat
    for qid in qids:
        q = db_conn.execute(
            "SELECT subject_id, category_id, subcategory_id FROM questions WHERE id=?", (qid,)
        ).fetchone()
        assert q["subject_id"] == subject_id
        assert q["category_id"] == cat
        assert q["subcategory_id"] is None


def test_patch_creates_new_category_by_name(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch2")
    subject_id = make_subject(name="Biology")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id, "new_category_name": "  Genetics  "},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["category_name"] == "Genetics"
    new_cat = db_conn.execute(
        "SELECT id FROM categories WHERE subject_id=? AND name='Genetics'", (subject_id,)
    ).fetchone()
    assert new_cat is not None
    assert body["category_id"] == new_cat["id"]


def test_patch_reassigns_subject_and_cascades(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch3")
    subj_a = make_subject(name="Biology")
    subj_b = make_subject(name="Chemistry")
    bid, qids = _paper_with_questions(db_conn, uid, subj_a)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subj_b, "new_category_name": "Bonding"},
    )
    assert resp.status_code == 200
    b = db_conn.execute("SELECT subject_id FROM upload_batches WHERE id=?", (bid,)).fetchone()
    assert b["subject_id"] == subj_b
    cat = db_conn.execute(
        "SELECT subject_id FROM categories WHERE id=?", (resp.json()["category_id"],)
    ).fetchone()
    assert cat["subject_id"] == subj_b
    for qid in qids:
        q = db_conn.execute("SELECT subject_id FROM questions WHERE id=?", (qid,)).fetchone()
        assert q["subject_id"] == subj_b


def test_patch_clears_category_when_none_given(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch4")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid, qids = _paper_with_questions(db_conn, uid, subject_id, category_id=cat)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 200
    assert resp.json()["category_id"] is None
    q = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (qids[0],)).fetchone()
    assert q["category_id"] is None


def test_patch_rejects_category_from_other_subject(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patch5")
    subj_a = make_subject(name="Biology")
    subj_b = make_subject(name="Chemistry")
    foreign_cat = _make_category(db_conn, subj_b, name="Bonding")
    bid, _ = _paper_with_questions(db_conn, uid, subj_a)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subj_a, "category_id": foreign_cat},
    )
    assert resp.status_code == 400


def test_patch_404_for_other_users_paper(client, db_conn, make_subject):
    owner, _ = _insert_user(db_conn, "patchowner")
    _, other_token = _insert_user(db_conn, "patchother")
    subject_id = make_subject(name="Biology")
    bid, _ = _paper_with_questions(db_conn, owner, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 404


def test_patch_404_for_ko_batch(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patchko")
    subject_id = make_subject(name="Biology")
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end,
            status, batch_type)
           VALUES (?, ?, 'ko.pdf', 'batch_1.pdf', 1, 2, 'completed', 'knowledge_organiser')""",
        (uid, subject_id),
    ).lastrowid
    db_conn.commit()

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": subject_id},
    )
    assert resp.status_code == 404


def test_patch_400_for_missing_subject(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "patchnosubj")
    subject_id = make_subject(name="Biology")
    bid, _ = _paper_with_questions(db_conn, uid, subject_id)

    resp = client.patch(
        f"/api/past-papers/{bid}",
        headers={"Authorization": f"Bearer {token}"},
        json={"subject_id": 999999},  # no such subject
    )
    assert resp.status_code == 400


def test_recrop_creates_new_image_and_links(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 40, "bbox_h_pct": 30},
    )
    assert r.status_code == 200
    new_image_id = r.json()["image_id"]
    row = db_conn.execute("SELECT image_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["image_id"] == new_image_id
    img = db_conn.execute("SELECT batch_id FROM images WHERE id=?", (new_image_id,)).fetchone()
    assert img["batch_id"] == batch_id


def test_recrop_does_not_mutate_shared_image(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    """Re-cropping one question must not change the figure of a sibling sharing the image."""
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id, page_number=1)
    q2 = make_question(batch_id, user_id, subject_id, page_number=1)
    shared_image = _add_image(db_conn, batch_id, q1)
    db_conn.execute("UPDATE questions SET image_id=? WHERE id=?", (shared_image, q2))
    db_conn.commit()
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q1}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 0, "bbox_y_pct": 0, "bbox_w_pct": 50, "bbox_h_pct": 50},
    )
    assert r.status_code == 200
    new_id = r.json()["image_id"]
    assert new_id != shared_image
    assert db_conn.execute("SELECT image_id FROM questions WHERE id=?", (q2,)).fetchone()["image_id"] == shared_image


def test_recrop_404_when_full_page_missing(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 40, "bbox_h_pct": 30},
    )
    assert r.status_code == 404


def test_recrop_400_on_invalid_bbox(
    client, db_conn, regular_user, user_headers, tmp_path, monkeypatch,
    make_subject, make_batch, make_question
):
    monkeypatch.setattr(past_papers, "DATA_DIR", tmp_path / "data")
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id, page_number=1)
    _write_full_page(tmp_path / "data", batch_id, page_number=1)

    r = client.post(
        f"/api/past-papers/questions/{q}/recrop",
        headers=user_headers,
        json={"bbox_x_pct": 10, "bbox_y_pct": 10, "bbox_w_pct": 0, "bbox_h_pct": 30},
    )
    assert r.status_code == 400


def test_detach_image(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    _add_image(db_conn, batch_id, q)

    r = client.put(
        f"/api/past-papers/questions/{q}/image",
        headers=user_headers, json={"image_id": None},
    )
    assert r.status_code == 200
    assert db_conn.execute(
        "SELECT image_id FROM questions WHERE id=?", (q,)
    ).fetchone()["image_id"] is None


def test_attach_image_same_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id)
    q2 = make_question(batch_id, user_id, subject_id)
    image_id = _add_image(db_conn, batch_id, q1)

    r = client.put(
        f"/api/past-papers/questions/{q2}/image",
        headers=user_headers, json={"image_id": image_id},
    )
    assert r.status_code == 200
    assert db_conn.execute(
        "SELECT image_id FROM questions WHERE id=?", (q2,)
    ).fetchone()["image_id"] == image_id


def test_attach_image_rejects_cross_batch(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_a = make_batch(user_id, subject_id)
    batch_b = make_batch(user_id, subject_id)
    qa = make_question(batch_a, user_id, subject_id)
    qb = make_question(batch_b, user_id, subject_id)
    foreign_image = _add_image(db_conn, batch_b, qb)

    r = client.put(
        f"/api/past-papers/questions/{qa}/image",
        headers=user_headers, json={"image_id": foreign_image},
    )
    assert r.status_code == 400


def test_attach_image_rejects_other_users_image(
    client, db_conn, regular_user, second_user, user_headers,
    make_subject, make_batch, make_question
):
    """A user must not be able to attach an image owned by another user."""
    user_id, _ = regular_user
    other_id, _ = second_user
    subject_id = make_subject()
    my_batch = make_batch(user_id, subject_id)
    my_q = make_question(my_batch, user_id, subject_id)
    other_batch = make_batch(other_id, subject_id)
    other_q = make_question(other_batch, other_id, subject_id)
    other_image = _add_image(db_conn, other_batch, other_q)  # owned by second_user

    r = client.put(
        f"/api/past-papers/questions/{my_q}/image",
        headers=user_headers, json={"image_id": other_image},
    )
    # Cross-batch (and cross-user) image is rejected
    assert r.status_code == 400
    assert db_conn.execute(
        "SELECT image_id FROM questions WHERE id=?", (my_q,)
    ).fetchone()["image_id"] is None


def test_detect_multi_response_endpoint_updates_questions(
    client, user_headers, regular_user, make_subject, make_batch, make_question, db_conn, monkeypatch
):
    import backend.services.multi_response_service as mrs
    uid, _ = regular_user
    sid = make_subject()
    bid = make_batch(uid, sid)
    q1 = make_question(bid, uid, sid, question_text="Which two? a b c", answer_text="a; b")
    db_conn.execute("UPDATE questions SET question_source='past_paper' WHERE id=?", (q1,))
    db_conn.commit()

    monkeypatch.setattr(
        mrs, "detect_multiple_response_batch",
        lambda questions, subject: (
            [{"select_count": 2, "stem": "Which two?",
              "options": [{"text": "a", "is_correct": True},
                          {"text": "b", "is_correct": True},
                          {"text": "c", "is_correct": False}]}],
            {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0},
        ),
    )

    r = client.post(f"/api/past-papers/{bid}/detect-multi-response", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["updated"] == 1
    assert body["scanned"] == 1


def test_detect_multi_response_rejects_other_users_batch(
    client, regular_user, second_user, make_subject, make_batch, make_question, db_conn
):
    owner_id, _ = second_user
    sid = make_subject()
    bid = make_batch(owner_id, sid)
    q1 = make_question(bid, owner_id, sid)
    db_conn.execute("UPDATE questions SET question_source='past_paper' WHERE id=?", (q1,))
    db_conn.commit()

    _, token1 = regular_user
    r = client.post(f"/api/past-papers/{bid}/detect-multi-response",
                    headers={"Authorization": f"Bearer {token1}"})
    assert r.status_code == 404


def test_list_past_papers_includes_category(client, db_conn, make_subject):
    uid, token = _insert_user(db_conn, "catlist")
    subject_id = make_subject(name="Biology")
    cat = _make_category(db_conn, subject_id, name="Cells")
    bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, ?, 'p.pdf', 'batch_1.pdf', 1, 2, 'completed', 'past_paper')""",
        (uid, subject_id, cat),
    ).lastrowid
    uncat_bid = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, category_id, filename, pdf_path, page_start,
            page_end, status, batch_type)
           VALUES (?, ?, NULL, 'p2.pdf', 'batch_2.pdf', 1, 2, 'completed', 'past_paper')""",
        (uid, subject_id),
    ).lastrowid
    db_conn.commit()

    resp = client.get(
        f"/api/past-papers?subject_id={subject_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()}
    row = rows[bid]
    assert row["category_id"] == cat
    assert row["category_name"] == "Cells"
    uncat = rows[uncat_bid]
    assert uncat["category_id"] is None
    assert uncat["category_name"] is None
