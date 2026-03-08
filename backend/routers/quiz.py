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
    category_id: int | None = None
    count: int = 20
    mode: str = "mixed"  # flashcard, mcq, typed, mixed


class AnswerRequest(BaseModel):
    question_id: int
    quiz_format: str  # flashcard, mcq, typed
    student_answer: str | None = None
    quality_rating: int | None = None  # for flashcard self-rating
    time_taken_ms: int | None = None


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
    if req.category_id:
        conditions.append("q.category_id = ?")
        params.append(req.category_id)
    where = " AND ".join(conditions)

    # 1. Overdue cards
    overdue = db.execute(
        f"""SELECT q.*, sc.easiness_factor, sc.interval_days, sc.repetitions,
                   sc.next_review_date, i.filename as image_filename,
                   s.name as subject_name, c.name as category_name
            FROM questions q
            JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            WHERE {where} AND sc.next_review_date < ?
            ORDER BY sc.next_review_date ASC""",
        params + [today],
    ).fetchall()

    # 2. Due today
    due_today = db.execute(
        f"""SELECT q.*, sc.easiness_factor, sc.interval_days, sc.repetitions,
                   sc.next_review_date, i.filename as image_filename,
                   s.name as subject_name, c.name as category_name
            FROM questions q
            JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
            WHERE {where} AND sc.next_review_date = ?""",
        params + [today],
    ).fetchall()

    # 3. New cards (no SRS entry yet) - fill remaining slots up to req.count
    new_cards = db.execute(
        f"""SELECT q.*, NULL as easiness_factor, NULL as interval_days,
                   NULL as repetitions, NULL as next_review_date,
                   i.filename as image_filename, s.name as subject_name,
                   c.name as category_name
            FROM questions q
            LEFT JOIN srs_cards sc ON sc.question_id = q.id AND sc.user_id = q.user_id
            LEFT JOIN images i ON i.id = q.image_id
            LEFT JOIN subjects s ON s.id = q.subject_id
            LEFT JOIN categories c ON c.id = q.category_id
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

    # Create quiz session
    cursor = db.execute(
        """INSERT INTO quiz_sessions (user_id, subject_id, quiz_mode, total_questions)
           VALUES (?, ?, ?, ?)""",
        (user["id"], req.subject_id, req.mode, len(selected)),
    )
    db.commit()
    session_id = cursor.lastrowid

    # For MCQ mode, ensure distractors exist (usually pre-generated at approval time)
    if req.mode in ("mcq", "mixed"):
        ensure_mcq_options(selected, db, user["id"])

    # Add MCQ options to questions that have them
    for q in selected:
        options = db.execute(
            "SELECT option_text, is_correct FROM mcq_options WHERE question_id = ?",
            (q["id"],),
        ).fetchall()
        if options:
            opts = [dict(o) for o in options]
            random.shuffle(opts)
            q["mcq_options"] = opts
        else:
            q["mcq_options"] = []

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
            quality_rating, time_taken_ms, ai_feedback)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
        "UPDATE quiz_sessions SET completed_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    db.commit()

    return {
        "total": session["total_questions"],
        "correct": session["correct_count"],
        "incorrect": session["incorrect_count"],
    }


@router.get("/{session_id}")
def get_session(
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

    answers = db.execute(
        """SELECT qa.*, q.question_text, q.answer_text
           FROM quiz_answers qa
           JOIN questions q ON q.id = qa.question_id
           WHERE qa.session_id = ?
           ORDER BY qa.answered_at""",
        (session_id,),
    ).fetchall()

    return {"session": dict(session), "answers": [dict(a) for a in answers]}
