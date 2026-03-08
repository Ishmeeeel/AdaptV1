"""
schemas.py – Pydantic models for all request bodies and API responses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field


# ─────────────────────────────────────────────────────────────────────────────
# Shared / Auth
# ─────────────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(..., pattern="^(teacher|admin)$")
    school_code: str = Field(..., min_length=4, max_length=20)


class OnboardingRequest(BaseModel):
    guide_type: str
    disability_profile: str
    language: str


class SettingsRequest(BaseModel):
    profile: Optional[str] = None
    language: Optional[str] = None
    font_size: Optional[str] = None
    voice_speed: Optional[str] = None
    high_contrast: Optional[bool] = None


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    school_id: Optional[str] = None
    disability_profile: Optional[str] = None
    profile: Optional[str] = None
    language: Optional[str] = None
    font_size: str = "medium"
    voice_speed: str = "normal"
    high_contrast: bool = False
    onboarding_complete: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Student
# ─────────────────────────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total_lessons: int
    completed: int
    in_progress: int
    overall_progress: int


class LessonSummary(BaseModel):
    id: str
    title: str
    subject: str
    page_count: int
    icon_emoji: str
    teacher_name: str
    progress_percent: int
    current_page: int
    is_completed: bool
    teacher_grade: Optional[str] = None       # ✅ NEW
    teacher_feedback: Optional[str] = None    # ✅ NEW


class SubjectBreakdown(BaseModel):
    subject: str
    done: int
    total: int


class StudentDashboard(BaseModel):
    stats: DashboardStats
    recent_lessons: List[LessonSummary]
    available_lessons: List[LessonSummary]
    subject_breakdown: List[SubjectBreakdown]


class PageContent(BaseModel):
    page_number: int
    content_original: Optional[str] = None
    content_simplified: Optional[str] = None
    image_description: Optional[str] = None


class AudioResponse(BaseModel):
    audio_url: Optional[str] = None
    language: str


class UpdateProgressRequest(BaseModel):
    current_page: int = Field(..., ge=1)
    is_completed: bool = False


class ActivityItem(BaseModel):
    action: str
    lesson_title: str
    created_at: str


class StudentProgressPage(BaseModel):
    stats: DashboardStats
    completed_lessons: List[LessonSummary]
    inprogress_lessons: List[LessonSummary]
    subject_breakdown: List[SubjectBreakdown]
    activity_log: List[ActivityItem]


# ─────────────────────────────────────────────────────────────────────────────
# Teacher
# ─────────────────────────────────────────────────────────────────────────────

class TeacherLesson(BaseModel):
    id: str
    title: str
    subject: str
    page_count: int
    icon_emoji: str
    is_published: bool
    processing_status: str
    student_count: int
    created_at: str


class ProcessingSteps(BaseModel):
    extract_text: bool = False
    audio_english: bool = False
    audio_hausa: bool = False
    audio_yoruba: bool = False
    audio_igbo: bool = False
    simplify_dyslexia: bool = False
    image_descriptions: bool = False


class ProcessingStatus(BaseModel):
    lesson_id: str
    status: str
    steps: ProcessingSteps
    error_message: Optional[str] = None


class UploadResponse(BaseModel):
    lesson_id: str
    message: str


class AssignLessonRequest(BaseModel):
    student_ids: List[str]


class StudentSummary(BaseModel):
    id: str
    name: str
    profile: str
    lessons: int
    progress: int
    last_active: str
    status: str
    class_tag: Optional[str] = None


class CreateStudentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    disability_profile: str
    language: str
    class_tag: Optional[str] = None


class CreateStudentResponse(BaseModel):
    student: StudentSummary
    temp_password: str


class StudentDetail(BaseModel):
    id: str
    name: str
    profile: str
    language: str
    progress: int
    lessons: int
    status: str
    last_active: str
    lesson_progress: List[LessonSummary]
    font_size: str
    voice_speed: str
    high_contrast: bool
    class_tag: Optional[str] = None
    teacher_note: Optional[str] = None        # ✅ NEW — pre-fills note textarea


class GradeLessonRequest(BaseModel):          # ✅ NEW
    grade: Optional[str] = None               # A / B / C / D / F or null to clear
    feedback: Optional[str] = Field(None, max_length=2000)


class SaveNoteRequest(BaseModel):
    note_text: str = Field(..., max_length=5000)


class TeacherDashboard(BaseModel):
    stats: Dict[str, int]
    recent_students: List[StudentSummary]
    profile_breakdown: List[Dict[str, Any]]
    top_lessons: List[TeacherLesson]


# ─────────────────────────────────────────────────────────────────────────────
# Admin
# ─────────────────────────────────────────────────────────────────────────────

class SchoolSummary(BaseModel):
    id: str
    name: str
    location: str
    access_code: str
    students: int
    teachers: int
    is_active: bool


class CreateSchoolRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    location: str = Field(..., min_length=2, max_length=200)


class AdminDashboard(BaseModel):
    stats: Dict[str, int]
    schools: List[SchoolSummary]
    profile_breakdown: List[Dict[str, Any]]