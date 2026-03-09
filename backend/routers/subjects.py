import sqlite3
from fastapi import APIRouter, Depends, HTTPException

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import SubjectCreate, SubjectUpdate

router = APIRouter()


@router.get("")
def list_subjects(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    rows = db.execute(
        """SELECT s.*,
                  SUM(CASE WHEN q.approved = 1 THEN 1 ELSE 0 END) as question_count,
                  SUM(CASE WHEN q.approved = 1 AND q.category_id IS NULL THEN 1 ELSE 0 END) as uncategorised_count
           FROM subjects s
           LEFT JOIN questions q ON q.subject_id = s.id AND q.user_id = ?
           GROUP BY s.id
           ORDER BY s.name""",
        (user["id"],),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_subject(
    req: SubjectCreate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    try:
        cursor = db.execute(
            "INSERT INTO subjects (name, icon, color) VALUES (?, ?, ?)",
            (req.name, req.icon, req.color),
        )
        db.commit()
        return {"id": cursor.lastrowid, "name": req.name, "icon": req.icon, "color": req.color}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Subject already exists")


@router.put("/{subject_id}")
def update_subject(
    subject_id: int,
    req: SubjectUpdate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Subject not found")

    updates = []
    params = []
    if req.name is not None:
        updates.append("name = ?")
        params.append(req.name)
    if req.icon is not None:
        updates.append("icon = ?")
        params.append(req.icon)
    if req.color is not None:
        updates.append("color = ?")
        params.append(req.color)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = datetime('now')")
    params.append(subject_id)
    try:
        db.execute(f"UPDATE subjects SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Subject name already exists")

    return {"message": "Subject updated"}


@router.delete("/{subject_id}")
def delete_subject(
    subject_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    existing = db.execute("SELECT id FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Subject not found")

    db.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    db.commit()
    return {"message": "Subject deleted"}
