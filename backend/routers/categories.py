import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import CategoryCreate, PageCategoryAssign

router = APIRouter()


@router.get("")
def list_categories(
    subject_id: int = Query(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """List all categories for a subject, including per-user question counts."""
    rows = db.execute(
        """SELECT c.*,
                  COUNT(q.id) as question_count,
                  SUM(CASE WHEN q.approved = 1 THEN 1 ELSE 0 END) as approved_count
           FROM categories c
           LEFT JOIN questions q ON q.category_id = c.id AND q.user_id = ?
           WHERE c.subject_id = ?
           GROUP BY c.id
           ORDER BY c.name""",
        (user["id"], subject_id),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{category_id}")
def get_category(
    category_id: int,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Get a single category with its subject name."""
    row = db.execute(
        """SELECT c.*, s.name as subject_name
           FROM categories c
           JOIN subjects s ON s.id = c.subject_id
           WHERE c.id = ?""",
        (category_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")
    return dict(row)


@router.post("", status_code=201)
def create_category(
    req: CategoryCreate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Create a new category for a subject."""
    try:
        cursor = db.execute(
            "INSERT INTO categories (subject_id, name) VALUES (?, ?)",
            (req.subject_id, req.name),
        )
        db.commit()
        return {"id": cursor.lastrowid, "subject_id": req.subject_id, "name": req.name}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Category already exists for this subject")


@router.post("/assign-page")
def assign_page_category(
    req: PageCategoryAssign,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    """Assign (or clear) a category for all questions on a given batch page."""
    db.execute(
        """UPDATE questions SET category_id = ?
           WHERE batch_id = ? AND page_number = ? AND user_id = ?""",
        (req.category_id, req.batch_id, req.page_number, user["id"]),
    )
    db.commit()
    return {"message": "Category assigned"}
