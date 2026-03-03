"""
routers/student.py – Student-facing endpoints.
All routes require a valid Supabase JWT (student role).
"""

from typing import List

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from dependencies import get_current_user
from schemas import (
    AudioResponse,
    LessonSummary,
    PageContent,
    StudentDashboard,
    StudentProgressPage,
    UpdateProgressRequest,
)
from services import student_service

router = APIRouter()


@router.get("/dashboard", response_model=StudentDashboard)
def dashboard(user_id: str = Depends(get_current_user)):
    """Return aggregated dashboard data for the authenticated student."""
    return student_service.get_student_dashboard(user_id)


@router.get("/lessons", response_model=List[LessonSummary])
def lessons(user_id: str = Depends(get_current_user)):
    """List all lessons available to the student with their progress."""
    return student_service.get_student_lessons(user_id)


@router.get("/lessons/{lesson_id}", response_model=LessonSummary)
def lesson(lesson_id: str, user_id: str = Depends(get_current_user)):
    """Get a single lesson summary with the student's current progress."""
    return student_service.get_student_lesson(user_id, lesson_id)


@router.get("/lessons/{lesson_id}/page/{page_num}", response_model=PageContent)
def lesson_page(lesson_id: str, page_num: int, user_id: str = Depends(get_current_user)):
    """Return the original and simplified content for a specific page."""
    return student_service.get_lesson_page(user_id, lesson_id, page_num)


@router.get("/lessons/{lesson_id}/audio", response_model=AudioResponse)
def lesson_audio(lesson_id: str, user_id: str = Depends(get_current_user)):
    """Return the pre-generated audio URL for the student's preferred language."""
    return student_service.get_lesson_audio(user_id, lesson_id)


@router.put("/lessons/{lesson_id}/progress")
def update_progress(
    lesson_id: str,
    body: UpdateProgressRequest,
    user_id: str = Depends(get_current_user),
):
    """Save reading progress (current page + completion flag) for a lesson."""
    return student_service.update_lesson_progress(
        user_id, lesson_id, body.current_page, body.is_completed
    )


@router.get("/progress", response_model=StudentProgressPage)
def progress(user_id: str = Depends(get_current_user)):
    """Return full progress data including completed/in-progress lists and activity log."""
    return student_service.get_student_progress(user_id)
