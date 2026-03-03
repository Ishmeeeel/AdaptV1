"""
routers/auth.py – Auth endpoints: register, me, onboarding, settings, logout.
"""

import logging

from fastapi import APIRouter, Depends, status

from dependencies import get_current_user
from schemas import (
    OnboardingRequest,
    RegisterRequest,
    SettingsRequest,
    UserResponse,
)
from services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest):
    """
    Create a teacher or admin account.
    Validates school_code, creates Supabase Auth user, inserts profile row,
    and fires a mock welcome email.
    """
    return await auth_service.register_user(body)


@router.get("/me", response_model=UserResponse)
def me(user_id: str = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return auth_service.get_profile(user_id)


@router.put("/onboarding", response_model=UserResponse)
def onboarding(body: OnboardingRequest, user_id: str = Depends(get_current_user)):
    """Save onboarding selections (disability profile, language) and complete onboarding."""
    return auth_service.update_onboarding(user_id, body)


@router.put("/settings", response_model=UserResponse)
def settings(body: SettingsRequest, user_id: str = Depends(get_current_user)):
    """Partially update accessibility settings (font_size, voice_speed, high_contrast, etc.)."""
    return auth_service.update_settings(user_id, body)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user_id: str = Depends(get_current_user)):
    """
    Server-side logout acknowledgement.
    The actual session invalidation is handled client-side via supabase.auth.signOut().
    """
    logger.info("User %s logged out", user_id)
