import sqlite3
from pathlib import Path

from backend import database

_PORTABLE_BATCH_FIELDS = (
    "filename", "batch_type", "exam_board", "exam_year", "paper_number",
    "tier", "page_start", "page_end", "source_type",
)
_PORTABLE_QUESTION_FIELDS = (
    "page_number", "question_text", "answer_text", "question_type", "difficulty",
    "approved", "question_source", "question_source_detail", "question_ref",
    "source_context", "options_json",
)


def _images_root() -> Path:
    return Path(database.DATA_DIR) / "images"


def serialize_paper(batch_id: int, user_id: int, db: sqlite3.Connection) -> dict:
    """Build a portable dict for one past-paper batch owned by user_id."""
    batch = db.execute(
        """SELECT b.*, s.name AS subject_name,
                  c.name AS category_name, sc.name AS subcategory_name
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           LEFT JOIN categories c ON c.id = b.category_id
           LEFT JOIN subcategories sc ON sc.id = b.subcategory_id
           WHERE b.id = ? AND b.user_id = ? AND b.batch_type = 'past_paper'""",
        (batch_id, user_id),
    ).fetchone()
    if not batch:
        raise ValueError(f"Past paper {batch_id} not found for this user")

    batch_out = {k: batch[k] for k in _PORTABLE_BATCH_FIELDS}
    batch_out["subject_name"] = batch["subject_name"]
    batch_out["category_name"] = batch["category_name"]
    batch_out["subcategory_name"] = batch["subcategory_name"]

    img_rows = db.execute(
        "SELECT * FROM images WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    images = []
    id_to_index = {}
    prefix = f"batch_{batch_id}/"
    for idx, img in enumerate(img_rows):
        id_to_index[img["id"]] = idx
        rel = img["filename"]
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        images.append({
            "image_index": idx,
            "page_number": img["page_number"],
            "rel_path": rel,
            "description": img["description"],
            "crop_x": img["crop_x"], "crop_y": img["crop_y"],
            "crop_w": img["crop_w"], "crop_h": img["crop_h"],
            "width": img["width"], "height": img["height"],
        })

    q_rows = db.execute(
        "SELECT * FROM questions WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    questions = []
    for q in q_rows:
        item = {k: q[k] for k in _PORTABLE_QUESTION_FIELDS}
        item["image_index"] = id_to_index.get(q["image_id"])
        opts = db.execute(
            "SELECT option_text, is_correct FROM mcq_options WHERE question_id = ? ORDER BY id",
            (q["id"],),
        ).fetchall()
        item["mcq_options"] = [
            {"option_text": o["option_text"], "is_correct": o["is_correct"]} for o in opts
        ]
        questions.append(item)

    return {"batch": batch_out, "images": images, "questions": questions}
