"""Deterministic functions the career-advisor agent can call as tools.

Same design principle as recommender.py: every fact returned here is computed
from the real dataset, nothing is invented. The LLM decides *when* to call a
tool and *how to phrase* the result — it never computes the numbers itself.
"""

from __future__ import annotations

from .data_store import DataStore
from .models import GRADE_ORDER, Employee
from .recommender import build_trajectories, effective_skills, pick_primary_trajectory

MENTOR_MIN_LEVEL = 3  # on the 0-5 proficiency scale: "solid enough to mentor others"


def _grade_rank(grade: str) -> int:
    try:
        return GRADE_ORDER.index(grade)
    except ValueError:
        return -1


def find_mentor(store: DataStore, skill_id: str, department: str | None = None, exclude_employee_id: str | None = None, limit: int = 5) -> dict:
    """Find real colleagues who already have a strong level in a given skill."""
    skill = store.skills.get(skill_id)
    skill_name = skill.name if skill else skill_id

    candidates = []
    for emp in store.employees.values():
        if emp.employee_id == exclude_employee_id:
            continue
        if department and emp.department != department:
            continue
        levels = effective_skills(store, emp)
        level = levels.get(skill_id, 0)
        if level >= MENTOR_MIN_LEVEL:
            candidates.append(
                {
                    "employee_id": emp.employee_id,
                    "full_name": emp.full_name,
                    "role": emp.role,
                    "grade": emp.grade,
                    "department": emp.department,
                    "level": level,
                    "same_department": department is not None and emp.department == department,
                }
            )

    candidates.sort(key=lambda c: (-c["level"], -_grade_rank(c["grade"]), c["full_name"]))

    if not candidates:
        return {
            "skill_id": skill_id,
            "skill_name": skill_name,
            "candidates": [],
            "note": f"В доступных данных нет сотрудников с уровнем {skill_name} >= {MENTOR_MIN_LEVEL}.",
        }

    return {"skill_id": skill_id, "skill_name": skill_name, "candidates": candidates[:limit]}


def simulate_department_impact(store: DataStore, event_id: str, department: str | None = None) -> dict:
    """What happens if everyone with a matching gap completes this event.

    Simulates applying the event's skill gains to every employee's current
    trajectory gaps — the same math recommender.py already uses per-person,
    just aggregated across a group instead of ranking candidates for one.
    """
    event = store.events.get(event_id)
    if not event:
        return {"error": f"событие '{event_id}' не найдено"}

    affected_skill_ids = {g.skill_id for g in event.develops_skills}
    if not affected_skill_ids:
        return {
            "event_id": event_id,
            "event_title": event.title,
            "employees_affected": 0,
            "note": "это событие не развивает измеримые навыки (например, compliance-тренинг)",
        }

    employees_affected = 0
    total_gap_before = 0
    total_gap_closed = 0
    critical_skills_fully_closed = 0

    for emp in store.employees.values():
        if department and emp.department != department:
            continue

        levels = effective_skills(store, emp)
        trajectories = build_trajectories(store, emp, levels)
        primary = pick_primary_trajectory(trajectories)
        if not primary or not primary.gaps:
            continue

        relevant_gaps = {sid: gap for sid, gap in primary.gaps.items() if sid in affected_skill_ids}
        if not relevant_gaps:
            continue

        employees_affected += 1
        person_gap_before = sum(relevant_gaps.values())
        person_gap_closed = 0
        for gain in event.develops_skills:
            if gain.skill_id not in relevant_gaps:
                continue
            closed = min(gain.gain, relevant_gaps[gain.skill_id])
            person_gap_closed += closed
            new_level = min(gain.max_level, levels.get(gain.skill_id, 0) + gain.gain)
            required = primary.profile.required_skills.get(gain.skill_id, 0)
            if gain.skill_id in primary.critical_gaps and new_level >= required:
                critical_skills_fully_closed += 1

        total_gap_before += person_gap_before
        total_gap_closed += person_gap_closed

    pct_closed = round(100 * total_gap_closed / total_gap_before, 1) if total_gap_before else 0.0

    return {
        "event_id": event_id,
        "event_title": event.title,
        "department": department or "вся компания",
        "employees_affected": employees_affected,
        "total_gap_units_before": total_gap_before,
        "total_gap_units_closed": total_gap_closed,
        "pct_of_gap_closed": pct_closed,
        "critical_skills_fully_closed_count": critical_skills_fully_closed,
    }


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_mentor",
            "description": (
                "Найти реальных сотрудников компании, у которых уже сильный уровень указанного навыка "
                "(потенциальных менторов или коллег для консультации). Используй, когда пользователь "
                "спрашивает 'кто может помочь', 'с кем поговорить', 'есть ли ментор' и т.п."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "description": "ID навыка, например SK_SYSTEM_DESIGN"},
                    "department": {"type": "string", "description": "Ограничить отделом (опционально)"},
                },
                "required": ["skill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_department_impact",
            "description": (
                "Смоделировать эффект от массового назначения активности развития: сколько сотрудников "
                "затронет, насколько закроет их разрывы в навыках. Используй для HR-вопросов вида "
                "'что если назначить курс X всему отделу Y'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "ID активности, например EV_005"},
                    "department": {"type": "string", "description": "Ограничить отделом (опционально, иначе вся компания)"},
                },
                "required": ["event_id"],
            },
        },
    },
]


def execute_tool(store: DataStore, name: str, arguments: dict, *, employee_id: str | None = None) -> dict:
    if name == "find_mentor":
        return find_mentor(
            store,
            skill_id=arguments.get("skill_id", ""),
            department=arguments.get("department"),
            exclude_employee_id=employee_id,
        )
    if name == "simulate_department_impact":
        return simulate_department_impact(
            store,
            event_id=arguments.get("event_id", ""),
            department=arguments.get("department"),
        )
    return {"error": f"unknown tool '{name}'"}
