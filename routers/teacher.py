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
    """Return aggregated stats, recent students, and top lessons for the teacher."""
    return teacher_service.get_teacher_dashboard(user_id)


# ─────────────────────────────────────────────────────────────────────────────
# Lessons
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/lessons", response_model=List[TeacherLesson])
def lessons(user_id: str = Depends(get_current_user)):
    """List all lessons created by the teacher."""
    return teacher_service.get_teacher_lessons(user_id)


@router.post("/lessons", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_lesson(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    subject: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
):
    """
    Upload a PDF lesson file.
    Stores the file in Supabase Storage and kicks off the async processing pipeline
    (text extraction → simplification → TTS in 4 languages).
    """
    return await teacher_service.upload_lesson(user_id, title, subject, file, background_tasks)


@router.delete("/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lesson(lesson_id: str, user_id: str = Depends(get_current_user)):
    """Delete a lesson and all its associated pages, audio, and processing jobs."""
    teacher_service.delete_lesson(user_id, lesson_id)


@router.post("/lessons/{lesson_id}/assign")
def assign_lesson(
    lesson_id: str,
    body: AssignLessonRequest,
    user_id: str = Depends(get_current_user),
):
    """Assign a lesson to one or more students."""
    return teacher_service.assign_lesson(user_id, lesson_id, body.student_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Processing
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/processing/{lesson_id}", response_model=ProcessingStatus)
def processing_status(lesson_id: str, user_id: str = Depends(get_current_user)):
    """Poll the processing status and step completion flags for a lesson."""
    return teacher_service.get_processing_status(user_id, lesson_id)


# ─────────────────────────────────────────────────────────────────────────────
# Students
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/students", response_model=List[StudentSummary])
def students(user_id: str = Depends(get_current_user)):
    """List all students enrolled in the teacher's school."""
    return teacher_service.get_teacher_students(user_id)


@router.post("/students", response_model=CreateStudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(body: CreateStudentRequest, user_id: str = Depends(get_current_user)):
    """Create a student account under the teacher's school (with a temporary password)."""
    return teacher_service.create_student(user_id, body)


@router.get("/students/{student_id}", response_model=StudentDetail)
def student_detail(student_id: str, user_id: str = Depends(get_current_user)):
    """Get a detailed profile view of a specific student including per-lesson progress."""
    return teacher_service.get_student_detail(user_id, student_id)


@router.put("/students/{student_id}/notes")
def save_note(
    student_id: str,
    body: SaveNoteRequest,
    user_id: str = Depends(get_current_user),
):
    """Save or update a teacher's note for a student."""
    return teacher_service.save_teacher_note(user_id, student_id, body.note_text)
