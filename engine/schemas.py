"""API request/response schemas — the contract the frontend (Codex) builds against."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class EmployeeBrief(BaseModel):
    employee_id: str
    full_name: str
    role: str
    grade: str
    department: str


class SkillLevel(BaseModel):
    skill_id: str
    name: str
    type: str
    current_level: int
    required_level: Optional[int] = None
    gap: int = 0
    critical: bool = False


class PromotionEstimateOut(BaseModel):
    skills_remaining: int
    critical_skills_remaining: int
    estimated_months: Optional[float] = None
    basis: str


class TrajectoryOut(BaseModel):
    label: str  # "next_grade" | "career_goal"
    role: str
    grade: str
    readiness_pct: float
    skills: list[SkillLevel]
    promotion_estimate: PromotionEstimateOut


class RecommendationFactorOut(BaseModel):
    kind: str
    detail: str
    payload: dict[str, Any]


class RecommendationOut(BaseModel):
    event_id: str
    title: str
    description: str
    type: str
    format: str
    duration_hours: float
    upcoming_sessions: list[str]
    score: float
    factors: list[RecommendationFactorOut]
    explanation: str
    score_breakdown: dict[str, Any]


class ActivityHistoryItem(BaseModel):
    event_id: str
    event_title: str
    date: str
    status: str
    completion_pct: int


class EmployeeProfileOut(BaseModel):
    employee_id: str
    full_name: str
    department: str
    role: str
    grade: str
    work_format: str
    preferred_language: str
    tenure_months: int
    career_goal: Optional[dict[str, str]] = None
    trajectories: list[TrajectoryOut]
    recommendations: list[RecommendationOut]
    recent_activity: list[ActivityHistoryItem]


class CompleteActivityRequest(BaseModel):
    event_id: str


class HRSkillGap(BaseModel):
    skill_id: str
    name: str
    employees_with_gap: int
    avg_gap: float


class HREmployeeFlag(BaseModel):
    employee_id: str
    full_name: str
    role: str
    grade: str
    reason: str


class HREventParticipation(BaseModel):
    event_id: str
    title: str
    completed: int
    negative: int
    total: int


class HRDepartmentGapCell(BaseModel):
    skill_id: str
    name: str
    employees_with_gap: int
    ratio: float  # employees_with_gap / department_employee_count, for heatmap color intensity


class HRDepartmentGap(BaseModel):
    department: str
    employee_count: int
    cells: list[HRDepartmentGapCell]


class HROverviewOut(BaseModel):
    total_employees: int
    top_skill_gaps: list[HRSkillGap]
    employees_without_recommendation: list[HREmployeeFlag]
    stalled_employees: list[HREmployeeFlag]
    department_gaps: list[HRDepartmentGap]
    event_participation: list[HREventParticipation]


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AgentChatRequest(BaseModel):
    messages: list[ChatMessage]
    employee_id: Optional[str] = None  # omitted/None = HR context


class AgentToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class AgentChatResponse(BaseModel):
    reply: str
    tool_trace: list[AgentToolCall]


class UploadEmployeesResult(BaseModel):
    merged_employees: int
    total_employees: int


class UploadActivityResult(BaseModel):
    merged_records: int
    total_records: int
