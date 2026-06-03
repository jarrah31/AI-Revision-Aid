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
