"""
services/admin_service.py – Business logic for admin-facing endpoints.
"""

from __future__ import annotations

import logging
import random
import string
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import HTTPException

from database import supabase
from schemas import AdminDashboard, SchoolSummary

logger = logging.getLogger(__name__)


def _generate_access_code(length: int = 8) -> str:
    """Generate a random alphanumeric school access code."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def _row_to_school(row: Dict[str, Any]) -> SchoolSummary:
    students_res = (
        supabase.table("profiles")
        .select("id", count="exact")
        .eq("school_id", row["id"])
        .eq("role", "student")
        .execute()
    )
    teachers_res = (
        supabase.table("profiles")
        .select("id", count="exact")
        .eq("school_id", row["id"])
        .eq("role", "teacher")
        .execute()
    )
    return SchoolSummary(
        id=row["id"],
        name=row.get("name", ""),
        location=row.get("location", ""),
        access_code=row.get("access_code", ""),
        students=students_res.count or 0,
        teachers=teachers_res.count or 0,
        is_active=row.get("is_active", True),
    )


def _verify_admin(user_id: str) -> None:
    res = (
        supabase.table("profiles")
        .select("role")
        .eq("id", user_id)
        .single()
        .execute()
    )
    if not res.data or res.data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

def get_admin_dashboard(user_id: str) -> AdminDashboard:
    _verify_admin(user_id)

    schools_res = supabase.table("schools").select("*").execute()
    schools = [_row_to_school(row) for row in (schools_res.data or [])]

    students_res = (
        supabase.table("profiles").select("id", count="exact").eq("role", "student").execute()
    )
    teachers_res = (
        supabase.table("profiles").select("id", count="exact").eq("role", "teacher").execute()
    )
    lessons_res = (
        supabase.table("lessons").select("id", count="exact").execute()
    )

    # Profile breakdown across all schools
    all_students_res = (
        supabase.table("profiles")
        .select("disability_profile")
        .eq("role", "student")
        .execute()
    )
    profile_counter: Dict[str, int] = {}
    for s in all_students_res.data or []:
        p = s.get("disability_profile") or "none"
        profile_counter[p] = profile_counter.get(p, 0) + 1
    profile_breakdown = [{"profile": k, "count": v} for k, v in profile_counter.items()]

    stats = {
        "total_schools": len(schools),
        "total_students": students_res.count or 0,
        "total_teachers": teachers_res.count or 0,
        "total_lessons": lessons_res.count or 0,
    }

    return AdminDashboard(
        stats=stats,
        schools=schools,
        profile_breakdown=profile_breakdown,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Schools
# ─────────────────────────────────────────────────────────────────────────────

def get_schools(user_id: str) -> List[SchoolSummary]:
    _verify_admin(user_id)
    res = supabase.table("schools").select("*").order("created_at", desc=True).execute()
    return [_row_to_school(row) for row in (res.data or [])]


def create_school(user_id: str, name: str, location: str) -> SchoolSummary:
    _verify_admin(user_id)
    school_id = str(uuid.uuid4())
    access_code = _generate_access_code()

    row = {
        "id": school_id,
        "name": name,
        "location": location,
        "access_code": access_code,
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase.table("schools").insert(row).execute()
    return _row_to_school(row)


def regenerate_access_code(user_id: str, school_id: str) -> Dict[str, str]:
    _verify_admin(user_id)
    new_code = _generate_access_code()
    supabase.table("schools").update({"access_code": new_code}).eq("id", school_id).execute()
    return {"access_code": new_code}

def get_all_users(user_id: str) -> List[Dict[str, Any]]:
    _verify_admin(user_id)
    res = (
        supabase.table("profiles")
        .select("id, full_name, email, role, school_id, disability_profile, language")
        .order("created_at", desc=True)
        .execute()
    )
    return [
        {
            "id":                 r["id"],
            "name":               r.get("full_name", ""),
            "email":              r.get("email", ""),
            "role":               r.get("role", ""),
            "school_id":          r.get("school_id"),
            "disability_profile": r.get("disability_profile"),
            "language":           r.get("language"),
        }
        for r in (res.data or [])
    ]