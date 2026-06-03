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
