from datetime import datetime, timedelta, timezone
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from backend.config import settings
from backend.database import get_setting

security = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _jwt_secret() -> str:
    """Return the active JWT secret (DB-first, falls back to config/env)."""
    return get_setting("jwt_secret") or settings.jwt_secret


def create_token(user_id: int, username: str, year_group: int, is_admin: bool) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_expiry_days)
    payload = {
        "sub": str(user_id),
        "username": username,
        "year_group": year_group,
        "is_admin": is_admin,
        "exp": expire,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """FastAPI dependency: returns the current authenticated user's token payload."""
    payload = decode_token(credentials.credentials)
    return {
        "id": int(payload["sub"]),
        "username": payload["username"],
        "year_group": payload["year_group"],
        "is_admin": payload.get("is_admin", False),
    }


def get_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """FastAPI dependency: requires the current user to be an admin."""
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
