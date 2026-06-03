import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.database import get_db
from backend.services.image_service import delete_batch_images, delete_batch_pdf

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()


@router.get("")
def list_past_papers(
    subject_id: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """List the user's past-paper batches for a subject, with question/figure counts."""
    rows = db.execute(
        """SELECT b.id, b.filename, b.exam_board, b.exam_year, b.paper_number,
                  b.tier, b.created_at,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) AS question_count,
                  (SELECT COUNT(*) FROM questions q
                     WHERE q.batch_id = b.id AND q.image_id IS NOT NULL) AS figure_count
           FROM upload_batches b
           WHERE b.user_id = ? AND b.subject_id = ? AND b.batch_type = 'past_paper'
           ORDER BY b.exam_year DESC, b.created_at DESC""",
        (user["id"], subject_id),
    ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{batch_id}")
def delete_past_paper(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Delete one of the user's past-paper batches and all its questions/figures."""
    batch = db.execute(
        "SELECT id FROM upload_batches WHERE id = ? AND user_id = ? AND batch_type = 'past_paper'",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Past paper not found")

    delete_batch_images(batch_id)
    delete_batch_pdf(batch_id)
    # Mark scheme PDF, if any (delete_batch_pdf only removes the question paper)
    ms_pdf = DATA_DIR / "pdfs" / f"batch_{batch_id}_ms.pdf"
    if ms_pdf.exists():
        ms_pdf.unlink()

    db.execute("DELETE FROM upload_batches WHERE id = ?", (batch_id,))
    db.commit()
    return {"message": "Past paper deleted"}
