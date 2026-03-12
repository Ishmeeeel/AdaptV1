"""
routers/admin.py – Admin-facing endpoints.
All routes require a valid Supabase JWT (admin role – verified server-side).

FIX 4: create_school now uses a Pydantic request body (CreateSchoolRequest)
       instead of bare `name: str, location: str` parameters which FastAPI
       would interpret as query params — causing 422 errors from the frontend
       which sends a JSON body.
"""

from typing import List

from fastapi import APIRouter, Depends, status

from dependencies import get_current_user
from schemas import AdminDashboard, CreateSchoolRequest, SchoolSummary
from services import admin_service

router = APIRouter()


@router.get("/dashboard", response_model=AdminDashboard)
def dashboard(user_id: str = Depends(get_current_user)):
    """Return platform-wide stats, school list, and disability profile breakdown."""
    return admin_service.get_admin_dashboard(user_id)


@router.get("/schools", response_model=List[SchoolSummary])
def schools(user_id: str = Depends(get_current_user)):
    """List all registered schools."""
    return admin_service.get_schools(user_id)


@router.post("/schools", response_model=SchoolSummary, status_code=status.HTTP_201_CREATED)
def create_school(
    body: CreateSchoolRequest,                  # FIX 4: JSON body, not query params
    user_id: str = Depends(get_current_user),
):
    """Create a new school and generate its initial access code."""
    return admin_service.create_school(user_id, body.name, body.location)


@router.post("/schools/{school_id}/access-code")
def regenerate_access_code(school_id: str, user_id: str = Depends(get_current_user)):
    """Generate a fresh access code for a school (invalidates the old one)."""
    return admin_service.regenerate_access_code(user_id, school_id)

@router.get("/users")
def users(user_id: str = Depends(get_current_user)):
    return admin_service.get_all_users(user_id)

@router.get("/lessons")
def all_lessons(user_id: str = Depends(get_current_user)):
    from database import supabase
    res = supabase.table("lessons").select("*, profiles(full_name)").order("created_at", desc=True).execute()
    return res.data or []