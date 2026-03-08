import sqlite3
from fastapi import APIRouter, Depends

from backend.auth import get_current_user
from backend.database import get_db
from backend.services import exchange

router = APIRouter()


@router.get("/rate")
def get_rate():
    """Return current USD→GBP exchange rate (cached 1 hour, no auth required)."""
    info = exchange.get_usd_to_gbp()
    return {
        "usd_to_gbp": info["rate"],
        "date": info["date"],
        "live": info["live"],
        "source": "frankfurter.app",
    }


@router.get("/summary")
def get_cost_summary(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return overall cost totals and per-type breakdown for the current user."""
    rows = db.execute(
        """SELECT call_type,
                  SUM(input_tokens) as input_tokens,
                  SUM(output_tokens) as output_tokens,
                  SUM(cost_usd) as cost_usd,
                  COUNT(*) as call_count
           FROM api_usage
           WHERE user_id = ?
           GROUP BY call_type""",
        (user["id"],),
    ).fetchall()

    breakdown = {r["call_type"]: dict(r) for r in rows}
    total_cost = sum(r["cost_usd"] for r in rows)
    total_input = sum(r["input_tokens"] for r in rows)
    total_output = sum(r["output_tokens"] for r in rows)
    total_calls = sum(r["call_count"] for r in rows)

    # This-month total
    month_cost = db.execute(
        """SELECT COALESCE(SUM(cost_usd), 0) as cost_usd
           FROM api_usage
           WHERE user_id = ? AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')""",
        (user["id"],),
    ).fetchone()["cost_usd"]

    _empty = {"cost_usd": 0, "call_count": 0, "input_tokens": 0, "output_tokens": 0}
    return {
        "total_cost_usd": total_cost,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_calls": total_calls,
        "this_month_usd": month_cost,
        "breakdown": {
            "scan":       breakdown.get("qa_extraction",  _empty),
            "mcq":        breakdown.get("mcq_generation", _empty),
            "judging":    breakdown.get("answer_judging", _empty),
            "fact_check": breakdown.get("fact_check",     _empty),
        },
    }


@router.get("/history")
def get_cost_history(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return per-batch upload history with cost data."""
    batches = db.execute(
        """SELECT b.id, b.filename, b.page_start, b.page_end, b.total_pages,
                  b.processed_pages, b.status, b.is_shared, b.cost_usd,
                  b.created_at, b.completed_at, b.error_message,
                  s.name as subject_name,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count,
                  (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id AND q.approved = 1) as approved_count,
                  (SELECT SUM(au.input_tokens) FROM api_usage au WHERE au.batch_id = b.id) as input_tokens,
                  (SELECT SUM(au.output_tokens) FROM api_usage au WHERE au.batch_id = b.id) as output_tokens,
                  (SELECT COUNT(*) FROM api_usage au WHERE au.batch_id = b.id) as api_calls
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           WHERE b.user_id = ?
           ORDER BY b.created_at DESC""",
        (user["id"],),
    ).fetchall()
    return [dict(b) for b in batches]
