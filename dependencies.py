"""
dependencies.py – Reusable FastAPI dependencies.

`get_current_user` validates the Supabase-issued JWT and returns the
caller's user_id (UUID string).

FIX 12 (partial): Added `require_role` factory so routes can enforce
role-based access at the dependency level, not just the backend service.
Usage:
    user_id: str = Depends(require_role("teacher"))
    user_id: str = Depends(require_role("admin"))
"""

import logging
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    """
    Validate a Supabase JWT and return the subject (user UUID).
    Raises 401 if the token is missing, expired, or tampered with.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"verify_exp": True},
        )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise ValueError("No sub claim in JWT")
        return user_id

    except jwt.ExpiredSignatureError:
        logger.warning("Rejected expired JWT")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (jwt.InvalidTokenError, ValueError) as exc:
        logger.warning("Rejected invalid JWT: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_role(role: str) -> Callable:
    """
    FIX 12: Factory that returns a dependency enforcing a specific role.

    Example:
        @router.get("/dashboard")
        def dashboard(user_id: str = Depends(require_role("teacher"))):
            ...
    """
    async def _check_role(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    ) -> str:
        from database import supabase  # local import to avoid circular

        # First validate the JWT
        token = credentials.credentials
        try:
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience="authenticated",
                options={"verify_exp": True},
            )
            user_id: str | None = payload.get("sub")
            if not user_id:
                raise ValueError("No sub claim")
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except (jwt.InvalidTokenError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Then check the role in the database
        res = (
            supabase.table("profiles")
            .select("role")
            .eq("id", user_id)
            .single()
            .execute()
        )
        db_role = (res.data or {}).get("role")
        if db_role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access requires '{role}' role.",
            )
        return user_id

    return _check_role
