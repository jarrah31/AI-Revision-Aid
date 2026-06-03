import io
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from PIL import Image

from backend.auth import get_current_user
from backend.database import get_db
from backend.services.image_service import delete_batch_images, delete_batch_pdf
from backend.services.multi_response_service import detect_and_store_multi_response
from backend.services.pdf_processor import crop_section_to_bytes

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


class RecropRequest(BaseModel):
    bbox_x_pct: float
    bbox_y_pct: float
    bbox_w_pct: float
    bbox_h_pct: float


@router.post("/questions/{question_id}/recrop")
def recrop_figure(
    question_id: int,
    req: RecropRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Re-crop a question's figure from the saved full-page image. Creates a NEW image row
    (never mutates a shared one) and repoints this question's image_id at it."""
    q = db.execute(
        "SELECT id, batch_id, page_number FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    if not (0 <= req.bbox_x_pct <= 100 and 0 <= req.bbox_y_pct <= 100
            and req.bbox_w_pct > 0 and req.bbox_h_pct > 0
            and req.bbox_x_pct + req.bbox_w_pct <= 100.5
            and req.bbox_y_pct + req.bbox_h_pct <= 100.5):
        raise HTTPException(status_code=400, detail="Invalid bounding box")

    full_page = DATA_DIR / "images" / f"batch_{q['batch_id']}" / f"page_{q['page_number']}_full.png"
    if not full_page.exists():
        raise HTTPException(
            status_code=404,
            detail="Original page image unavailable; re-upload the paper to enable re-cropping",
        )

    png_bytes = full_page.read_bytes()
    cropped = crop_section_to_bytes(
        png_bytes, req.bbox_x_pct, req.bbox_y_pct, req.bbox_w_pct, req.bbox_h_pct
    )
    width, height = Image.open(io.BytesIO(cropped)).size

    rel_name = f"batch_{q['batch_id']}/page_{q['page_number']}_recrop_q{question_id}.png"
    out_path = DATA_DIR / "images" / rel_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(cropped)

    cur = db.execute(
        """INSERT INTO images (batch_id, page_number, filename, description,
           crop_x, crop_y, crop_w, crop_h, width, height)
           VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?)""",
        (q["batch_id"], q["page_number"], rel_name,
         req.bbox_x_pct, req.bbox_y_pct, req.bbox_w_pct, req.bbox_h_pct, width, height),
    )
    new_image_id = cur.lastrowid
    db.execute("UPDATE questions SET image_id = ? WHERE id = ?", (new_image_id, question_id))
    db.commit()
    return {"image_id": new_image_id, "filename": rel_name}


class SetImageRequest(BaseModel):
    image_id: int | None = None


@router.put("/questions/{question_id}/image")
def set_question_image(
    question_id: int,
    req: SetImageRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Attach an existing same-batch figure to a question, or detach (image_id=null)."""
    q = db.execute(
        "SELECT id, batch_id FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    if req.image_id is not None:
        img = db.execute(
            "SELECT batch_id FROM images WHERE id = ?", (req.image_id,)
        ).fetchone()
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        if img["batch_id"] != q["batch_id"]:
            raise HTTPException(status_code=400, detail="Image belongs to a different paper")

    db.execute(
        "UPDATE questions SET image_id = ? WHERE id = ?", (req.image_id, question_id)
    )
    db.commit()
    return {"message": "Image updated", "image_id": req.image_id}


@router.post("/{batch_id}/detect-multi-response")
def detect_multi_response(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Re-scan an existing past-paper batch for 'tick N boxes' multiple-response
    questions and store their structured form. Returns counts."""
    batch = db.execute(
        "SELECT b.id, s.name as subject_name FROM upload_batches b "
        "LEFT JOIN subjects s ON s.id = b.subject_id "
        "WHERE b.id = ? AND b.user_id = ?",
        (batch_id, user["id"]),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Past paper not found")

    scanned = db.execute(
        "SELECT COUNT(*) AS c FROM questions "
        "WHERE batch_id = ? AND user_id = ? AND question_source = 'past_paper'",
        (batch_id, user["id"]),
    ).fetchone()["c"]

    updated = detect_and_store_multi_response(
        batch_id, batch["subject_name"] or "General", user["id"], db
    )
    return {"updated": updated, "scanned": scanned}
