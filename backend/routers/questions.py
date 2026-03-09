import json
import sqlite3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import QuestionUpdate
from backend.services.claude_service import fact_check_question
from backend.services.mcq_service import ensure_mcq_options_bg

router = APIRouter()


@router.get("")
def list_questions(
    subject_id: int | None = None,
    batch_id: int | None = None,
    category_id: int | None = None,
    approved: int | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    conditions = ["q.user_id = ?"]
    params = [user["id"]]

    if subject_id is not None:
        conditions.append("q.subject_id = ?")
        params.append(subject_id)
    if batch_id is not None:
        conditions.append("q.batch_id = ?")
        params.append(batch_id)
    if category_id is not None:
        conditions.append("q.category_id = ?")
        params.append(category_id)
    if approved is not None:
        conditions.append("q.approved = ?")
        params.append(approved)

    where = " AND ".join(conditions)
    offset = (page - 1) * limit
    params.extend([limit, offset])

    rows = db.execute(
        f"""SELECT q.*, i.filename as image_filename, i.description as image_description,
                   s.name as subject_name, c.name as category_name
            FROM questions q
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            WHERE {where}
            ORDER BY q.page_number, q.id
            LIMIT ? OFFSET ?""",
        params,
    ).fetchall()

    count_params = params[:-2]  # without limit/offset
    total = db.execute(
        f"SELECT COUNT(*) as c FROM questions q WHERE {where}",
        count_params,
    ).fetchone()["c"]

    return {
        "questions": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/{question_id}/fact-check")
def fact_check(
    question_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Fact-check a question/answer using Claude with live web search.
    Returns a cached result immediately if one exists; otherwise calls Claude and stores the result."""
    row = db.execute(
        """SELECT q.*, s.name as subject_name
           FROM questions q
           JOIN subjects s ON s.id = q.subject_id
           WHERE q.id = ? AND q.user_id = ?""",
        (question_id, user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")

    q = dict(row)

    # Return cached result without calling Claude again
    if q.get("fact_check_result"):
        return json.loads(q["fact_check_result"])

    result, usage = fact_check_question(
        question=q["question_text"],
        answer=q["answer_text"],
        subject=q["subject_name"],
    )

    # Persist the result so future calls are free
    db.execute(
        "UPDATE questions SET fact_check_result = ?, fact_checked_at = datetime('now') WHERE id = ?",
        (json.dumps(result), question_id),
    )

    # Record cost in api_usage
    db.execute(
        """INSERT INTO api_usage (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, 'fact_check', ?, ?, ?)""",
        (user["id"], q.get("batch_id"), usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
    )
    db.commit()

    return result


@router.get("/export")
def export_questions(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Export all approved questions with SRS data for offline cache."""
    rows = db.execute(
        """SELECT q.*, i.filename as image_filename,
                  s.name as subject_name,
                  sc.easiness_factor, sc.interval_days, sc.repetitions, sc.next_review_date
           FROM questions q
           LEFT JOIN images i ON i.id = q.image_id
           LEFT JOIN subjects s ON s.id = q.subject_id
           LEFT JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
           WHERE q.user_id = ? AND q.approved = 1
           ORDER BY q.subject_id, q.id""",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{question_id}")
def get_question(
    question_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute(
        """SELECT q.*, i.filename as image_filename, i.description as image_description,
                  s.name as subject_name,
                  b.page_start, b.page_end, b.pdf_path
           FROM questions q
           LEFT JOIN images i ON i.id = q.image_id
           LEFT JOIN subjects s ON s.id = q.subject_id
           LEFT JOIN upload_batches b ON b.id = q.batch_id
           WHERE q.id = ? AND q.user_id = ?""",
        (question_id, user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Question not found")
    return dict(row)


@router.put("/{question_id}")
def update_question(
    question_id: int,
    req: QuestionUpdate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute(
        "SELECT id FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")

    updates = []
    params = []
    if req.question_text is not None:
        updates.append("question_text = ?")
        params.append(req.question_text)
    if req.answer_text is not None:
        updates.append("answer_text = ?")
        params.append(req.answer_text)
    if req.question_type is not None:
        updates.append("question_type = ?")
        params.append(req.question_type)
    if req.difficulty is not None:
        updates.append("difficulty = ?")
        params.append(req.difficulty)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(question_id)
    db.execute(f"UPDATE questions SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return {"message": "Question updated"}


@router.post("/{question_id}/approve")
def approve_question(
    question_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute(
        "SELECT id FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")

    db.execute("UPDATE questions SET approved = 1 WHERE id = ?", (question_id,))
    db.commit()
    # Pre-generate MCQ options in the background so quiz starts are instant
    background_tasks.add_task(ensure_mcq_options_bg, [question_id], user["id"])
    return {"message": "Question approved"}


@router.post("/approve-batch")
def approve_batch(
    background_tasks: BackgroundTasks,
    batch_id: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    # Capture IDs before updating so we know which ones need MCQ generation
    to_approve = db.execute(
        "SELECT id FROM questions WHERE batch_id = ? AND user_id = ? AND approved = 0",
        (batch_id, user["id"]),
    ).fetchall()
    question_ids = [r["id"] for r in to_approve]

    count = db.execute(
        "UPDATE questions SET approved = 1 WHERE batch_id = ? AND user_id = ? AND approved = 0",
        (batch_id, user["id"]),
    ).rowcount
    db.commit()

    if question_ids:
        background_tasks.add_task(ensure_mcq_options_bg, question_ids, user["id"])
    return {"message": f"{count} questions approved"}


@router.post("/approve-page")
def approve_page(
    background_tasks: BackgroundTasks,
    batch_id: int = Query(...),
    page_number: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    to_approve = db.execute(
        "SELECT id FROM questions WHERE batch_id = ? AND page_number = ? AND user_id = ? AND approved = 0",
        (batch_id, page_number, user["id"]),
    ).fetchall()
    question_ids = [r["id"] for r in to_approve]

    count = db.execute(
        "UPDATE questions SET approved = 1 WHERE batch_id = ? AND page_number = ? AND user_id = ? AND approved = 0",
        (batch_id, page_number, user["id"]),
    ).rowcount
    db.commit()

    if question_ids:
        background_tasks.add_task(ensure_mcq_options_bg, question_ids, user["id"])
    return {"message": f"{count} questions approved"}


@router.delete("/{question_id}")
def delete_question(
    question_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute(
        "SELECT id FROM questions WHERE id = ? AND user_id = ?",
        (question_id, user["id"]),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")

    db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    db.commit()
    return {"message": "Question deleted"}
