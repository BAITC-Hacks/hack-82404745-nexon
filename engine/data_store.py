"""In-memory dataset store: loads the Career Quest dataset and lets jury profiles be merged in."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .models import ActivityRecord, Employee, Event, RoleProfile, Skill

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AS_OF_DATE = date(2026, 10, 1)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


class DataStore:
    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}
        self.role_profiles: dict[tuple[str, str], RoleProfile] = {}
        self.proficiency_scale: dict[str, str] = {}
        self.employees: dict[str, Employee] = {}
        self.events: dict[str, Event] = {}
        self.activity: list[ActivityRecord] = []
        self.activity_by_employee: dict[str, list[ActivityRecord]] = {}

    # ── loading ──────────────────────────────────────────────────────

    def load_base_dataset(self) -> None:
        logger.info(f"Loading base dataset from {DATA_DIR}")
        skills_raw = json.loads((DATA_DIR / "skills.json").read_text(encoding="utf-8"))
        for s in skills_raw["skills"]:
            skill = Skill(**s)
            self.skills[skill.skill_id] = skill
        for rp in skills_raw["role_profiles"]:
            profile = RoleProfile(**rp)
            self.role_profiles[(profile.role, profile.grade)] = profile
        self.proficiency_scale = {
            str(k): v for k, v in skills_raw["proficiency_scale"].items()
        }

        employees_raw = json.loads((DATA_DIR / "employees.json").read_text(encoding="utf-8"))
        for e in employees_raw["employees"]:
            emp = Employee(**e)
            self.employees[emp.employee_id] = emp

        events_raw = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
        for ev in events_raw["events"]:
            event = Event(**ev)
            self.events[event.event_id] = event

        with open(DATA_DIR / "activity_history.csv", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self._add_activity_row(row)

        logger.info(
            f"Loaded {len(self.employees)} employees, {len(self.events)} events, "
            f"{len(self.skills)} skills, {len(self.activity)} activity records"
        )

    def _add_activity_row(self, row: dict[str, Any]) -> None:
        record = ActivityRecord(
            record_id=row["record_id"],
            employee_id=row["employee_id"],
            event_id=row["event_id"],
            date=_parse_date(row["date"]),
            due_date=_parse_date(row["due_date"]) if row.get("due_date") else None,
            status=row["status"],
            completion_pct=int(row["completion_pct"]),
            score=int(row["score"]) if row.get("score") else None,
            feedback_rating=int(row["feedback_rating"]) if row.get("feedback_rating") else None,
            assigned_by=row["assigned_by"],
        )
        self.activity.append(record)
        self.activity_by_employee.setdefault(record.employee_id, []).append(record)

    # ── jury upload merge ───────────────────────────────────────────

    def merge_employees(self, employees_payload: dict[str, Any] | list[dict[str, Any]]) -> int:
        # Accept every shape the case brief or a naive checker script might send:
        # {"employees": [...]}, {"employees": {...}}, a single bare profile (the
        # literal example in the brief), or a bare top-level list of profiles.
        if isinstance(employees_payload, list):
            records: Any = employees_payload
        else:
            records = employees_payload.get("employees", employees_payload)
        if isinstance(records, dict):
            records = [records]
        count = 0
        for e in records:
            emp = Employee(**e)
            self.employees[emp.employee_id] = emp
            count += 1
        logger.info(f"Merged {count} jury employee profiles")
        return count

    def merge_activity_csv_text(self, csv_text: str) -> int:
        reader = csv.DictReader(csv_text.splitlines())
        count = 0
        for row in reader:
            self._add_activity_row(row)
            count += 1
        logger.info(f"Merged {count} jury activity records")
        return count

    # ── accessors ────────────────────────────────────────────────────

    def get_employee(self, employee_id: str) -> Optional[Employee]:
        return self.employees.get(employee_id)

    def get_role_profile(self, role: str, grade: str) -> Optional[RoleProfile]:
        return self.role_profiles.get((role, grade))

    def get_activity_for(self, employee_id: str) -> list[ActivityRecord]:
        return self.activity_by_employee.get(employee_id, [])

    def list_employees_brief(self) -> list[dict[str, str]]:
        return [
            {
                "employee_id": e.employee_id,
                "full_name": e.full_name,
                "role": e.role,
                "grade": e.grade,
                "department": e.department,
            }
            for e in sorted(self.employees.values(), key=lambda x: x.employee_id)
        ]


store = DataStore()
