"""
services/auth_service.py – Business logic for authentication & user profiles.

FIX 9: register_user now rolls back the Supabase auth user if the profile
       insert fails, preventing "ghost" users who can never log in or
       re-register.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException, status

from database import supabase
from schemas import (
    OnboardingRequest,
    RegisterRequest,
    SettingsRequest,
    UserResponse,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_user(row: Dict[str, Any]) -> UserResponse:
    """Convert a profiles DB row to a UserResponse."""
    dp = row.get("disability_profile")
    return UserResponse(
        id=row["id"],
        name=row.get("full_name", ""),
        email=row.get("email", ""),
        role=row.get("role", "student"),
        school_id=row.get("school_id"),
        disability_profile=dp,
        profile=dp,                          # duplicate for frontend compat
        language=row.get("language"),
        font_size=row.get("font_size", "medium"),
        voice_speed=row.get("voice_speed", "normal"),
        high_contrast=row.get("high_contrast", False),
        onboarding_complete=row.get("onboarding_complete", False),
    )


def _validate_school_code(school_code: str) -> str:
    """
    Look up a school by access_code.
    Returns school_id or raises 400.
    """
    res = (
        supabase.table("schools")
        .select("id")
        .eq("access_code", school_code)
        .eq("is_active", True)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or inactive school access code.",
        )
    return res.data["id"]


# ─────────────────────────────────────────────────────────────────────────────
# Register
# ─────────────────────────────────────────────────────────────────────────────

async def register_user(body: RegisterRequest) -> Dict[str, Any]:
    """
    1. Validate school_code → school_id
    2. Create Supabase Auth user (service-role Admin API)
    3. Insert profile row  ← FIX 9: rollback auth user if this fails
    4. Background: send welcome email (mock)
    """
    school_id = _validate_school_code(body.school_code)

    # Create auth user
    try:
        auth_res = supabase.auth.admin.create_user(
            {
                "email": body.email,
                "password": body.password,
                "email_confirm": True,
                "user_metadata": {"full_name": body.name},
            }
        )
    except Exception as exc:
        logger.error("Supabase create_user failed: %s", exc)
        # Surface the Supabase error message (e.g. "User already registered")
        detail = str(exc)
        if "already registered" in detail.lower() or "already exists" in detail.lower():
            detail = "An account with this email already exists."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    user_id: str = auth_res.user.id  # type: ignore[union-attr]

    # Insert profile — FIX 9: rollback auth user on failure
    profile_row = {
        "id": user_id,
        "full_name": body.name,
        "email": body.email,
        "role": body.role,
        "school_id": school_id,
        "onboarding_complete": False,
        "font_size": "medium",
        "voice_speed": "normal",
        "high_contrast": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("profiles").insert(profile_row).execute()
    except Exception as exc:
        # Rollback: delete the newly created auth user so the email can be reused
        logger.error("Profile insert failed for %s, rolling back auth user: %s", user_id, exc)
        try:
            supabase.auth.admin.delete_user(user_id)
        except Exception as rollback_exc:
            logger.error("Rollback failed — ghost user %s may exist: %s", user_id, rollback_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user profile. Please try again.",
        )

    # Mock welcome email
    _send_welcome_email(body.email, body.name)

    profile = _row_to_user(profile_row)
    return {"user": profile}


def _send_welcome_email(email: str, name: str) -> None:
    """Mock email service – replace with SendGrid / SES in production."""
    logger.info("[EMAIL] Welcome email sent to %s <%s>", name, email)


# ─────────────────────────────────────────────────────────────────────────────
# Me / Onboarding / Settings
# ─────────────────────────────────────────────────────────────────────────────

def get_profile(user_id: str) -> UserResponse:
    """Fetch user profile by id."""
    res = (
        supabase.table("profiles")
        .select("*")
        .eq("id", user_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return _row_to_user(res.data)


def update_onboarding(user_id: str, body: OnboardingRequest) -> UserResponse:
    """Save onboarding selections and mark onboarding complete."""
    updates = {
        "disability_profile": body.disability_profile,
        "language": body.language,
        "onboarding_complete": True,
    }
    res = (
        supabase.table("profiles")
        .update(updates)
        .eq("id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return _row_to_user(res.data[0])


def update_settings(user_id: str, body: SettingsRequest) -> UserResponse:
    """Partial-update user accessibility settings."""
    updates: Dict[str, Any] = {}
    if body.profile is not None:
        updates["disability_profile"] = body.profile
    if body.language is not None:
        updates["language"] = body.language
    if body.font_size is not None:
        updates["font_size"] = body.font_size
    if body.voice_speed is not None:
        updates["voice_speed"] = body.voice_speed
    if body.high_contrast is not None:
        updates["high_contrast"] = body.high_contrast

    if not updates:
        return get_profile(user_id)

    res = (
        supabase.table("profiles")
        .update(updates)
        .eq("id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return _row_to_user(res.data[0])
