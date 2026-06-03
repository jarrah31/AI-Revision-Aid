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


class TagRequest(BaseModel):
    question_ids: list[int]
    category_id: int | None = None
    subcategory_id: int | None = None


@router.post("/tag")
def tag_questions(
    req: TagRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Set (or clear) category/subcategory on the user's questions. Per-question or bulk."""
    if not req.question_ids:
        return {"message": "No questions to tag", "updated": 0}

    placeholders = ",".join("?" for _ in req.question_ids)
    owned = db.execute(
        f"""SELECT id, subject_id FROM questions
            WHERE id IN ({placeholders}) AND user_id = ?""",
        (*req.question_ids, user["id"]),
    ).fetchall()
    if not owned:
        return {"message": "No matching questions", "updated": 0}

    if req.category_id is not None:
        subject_ids = {row["subject_id"] for row in owned}
        cat = db.execute(
            "SELECT subject_id FROM categories WHERE id = ?", (req.category_id,)
        ).fetchone()
        if not cat or cat["subject_id"] not in subject_ids:
            raise HTTPException(
                status_code=400, detail="Category does not belong to the question's subject"
            )
    if req.subcategory_id is not None:
        sub = db.execute(
            "SELECT category_id FROM subcategories WHERE id = ?", (req.subcategory_id,)
        ).fetchone()
        if not sub or sub["category_id"] != req.category_id:
            raise HTTPException(
                status_code=400, detail="Subcategory does not belong to the category"
            )

    owned_ids = [row["id"] for row in owned]
    ph = ",".join("?" for _ in owned_ids)
    db.execute(
        f"UPDATE questions SET category_id = ?, subcategory_id = ? WHERE id IN ({ph})",
        (req.category_id, req.subcategory_id, *owned_ids),
    )
    db.commit()
    return {"message": "Tagged", "updated": len(owned_ids)}
