import sqlite3
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel

from backend.auth import get_admin_user
from backend.database import get_db, DB_PATH
from backend.services.image_service import delete_batch_images, delete_batch_pdf
from backend.services.claude_service import validate_api_key, AI_SETTING_DEFAULTS
from backend.routers.upload import process_batch

DATA_DIR = Path(__file__).parent.parent.parent / "data"

router = APIRouter()


class UserUpdate(BaseModel):
    display_name: str | None = None
    year_group: int | None = None
    is_admin: int | None = None


class AdminQuestionUpdate(BaseModel):
    question_text: str | None = None
    answer_text: str | None = None


class ReprocessRequest(BaseModel):
    mode: str = "replace"  # replace or append


class SettingUpdate(BaseModel):
    value: str


# ── User Management ──

@router.get("/users")
def list_users(
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    rows = db.execute(
        """SELECT u.id, u.username, u.display_name, u.year_group, u.is_admin, u.created_at,
                  (SELECT COUNT(*) FROM questions q WHERE q.user_id = u.id) as question_count,
                  (SELECT MAX(qa.answered_at) FROM quiz_answers qa
                   JOIN quiz_sessions qs ON qs.id = qa.session_id
                   WHERE qs.user_id = u.id) as last_active,
                  (SELECT COALESCE(SUM(au.cost_usd), 0) FROM api_usage au
                   WHERE au.user_id = u.id) as total_cost_usd
           FROM users u ORDER BY u.created_at DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    req: UserUpdate,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    updates = []
    params = []
    if req.display_name is not None:
        updates.append("display_name = ?")
        params.append(req.display_name)
    if req.year_group is not None:
        updates.append("year_group = ?")
        params.append(req.year_group)
    if req.is_admin is not None:
        updates.append("is_admin = ?")
        params.append(req.is_admin)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(user_id)
    db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return {"message": "User updated"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    existing = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    # Clean up images for user's batches
    batches = db.execute("SELECT id FROM upload_batches WHERE user_id = ?", (user_id,)).fetchall()
    for b in batches:
        delete_batch_images(b["id"])
        delete_batch_pdf(b["id"])

    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return {"message": "User deleted"}


# ── Content Management ──

@router.get("/batches")
def list_all_batches(
    user_id: int | None = None,
    subject_id: int | None = None,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    conditions = ["1=1"]
    params = []
    if user_id:
        conditions.append("b.user_id = ?")
        params.append(user_id)
    if subject_id:
        conditions.append("b.subject_id = ?")
        params.append(subject_id)

    where = " AND ".join(conditions)
    rows = db.execute(
        f"""SELECT b.*, s.name as subject_name, u.display_name as owner_name,
                   u.username as owner_username,
                   (SELECT COUNT(*) FROM questions q WHERE q.batch_id = b.id) as question_count
            FROM upload_batches b
            JOIN subjects s ON s.id = b.subject_id
            JOIN users u ON u.id = b.user_id
            WHERE {where}
            ORDER BY b.created_at DESC""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/batches/{batch_id}/questions")
def get_batch_questions(
    batch_id: int,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batch = db.execute(
        """SELECT b.*, s.name as subject_name, u.display_name as owner_name
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           JOIN users u ON u.id = b.user_id
           WHERE b.id = ?""",
        (batch_id,),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    questions = db.execute(
        """SELECT q.*, i.filename as image_filename
           FROM questions q
           LEFT JOIN images i ON i.id = q.image_id
           WHERE q.batch_id = ?
           ORDER BY q.page_number, q.id""",
        (batch_id,),
    ).fetchall()

    return {"batch": dict(batch), "questions": [dict(q) for q in questions]}


@router.put("/questions/{question_id}")
def update_question(
    question_id: int,
    req: AdminQuestionUpdate,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM questions WHERE id = ?", (question_id,)).fetchone()
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

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(question_id)
    db.execute(f"UPDATE questions SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return {"message": "Question updated"}


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Question not found")

    db.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    db.commit()
    return {"message": "Question deleted"}


@router.delete("/batches/{batch_id}")
def delete_batch(
    batch_id: int,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM upload_batches WHERE id = ?", (batch_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Batch not found")

    delete_batch_images(batch_id)
    delete_batch_pdf(batch_id)
    db.execute("DELETE FROM upload_batches WHERE id = ?", (batch_id,))
    db.commit()
    return {"message": "Batch deleted"}


@router.put("/batches/{batch_id}/sharing")
def toggle_sharing(
    batch_id: int,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batch = db.execute("SELECT * FROM upload_batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    new_val = 0 if batch["is_shared"] else 1
    db.execute("UPDATE upload_batches SET is_shared = ? WHERE id = ?", (new_val, batch_id))
    db.commit()
    return {"is_shared": bool(new_val)}


@router.post("/batches/{batch_id}/reprocess")
def reprocess_batch(
    batch_id: int,
    req: ReprocessRequest,
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    batch = db.execute(
        """SELECT b.*, s.name as subject_name
           FROM upload_batches b
           JOIN subjects s ON s.id = b.subject_id
           WHERE b.id = ?""",
        (batch_id,),
    ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    pdf_path = DATA_DIR / "pdfs" / batch["pdf_path"]
    if not pdf_path.exists():
        raise HTTPException(status_code=400, detail="Original PDF not found")

    if req.mode == "replace":
        # Delete existing questions and images for this batch
        db.execute("DELETE FROM questions WHERE batch_id = ?", (batch_id,))
        db.execute("DELETE FROM images WHERE batch_id = ?", (batch_id,))
        delete_batch_images(batch_id)
        db.commit()

    # Reset batch status
    db.execute(
        "UPDATE upload_batches SET status = 'pending', processed_pages = 0, error_message = NULL WHERE id = ?",
        (batch_id,),
    )
    db.commit()

    # Kick off reprocessing
    background_tasks.add_task(
        process_batch,
        batch_id,
        str(pdf_path),
        batch["subject_name"],
        batch["subject_id"],
        batch["user_id"],
        batch["page_start"],
        batch["page_end"],
    )

    return {"message": "Reprocessing started", "batch_id": batch_id}


@router.get("/settings")
def get_settings(
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return all admin-managed settings (API key masked for display)."""
    rows = db.execute("SELECT key, value, updated_at FROM settings ORDER BY key").fetchall()
    result = {}
    for row in rows:
        val = row["value"]
        # Mask the API key so it isn't sent in full to the browser
        if row["key"] == "anthropic_api_key" and val:
            masked = val[:8] + "•" * max(0, len(val) - 12) + val[-4:] if len(val) > 12 else "•" * len(val)
        else:
            masked = val
        result[row["key"]] = {
            "value": val,
            "masked": masked,
            "updated_at": row["updated_at"],
        }
    return result


_ALLOWED_SETTINGS = {"anthropic_api_key", "jwt_secret"}


@router.put("/settings/{key}")
def update_setting(
    key: str,
    req: SettingUpdate,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Update a single setting value.
    For anthropic_api_key, the key is validated against the API before saving.
    """
    if key not in _ALLOWED_SETTINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting. Allowed keys: {', '.join(sorted(_ALLOWED_SETTINGS))}",
        )
    if not req.value.strip():
        raise HTTPException(status_code=400, detail="Value must not be empty")

    # Validate the Anthropic API key before persisting it
    if key == "anthropic_api_key":
        ok, validation_message = validate_api_key(req.value.strip())
        if not ok:
            raise HTTPException(status_code=400, detail=validation_message)

    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, req.value.strip()),
    )
    db.commit()

    response = {"message": "Setting updated", "key": key}
    if key == "anthropic_api_key":
        response["validation"] = "API key is valid"
    return response


# ── AI Settings ──

# Human-readable metadata for each AI setting key
_AI_SETTING_METADATA: dict[str, dict] = {
    "ai_model_ko_extraction":          {"label": "Model",  "type": "model",  "group": "Knowledge Organiser Extraction",            "group_key": "ko_extraction"},
    "ai_model_past_paper_extraction":  {"label": "Model",  "type": "model",  "group": "Past Paper Extraction",                    "group_key": "past_paper_extraction"},
    "ai_model_mcq":                    {"label": "Model",  "type": "model",  "group": "MCQ Generation",                           "group_key": "mcq"},
    "ai_model_judging":                {"label": "Model",  "type": "model",  "group": "Answer Judging",                           "group_key": "judging"},
    "ai_model_fact_check":             {"label": "Model",  "type": "model",  "group": "Fact Check",                               "group_key": "fact_check"},
    "ai_model_matching":               {"label": "Model",  "type": "model",  "group": "Knowledge Organiser → Past Paper Matching", "group_key": "matching"},
    "ai_prompt_ko_extraction":         {"label": "Prompt", "type": "prompt", "group": "Knowledge Organiser Extraction",            "group_key": "ko_extraction"},
    "ai_prompt_past_paper_extraction": {"label": "Prompt", "type": "prompt", "group": "Past Paper Extraction",                    "group_key": "past_paper_extraction"},
    "ai_prompt_mcq":                   {"label": "Prompt", "type": "prompt", "group": "MCQ Generation",                           "group_key": "mcq"},
    "ai_prompt_judging":               {"label": "Prompt", "type": "prompt", "group": "Answer Judging",                           "group_key": "judging"},
    "ai_prompt_fact_check":            {"label": "Prompt", "type": "prompt", "group": "Fact Check",                               "group_key": "fact_check"},
    "ai_prompt_matching":              {"label": "Prompt", "type": "prompt", "group": "Knowledge Organiser → Past Paper Matching", "group_key": "matching"},
}

AVAILABLE_MODELS: list[str] = [
    # Claude 4.6 (latest)
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    # Claude 4.5
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    # Claude 4.1 / 4 (first release)
    "claude-opus-4-1",
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
    # Claude 3.x
    "claude-3-7-sonnet-20250219",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]


@router.get("/ai-settings")
def get_ai_settings(
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Return all AI model and prompt settings with current values, defaults, and override status."""
    db_rows = {
        row["key"]: {"value": row["value"], "updated_at": row["updated_at"]}
        for row in db.execute(
            "SELECT key, value, updated_at FROM settings WHERE key LIKE 'ai_%'"
        ).fetchall()
    }
    result = []
    for key, meta in _AI_SETTING_METADATA.items():
        default_val = AI_SETTING_DEFAULTS[key]
        db_entry = db_rows.get(key)
        result.append({
            "key":          key,
            "label":        meta["label"],
            "type":         meta["type"],
            "group":        meta["group"],
            "group_key":    meta["group_key"],
            "value":        db_entry["value"] if db_entry else default_val,
            "default":      default_val,
            "is_overridden": db_entry is not None,
            "updated_at":   db_entry["updated_at"] if db_entry else None,
        })
    return {"settings": result, "available_models": AVAILABLE_MODELS}


@router.put("/ai-settings/{key}")
def update_ai_setting(
    key: str,
    req: SettingUpdate,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Save a custom value for an AI model or prompt setting."""
    if key not in _AI_SETTING_METADATA:
        raise HTTPException(status_code=400, detail=f"Unknown AI setting key: {key}")
    if not req.value.strip():
        raise HTTPException(status_code=400, detail="Value must not be empty")
    if _AI_SETTING_METADATA[key]["type"] == "model" and req.value.strip() not in AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model. Allowed: {', '.join(AVAILABLE_MODELS)}",
        )
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, req.value.strip()),
    )
    db.commit()
    return {"message": "AI setting updated", "key": key}


@router.delete("/ai-settings/{key}")
def reset_ai_setting(
    key: str,
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Reset an AI setting to its built-in default by removing the DB override."""
    if key not in _AI_SETTING_METADATA:
        raise HTTPException(status_code=400, detail=f"Unknown AI setting key: {key}")
    db.execute("DELETE FROM settings WHERE key = ?", (key,))
    db.commit()
    return {
        "message": "AI setting reset to default",
        "key":     key,
        "default": AI_SETTING_DEFAULTS[key],
    }


@router.get("/stats")
def system_stats(
    admin: dict = Depends(get_admin_user),
    db: sqlite3.Connection = Depends(get_db),
):
    users = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    questions = db.execute("SELECT COUNT(*) as c FROM questions").fetchone()["c"]
    approved = db.execute("SELECT COUNT(*) as c FROM questions WHERE approved = 1").fetchone()["c"]
    batches = db.execute("SELECT COUNT(*) as c FROM upload_batches").fetchone()["c"]
    sessions = db.execute("SELECT COUNT(*) as c FROM quiz_sessions").fetchone()["c"]
    answers = db.execute("SELECT COUNT(*) as c FROM quiz_answers").fetchone()["c"]
    total_cost = db.execute("SELECT COALESCE(SUM(cost_usd), 0) as c FROM api_usage").fetchone()["c"]

    return {
        "total_users": users,
        "total_questions": questions,
        "approved_questions": approved,
        "total_batches": batches,
        "total_quiz_sessions": sessions,
        "total_quiz_answers": answers,
        "total_cost_usd": total_cost,
    }
