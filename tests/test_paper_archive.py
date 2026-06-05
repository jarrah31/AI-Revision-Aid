import io
import json
import zipfile
from pathlib import Path

import pytest

from backend import database
from backend.services import paper_archive


def _make_past_paper(db_conn, user_id, subject_id, *, filename="Bio-QP.PDF",
                     exam_board="AQA", exam_year=2023, paper_number="Paper 1",
                     tier="Foundation", category_id=None, subcategory_id=None):
    cur = db_conn.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end, status,
            batch_type, exam_board, exam_year, paper_number, tier, source_type,
            category_id, subcategory_id)
           VALUES (?, ?, ?, 'x.pdf', 1, 40, 'completed',
                   'past_paper', ?, ?, ?, ?, 'pdf', ?, ?)""",
        (user_id, subject_id, filename, exam_board, exam_year, paper_number, tier,
         category_id, subcategory_id),
    )
    db_conn.commit()
    bid = cur.lastrowid
    db_conn.execute("UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
                    (f"batch_{bid}.pdf", bid))
    db_conn.commit()
    return bid


def _add_image(db_conn, batch_id, page_number=25, rel="page_25_img_0.png",
               write_bytes=b"PNGDATA"):
    cur = db_conn.execute(
        """INSERT INTO images
           (batch_id, page_number, filename, description, crop_x, crop_y,
            crop_w, crop_h, width, height)
           VALUES (?, ?, ?, 'fig', 1.0, 2.0, 3.0, 4.0, 100, 80)""",
        (batch_id, page_number, f"batch_{batch_id}/{rel}"),
    )
    db_conn.commit()
    img_dir = Path(database.DATA_DIR) / "images" / f"batch_{batch_id}"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / rel).write_bytes(write_bytes)
    return cur.lastrowid


def test_serialize_paper_captures_rows_by_name(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid)
    img_id = _add_image(db_conn, bid)
    q = db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source, question_ref, image_id)
           VALUES (?, ?, ?, 25, 'Q?', 'A.', 1, 'past_paper', '1a', ?)""",
        (bid, user_id, sid, img_id),
    )
    db_conn.commit()
    qid = q.lastrowid
    db_conn.execute(
        "INSERT INTO mcq_options (question_id, option_text, is_correct) VALUES (?, 'A.', 1)",
        (qid,),
    )
    db_conn.commit()

    data = paper_archive.serialize_paper(bid, user_id, db_conn)

    assert data["batch"]["subject_name"] == "Biology"
    assert data["batch"]["exam_board"] == "AQA"
    assert "id" not in data["batch"]            # no raw IDs exported
    assert data["images"][0]["rel_path"] == "page_25_img_0.png"
    assert data["images"][0]["image_index"] == 0
    assert data["questions"][0]["question_ref"] == "1a"
    assert data["questions"][0]["image_index"] == 0
    assert data["questions"][0]["mcq_options"] == [{"option_text": "A.", "is_correct": 1}]


def test_serialize_paper_rejects_foreign_or_non_pp(db_conn, regular_user, second_user, make_subject):
    user_id, _ = regular_user
    other_id, _ = second_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid)
    with pytest.raises(ValueError):
        paper_archive.serialize_paper(bid, other_id, db_conn)  # not owner


def test_serialize_paper_question_without_image_has_null_index(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid)
    db_conn.execute(
        """INSERT INTO questions
           (batch_id, user_id, subject_id, page_number, question_text, answer_text,
            approved, question_source)
           VALUES (?, ?, ?, 3, 'No figure?', 'Right.', 1, 'past_paper')""",
        (bid, user_id, sid),
    )
    db_conn.commit()
    data = paper_archive.serialize_paper(bid, user_id, db_conn)
    assert data["questions"][0]["image_index"] is None


def test_build_archive_single_paper_filename_and_contents(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    bid = _make_past_paper(db_conn, user_id, sid,
                           filename="Biology-AQA-84611F-QP-JUN23.PDF")
    _add_image(db_conn, bid, rel="page_25_img_0.png", write_bytes=b"CROP")
    # full-page PNG present on disk but NOT in images table -> must still be bundled
    (Path(database.DATA_DIR) / "images" / f"batch_{bid}" / "page_25_full.png").write_bytes(b"FULL")

    blob, filename = paper_archive.build_archive([bid], user_id, db_conn)

    assert filename == "Biology-AQA-84611F-QP-JUN23.revaid.zip"
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "revisionaid-pastpapers"
    assert manifest["version"] == 1
    assert len(manifest["papers"]) == 1
    slug = manifest["papers"][0]["slug"]
    assert f"papers/{slug}/paper.json" in names
    assert f"papers/{slug}/images/page_25_img_0.png" in names
    assert f"papers/{slug}/images/page_25_full.png" in names   # full page bundled
    assert zf.read(f"papers/{slug}/images/page_25_full.png") == b"FULL"


def test_build_archive_multi_paper_combined_name(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    b1 = _make_past_paper(db_conn, user_id, sid, filename="P1.PDF", paper_number="Paper 1")
    b2 = _make_past_paper(db_conn, user_id, sid, filename="P2.PDF", paper_number="Paper 2")
    blob, filename = paper_archive.build_archive([b1, b2], user_id, db_conn)
    assert filename.startswith("RevisionAid-PastPapers-") and filename.endswith(".zip")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert len(json.loads(zf.read("manifest.json"))["papers"]) == 2


def test_build_archive_skips_foreign_paper(db_conn, regular_user, second_user, make_subject):
    user_id, _ = regular_user
    other_id, _ = second_user
    sid = make_subject("Biology")
    mine = _make_past_paper(db_conn, user_id, sid, filename="Mine.PDF")
    theirs = _make_past_paper(db_conn, other_id, sid, filename="Theirs.PDF")
    blob, _ = paper_archive.build_archive([mine, theirs], user_id, db_conn)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    assert len(json.loads(zf.read("manifest.json"))["papers"]) == 1


def test_build_archive_no_valid_ids_raises(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    with pytest.raises(ValueError):
        paper_archive.build_archive([99999], user_id, db_conn)


def test_build_archive_dedupes_identical_filenames(db_conn, regular_user, make_subject):
    user_id, _ = regular_user
    sid = make_subject("Biology")
    b1 = _make_past_paper(db_conn, user_id, sid, filename="P1.PDF", paper_number="Paper 1")
    b2 = _make_past_paper(db_conn, user_id, sid, filename="P1.PDF", paper_number="Paper 2")
    blob, _ = paper_archive.build_archive([b1, b2], user_id, db_conn)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    slugs = [p["slug"] for p in json.loads(zf.read("manifest.json"))["papers"]]
    assert len(set(slugs)) == 2   # collision suffix applied
