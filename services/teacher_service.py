"""
services/teacher_service.py – Business logic for teacher-facing endpoints.

FIX 6:  total_students was always ≤ 5 because the query had .limit(5).
        Now uses a separate count query for the total.
FIX 13: active_students now counts from the full student list, not just
        the 5 most recent.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import BackgroundTasks, HTTPException, UploadFile

from database import supabase
from schemas import (
    CreateStudentRequest,
    CreateStudentResponse,
    LessonSummary,
    ProcessingStatus,
    ProcessingSteps,
    StudentDetail,
    StudentSummary,
    TeacherDashboard,
    TeacherLesson,
    UploadResponse,
)
from services.processing_service import enqueue_lesson_processing

logger = logging.getLogger(__name__)

EMOJI_MAP: Dict[str, str] = {
    "mathematics": "🔢",
    "english": "📚",
    "science": "🔬",
    "social studies": "🌍",
    "civic education": "🏛️",
    "yoruba": "🗣️",
    "hausa": "🗣️",
    "igbo": "🗣️",
    "agriculture": "🌱",
    "computer": "💻",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_school_id(teacher_id: str) -> str | None:
    res = (
        supabase.table("profiles")
        .select("school_id")
        .eq("id", teacher_id)
        .single()
        .execute()
    )
    return (res.data or {}).get("school_id")


def _row_to_teacher_lesson(row: Dict[str, Any]) -> TeacherLesson:
    count_res = (
        supabase.table("student_lessons")
        .select("id", count="exact")
        .eq("lesson_id", row["id"])
        .execute()
    )
    student_count = count_res.count or 0
    return TeacherLesson(
        id=row["id"],
        title=row.get("title", ""),
        subject=row.get("subject", ""),
        page_count=row.get("page_count", 0),
        icon_emoji=row.get("icon_emoji", "📖"),
        is_published=row.get("is_published", False),
        processing_status=row.get("processing_status", "pending"),
        student_count=student_count,
        created_at=row.get("created_at", ""),
    )


def _row_to_student_summary(row: Dict[str, Any]) -> StudentSummary:
    lesson_count_res = (
        supabase.table("student_lessons")
        .select("id", count="exact")
        .eq("student_id", row["id"])
        .execute()
    )
    lessons = lesson_count_res.count or 0

    progress_res = (
        supabase.table("student_lessons")
        .select("progress_percent")
        .eq("student_id", row["id"])
        .execute()
    )
    rows = progress_res.data or []
    progress = int(sum(r["progress_percent"] for r in rows) / len(rows)) if rows else 0

    last_active_res = (
        supabase.table("student_lessons")
        .select("last_accessed_at")
        .eq("student_id", row["id"])
        .order("last_accessed_at", desc=True)
        .limit(1)
        .execute()
    )
    last_active = ""
    if last_active_res.data:
        last_active = last_active_res.data[0].get("last_accessed_at", "")

    return StudentSummary(
        id=row["id"],
        name=row.get("full_name", ""),
        profile=row.get("disability_profile", "none") or "none",
        lessons=lessons,
        progress=progress,
        last_active=last_active,
        status="active" if last_active else "inactive",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def get_teacher_dashboard(teacher_id: str) -> TeacherDashboard:
    school_id = _get_school_id(teacher_id)

    # Lessons by this teacher
    lessons_res = (
        supabase.table("lessons")
        .select("*")
        .eq("teacher_id", teacher_id)
        .execute()
    )
    lessons_data = lessons_res.data or []
    total_lessons = len(lessons_data)
    published = sum(1 for l in lessons_data if l.get("is_published"))

    # FIX 6: Get TOTAL student count separately from the recent-5 query
    total_count_res = (
        supabase.table("profiles")
        .select("id", count="exact")
        .eq("role", "student")
        .eq("school_id", school_id)
        .execute()
    )
    total_students = total_count_res.count or 0

    # Get recent 5 students (for the dashboard widget)
    recent_res = (
        supabase.table("profiles")
        .select("*")
        .eq("role", "student")
        .eq("school_id", school_id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    recent_students = [_row_to_student_summary(s) for s in (recent_res.data or [])]

    # FIX 13: active_students from ALL students (not just recent 5)
    # We consider a student active if they have any lesson access in the DB
    active_count_res = (
        supabase.table("student_lessons")
        .select("student_id", count="exact")
        .not_.is_("last_accessed_at", "null")
        .execute()
    )
    active_students = active_count_res.count or 0

    # Profile breakdown
    all_students_res = (
        supabase.table("profiles")
        .select("disability_profile")
        .eq("role", "student")
        .eq("school_id", school_id)
        .execute()
    )
    profile_counter: Dict[str, int] = {}
    for s in all_students_res.data or []:
        p = s.get("disability_profile") or "none"
        profile_counter[p] = profile_counter.get(p, 0) + 1
    profile_breakdown = [{"profile": k, "count": v} for k, v in profile_counter.items()]

    # Top lessons (by student count)
    top_lessons = sorted(
        [_row_to_teacher_lesson(l) for l in lessons_data],
        key=lambda x: x.student_count,
        reverse=True,
    )[:5]

    stats = {
        "total_lessons": total_lessons,
        "published_lessons": published,
        "total_students": total_students,       # FIX 6: now accurate
        "active_students": active_students,     # FIX 13: now accurate
    }

    return TeacherDashboard(
        stats=stats,
        recent_students=recent_students,
        profile_breakdown=profile_breakdown,
        top_lessons=top_lessons,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lessons
# ─────────────────────────────────────────────────────────────────────────────

def get_teacher_lessons(teacher_id: str) -> List[TeacherLesson]:
    res = (
        supabase.table("lessons")
        .select("*")
        .eq("teacher_id", teacher_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_row_to_teacher_lesson(row) for row in (res.data or [])]


async def upload_lesson(
    teacher_id: str,
    title: str,
    subject: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
) -> UploadResponse:
    """
    Accept a PDF lesson file, store metadata in DB, upload to Supabase Storage,
    then enqueue the async processing pipeline.
    """
    school_id = _get_school_id(teacher_id)
    lesson_id = str(uuid.uuid4())
    icon_emoji = EMOJI_MAP.get(subject.lower(), "📖")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    storage_path = f"lessons/{lesson_id}/{file.filename}"
    try:
        supabase.storage.from_("lesson-files").upload(
            storage_path,
            file_bytes,
            file_options={"content-type": file.content_type or "application/pdf"},
        )
    except Exception as exc:
        logger.error("Storage upload failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to store lesson file.")

    lesson_row = {
        "id": lesson_id,
        "teacher_id": teacher_id,
        "school_id": school_id,
        "title": title,
        "subject": subject,
        "icon_emoji": icon_emoji,
        "page_count": 0,
        "is_published": False,
        "processing_status": "pending",
        "storage_path": storage_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase.table("lessons").insert(lesson_row).execute()

    supabase.table("processing_jobs").insert(
        {
            "id": str(uuid.uuid4()),
            "lesson_id": lesson_id,
            "status": "pending",
            "steps": {
                "extract_text": False,
                "audio_english": False,
                "audio_hausa": False,
                "audio_yoruba": False,
                "audio_igbo": False,
                "simplify_dyslexia": False,
                "image_descriptions": False,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()

    background_tasks.add_task(enqueue_lesson_processing, lesson_id, storage_path)

    return UploadResponse(
        lesson_id=lesson_id,
        message="Lesson uploaded successfully. Processing has started.",
    )


def delete_lesson(teacher_id: str, lesson_id: str) -> None:
    res = (
        supabase.table("lessons")
        .select("id, teacher_id")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    if res.data["teacher_id"] != teacher_id:
        raise HTTPException(status_code=403, detail="Not authorised to delete this lesson.")

    supabase.table("student_lessons").delete().eq("lesson_id", lesson_id).execute()
    supabase.table("lesson_pages").delete().eq("lesson_id", lesson_id).execute()
    supabase.table("lesson_audio").delete().eq("lesson_id", lesson_id).execute()
    supabase.table("processing_jobs").delete().eq("lesson_id", lesson_id).execute()
    supabase.table("lessons").delete().eq("id", lesson_id).execute()


def assign_lesson(
    teacher_id: str,
    lesson_id: str,
    student_ids: List[str],
) -> Dict[str, Any]:
    res = (
        supabase.table("lessons")
        .select("id, teacher_id")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not res.data or res.data["teacher_id"] != teacher_id:
        raise HTTPException(status_code=403, detail="Not authorised to assign this lesson.")

    rows = [
        {
            "student_id": sid,
            "lesson_id": lesson_id,
            "current_page": 1,
            "progress_percent": 0,
            "is_completed": False,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
        }
        for sid in student_ids
    ]
    supabase.table("student_lessons").upsert(
        rows, on_conflict="student_id,lesson_id"
    ).execute()

    supabase.table("lessons").update({"is_published": True}).eq("id", lesson_id).execute()

    return {"ok": True, "assigned": len(rows)}


def get_processing_status(teacher_id: str, lesson_id: str) -> ProcessingStatus:
    res = (
        supabase.table("processing_jobs")
        .select("*")
        .eq("lesson_id", lesson_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Processing job not found.")
    row = res.data
    steps_raw = row.get("steps") or {}
    return ProcessingStatus(
        lesson_id=lesson_id,
        status=row.get("status", "pending"),
        steps=ProcessingSteps(**steps_raw),
        error_message=row.get("error_message"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Students
# ─────────────────────────────────────────────────────────────────────────────

def get_teacher_students(teacher_id: str) -> List[StudentSummary]:
    school_id = _get_school_id(teacher_id)
    res = (
        supabase.table("profiles")
        .select("*")
        .eq("role", "student")
        .eq("school_id", school_id)
        .execute()
    )
    return [_row_to_student_summary(row) for row in (res.data or [])]


def create_student(teacher_id: str, body: CreateStudentRequest) -> CreateStudentResponse:
    """Create a student account under the teacher's school."""
    school_id = _get_school_id(teacher_id)
    temp_password = secrets.token_urlsafe(10)

    try:
        auth_res = supabase.auth.admin.create_user(
            {
                "email": body.email,
                "password": temp_password,
                "email_confirm": True,
                "user_metadata": {"full_name": body.name},
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    student_id: str = auth_res.user.id  # type: ignore[union-attr]

    profile_row = {
        "id": student_id,
        "full_name": body.name,
        "email": body.email,
        "role": "student",
        "school_id": school_id,
        "disability_profile": body.disability_profile,
        "language": body.language,
        "font_size": "medium",
        "voice_speed": "normal",
        "high_contrast": False,
        "onboarding_complete": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("profiles").insert(profile_row).execute()
    except Exception as exc:
        # Rollback auth user
        try:
            supabase.auth.admin.delete_user(student_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to create student profile.")

    summary = StudentSummary(
        id=student_id,
        name=body.name,
        profile=body.disability_profile,
        lessons=0,
        progress=0,
        last_active="",
        status="inactive",
    )
    return CreateStudentResponse(student=summary, temp_password=temp_password)


def get_student_detail(teacher_id: str, student_id: str) -> StudentDetail:
    """Get a detailed view of a student, including per-lesson progress."""
    teacher_school = _get_school_id(teacher_id)

    profile_res = (
        supabase.table("profiles")
        .select("*")
        .eq("id", student_id)
        .single()
        .execute()
    )
    if not profile_res.data:
        raise HTTPException(status_code=404, detail="Student not found.")
    row = profile_res.data
    if row.get("school_id") != teacher_school:
        raise HTTPException(status_code=403, detail="Student not in your school.")

    progress_res = (
        supabase.table("student_lessons")
        .select("*, lessons(*)")
        .eq("student_id", student_id)
        .execute()
    )
    lesson_progress: List[LessonSummary] = []
    total_progress = 0
    for p in progress_res.data or []:
        lrow = p.get("lessons") or {}
        lesson_progress.append(
            LessonSummary(
                id=lrow.get("id", ""),
                title=lrow.get("title", ""),
                subject=lrow.get("subject", ""),
                page_count=lrow.get("page_count", 1),
                icon_emoji=lrow.get("icon_emoji", "📖"),
                teacher_name="",
                progress_percent=p.get("progress_percent", 0),
                current_page=p.get("current_page", 1),
                is_completed=p.get("is_completed", False),
            )
        )
        total_progress += p.get("progress_percent", 0)

    n = len(lesson_progress)
    avg_progress = int(total_progress / n) if n else 0

    last_active_res = (
        supabase.table("student_lessons")
        .select("last_accessed_at")
        .eq("student_id", student_id)
        .order("last_accessed_at", desc=True)
        .limit(1)
        .execute()
    )
    last_active = ""
    if last_active_res.data:
        last_active = last_active_res.data[0].get("last_accessed_at", "")

    return StudentDetail(
        id=student_id,
        name=row.get("full_name", ""),
        profile=row.get("disability_profile", "none") or "none",
        language=row.get("language", "english") or "english",
        progress=avg_progress,
        lessons=n,
        status="active" if last_active else "inactive",
        last_active=last_active,
        lesson_progress=lesson_progress,
        font_size=row.get("font_size", "medium"),
        voice_speed=row.get("voice_speed", "normal"),
        high_contrast=row.get("high_contrast", False),
    )


def save_teacher_note(teacher_id: str, student_id: str, note_text: str) -> Dict[str, bool]:
    supabase.table("teacher_notes").upsert(
        {
            "teacher_id": teacher_id,
            "student_id": student_id,
            "note_text": note_text,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="teacher_id,student_id",
    ).execute()
    return {"ok": True}
