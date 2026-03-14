import sqlite3
import json
import random
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import get_current_user
from backend.database import get_db
from backend.services.spaced_repetition import sm2_update
from backend.services.claude_service import judge_typed_answer
from backend.services.mcq_service import ensure_mcq_options

router = APIRouter()


class QuizStartRequest(BaseModel):
    subject_id: int | None = None
    category_ids: list[int] | None = None      # multi-select
    subcategory_ids: list[int] | None = None   # multi-select
    count: int = 20
    mode: str = "mixed"  # flashcard, mcq, typed, mixed
    question_sources: list[str] | None = None  # e.g. ['ai_generated'], ['past_paper'], None = all


class AnswerRequest(BaseModel):
    question_id: int
    quiz_format: str  # flashcard, mcq, typed
    student_answer: str | None = None
    quality_rating: int | None = None  # for flashcard self-rating
    time_taken_ms: int | None = None
    is_skipped: bool = False


class ProgressUpdate(BaseModel):
    current_index: int


def _cat_subcat_filter(category_ids: list[int] | None, subcategory_ids: list[int] | None):
    """Return (sql_fragment, params) for multi-select category/subcategory filtering.

    Narrowing logic:
    - Only categories: include all questions from those categories.
    - Only subcategories: include questions from those specific subcategories.
    - Both: subcategory selections narrow their parent category; categories that have
      NO selected subcategory are included in full.
      e.g. categories=[Bio, Chem], subcategories=[Cells (Bio)]
           → Cells questions  +  all Chemistry questions

    Returns (None, []) when neither list has entries (no filter applied).
    """
    cats = list(category_ids) if category_ids else []
    subs = list(subcategory_ids) if subcategory_ids else []

    if not cats and not subs:
        return None, []

    if cats and not subs:
        ph = ",".join("?" * len(cats))
        return f"q.category_id IN ({ph})", cats

    if subs and not cats:
        ph = ",".join("?" * len(subs))
        return f"q.subcategory_id IN ({ph})", subs

    # Both provided: subcategories narrow their parent; unnarrowed categories included whole
    sub_ph = ",".join("?" * len(subs))
    cat_ph = ",".join("?" * len(cats))
    sql = (
        f"(q.subcategory_id IN ({sub_ph})"
        f" OR (q.category_id IN ({cat_ph})"
        f"     AND q.category_id NOT IN"
        f"     (SELECT sc2.category_id FROM subcategories sc2 WHERE sc2.id IN ({sub_ph}))))"
    )
    # params: subs (first IN), cats, subs again (NOT IN subquery)
    return sql, subs + cats + subs


@router.get("/count")
def get_question_count(
    subject_id: int | None = Query(None),
    category_ids: list[int] | None = Query(None),
    subcategory_ids: list[int] | None = Query(None),
    question_sources: list[str] | None = Query(None),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return the total number of approved questions matching the given filters."""
    conditions = ["q.user_id = ?", "q.approved = 1"]
    params: list = [user["id"]]
    if subject_id:
        conditions.append("q.subject_id = ?")
        params.append(subject_id)
    cat_filter, cat_params = _cat_subcat_filter(category_ids, subcategory_ids)
    if cat_filter:
        conditions.append(cat_filter)
        params.extend(cat_params)
    if question_sources:
        placeholders = ",".join("?" * len(question_sources))
        conditions.append(f"q.question_source IN ({placeholders})")
        params.extend(question_sources)
    where = " AND ".join(conditions)
    row = db.execute(f"SELECT COUNT(*) FROM questions q WHERE {where}", params).fetchone()
    return {"count": row[0]}


@router.get("/in-progress")
def get_in_progress_quizzes(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return all incomplete quiz sessions that have saved questions (resumable)."""
    sessions = db.execute(
        """SELECT qs.id, qs.quiz_mode, qs.total_questions, qs.current_index,
                  qs.question_sources_json, qs.started_at,
                  qs.category_ids_json, qs.subcategory_ids_json,
                  s.name as subject_name, c.name as category_name
           FROM quiz_sessions qs
           LEFT JOIN subjects s ON s.id = qs.subject_id
           LEFT JOIN categories c ON c.id = qs.category_id
           WHERE qs.user_id = ? AND qs.completed_at IS NULL AND qs.questions_json IS NOT NULL
           ORDER BY qs.started_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(s) for s in sessions]


@router.post("/start")
def start_quiz(
    req: QuizStartRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    today = date.today().isoformat()

    # Build base query for approved questions
    conditions = ["q.user_id = ?", "q.approved = 1"]
    params = [user["id"]]
    if req.subject_id:
        conditions.append("q.subject_id = ?")
        params.append(req.subject_id)
    cat_filter, cat_params = _cat_subcat_filter(req.category_ids, req.subcategory_ids)
    if cat_filter:
        conditions.append(cat_filter)
        params.extend(cat_params)
    if req.question_sources:
        placeholders = ",".join("?" * len(req.question_sources))
        conditions.append(f"q.question_source IN ({placeholders})")
        params.extend(req.question_sources)
    where = " AND ".join(conditions)

    # 1. Overdue cards
    overdue = db.execute(
        f"""SELECT q.*, sc.easiness_factor, sc.interval_days, sc.repetitions,
                   sc.next_review_date, i.filename as image_filename,
                   s.name as subject_name, c.name as category_name,
                   sub.name as subcategory_name
            FROM questions q
            JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            LEFT JOIN subcategories sub ON sub.id = q.subcategory_id
            WHERE {where} AND sc.next_review_date < ?
            ORDER BY sc.next_review_date ASC""",
        params + [today],
    ).fetchall()

    # 2. Due today
    due_today = db.execute(
        f"""SELECT q.*, sc.easiness_factor, sc.interval_days, sc.repetitions,
                   sc.next_review_date, i.filename as image_filename,
                   s.name as subject_name, c.name as category_name,
                   sub.name as subcategory_name
            FROM questions q
            JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            LEFT JOIN subcategories sub ON sub.id = q.subcategory_id
            WHERE {where} AND sc.next_review_date = ?""",
        params + [today],
    ).fetchall()

    # 3. New cards (no SRS entry yet) - fill remaining slots up to req.count
    new_cards = db.execute(
        f"""SELECT q.*, NULL as easiness_factor, NULL as interval_days,
                   NULL as repetitions, NULL as next_review_date,
                   i.filename as image_filename, s.name as subject_name,
                   c.name as category_name, sub.name as subcategory_name
            FROM questions q
            LEFT JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            LEFT JOIN subcategories sub ON sub.id = q.subcategory_id
            WHERE {where} AND sc.id IS NULL
            LIMIT {req.count}""",
        params,
    ).fetchall()

    # Combine and limit
    all_cards = [dict(r) for r in overdue] + [dict(r) for r in due_today] + [dict(r) for r in new_cards]
    random.shuffle(all_cards)
    selected = all_cards[: req.count]

    if not selected:
        return {"session_id": None, "questions": [], "message": "No cards due for review"}

    # For MCQ mode, ensure distractors exist (usually pre-generated at approval time)
    if req.mode in ("mcq", "mixed"):
        ensure_mcq_options(selected, db, user["id"])

    # Add MCQ options to questions that have them (max 4: 1 correct + 3 distractors)
    for q in selected:
        options = db.execute(
            "SELECT option_text, is_correct FROM mcq_options WHERE question_id = ?",
            (q["id"],),
        ).fetchall()
        if options:
            opts = [dict(o) for o in options]
            correct = [o for o in opts if o["is_correct"]]
            wrong = [o for o in opts if not o["is_correct"]]
            random.shuffle(wrong)
            combined = correct + wrong[:3]  # 1 correct + up to 3 distractors = max 4
            random.shuffle(combined)
            q["mcq_options"] = combined
        else:
            q["mcq_options"] = []

    # Look up names for the selected categories/subcategories (stored for display)
    def _fetch_named_items(table: str, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        rows = db.execute(
            f"SELECT id, name FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()
        name_map = {r["id"]: r["name"] for r in rows}
        return [{"id": i, "name": name_map.get(i, "")} for i in ids]

    cat_items = _fetch_named_items("categories", req.category_ids or [])
    subcat_items = _fetch_named_items("subcategories", req.subcategory_ids or [])

    # first IDs for backward-compat category_id / subcategory_id columns
    first_cat_id = req.category_ids[0] if req.category_ids else None
    first_subcat_id = req.subcategory_ids[0] if req.subcategory_ids else None

    # Create quiz session — store full questions JSON for cross-device resumption
    cursor = db.execute(
        """INSERT INTO quiz_sessions
               (user_id, subject_id, category_id, subcategory_id,
                category_ids_json, subcategory_ids_json,
                quiz_mode, total_questions, questions_json, question_sources_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user["id"], req.subject_id, first_cat_id, first_subcat_id,
            json.dumps(cat_items) if cat_items else None,
            json.dumps(subcat_items) if subcat_items else None,
            req.mode, len(selected),
            json.dumps(selected), json.dumps(req.question_sources or []),
        ),
    )
    db.commit()
    session_id = cursor.lastrowid

    return {"session_id": session_id, "questions": selected}


@router.post("/{session_id}/answer")
def submit_answer(
    session_id: int,
    req: AnswerRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    # Verify session belongs to user
    session = db.execute(
        "SELECT * FROM quiz_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get the question
    question = db.execute(
        """SELECT q.*, s.name as subject_name
           FROM questions q
           LEFT JOIN subjects s ON s.id = q.subject_id
           WHERE q.id = ?""",
        (req.question_id,),
    ).fetchone()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # ── Skip fast-path ────────────────────────────────────────────────────────
    if req.is_skipped:
        db.execute(
            """INSERT INTO quiz_answers
               (session_id, question_id, quiz_format, student_answer, is_correct,
                quality_rating, time_taken_ms, ai_feedback, is_skipped)
               VALUES (?, ?, ?, NULL, 0, 1, ?, NULL, 1)""",
            (session_id, req.question_id, req.quiz_format, req.time_taken_ms),
        )
        db.execute(
            "UPDATE quiz_sessions SET skipped_count = skipped_count + 1 WHERE id = ?",
            (session_id,),
        )
        # Treat skip as quality=1 (worst) so the card surfaces again soon
        srs = db.execute(
            "SELECT * FROM srs_cards WHERE user_id = ? AND question_id = ?",
            (user["id"], req.question_id),
        ).fetchone()
        update = sm2_update(1, srs["easiness_factor"], srs["interval_days"], srs["repetitions"]) if srs else sm2_update(1)
        if srs:
            db.execute(
                """UPDATE srs_cards
                   SET easiness_factor = ?, interval_days = ?, repetitions = ?,
                       next_review_date = ?, last_reviewed_at = datetime('now')
                   WHERE id = ?""",
                (update.easiness_factor, update.interval_days, update.repetitions,
                 update.next_review_date.isoformat(), srs["id"]),
            )
        else:
            db.execute(
                """INSERT INTO srs_cards
                   (user_id, question_id, easiness_factor, interval_days, repetitions,
                    next_review_date, last_reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (user["id"], req.question_id, update.easiness_factor, update.interval_days,
                 update.repetitions, update.next_review_date.isoformat()),
            )
        db.commit()
        return {"is_correct": False, "correct_answer": question["answer_text"], "feedback": None, "quality": 1}
    # ─────────────────────────────────────────────────────────────────────────

    is_correct = None
    ai_feedback = None
    quality = req.quality_rating

    if req.quiz_format == "flashcard":
        # Self-rated: quality comes from the request
        is_correct = 1 if (quality and quality >= 3) else 0

    elif req.quiz_format == "mcq":
        # Check if selected answer is correct
        if req.student_answer:
            correct_opt = db.execute(
                "SELECT option_text FROM mcq_options WHERE question_id = ? AND is_correct = 1",
                (req.question_id,),
            ).fetchone()
            is_correct = 1 if (correct_opt and req.student_answer == correct_opt["option_text"]) else 0
            quality = 4 if is_correct else 1

    elif req.quiz_format == "typed":
        # Ask Claude to judge
        try:
            result, usage = judge_typed_answer(
                question["question_text"],
                question["answer_text"],
                req.student_answer or "",
                question["subject_name"] or "General",
            )
            db.execute(
                """INSERT INTO api_usage
                   (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
                   VALUES (?, NULL, 'answer_judging', ?, ?, ?)""",
                (user["id"], usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
            )
            verdict = result.get("verdict", "incorrect")
            is_correct = 1 if verdict == "correct" else 0
            ai_feedback = result.get("feedback", "")
            quality = result.get("quality_score", 1)
        except Exception as e:
            # Fallback: simple string comparison
            is_correct = 1 if (req.student_answer or "").lower().strip() == question["answer_text"].lower().strip() else 0
            quality = 5 if is_correct else 1
            ai_feedback = "Auto-judged (AI unavailable)"

    # Record answer
    db.execute(
        """INSERT INTO quiz_answers
           (session_id, question_id, quiz_format, student_answer, is_correct,
            quality_rating, time_taken_ms, ai_feedback, is_skipped)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
        (session_id, req.question_id, req.quiz_format, req.student_answer,
         is_correct, quality, req.time_taken_ms, ai_feedback),
    )

    # Update session counts
    if is_correct:
        db.execute("UPDATE quiz_sessions SET correct_count = correct_count + 1 WHERE id = ?", (session_id,))
    elif is_correct == 0:
        db.execute("UPDATE quiz_sessions SET incorrect_count = incorrect_count + 1 WHERE id = ?", (session_id,))

    # Update SRS
    if quality is not None:
        srs = db.execute(
            "SELECT * FROM srs_cards WHERE user_id = ? AND question_id = ?",
            (user["id"], req.question_id),
        ).fetchone()

        if srs:
            update = sm2_update(quality, srs["easiness_factor"], srs["interval_days"], srs["repetitions"])
            db.execute(
                """UPDATE srs_cards
                   SET easiness_factor = ?, interval_days = ?, repetitions = ?,
                       next_review_date = ?, last_reviewed_at = datetime('now')
                   WHERE id = ?""",
                (update.easiness_factor, update.interval_days, update.repetitions,
                 update.next_review_date.isoformat(), srs["id"]),
            )
        else:
            update = sm2_update(quality)
            db.execute(
                """INSERT INTO srs_cards
                   (user_id, question_id, easiness_factor, interval_days, repetitions,
                    next_review_date, last_reviewed_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (user["id"], req.question_id, update.easiness_factor, update.interval_days,
                 update.repetitions, update.next_review_date.isoformat()),
            )

    db.commit()

    return {
        "is_correct": bool(is_correct),
        "correct_answer": question["answer_text"],
        "feedback": ai_feedback,
        "quality": quality,
    }


@router.post("/{session_id}/complete")
def complete_session(
    session_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    session = db.execute(
        "SELECT * FROM quiz_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.execute(
        "UPDATE quiz_sessions SET completed_at = datetime('now'), questions_json = NULL WHERE id = ?",
        (session_id,),
    )
    db.commit()

    return {
        "total": session["total_questions"],
        "correct": session["correct_count"],
        "incorrect": session["incorrect_count"],
        "skipped": session["skipped_count"],
    }


@router.put("/{session_id}/progress")
def update_progress(
    session_id: int,
    req: ProgressUpdate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Fire-and-forget endpoint to persist the current question index."""
    session = db.execute(
        "SELECT id FROM quiz_sessions WHERE id = ? AND user_id = ? AND completed_at IS NULL",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.execute(
        "UPDATE quiz_sessions SET current_index = ? WHERE id = ?",
        (req.current_index, session_id),
    )
    db.commit()
    return {"ok": True}


@router.delete("/{session_id}/progress")
def abandon_quiz(
    session_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Discard a saved in-progress quiz (marks it completed and clears stored questions)."""
    session = db.execute(
        "SELECT id FROM quiz_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    db.execute(
        "UPDATE quiz_sessions SET questions_json = NULL, completed_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    db.commit()
    return {"ok": True}


@router.get("/{session_id}/resume")
def resume_quiz(
    session_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return the saved question list and progress for an in-progress session."""
    session = db.execute(
        """SELECT qs.*, s.name as subject_name
           FROM quiz_sessions qs
           LEFT JOIN subjects s ON s.id = qs.subject_id
           WHERE qs.id = ? AND qs.user_id = ? AND qs.completed_at IS NULL
             AND qs.questions_json IS NOT NULL""",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or no saved progress")

    session_dict = dict(session)
    questions = json.loads(session_dict["questions_json"] or "[]")
    question_sources = json.loads(session_dict["question_sources_json"] or "[]")

    return {
        "session_id": session_id,
        "current_index": session_dict["current_index"],
        "quiz_mode": session_dict["quiz_mode"],
        "question_sources": question_sources,
        "questions": questions,
    }


@router.get("/{session_id}")
def get_session(
    session_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    session = db.execute(
        """SELECT qs.*, s.name as subject_name, c.name as category_name,
                  sc.name as subcategory_name
           FROM quiz_sessions qs
           LEFT JOIN subjects s ON s.id = qs.subject_id
           LEFT JOIN categories c ON c.id = qs.category_id
           LEFT JOIN subcategories sc ON sc.id = qs.subcategory_id
           WHERE qs.id = ? AND qs.user_id = ?""",
        (session_id, user["id"]),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    answers = db.execute(
        """SELECT qa.*, q.question_text, q.answer_text
           FROM quiz_answers qa
           JOIN questions q ON q.id = qa.question_id
           WHERE qa.session_id = ?
           ORDER BY qa.answered_at""",
        (session_id,),
    ).fetchall()

    # Exclude large fields from session dict returned to review page
    session_dict = dict(session)
    session_dict.pop("questions_json", None)
    session_dict.pop("question_sources_json", None)

    return {"session": session_dict, "answers": [dict(a) for a in answers]}
