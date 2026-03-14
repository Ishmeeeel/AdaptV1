"""
services/teacher_service.py – Business logic for teacher-facing endpoints.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
    "mathematics":     "🔢",
    "english":         "📚",
    "science":         "🔬",
    "social studies":  "🌍",
    "civic education": "🏛️",
    "yoruba":          "🗣️",
    "hausa":           "🗣️",
    "igbo":            "🗣️",
    "agriculture":     "🌱",
    "computer":        "💻",
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
    return TeacherLesson(
        id=row["id"],
        title=row.get("title", ""),
        subject=row.get("subject", ""),
        page_count=row.get("page_count", 0),
        icon_emoji=row.get("icon_emoji", "📖"),
        is_published=row.get("is_published", False),
        processing_status=row.get("processing_status", "pending"),
        student_count=count_res.count or 0,
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
        last_active = last_active_res.data[0].get("last_accessed_at", "") or ""

    return StudentSummary(
        id=row["id"],
        name=row.get("full_name", ""),
        profile=row.get("disability_profile", "none") or "none",
        lessons=lessons,
        progress=progress,
        last_active=last_active,
        status="active" if last_active else "inactive",
        class_tag=row.get("class_tag"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def get_teacher_dashboard(teacher_id: str) -> TeacherDashboard:
    school_id = _get_school_id(teacher_id)

    lessons_res = (
        supabase.table("lessons")
        .select("*")
        .eq("teacher_id", teacher_id)
        .execute()
    )
    lessons_data = lessons_res.data or []
    total_lessons = len(lessons_data)
    published = sum(1 for lesson in lessons_data if lesson.get("is_published"))

    total_count_res = (
        supabase.table("profiles")
        .select("id", count="exact")
        .eq("role", "student")
        .eq("school_id", school_id)
        .execute()
    )
    total_students = total_count_res.count or 0

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

    active_count_res = (
        supabase.table("student_lessons")
        .select("student_id", count="exact")
        .not_.is_("last_accessed_at", "null")
        .execute()
    )
    active_students = active_count_res.count or 0

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

    top_lessons = sorted(
        [_row_to_teacher_lesson(lesson) for lesson in lessons_data],
        key=lambda x: x.student_count,
        reverse=True,
    )[:5]

    return TeacherDashboard(
        stats={
            "total_lessons":     total_lessons,
            "published_lessons": published,
            "total_students":    total_students,
            "active_students":   active_students,
        },
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
    assigned_student_ids: List[str] = [],
) -> UploadResponse:
    school_id  = _get_school_id(teacher_id)
    lesson_id  = str(uuid.uuid4())
    icon_emoji = EMOJI_MAP.get((subject or "").lower(), "📖")

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

    supabase.table("lessons").insert({
        "id":                lesson_id,
        "teacher_id":        teacher_id,
        "school_id":         school_id,
        "title":             title,
        "subject":           subject,
        "icon_emoji":        icon_emoji,
        "page_count":        0,
        "is_published":      False,
        "processing_status": "pending",
        "storage_path":      storage_path,
        "created_at":        datetime.now(timezone.utc).isoformat(),
    }).execute()

    supabase.table("processing_jobs").insert({
        "id":        str(uuid.uuid4()),
        "lesson_id": lesson_id,
        "status":    "pending",
        "steps": {
            "extract_text":       False,
            "audio_english":      False,
            "audio_hausa":        False,
            "audio_yoruba":       False,
            "audio_igbo":         False,
            "simplify_dyslexia":  False,
            "image_descriptions": False,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    if assigned_student_ids:
        supabase.table("student_lessons").upsert(
            [
                {
                    "student_id":       sid,
                    "lesson_id":        lesson_id,
                    "current_page":     1,
                    "progress_percent": 0,
                    "is_completed":     False,
                    "enrolled_at":      datetime.now(timezone.utc).isoformat(),
                }
                for sid in assigned_student_ids
            ],
            on_conflict="student_id,lesson_id",
        ).execute()
        supabase.table("lessons").update(
            {"is_published": True}
        ).eq("id", lesson_id).execute()

    background_tasks.add_task(enqueue_lesson_processing, lesson_id, storage_path)

    return UploadResponse(
        lesson_id=lesson_id,
        message="Lesson uploaded and assigned successfully. Processing has started.",
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


def assign_lesson(teacher_id: str, lesson_id: str, student_ids: List[str]) -> Dict[str, Any]:
    res = (
        supabase.table("lessons")
        .select("id, teacher_id")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not res.data or res.data["teacher_id"] != teacher_id:
        raise HTTPException(status_code=403, detail="Not authorised to assign this lesson.")

    supabase.table("student_lessons").upsert(
        [
            {
                "student_id":       sid,
                "lesson_id":        lesson_id,
                "current_page":     1,
                "progress_percent": 0,
                "is_completed":     False,
                "enrolled_at":      datetime.now(timezone.utc).isoformat(),
            }
            for sid in student_ids
        ],
        on_conflict="student_id,lesson_id",
    ).execute()
    supabase.table("lessons").update({"is_published": True}).eq("id", lesson_id).execute()
    return {"ok": True, "assigned": len(student_ids)}


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
    return ProcessingStatus(
        lesson_id=lesson_id,
        status=row.get("status", "pending"),
        steps=ProcessingSteps(**(row.get("steps") or {})),
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
    school_id     = _get_school_id(teacher_id)
    temp_password = secrets.token_urlsafe(10)

    try:
        auth_res = supabase.auth.admin.create_user({
            "email":         body.email,
            "password":      temp_password,
            "email_confirm": True,
            "user_metadata": {"full_name": body.name},
        })
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    student_id: str = auth_res.user.id  # type: ignore[union-attr]

    try:
        supabase.table("profiles").insert({
            "id":                 student_id,
            "full_name":          body.name,
            "email":              body.email,
            "role":               "student",
            "school_id":          school_id,
            "disability_profile": body.disability_profile,
            "language":           body.language,
            "class_tag":          body.class_tag,
            "font_size":          "medium",
            "voice_speed":        "normal",
            "high_contrast":      False,
            "onboarding_complete": True,
            "created_at":         datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        try:
            supabase.auth.admin.delete_user(student_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to create student profile.")

    return CreateStudentResponse(
        student=StudentSummary(
            id=student_id,
            name=body.name,
            profile=body.disability_profile,
            lessons=0,
            progress=0,
            last_active="",
            status="inactive",
            class_tag=body.class_tag,
        ),
        temp_password=temp_password,
    )


def get_student_detail(teacher_id: str, student_id: str) -> StudentDetail:
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

    # Fetch lesson progress WITH grade and feedback
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
        lesson_progress.append(LessonSummary(
            id=lrow.get("id", ""),
            title=lrow.get("title", ""),
            subject=lrow.get("subject", ""),
            page_count=lrow.get("page_count", 1),
            icon_emoji=lrow.get("icon_emoji", "📖"),
            teacher_name="",
            progress_percent=p.get("progress_percent", 0),
            current_page=p.get("current_page", 1),
            is_completed=p.get("is_completed", False),
            teacher_grade=p.get("teacher_grade"),       # ✅ NEW
            teacher_feedback=p.get("teacher_feedback"), # ✅ NEW
        ))
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
        last_active = last_active_res.data[0].get("last_accessed_at", "") or ""

    # ✅ Fetch existing teacher note to pre-fill the textarea
    note_res = (
        supabase.table("teacher_notes")
        .select("note_text")
        .eq("teacher_id", teacher_id)
        .eq("student_id", student_id)
        .execute()
    )
    teacher_note = note_res.data[0]["note_text"] if note_res.data else None

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
        class_tag=row.get("class_tag"),
        teacher_note=teacher_note,  # ✅ NEW
    )


def save_teacher_note(teacher_id: str, student_id: str, note_text: str) -> Dict[str, bool]:
    supabase.table("teacher_notes").upsert(
        {
            "teacher_id": teacher_id,
            "student_id": student_id,
            "note_text":  note_text,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="teacher_id,student_id",
    ).execute()
    return {"ok": True}


# ✅ NEW — Save grade + feedback for a student's specific lesson
def save_lesson_grade(
    teacher_id: str,
    student_id: str,
    lesson_id: str,
    grade: Optional[str],
    feedback: Optional[str],
) -> Dict[str, bool]:
    # Verify the lesson belongs to this teacher
    lesson_res = (
        supabase.table("lessons")
        .select("teacher_id")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not lesson_res.data or lesson_res.data["teacher_id"] != teacher_id:
        raise HTTPException(status_code=403, detail="Not authorised to grade this lesson.")

    # Verify the student_lessons row exists
    sl_res = (
        supabase.table("student_lessons")
        .select("student_id")
        .eq("student_id", student_id)
        .eq("lesson_id", lesson_id)
        .execute()
    )
    if not sl_res.data:
        raise HTTPException(status_code=404, detail="Student not assigned to this lesson.")

    supabase.table("student_lessons").update({
        "teacher_grade":    grade,
        "teacher_feedback": feedback,
    }).eq("student_id", student_id).eq("lesson_id", lesson_id).execute()

    return {"ok": True}

# ─────────────────────────────────────────────────────────────────────────────
# Paste this at the bottom of services/teacher_service.py
# ─────────────────────────────────────────────────────────────────────────────

async def reprocess_lesson(user_id: str, lesson_id: str, background_tasks):
    lesson_res = supabase.table("lessons").select("*").eq("id", lesson_id).single().execute()
    if not lesson_res.data:
        raise HTTPException(status_code=404, detail="Lesson not found")

    lesson = lesson_res.data
    if lesson["teacher_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorised")

    pages_res = (
        supabase.table("lesson_pages")
        .select("*")
        .eq("lesson_id", lesson_id)
        .order("page_number")
        .execute()
    )
    pages = pages_res.data or []
    if not pages:
        raise HTTPException(status_code=404, detail="No pages found for this lesson")

    supabase.table("lessons").update({"processing_status": "running"}).eq("id", lesson_id).execute()

    background_tasks.add_task(_run_reprocess, lesson_id=lesson_id, pages=pages)

    return {
        "message": f"Reprocessing started for '{lesson['title']}'",
        "lesson_id": lesson_id,
        "pages": len(pages),
    }


async def _run_reprocess(lesson_id: str, pages: list):
    from services.processing_service import _simplify_text

    completed = 0
    failed    = 0

    for page in pages:
        try:
            page_num = page["page_number"]
            original = page.get("content_original", "")

            if not original:
                logger.warning("⚠️ Page %d — no original content, skipping", page_num)
                continue

            logger.info("🔄 Reprocessing page %d of lesson %s…", page_num, lesson_id)

            simplified = await _simplify_text(original)

            update_data: dict = {}
            if simplified:
                update_data["content_simplified"] = simplified

            if update_data:
                supabase.table("lesson_pages").update(update_data).eq("id", page["id"]).execute()
                logger.info("✅ Page %d done — simplified=%s", page_num, bool(simplified))
                completed += 1
            else:
                logger.warning("❌ Page %d — nothing updated", page_num)
                failed += 1

        except Exception as e:
            logger.error("❌ Page %d error: %s", page["page_number"], e)
            failed += 1

    final_status = "done" if failed == 0 else "done"
    supabase.table("lessons").update({"processing_status": final_status}).eq("id", lesson_id).execute()
    logger.info("🏁 Reprocess done — %d ok, %d failed", completed, failed)