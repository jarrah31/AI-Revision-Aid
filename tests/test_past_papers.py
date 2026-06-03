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


def _make_subcategory(db_conn, category_id, name="Mitosis"):
    cur = db_conn.execute(
        "INSERT INTO subcategories (category_id, name) VALUES (?, ?)", (category_id, name)
    )
    db_conn.commit()
    return cur.lastrowid


def test_tag_questions_bulk(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q1 = make_question(batch_id, user_id, subject_id)
    q2 = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q1, q2], "category_id": cat, "subcategory_id": None},
    )
    assert r.status_code == 200
    for q in (q1, q2):
        row = db_conn.execute(
            "SELECT category_id FROM questions WHERE id=?", (q,)
        ).fetchone()
        assert row["category_id"] == cat


def test_tag_clears_with_null(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)
    db_conn.execute("UPDATE questions SET category_id=? WHERE id=?", (cat, q))
    db_conn.commit()

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": None, "subcategory_id": None},
    )
    assert r.status_code == 200
    row = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["category_id"] is None


def test_tag_rejects_cross_subject_category(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_a = make_subject("Biology")
    subject_b = make_subject("Chemistry")
    batch_id = make_batch(user_id, subject_a)
    q = make_question(batch_id, user_id, subject_a)
    foreign_cat = _make_category(db_conn, subject_b)  # belongs to a different subject

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": foreign_cat, "subcategory_id": None},
    )
    assert r.status_code == 400
    row = db_conn.execute("SELECT category_id FROM questions WHERE id=?", (q,)).fetchone()
    assert row["category_id"] is None


def test_tag_rejects_subcategory_not_under_category(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)
    other_cat = _make_category(db_conn, subject_id, name="Other")
    foreign_sub = _make_subcategory(db_conn, other_cat)  # belongs to a different category

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": cat, "subcategory_id": foreign_sub},
    )
    assert r.status_code == 400
    row = db_conn.execute(
        "SELECT category_id, subcategory_id FROM questions WHERE id=?", (q,)
    ).fetchone()
    assert row["category_id"] is None
    assert row["subcategory_id"] is None


def test_tag_with_valid_subcategory(
    client, db_conn, regular_user, user_headers, make_subject, make_batch, make_question
):
    user_id, _ = regular_user
    subject_id = make_subject()
    batch_id = make_batch(user_id, subject_id)
    q = make_question(batch_id, user_id, subject_id)
    cat = _make_category(db_conn, subject_id)
    sub = _make_subcategory(db_conn, cat)

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [q], "category_id": cat, "subcategory_id": sub},
    )
    assert r.status_code == 200
    row = db_conn.execute(
        "SELECT category_id, subcategory_id FROM questions WHERE id=?", (q,)
    ).fetchone()
    assert row["category_id"] == cat
    assert row["subcategory_id"] == sub


def test_tag_ignores_questions_not_owned(
    client, db_conn, regular_user, second_user, user_headers,
    make_subject, make_batch, make_question
):
    owner_id, _ = regular_user
    other_id, _ = second_user
    subject_id = make_subject()
    batch_id = make_batch(other_id, subject_id)
    foreign_q = make_question(batch_id, other_id, subject_id)  # owned by second_user
    cat = _make_category(db_conn, subject_id)

    r = client.post(
        "/api/past-papers/tag",
        headers=user_headers,
        json={"question_ids": [foreign_q], "category_id": cat, "subcategory_id": None},
    )
    assert r.status_code == 200  # no error, but nothing changes
    row = db_conn.execute(
        "SELECT category_id FROM questions WHERE id=?", (foreign_q,)
    ).fetchone()
    assert row["category_id"] is None
