import sqlite3
from datetime import date
from fastapi import APIRouter, Depends

from backend.auth import get_current_user
from backend.database import get_db

router = APIRouter()


@router.get("/summary")
def dashboard_summary(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    uid = user["id"]

    total_questions = db.execute(
        "SELECT COUNT(*) as c FROM questions WHERE user_id = ? AND approved = 1", (uid,)
    ).fetchone()["c"]

    total_sessions = db.execute(
        "SELECT COUNT(*) as c FROM quiz_sessions WHERE user_id = ?", (uid,)
    ).fetchone()["c"]

    total_answers = db.execute(
        """SELECT COUNT(*) as c FROM quiz_answers qa
           JOIN quiz_sessions qs ON qs.id = qa.session_id
           WHERE qs.user_id = ?""",
        (uid,),
    ).fetchone()["c"]

    correct_answers = db.execute(
        """SELECT COUNT(*) as c FROM quiz_answers qa
           JOIN quiz_sessions qs ON qs.id = qa.session_id
           WHERE qs.user_id = ? AND qa.is_correct = 1""",
        (uid,),
    ).fetchone()["c"]

    due_today = db.execute(
        "SELECT COUNT(*) as c FROM srs_cards WHERE user_id = ? AND next_review_date <= ?",
        (uid, date.today().isoformat()),
    ).fetchone()["c"]

    # Batches with questions awaiting review (completed, has unapproved questions)
    pending_review_batches = db.execute(
        """SELECT b.id, b.filename, s.name as subject_name,
                  COUNT(q.id) as pending_count
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           JOIN questions q ON q.batch_id = b.id
           WHERE b.user_id = ? AND b.status = 'completed' AND q.approved = 0
           GROUP BY b.id
           ORDER BY b.created_at DESC""",
        (uid,),
    ).fetchall()

    # Per-subject breakdown
    subjects = db.execute(
        """SELECT s.id, s.name, s.icon, s.color,
                  COUNT(DISTINCT q.id) as question_count,
                  (SELECT COUNT(*) FROM srs_cards sc
                   JOIN questions q2 ON q2.id = sc.question_id
                   WHERE sc.user_id = ? AND q2.subject_id = s.id
                     AND sc.next_review_date <= ?) as due_count
           FROM subjects s
           LEFT JOIN questions q ON q.subject_id = s.id AND q.user_id = ? AND q.approved = 1
           GROUP BY s.id
           HAVING question_count > 0
           ORDER BY s.name""",
        (uid, date.today().isoformat(), uid),
    ).fetchall()

    return {
        "total_questions": total_questions,
        "total_sessions": total_sessions,
        "total_answers": total_answers,
        "correct_answers": correct_answers,
        "accuracy": round(correct_answers / total_answers * 100) if total_answers > 0 else 0,
        "due_today": due_today,
        "pending_review_batches": [dict(r) for r in pending_review_batches],
        "subjects": [dict(s) for s in subjects],
    }


@router.get("/due-cards")
def due_cards(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    today = date.today().isoformat()
    rows = db.execute(
        """SELECT s.name as subject_name, s.color, COUNT(*) as count
           FROM srs_cards sc
           JOIN questions q ON q.id = sc.question_id
           JOIN subjects s ON s.id = q.subject_id
           WHERE sc.user_id = ? AND sc.next_review_date <= ?
           GROUP BY s.id
           ORDER BY count DESC""",
        (user["id"], today),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/history")
def quiz_history(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    rows = db.execute(
        """SELECT qs.*, s.name as subject_name, c.name as category_name,
                  sc.name as subcategory_name
           FROM quiz_sessions qs
           LEFT JOIN subjects s ON s.id = qs.subject_id
           LEFT JOIN categories c ON c.id = qs.category_id
           LEFT JOIN subcategories sc ON sc.id = qs.subcategory_id
           WHERE qs.user_id = ?
           ORDER BY qs.started_at DESC
           LIMIT 20""",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/subject/{subject_id}")
def subject_stats(
    subject_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    uid = user["id"]

    total = db.execute(
        "SELECT COUNT(*) as c FROM questions WHERE user_id = ? AND subject_id = ? AND approved = 1",
        (uid, subject_id),
    ).fetchone()["c"]

    answers = db.execute(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN qa.is_correct = 1 THEN 1 ELSE 0 END) as correct
           FROM quiz_answers qa
           JOIN quiz_sessions qs ON qs.id = qa.session_id
           JOIN questions q ON q.id = qa.question_id
           WHERE qs.user_id = ? AND q.subject_id = ?""",
        (uid, subject_id),
    ).fetchone()

    # Weakest questions (lowest easiness factor)
    weak = db.execute(
        """SELECT q.question_text, q.answer_text, sc.easiness_factor, sc.repetitions
           FROM srs_cards sc
           JOIN questions q ON q.id = sc.question_id
           WHERE sc.user_id = ? AND q.subject_id = ?
           ORDER BY sc.easiness_factor ASC
           LIMIT 5""",
        (uid, subject_id),
    ).fetchall()

    return {
        "total_questions": total,
        "total_answers": answers["total"] or 0,
        "correct_answers": answers["correct"] or 0,
        "accuracy": round((answers["correct"] or 0) / answers["total"] * 100) if answers["total"] else 0,
        "weakest_questions": [dict(w) for w in weak],
    }
