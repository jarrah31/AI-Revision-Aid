import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.auth import get_current_user
from backend.database import get_db

router = APIRouter()


class SubcategoryCreate(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=100)


class SubcategoryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("")
def list_subcategories(
    category_id: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """List all subcategories for a category, including per-user question counts."""
    rows = db.execute(
        """SELECT sc.*,
                  COUNT(q.id) as question_count,
                  SUM(CASE WHEN q.approved = 1 THEN 1 ELSE 0 END) as approved_count
           FROM subcategories sc
           LEFT JOIN questions q ON q.subcategory_id = sc.id AND q.user_id = ?
           WHERE sc.category_id = ?
           GROUP BY sc.id
           ORDER BY sc.name""",
        (user["id"], category_id),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{subcategory_id}")
def get_subcategory(
    subcategory_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Get a single subcategory with its category and subject names."""
    row = db.execute(
        """SELECT sc.*, c.name as category_name, c.subject_id,
                  s.name as subject_name
           FROM subcategories sc
           JOIN categories c ON c.id = sc.category_id
           JOIN subjects s ON s.id = c.subject_id
           WHERE sc.id = ?""",
        (subcategory_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    return dict(row)


@router.post("", status_code=201)
def create_subcategory(
    req: SubcategoryCreate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Create a new subcategory for a category."""
    try:
        cursor = db.execute(
            "INSERT INTO subcategories (category_id, name) VALUES (?, ?)",
            (req.category_id, req.name),
        )
        db.commit()
        return {"id": cursor.lastrowid, "category_id": req.category_id, "name": req.name}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Subcategory already exists for this category")


@router.put("/{subcategory_id}")
def update_subcategory(
    subcategory_id: int,
    req: SubcategoryUpdate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Rename a subcategory."""
    row = db.execute("SELECT * FROM subcategories WHERE id = ?", (subcategory_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    try:
        db.execute("UPDATE subcategories SET name = ? WHERE id = ?", (req.name, subcategory_id))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="A subcategory with that name already exists for this category")
    return {"id": subcategory_id, "category_id": row["category_id"], "name": req.name}


@router.delete("/{subcategory_id}", status_code=204)
def delete_subcategory(
    subcategory_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Delete a subcategory and all questions belonging to it (for the current user)."""
    row = db.execute("SELECT * FROM subcategories WHERE id = ?", (subcategory_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    # Delete all questions in this subcategory owned by this user
    db.execute(
        "DELETE FROM questions WHERE subcategory_id = ? AND user_id = ?",
        (subcategory_id, user["id"]),
    )
    # Delete the subcategory itself
    db.execute("DELETE FROM subcategories WHERE id = ?", (subcategory_id,))
    db.commit()
