import sqlite3
from fastapi import APIRouter, Depends, HTTPException, status

from backend.auth import (
    hash_password,
    verify_password,
    create_token,
    get_current_user,
)
from backend.database import get_db
from backend.models import SignupRequest, LoginRequest, ProfileUpdate, ChangePasswordRequest

router = APIRouter()


@router.post("/signup")
def signup(req: SignupRequest, db: sqlite3.Connection = Depends(get_db)):
    # Check if username taken
    existing = db.execute(
        "SELECT id FROM users WHERE username = ?", (req.username,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")

    # First user becomes admin
    user_count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    is_admin = 1 if user_count == 0 else 0

    password_hash = hash_password(req.password)
    cursor = db.execute(
        """INSERT INTO users (username, password_hash, display_name, year_group, is_admin)
           VALUES (?, ?, ?, ?, ?)""",
        (req.username, password_hash, req.display_name, req.year_group, is_admin),
    )
    db.commit()
    user_id = cursor.lastrowid

    token = create_token(user_id, req.username, req.year_group, bool(is_admin))
    return {
        "token": token,
        "user": {
            "id": user_id,
            "username": req.username,
            "display_name": req.display_name,
            "year_group": req.year_group,
            "is_admin": bool(is_admin),
        },
    }


@router.post("/login")
def login(req: LoginRequest, db: sqlite3.Connection = Depends(get_db)):
    user = db.execute(
        "SELECT * FROM users WHERE username = ?", (req.username,)
    ).fetchone()
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_token(
        user["id"], user["username"], user["year_group"], bool(user["is_admin"])
    )
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "year_group": user["year_group"],
            "is_admin": bool(user["is_admin"]),
        },
    }


@router.get("/me")
def get_me(user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "year_group": row["year_group"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


@router.put("/profile")
def update_profile(
    req: ProfileUpdate,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    updates = []
    params = []
    if req.display_name is not None:
        updates.append("display_name = ?")
        params.append(req.display_name)
    if req.year_group is not None:
        updates.append("year_group = ?")
        params.append(req.year_group)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(user["id"])
    db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return {"message": "Profile updated"}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    row = db.execute(
        "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
    ).fetchone()
    if not row or not verify_password(req.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(req.new_password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
    db.commit()
    return {"message": "Password changed successfully"}
