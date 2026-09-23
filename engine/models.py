"""Domain models for Career Quest dataset (employees, events, skills, activity history)."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


class Skill(BaseModel):
    skill_id: str
    name: str
    type: str
    category: str
    description: str = ""


class RoleProfile(BaseModel):
    role: str
    grade: str
    required_skills: dict[str, int]
    critical_skills: list[str] = Field(default_factory=list)


class CareerGoal(BaseModel):
    target_role: str
    target_grade: str


class Employee(BaseModel):
    employee_id: str
    full_name: str
    department: str
    role: str
    grade: str
    manager_id: Optional[str] = None
    hire_date: date
    tenure_months: int
    work_format: str
    preferred_language: str = "ru"
    career_goal: Optional[CareerGoal] = None
    skills: dict[str, int] = Field(default_factory=dict)
    last_review_date: date


class SkillGain(BaseModel):
    skill_id: str
    gain: int
    max_level: int


class Event(BaseModel):
    event_id: str
    title: str
    description: str = ""
    type: str
    format: str
    duration_hours: float
    mandatory: bool
    target_roles: list[str] = Field(default_factory=list)
    target_grades: list[str] = Field(default_factory=list)
    develops_skills: list[SkillGain] = Field(default_factory=list)
    prerequisites: dict[str, int] = Field(default_factory=dict)
    upcoming_sessions: list[date] = Field(default_factory=list)


class ActivityRecord(BaseModel):
    record_id: str
    employee_id: str
    event_id: str
    date: date
    due_date: Optional[date] = None
    status: str
    completion_pct: int
    score: Optional[int] = None
    feedback_rating: Optional[int] = None
    assigned_by: str


GRADE_ORDER = ["Junior", "Middle", "Senior", "Lead"]

NEGATIVE_STATUSES = {"no_show", "dropped", "declined", "overdue"}
POSITIVE_STATUSES = {"completed"}


def next_grade(grade: str) -> Optional[str]:
    try:
        idx = GRADE_ORDER.index(grade)
    except ValueError:
        return None
    if idx + 1 >= len(GRADE_ORDER):
        return None
    return GRADE_ORDER[idx + 1]
