import sqlite3
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from backend.auth import get_current_user
from backend.database import get_db

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()


@router.get("")
def list_shared(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """List shared batches from same-year peers (excludes own)."""
    batches = db.execute(
        """SELECT b.*, s.name as subject_name, u.display_name as shared_by, u.username,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as question_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           JOIN users u ON u.id = b.user_id
           WHERE b.is_shared = 1
             AND b.status = 'completed'
             AND b.user_id != ?
             AND u.year_group = ?
             AND b.source_batch_id IS NULL
           ORDER BY b.created_at DESC""",
        (user["id"], user["year_group"]),
    ).fetchall()
    return [dict(b) for b in batches]


@router.get("/{batch_id}")
def preview_shared(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Preview questions in a shared batch."""
    batch = db.execute(
        """SELECT b.*, s.name as subject_name, u.display_name as shared_by
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           JOIN users u ON u.id = b.user_id
           WHERE b.id = ? AND b.is_shared = 1 AND b.status = 'completed'
             AND u.year_group = ?""",
        (batch_id, user["year_group"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Shared batch not found")

    questions = db.execute(
        """SELECT q.*, i.filename as image_filename
           FROM questions q
           LEFT JOIN images i ON i.id = q.image_id
           WHERE q.batch_id = ? AND q.approved = 1
           ORDER BY q.page_number, q.id""",
        (batch_id,),
    ).fetchall()

    return {"batch": dict(batch), "questions": [dict(q) for q in questions]}


@router.post("/{batch_id}/import")
def import_shared(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Copy a shared batch's questions into the current user's collection."""
    # Verify batch is shared and accessible
    source_batch = db.execute(
        """SELECT b.*, u.year_group
           FROM upload_batches b
           JOIN users u ON u.id = b.user_id
           WHERE b.id = ? AND b.is_shared = 1 AND b.status = 'completed'
             AND u.year_group = ?""",
        (batch_id, user["year_group"]),
    ).fetchone()
    if not source_batch:
        raise HTTPException(status_code=404, detail="Shared batch not found")

    # Check not already imported
    existing = db.execute(
        "SELECT id FROM upload_batches WHERE source_batch_id = ? AND user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Already imported this batch")

    # Create new batch for the importing user
    cursor = db.execute(
        """INSERT INTO upload_batches
           (user_id, subject_id, filename, pdf_path, page_start, page_end,
            status, is_shared, source_batch_id, total_pages, processed_pages, completed_at)
           VALUES (?, ?, ?, ?, ?, ?, 'completed', 0, ?, ?, ?, datetime('now'))""",
        (
            user["id"],
            source_batch["subject_id"],
            source_batch["filename"] + " (imported)",
            source_batch["pdf_path"],
            source_batch["page_start"],
            source_batch["page_end"],
            batch_id,
            source_batch["total_pages"],
            source_batch["total_pages"],
        ),
    )
    new_batch_id = cursor.lastrowid

    # Copy images to new batch directory
    source_img_dir = DATA_DIR / "images" / f"batch_{batch_id}"
    dest_img_dir = DATA_DIR / "images" / f"batch_{new_batch_id}"
    if source_img_dir.exists():
        shutil.copytree(source_img_dir, dest_img_dir)

    # Copy approved questions
    source_questions = db.execute(
        """SELECT q.*, i.filename as src_image_filename, i.description as img_desc,
                  i.crop_x, i.crop_y, i.crop_w, i.crop_h, i.width as img_w, i.height as img_h
           FROM questions q
           LEFT JOIN images i ON i.id = q.image_id
           WHERE q.batch_id = ? AND q.approved = 1""",
        (batch_id,),
    ).fetchall()

    copied = 0
    for sq in source_questions:
        # If question had an image, create new image record with updated path
        new_image_id = None
        if sq["src_image_filename"]:
            new_filename = sq["src_image_filename"].replace(
                f"batch_{batch_id}", f"batch_{new_batch_id}"
            )
            img_cursor = db.execute(
                """INSERT INTO images (batch_id, page_number, filename, description,
                   crop_x, crop_y, crop_w, crop_h, width, height)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_batch_id, sq["page_number"], new_filename, sq["img_desc"],
                    sq["crop_x"], sq["crop_y"], sq["crop_w"], sq["crop_h"],
                    sq["img_w"], sq["img_h"],
                ),
            )
            new_image_id = img_cursor.lastrowid

        db.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, question_type, difficulty, approved, image_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                new_batch_id, user["id"], sq["subject_id"], sq["page_number"],
                sq["question_text"], sq["answer_text"], sq["question_type"],
                sq["difficulty"], new_image_id,
            ),
        )
        copied += 1

    db.commit()
    return {"message": f"Imported {copied} questions", "batch_id": new_batch_id}
