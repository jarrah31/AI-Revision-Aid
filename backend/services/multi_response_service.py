"""Detect and store structured multiple-response ('tick N boxes') questions.

Operates on already-extracted PAST-PAPER questions for a batch. Used by:
- the upload post-processing step (automatic, after extraction + mark scheme), and
- the Past Papers 'Re-detect' endpoint (on demand for existing papers).
"""
import json
import sqlite3

from backend.services.claude_service import detect_multiple_response_batch


def detect_and_store_multi_response(
    batch_id: int, subject: str, user_id: int, db: sqlite3.Connection
) -> int:
    """Run detection over a batch's past-paper questions; persist structured ones.

    Returns the number of questions updated. Makes no AI call when the batch has
    no past-paper questions. Records cost in api_usage.
    """
    rows = db.execute(
        """SELECT id, question_text, answer_text FROM questions
           WHERE batch_id = ? AND user_id = ? AND question_source = 'past_paper'""",
        (batch_id, user_id),
    ).fetchall()
    questions = [dict(r) for r in rows]
    if not questions:
        return 0

    try:
        results, usage = detect_multiple_response_batch(questions, subject)
    except Exception as e:  # non-fatal: detection is an enhancement
        print(f"[multi_response] detection failed for batch {batch_id}: {e}")
        return 0

    db.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
           VALUES (?, ?, 'multi_response_detection', ?, ?, ?)""",
        (user_id, batch_id, usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
    )

    updated = 0
    for q, result in zip(questions, results):
        if not result:
            continue
        db.execute(
            "UPDATE questions SET question_text = ?, options_json = ?, updated_at = datetime('now') WHERE id = ?",
            (result["stem"], json.dumps({
                "select_count": result["select_count"],
                "options": result["options"],
            }), q["id"]),
        )
        updated += 1
    db.commit()
    return updated
