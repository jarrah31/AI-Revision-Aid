"""
MCQ pre-generation service.

Extracts the distractor-generation logic so it can be called both from:
- quiz/start (lazy safety-net)
- question approval (background pre-generation)
"""
import sqlite3

from backend.database import DB_PATH
from backend.services.claude_service import generate_mcq_distractors


def ensure_mcq_options(questions: list[dict], db: sqlite3.Connection, user_id: int) -> None:
    """Generate and store MCQ distractors for questions that don't have them yet.

    Uses the provided DB connection (suitable for use within an existing request).
    """
    need_mcq = [
        q for q in questions
        if db.execute(
            "SELECT COUNT(*) as c FROM mcq_options WHERE question_id = ?", (q["id"],)
        ).fetchone()["c"] == 0
    ]

    if not need_mcq:
        return

    # Group by subject for richer distractor context
    by_subject: dict[str, list] = {}
    for q in need_mcq:
        subj = q.get("subject_name", "General")
        by_subject.setdefault(subj, []).append(q)

    for subject, qs in by_subject.items():
        try:
            results, usage = generate_mcq_distractors(qs, subject)
            db.execute(
                """INSERT INTO api_usage
                   (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
                   VALUES (?, NULL, 'mcq_generation', ?, ?, ?)""",
                (user_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
            )
            for result in results:
                qid = result["question_id"]
                q_match = next((q for q in qs if q["id"] == qid), None)
                if q_match:
                    db.execute(
                        "INSERT OR IGNORE INTO mcq_options (question_id, option_text, is_correct) VALUES (?, ?, 1)",
                        (qid, q_match["answer_text"]),
                    )
                    for distractor in result.get("distractors", []):
                        db.execute(
                            "INSERT OR IGNORE INTO mcq_options (question_id, option_text, is_correct) VALUES (?, ?, 0)",
                            (qid, distractor),
                        )
            db.commit()
        except Exception as e:
            print(f"MCQ generation failed for subject '{subject}': {e}")


def ensure_mcq_options_bg(question_ids: list[int], user_id: int) -> None:
    """Background-task variant: opens its own DB connection.

    Safe to call from FastAPI BackgroundTasks (runs after response is sent).
    """
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        # Load full question data for each ID
        questions = []
        for qid in question_ids:
            row = db.execute(
                """SELECT q.*, s.name as subject_name
                   FROM questions q
                   LEFT JOIN subjects s ON s.id = q.subject_id
                   WHERE q.id = ?""",
                (qid,),
            ).fetchone()
            if row:
                questions.append(dict(row))

        if questions:
            ensure_mcq_options(questions, db, user_id)
    finally:
        db.close()
