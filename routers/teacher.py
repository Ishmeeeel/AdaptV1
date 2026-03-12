"""
routers/teacher.py – Teacher-facing endpoints.
All routes require a valid Supabase JWT (teacher role).
"""

from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status

from dependencies import get_current_user
from schemas import (
    AssignLessonRequest,
    CreateStudentRequest,
    CreateStudentResponse,
    GradeLessonRequest,
    ProcessingStatus,
    SaveNoteRequest,
    StudentDetail,
    StudentSummary,
    TeacherDashboard,
    TeacherLesson,
    UploadResponse,
)
from services import teacher_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=TeacherDashboard)
def dashboard(user_id: str = Depends(get_current_user)):
    return teacher_service.get_teacher_dashboard(user_id)


# ─────────────────────────────────────────────────────────────────────────────
# Lessons
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/lessons", response_model=List[TeacherLesson])
def lessons(user_id: str = Depends(get_current_user)):
    return teacher_service.get_teacher_lessons(user_id)


@router.post("/lessons", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_lesson(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    subject: str = Form(...),
    file: UploadFile = File(...),
    assigned_student_ids: str = Form("[]"),
    user_id: str = Depends(get_current_user),
):
    import json
    try:
        student_ids = json.loads(assigned_student_ids)
    except Exception:
        student_ids = []
    return await teacher_service.upload_lesson(
        user_id, title, subject, file, background_tasks, student_ids
    )


@router.delete("/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lesson(lesson_id: str, user_id: str = Depends(get_current_user)):
    teacher_service.delete_lesson(user_id, lesson_id)


@router.post("/lessons/{lesson_id}/assign")
def assign_lesson(
    lesson_id: str,
    body: AssignLessonRequest,
    user_id: str = Depends(get_current_user),
):
    return teacher_service.assign_lesson(user_id, lesson_id, body.student_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Reprocess — re-runs Groq + Audio on all pages of a lesson
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/lessons/{lesson_id}/reprocess")
async def reprocess_lesson(
    lesson_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
):
    return await teacher_service.reprocess_lesson(user_id, lesson_id, background_tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Processing status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/processing/{lesson_id}", response_model=ProcessingStatus)
def processing_status(lesson_id: str, user_id: str = Depends(get_current_user)):
    return teacher_service.get_processing_status(user_id, lesson_id)


# ─────────────────────────────────────────────────────────────────────────────
# Students
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/students", response_model=List[StudentSummary])
def students(user_id: str = Depends(get_current_user)):
    return teacher_service.get_teacher_students(user_id)


@router.post("/students", response_model=CreateStudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(body: CreateStudentRequest, user_id: str = Depends(get_current_user)):
    return teacher_service.create_student(user_id, body)


@router.get("/students/{student_id}", response_model=StudentDetail)
def student_detail(student_id: str, user_id: str = Depends(get_current_user)):
    return teacher_service.get_student_detail(user_id, student_id)


@router.put("/students/{student_id}/notes")
def save_note(
    student_id: str,
    body: SaveNoteRequest,
    user_id: str = Depends(get_current_user),
):
    return teacher_service.save_teacher_note(user_id, student_id, body.note_text)


@router.put("/students/{student_id}/lessons/{lesson_id}/grade")
def grade_lesson(
    student_id: str,
    lesson_id: str,
    body: GradeLessonRequest,
    user_id: str = Depends(get_current_user),
):
    """Save or update a teacher's grade and feedback for a student's lesson."""
    return teacher_service.save_lesson_grade(
        user_id, student_id, lesson_id, body.grade, body.feedback
    )