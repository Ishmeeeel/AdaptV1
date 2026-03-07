import logging
from typing import Callable
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=True)

# ✅ FIX: Use Supabase JWKS endpoint to verify ES256 tokens
@lru_cache
def get_jwks_client() -> PyJWKClient:
    return PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def verify_token(token: str) -> str:
    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> str:
    return verify_token(credentials.credentials)


def require_role(role: str) -> Callable:
    async def _check_role(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    ) -> str:
        from database import supabase

        user_id = verify_token(credentials.credentials)

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