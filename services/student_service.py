"""
services/student_service.py – All student-facing business logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from database import supabase
from schemas import (
    ActivityItem,
    AudioResponse,
    DashboardStats,
    LessonSummary,
    PageContent,
    StudentDashboard,
    StudentProgressPage,
    SubjectBreakdown,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_lesson_summary(
    row: Dict[str, Any],
    enroll: Optional[Dict[str, Any]],
    teacher_name: str,
) -> LessonSummary:
    progress = enroll.get("progress_percent", 0) if enroll else 0
    current = enroll.get("current_page", 1) if enroll else 1
    completed = enroll.get("is_completed", False) if enroll else False
    return LessonSummary(
        id=row["id"],
        title=row.get("title", ""),
        subject=row.get("subject", ""),
        page_count=row.get("page_count", 1),
        icon_emoji=row.get("icon_emoji", "📖"),
        teacher_name=teacher_name,
        progress_percent=progress,
        current_page=current,
        is_completed=completed,
    )


def _get_teacher_name(teacher_id: str) -> str:
    try:
        res = (
            supabase.table("profiles")
            .select("full_name")
            .eq("id", teacher_id)
            .single()
            .execute()
        )
        return res.data.get("full_name", "Teacher") if res.data else "Teacher"
    except Exception:
        return "Teacher"


def _get_student_lessons(user_id: str) -> List[LessonSummary]:
    """
    Return all lessons available to a student with their progress.
    """
    # Get enrollments for this student
    enroll_res = (
        supabase.table("student_lessons")
        .select("*")
        .eq("student_id", user_id)
        .execute()
    )
    enrollments: Dict[str, Dict] = {
        e["lesson_id"]: e for e in (enroll_res.data or [])
    }

    # Get student's school
    profile_res = (
        supabase.table("profiles")
        .select("school_id")
        .eq("id", user_id)
        .single()
        .execute()
    )
    school_id = profile_res.data.get("school_id") if profile_res.data else None

    lesson_query = (
        supabase.table("lessons")
        .select("*")
        .eq("is_published", True)
    )
    if school_id:
        lesson_query = lesson_query.eq("school_id", school_id)

    lessons_res = lesson_query.execute()
    lessons: List[LessonSummary] = []
    for row in lessons_res.data or []:
        teacher_name = _get_teacher_name(row.get("teacher_id", ""))
        enroll = enrollments.get(row["id"])
        lessons.append(_build_lesson_summary(row, enroll, teacher_name))
    return lessons


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def get_student_dashboard(user_id: str) -> StudentDashboard:
    lessons = _get_student_lessons(user_id)
    total = len(lessons)
    completed = sum(1 for l in lessons if l.is_completed)
    in_progress = sum(1 for l in lessons if l.progress_percent > 0 and not l.is_completed)
    overall = int(sum(l.progress_percent for l in lessons) / total) if total else 0

    stats = DashboardStats(
        total_lessons=total,
        completed=completed,
        in_progress=in_progress,
        overall_progress=overall,
    )

    subjects: Dict[str, Dict] = {}
    for l in lessons:
        s = l.subject
        if s not in subjects:
            subjects[s] = {"done": 0, "total": 0}
        subjects[s]["total"] += 1
        if l.is_completed:
            subjects[s]["done"] += 1

    subject_breakdown = [
        SubjectBreakdown(subject=s, done=v["done"], total=v["total"])
        for s, v in subjects.items()
    ]

    recent = sorted(lessons, key=lambda x: x.progress_percent, reverse=True)[:5]
    available = [l for l in lessons if not l.is_completed][:10]

    return StudentDashboard(
        stats=stats,
        recent_lessons=recent,
        available_lessons=available,
        subject_breakdown=subject_breakdown,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lessons
# ─────────────────────────────────────────────────────────────────────────────

def get_student_lessons(user_id: str) -> List[LessonSummary]:
    return _get_student_lessons(user_id)


def get_student_lesson(user_id: str, lesson_id: str) -> LessonSummary:
    res = (
        supabase.table("lessons")
        .select("*")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    row = res.data

    # ✅ FIX: Use .execute() without .single() so missing enrollment
    # returns empty list instead of throwing a 500 error
    enroll_res = (
        supabase.table("student_lessons")
        .select("*")
        .eq("student_id", user_id)
        .eq("lesson_id", lesson_id)
        .execute()
    )
    enroll = enroll_res.data[0] if enroll_res.data else None

    teacher_name = _get_teacher_name(row.get("teacher_id", ""))
    return _build_lesson_summary(row, enroll, teacher_name)


# ─────────────────────────────────────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────────────────────────────────────

def get_lesson_page(user_id: str, lesson_id: str, page_num: int) -> PageContent:
    """Return original + simplified content for a page."""
    lesson_res = (
        supabase.table("lessons")
        .select("id, page_count")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not lesson_res.data:
        raise HTTPException(status_code=404, detail="Lesson not found.")

    page_res = (
        supabase.table("lesson_pages")
        .select("*")
        .eq("lesson_id", lesson_id)
        .eq("page_number", page_num)
        .single()
        .execute()
    )
    if not page_res.data:
        raise HTTPException(status_code=404, detail="Page not found.")

    row = page_res.data
    return PageContent(
        page_number=row["page_number"],
        content_original=row.get("content_original"),
        content_simplified=row.get("content_simplified"),
        image_description=row.get("image_description"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Audio
# ─────────────────────────────────────────────────────────────────────────────

def get_lesson_audio(user_id: str, lesson_id: str) -> AudioResponse:
    """Return pre-generated audio URL for student's preferred language."""
    profile_res = (
        supabase.table("profiles")
        .select("language")
        .eq("id", user_id)
        .single()
        .execute()
    )
    language = (profile_res.data or {}).get("language", "english") or "english"

    audio_res = (
        supabase.table("lesson_audio")
        .select("audio_url, language")
        .eq("lesson_id", lesson_id)
        .eq("language", language)
        .execute()
    )
    if not audio_res.data:
        # Fallback to English
        audio_res = (
            supabase.table("lesson_audio")
            .select("audio_url, language")
            .eq("lesson_id", lesson_id)
            .eq("language", "english")
            .execute()
        )

    if audio_res.data:
        return AudioResponse(audio_url=audio_res.data[0]["audio_url"], language=language)
    return AudioResponse(audio_url=None, language=language)


# ─────────────────────────────────────────────────────────────────────────────
# Progress
# ─────────────────────────────────────────────────────────────────────────────

def update_lesson_progress(
    user_id: str,
    lesson_id: str,
    current_page: int,
    is_completed: bool,
) -> Dict[str, bool]:
    lesson_res = (
        supabase.table("lessons")
        .select("page_count")
        .eq("id", lesson_id)
        .single()
        .execute()
    )
    if not lesson_res.data:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    page_count = lesson_res.data.get("page_count", 1)
    progress_percent = min(100, int((current_page / page_count) * 100))

    upsert_data = {
        "student_id": user_id,
        "lesson_id": lesson_id,
        "current_page": current_page,
        "is_completed": is_completed,
        "progress_percent": 100 if is_completed else progress_percent,
        "last_accessed_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase.table("student_lessons").upsert(
        upsert_data, on_conflict="student_id,lesson_id"
    ).execute()

    if is_completed:
        title_res = (
            supabase.table("lessons")
            .select("title")
            .eq("id", lesson_id)
            .single()
            .execute()
        )
        title = (title_res.data or {}).get("title", "Lesson")
        supabase.table("activity_log").insert(
            {
                "user_id": user_id,
                "action": "completed",
                "lesson_id": lesson_id,
                "lesson_title": title,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

    return {"ok": True}


def get_student_progress(user_id: str) -> StudentProgressPage:
    lessons = _get_student_lessons(user_id)
    total = len(lessons)
    completed_list = [l for l in lessons if l.is_completed]
    inprogress_list = [l for l in lessons if l.progress_percent > 0 and not l.is_completed]
    completed_count = len(completed_list)
    in_progress_count = len(inprogress_list)
    overall = int(sum(l.progress_percent for l in lessons) / total) if total else 0

    stats = DashboardStats(
        total_lessons=total,
        completed=completed_count,
        in_progress=in_progress_count,
        overall_progress=overall,
    )

    subjects: Dict[str, Dict] = {}
    for l in lessons:
        s = l.subject
        if s not in subjects:
            subjects[s] = {"done": 0, "total": 0}
        subjects[s]["total"] += 1
        if l.is_completed:
            subjects[s]["done"] += 1

    subject_breakdown = [
        SubjectBreakdown(subject=s, done=v["done"], total=v["total"])
        for s, v in subjects.items()
    ]

    activity_res = (
        supabase.table("activity_log")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    activity = [
        ActivityItem(
            action=row.get("action", ""),
            lesson_title=row.get("lesson_title", ""),
            created_at=row.get("created_at", ""),
        )
        for row in (activity_res.data or [])
    ]

    return StudentProgressPage(
        stats=stats,
        completed_lessons=completed_list,
        inprogress_lessons=inprogress_list,
        subject_breakdown=subject_breakdown,
        activity_log=activity,
    )