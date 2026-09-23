"""Deterministic recommendation engine: effective skills, gaps, candidate ranking.

Design principle: every number the jury can audit is computed here, not by an LLM.
The LLM (see llm_explainer.py) only turns the structured factors below into prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .data_store import AS_OF_DATE, DataStore
from .models import ActivityRecord, Employee, Event, RoleProfile, next_grade

NEGATIVE_STATUSES = {"no_show", "declined", "dropped"}
COMPLETED_STATUS = "completed"
REPEATABLE_EVENTS = {"EV_036"}


@dataclass
class TrajectoryTarget:
    label: str  # "next_grade" | "career_goal"
    role: str
    grade: str
    profile: RoleProfile
    gaps: dict[str, int]
    critical_gaps: dict[str, int]
    readiness_pct: float


@dataclass
class SkillFormatSignal:
    format: str
    total: int
    negative: int
    negative_examples: list[str] = field(default_factory=list)

    @property
    def negative_ratio(self) -> float:
        if self.total == 0:
            return 0.0
        return self.negative / self.total


@dataclass
class RecommendationFactor:
    kind: str
    detail: str
    payload: dict


@dataclass
class Recommendation:
    event: Event
    score: float
    factors: list[RecommendationFactor]
    closes_skills: dict[str, int]  # skill_id -> level gained toward the gap
    score_breakdown: dict[str, float]  # raw scoring weights, for the "under the hood" UI toggle


def effective_skills(store: DataStore, employee: Employee) -> dict[str, int]:
    """Base skill levels + gains from activities completed after last_review_date."""
    levels = dict(employee.skills)
    records = [
        r
        for r in store.get_activity_for(employee.employee_id)
        if r.status == COMPLETED_STATUS and r.date > employee.last_review_date
    ]
    records.sort(key=lambda r: r.date)
    for record in records:
        event = store.events.get(record.event_id)
        if not event:
            continue
        for gain in event.develops_skills:
            current = levels.get(gain.skill_id, 0)
            levels[gain.skill_id] = min(gain.max_level, current + gain.gain)
    return levels


def _compute_gaps(levels: dict[str, int], profile: RoleProfile) -> tuple[dict[str, int], dict[str, int]]:
    gaps: dict[str, int] = {}
    critical_gaps: dict[str, int] = {}
    for skill_id, required in profile.required_skills.items():
        current = levels.get(skill_id, 0)
        gap = max(0, required - current)
        if gap > 0:
            gaps[skill_id] = gap
            if skill_id in profile.critical_skills:
                critical_gaps[skill_id] = gap
    return gaps, critical_gaps


def _readiness_pct(levels: dict[str, int], profile: RoleProfile) -> float:
    if not profile.required_skills:
        return 100.0
    met = sum(
        1
        for skill_id, required in profile.required_skills.items()
        if levels.get(skill_id, 0) >= required
    )
    return round(100 * met / len(profile.required_skills), 1)


def build_trajectories(store: DataStore, employee: Employee, levels: dict[str, int]) -> list[TrajectoryTarget]:
    trajectories: list[TrajectoryTarget] = []

    ng = next_grade(employee.grade)
    if ng:
        profile = store.get_role_profile(employee.role, ng)
        if profile:
            gaps, critical = _compute_gaps(levels, profile)
            trajectories.append(
                TrajectoryTarget(
                    label="next_grade",
                    role=employee.role,
                    grade=ng,
                    profile=profile,
                    gaps=gaps,
                    critical_gaps=critical,
                    readiness_pct=_readiness_pct(levels, profile),
                )
            )

    if employee.career_goal and (
        employee.career_goal.target_role != employee.role
        or employee.career_goal.target_grade != employee.grade
    ):
        profile = store.get_role_profile(employee.career_goal.target_role, employee.career_goal.target_grade)
        if profile:
            gaps, critical = _compute_gaps(levels, profile)
            trajectories.append(
                TrajectoryTarget(
                    label="career_goal",
                    role=employee.career_goal.target_role,
                    grade=employee.career_goal.target_grade,
                    profile=profile,
                    gaps=gaps,
                    critical_gaps=critical,
                    readiness_pct=_readiness_pct(levels, profile),
                )
            )

    return trajectories


def pick_primary_trajectory(trajectories: list[TrajectoryTarget]) -> TrajectoryTarget | None:
    """Career goal wins over the default next-grade path when both are set."""
    for t in trajectories:
        if t.label == "career_goal":
            return t
    for t in trajectories:
        if t.label == "next_grade":
            return t
    return None


def _format_signals(store: DataStore, employee: Employee) -> dict[str, SkillFormatSignal]:
    """Per-format participation pattern: how often this employee skips/drops that format."""
    signals: dict[str, SkillFormatSignal] = {}
    for record in store.get_activity_for(employee.employee_id):
        event = store.events.get(record.event_id)
        if not event or event.mandatory:
            continue
        if record.status not in NEGATIVE_STATUSES and record.status != COMPLETED_STATUS:
            continue
        sig = signals.setdefault(event.format, SkillFormatSignal(format=event.format, total=0, negative=0))
        sig.total += 1
        if record.status in NEGATIVE_STATUSES:
            sig.negative += 1
            if len(sig.negative_examples) < 3:
                sig.negative_examples.append(f"{event.title} ({record.status})")
    return signals


def _already_done_event_ids(store: DataStore, employee: Employee) -> set[str]:
    done = set()
    for record in store.get_activity_for(employee.employee_id):
        if record.status == COMPLETED_STATUS and record.event_id not in REPEATABLE_EVENTS:
            done.add(record.event_id)
    return done


def _has_future_session(event: Event, as_of: date) -> bool:
    if event.format == "self_paced":
        return True
    return any(s >= as_of for s in event.upcoming_sessions)


def _prerequisites_met(event: Event, levels: dict[str, int]) -> bool:
    return all(levels.get(skill_id, 0) >= min_level for skill_id, min_level in event.prerequisites.items())


def recommend(
    store: DataStore,
    employee: Employee,
    levels: dict[str, int],
    target: TrajectoryTarget,
    as_of: date = AS_OF_DATE,
    top_n: int = 3,
) -> list[Recommendation]:
    if not target.gaps:
        return []

    done_ids = _already_done_event_ids(store, employee)
    format_signals = _format_signals(store, employee)

    candidates: list[Recommendation] = []

    for event in store.events.values():
        if event.mandatory:
            continue
        if event.event_id in done_ids:
            continue
        if event.target_roles and employee.role not in event.target_roles:
            continue
        if event.target_grades and employee.grade not in event.target_grades:
            continue
        if not _prerequisites_met(event, levels):
            continue
        if not _has_future_session(event, as_of):
            continue

        closes_skills: dict[str, int] = {}
        gap_score = 0.0
        for gain in event.develops_skills:
            if gain.skill_id not in target.gaps:
                continue
            closed = min(gain.gain, target.gaps[gain.skill_id])
            if closed <= 0:
                continue
            weight = 2.0 if gain.skill_id in target.critical_gaps else 1.0
            gap_score += closed * weight
            closes_skills[gain.skill_id] = closed

        if gap_score <= 0:
            continue

        signal = format_signals.get(event.format)
        risk_ratio = signal.negative_ratio if signal and signal.total >= 2 else 0.0
        risk_multiplier = max(0.4, 1.0 - 0.5 * risk_ratio)
        final_score = round(gap_score * risk_multiplier, 3)

        factors: list[RecommendationFactor] = []
        for skill_id, closed in closes_skills.items():
            skill = store.skills.get(skill_id)
            is_critical = skill_id in target.critical_gaps
            factors.append(
                RecommendationFactor(
                    kind="skill_gap",
                    detail=(
                        f"{skill.name if skill else skill_id}: текущий уровень "
                        f"{levels.get(skill_id, 0)} при требуемых {target.profile.required_skills[skill_id]} "
                        f"для {target.role} {target.grade}"
                        + (" — критичный навык для повышения" if is_critical else "")
                    ),
                    payload={
                        "skill_id": skill_id,
                        "skill_name": skill.name if skill else skill_id,
                        "current_level": levels.get(skill_id, 0),
                        "required_level": target.profile.required_skills[skill_id],
                        "gap_closed": closed,
                        "critical": is_critical,
                    },
                )
            )

        if signal and signal.total >= 2:
            if risk_ratio > 0:
                factors.append(
                    RecommendationFactor(
                        kind="history_risk",
                        detail=(
                            f"формат «{event.format}»: {signal.negative} из {signal.total} прошлых "
                            f"активностей в этом формате пропущены/брошены ({', '.join(signal.negative_examples)})"
                        ),
                        payload={"format": event.format, "negative": signal.negative, "total": signal.total},
                    )
                )
            else:
                factors.append(
                    RecommendationFactor(
                        kind="history_positive",
                        detail=f"формат «{event.format}»: все {signal.total} прошлых активности в этом формате пройдены",
                        payload={"format": event.format, "negative": 0, "total": signal.total},
                    )
                )

        factors.append(
            RecommendationFactor(
                kind="logistics",
                detail=(
                    f"{event.format}, {event.duration_hours} ч"
                    + (
                        f", ближайшая сессия {min(s for s in event.upcoming_sessions if s >= as_of)}"
                        if event.upcoming_sessions and event.format != "self_paced"
                        else ", доступно в любое время"
                    )
                ),
                payload={"format": event.format, "duration_hours": event.duration_hours},
            )
        )

        score_breakdown = {
            "gap_score": round(gap_score, 3),
            "critical_weight_applied": any(sid in target.critical_gaps for sid in closes_skills),
            "history_risk_ratio": round(risk_ratio, 3),
            "risk_multiplier": round(risk_multiplier, 3),
            "final_score": final_score,
        }

        candidates.append(
            Recommendation(
                event=event,
                score=final_score,
                factors=factors,
                closes_skills=closes_skills,
                score_breakdown=score_breakdown,
            )
        )

    candidates.sort(
        key=lambda r: (
            -r.score,
            min((s for s in r.event.upcoming_sessions if s >= as_of), default=date.max),
            r.event.duration_hours,
        )
    )
    return candidates[:top_n]


@dataclass
class PromotionEstimate:
    skills_remaining: int
    critical_skills_remaining: int
    estimated_months: float | None
    basis: str  # human-readable note on where the pace number came from


_company_pace_cache: float | None = None


def _voluntary_completed(store: DataStore, employee_id: str) -> list[ActivityRecord]:
    out = []
    for r in store.get_activity_for(employee_id):
        if r.status != COMPLETED_STATUS:
            continue
        event = store.events.get(r.event_id)
        if event and not event.mandatory:
            out.append(r)
    return out


def _company_average_pace(store: DataStore) -> float:
    """Average voluntary activities completed per month, across employees with 2+ completions.

    Used as a fallback when a specific employee doesn't have enough personal history yet.
    """
    global _company_pace_cache
    if _company_pace_cache is not None:
        return _company_pace_cache

    paces = []
    for employee_id in store.employees:
        records = sorted(_voluntary_completed(store, employee_id), key=lambda r: r.date)
        if len(records) < 2:
            continue
        span_days = (records[-1].date - records[0].date).days
        if span_days <= 0:
            continue
        paces.append(len(records) / (span_days / 30))

    _company_pace_cache = round(sum(paces) / len(paces), 3) if paces else 0.5
    return _company_pace_cache


def _average_event_gain(store: DataStore) -> float:
    gains = [g.gain for e in store.events.values() for g in e.develops_skills]
    return sum(gains) / len(gains) if gains else 1.0


def estimate_time_to_promotion(store: DataStore, employee: Employee, target: TrajectoryTarget) -> PromotionEstimate:
    """Rough, auditable estimate — not an LLM guess. Based on this employee's own
    historical pace of completing voluntary development activities, projected onto
    how many skill-levels are still missing for the target grade.
    """
    skills_remaining = len(target.gaps)
    critical_remaining = len(target.critical_gaps)

    if skills_remaining == 0:
        return PromotionEstimate(0, 0, 0.0, "все требования уже выполнены")

    total_level_gap = sum(target.gaps.values())
    avg_gain = _average_event_gain(store)
    steps_needed = total_level_gap / avg_gain if avg_gain > 0 else total_level_gap

    personal_records = sorted(_voluntary_completed(store, employee.employee_id), key=lambda r: r.date)
    if len(personal_records) >= 2:
        span_days = (personal_records[-1].date - personal_records[0].date).days
        pace = len(personal_records) / (span_days / 30) if span_days > 0 else len(personal_records)
        basis = f"по личному темпу: {len(personal_records)} активностей за {max(span_days, 1)} дн."
    else:
        pace = _company_average_pace(store)
        basis = "недостаточно личной истории — использован средний темп по компании"

    if pace <= 0:
        return PromotionEstimate(skills_remaining, critical_remaining, None, "темп активности слишком низкий для оценки")

    estimated_months = round(steps_needed / pace, 1)
    return PromotionEstimate(skills_remaining, critical_remaining, estimated_months, basis)
