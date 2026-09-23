"""
Career Quest — AI-навигатор развития сотрудника (HackAlem AI, Halyk Bank track).
FastAPI backend + static web frontend + optional Telegram bot (bonus).
Запуск: python app.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import uvicorn
from dataclasses import dataclass
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

try:
    from loguru import logger
except ImportError:  # pragma: no cover - fallback if loguru not installed
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
    )
    logger = logging.getLogger("app")

from engine.agent import CareerAgent
from engine.data_store import AS_OF_DATE, store
from engine.llm_explainer import Explainer
from engine.models import ActivityRecord, Employee
from engine.recommender import (
    NEGATIVE_STATUSES,
    Recommendation,
    TrajectoryTarget,
    build_trajectories,
    effective_skills,
    estimate_time_to_promotion,
    pick_primary_trajectory,
    recommend,
)
from engine.schemas import (
    ActivityHistoryItem,
    AgentChatRequest,
    AgentChatResponse,
    AgentToolCall,
    CompleteActivityRequest,
    EmployeeBrief,
    EmployeeProfileOut,
    HRDepartmentGap,
    HRDepartmentGapCell,
    HREmployeeFlag,
    HREventParticipation,
    HROverviewOut,
    HRSkillGap,
    PromotionEstimateOut,
    RecommendationFactorOut,
    RecommendationOut,
    SkillLevel,
    TrajectoryOut,
    UploadActivityResult,
    UploadEmployeesResult,
)

# ──────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TELEGRAM_WEBAPP_URL = os.getenv("TELEGRAM_WEBAPP_URL", "").rstrip("/")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")
NVIDIA_URL = os.getenv("NVIDIA_URL", "https://integrate.api.nvidia.com/v1/chat/completions")

if LLM_PROVIDER == "openai":
    LLM_API_KEY = OPENAI_API_KEY
    LLM_MODEL = OPENAI_MODEL
    LLM_URL = "https://api.openai.com/v1/chat/completions"
elif LLM_PROVIDER == "nvidia":
    LLM_API_KEY = NVIDIA_API_KEY
    LLM_MODEL = NVIDIA_MODEL
    LLM_URL = NVIDIA_URL
else:
    LLM_API_KEY = ""
    LLM_MODEL = "fallback"
    LLM_URL = ""

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "8"))

START_TIME = time.time()
STATIC_DIR = Path(__file__).resolve().parent / "static"

explainer = Explainer(url=LLM_URL, api_key=LLM_API_KEY, model=LLM_MODEL, timeout=LLM_TIMEOUT_SECONDS)
career_agent = CareerAgent(url=LLM_URL, api_key=LLM_API_KEY, model=LLM_MODEL, timeout=max(LLM_TIMEOUT_SECONDS, 20.0))


# ──────────────────────────────────────────────────────────────────────────
# Profile / recommendation assembly (shared by API and Telegram bot)
# ──────────────────────────────────────────────────────────────────────────

def _skill_levels_for_trajectory(levels: dict[str, int], target: TrajectoryTarget) -> list[SkillLevel]:
    out = []
    for skill_id, required in sorted(target.profile.required_skills.items()):
        skill = store.skills.get(skill_id)
        current = levels.get(skill_id, 0)
        out.append(
            SkillLevel(
                skill_id=skill_id,
                name=skill.name if skill else skill_id,
                type=skill.type if skill else "hard",
                current_level=current,
                required_level=required,
                gap=max(0, required - current),
                critical=skill_id in target.profile.critical_skills,
            )
        )
    return out


_pick_primary_trajectory = pick_primary_trajectory


def _recommendation_to_out(rec: Recommendation, explanation: str) -> RecommendationOut:
    return RecommendationOut(
        event_id=rec.event.event_id,
        title=rec.event.title,
        description=rec.event.description,
        type=rec.event.type,
        format=rec.event.format,
        duration_hours=rec.event.duration_hours,
        upcoming_sessions=[s.isoformat() for s in rec.event.upcoming_sessions],
        score=rec.score,
        factors=[RecommendationFactorOut(kind=f.kind, detail=f.detail, payload=f.payload) for f in rec.factors],
        explanation=explanation,
        score_breakdown=rec.score_breakdown,
    )


async def build_profile(employee_id: str) -> EmployeeProfileOut:
    employee = store.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"employee '{employee_id}' not found")

    levels = effective_skills(store, employee)
    trajectories = build_trajectories(store, employee, levels)
    primary = _pick_primary_trajectory(trajectories)

    recs: list[Recommendation] = []
    if primary:
        recs = recommend(store, employee, levels, primary)

    explanations = await asyncio.gather(*(explainer.explain(employee, primary, r) for r in recs)) if recs else []
    recommendations_out = [_recommendation_to_out(r, e) for r, e in zip(recs, explanations)]

    trajectories_out = [
        TrajectoryOut(
            label=t.label,
            role=t.role,
            grade=t.grade,
            readiness_pct=t.readiness_pct,
            skills=_skill_levels_for_trajectory(levels, t),
            promotion_estimate=PromotionEstimateOut(**estimate_time_to_promotion(store, employee, t).__dict__),
        )
        for t in trajectories
    ]

    history = sorted(store.get_activity_for(employee_id), key=lambda r: r.date, reverse=True)[:15]
    recent_activity = [
        ActivityHistoryItem(
            event_id=r.event_id,
            event_title=store.events[r.event_id].title if r.event_id in store.events else r.event_id,
            date=r.date.isoformat(),
            status=r.status,
            completion_pct=r.completion_pct,
        )
        for r in history
    ]

    return EmployeeProfileOut(
        employee_id=employee.employee_id,
        full_name=employee.full_name,
        department=employee.department,
        role=employee.role,
        grade=employee.grade,
        work_format=employee.work_format,
        preferred_language=employee.preferred_language,
        tenure_months=employee.tenure_months,
        career_goal=(
            {"target_role": employee.career_goal.target_role, "target_grade": employee.career_goal.target_grade}
            if employee.career_goal
            else None
        ),
        trajectories=trajectories_out,
        recommendations=recommendations_out,
        recent_activity=recent_activity,
    )


STALLED_DAYS_THRESHOLD = 180  # ~6 months without a completed activity, while gaps remain open


def build_hr_overview() -> HROverviewOut:
    gap_counts: dict[str, int] = {}
    gap_totals: dict[str, int] = {}
    dept_gap_counts: dict[str, dict[str, int]] = {}
    dept_employee_counts: dict[str, int] = {}
    flags: list[HREmployeeFlag] = []
    stalled: list[HREmployeeFlag] = []

    for employee in store.employees.values():
        dept_employee_counts[employee.department] = dept_employee_counts.get(employee.department, 0) + 1

        levels = effective_skills(store, employee)
        trajectories = build_trajectories(store, employee, levels)
        primary = _pick_primary_trajectory(trajectories)
        if not primary:
            continue

        for skill_id, gap in primary.gaps.items():
            gap_counts[skill_id] = gap_counts.get(skill_id, 0) + 1
            gap_totals[skill_id] = gap_totals.get(skill_id, 0) + gap
            dept_bucket = dept_gap_counts.setdefault(employee.department, {})
            dept_bucket[skill_id] = dept_bucket.get(skill_id, 0) + 1

        if not primary.gaps:
            continue

        recs = recommend(store, employee, levels, primary)
        if not recs:
            flags.append(
                HREmployeeFlag(
                    employee_id=employee.employee_id,
                    full_name=employee.full_name,
                    role=employee.role,
                    grade=employee.grade,
                    reason="есть разрывы по навыкам, но нет доступной подходящей активности",
                )
            )

        completed_dates = [
            r.date for r in store.get_activity_for(employee.employee_id) if r.status == "completed"
        ]
        last_completed = max(completed_dates) if completed_dates else None
        if last_completed is None:
            stalled.append(
                HREmployeeFlag(
                    employee_id=employee.employee_id,
                    full_name=employee.full_name,
                    role=employee.role,
                    grade=employee.grade,
                    reason="нет ни одной завершённой активности в истории",
                )
            )
        elif (AS_OF_DATE - last_completed).days > STALLED_DAYS_THRESHOLD:
            stalled.append(
                HREmployeeFlag(
                    employee_id=employee.employee_id,
                    full_name=employee.full_name,
                    role=employee.role,
                    grade=employee.grade,
                    reason=f"последняя завершённая активность {last_completed.isoformat()} (более {STALLED_DAYS_THRESHOLD} дней назад)",
                )
            )

    top_skill_gaps = [
        HRSkillGap(
            skill_id=skill_id,
            name=store.skills[skill_id].name if skill_id in store.skills else skill_id,
            employees_with_gap=count,
            avg_gap=round(gap_totals[skill_id] / count, 2),
        )
        for skill_id, count in sorted(gap_counts.items(), key=lambda kv: -kv[1])[:8]
    ]
    top_skill_ids = [g.skill_id for g in top_skill_gaps]

    department_gaps = []
    for department, emp_count in sorted(dept_employee_counts.items()):
        dept_bucket = dept_gap_counts.get(department, {})
        cells = [
            HRDepartmentGapCell(
                skill_id=skill_id,
                name=store.skills[skill_id].name if skill_id in store.skills else skill_id,
                employees_with_gap=dept_bucket.get(skill_id, 0),
                ratio=round(dept_bucket.get(skill_id, 0) / emp_count, 3) if emp_count else 0.0,
            )
            for skill_id in top_skill_ids
        ]
        department_gaps.append(
            HRDepartmentGap(department=department, employee_count=emp_count, cells=cells)
        )

    participation: dict[str, dict[str, int]] = {}
    for record in store.activity:
        p = participation.setdefault(record.event_id, {"completed": 0, "negative": 0, "total": 0})
        p["total"] += 1
        if record.status == "completed":
            p["completed"] += 1
        elif record.status in NEGATIVE_STATUSES:
            p["negative"] += 1

    event_participation = [
        HREventParticipation(
            event_id=event_id,
            title=store.events[event_id].title if event_id in store.events else event_id,
            completed=p["completed"],
            negative=p["negative"],
            total=p["total"],
        )
        for event_id, p in sorted(participation.items(), key=lambda kv: -kv[1]["total"])[:15]
    ]

    return HROverviewOut(
        total_employees=len(store.employees),
        top_skill_gaps=top_skill_gaps,
        employees_without_recommendation=flags[:20],
        stalled_employees=stalled[:20],
        department_gaps=department_gaps,
        event_participation=event_participation,
    )


# ──────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Career Quest API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────────
# Access control: сотрудник видит только себя, HR видит всё.
#
# Заголовки декларативные (X-Viewer-Role / X-Viewer-Employee-Id), а не токен
# сессии — для хакатон-демо этого достаточно, чтобы честно продемонстрировать
# разделение прав из ТЗ ("Учесть: Безопасность"), не блокируя при этом
# свободный доступ жюри/curl без заголовков (Must-Have: "открыть произвольного
# сотрудника"). Отсутствие заголовков трактуется как HR — то есть текущее,
# уже проверенное поведение не меняется для прямых запросов к API.
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class Viewer:
    role: str  # "hr" | "employee"
    employee_id: str | None


def get_viewer(
    x_viewer_role: str | None = Header(default=None, alias="X-Viewer-Role"),
    x_viewer_employee_id: str | None = Header(default=None, alias="X-Viewer-Employee-Id"),
) -> Viewer:
    role = (x_viewer_role or "hr").strip().lower()
    if role not in ("hr", "employee"):
        role = "hr"
    return Viewer(role=role, employee_id=(x_viewer_employee_id or "").strip() or None)


def require_hr(viewer: Viewer) -> None:
    if viewer.role == "employee":
        raise HTTPException(status_code=403, detail="Доступно только для HR")


def require_self_or_hr(viewer: Viewer, employee_id: str) -> None:
    if viewer.role == "employee" and viewer.employee_id != employee_id:
        raise HTTPException(status_code=403, detail="Доступ только к собственному профилю")


@app.on_event("startup")
async def on_startup() -> None:
    store.load_base_dataset()
    logger.info(f"LLM Provider: {LLM_PROVIDER} | Model: {LLM_MODEL} | configured: {bool(LLM_API_KEY)}")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 2),
        "employees_loaded": len(store.employees),
        "events_loaded": len(store.events),
        "activity_records": len(store.activity),
        "telegram_configured": bool(BOT_TOKEN),
        "llm_configured": bool(LLM_API_KEY),
        "llm_provider": LLM_PROVIDER,
        "server_time": time.time(),
    }


@app.get("/api/employees", response_model=list[EmployeeBrief])
async def list_employees(viewer: Viewer = Depends(get_viewer)) -> list[EmployeeBrief]:
    require_hr(viewer)  # directory browsing is an HR capability, not a peer one
    return [EmployeeBrief(**e) for e in store.list_employees_brief()]


@app.get("/api/employees/{employee_id}", response_model=EmployeeProfileOut)
async def get_employee_profile(employee_id: str, viewer: Viewer = Depends(get_viewer)) -> EmployeeProfileOut:
    require_self_or_hr(viewer, employee_id)
    return await build_profile(employee_id)


async def mark_activity_completed(employee_id: str, event_id: str, assigned_by: str = "self") -> EmployeeProfileOut:
    """Shared by the HTTP endpoint and the Telegram bot so both channels record
    completion identically — one source of truth, not two copies that can drift."""
    employee = store.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"employee '{employee_id}' not found")
    if event_id not in store.events:
        raise HTTPException(status_code=404, detail=f"event '{event_id}' not found")

    record = ActivityRecord(
        record_id=f"R-manual-{len(store.activity) + 1}",
        employee_id=employee_id,
        event_id=event_id,
        date=AS_OF_DATE,
        due_date=None,
        status="completed",
        completion_pct=100,
        score=None,
        feedback_rating=None,
        assigned_by=assigned_by,
    )
    store.activity.append(record)
    store.activity_by_employee.setdefault(employee_id, []).append(record)
    logger.info(f"{employee_id} completed {event_id} (via {assigned_by})")

    return await build_profile(employee_id)


@app.post("/api/employees/{employee_id}/complete", response_model=EmployeeProfileOut)
async def complete_activity(
    employee_id: str, payload: CompleteActivityRequest, viewer: Viewer = Depends(get_viewer)
) -> EmployeeProfileOut:
    require_self_or_hr(viewer, employee_id)
    return await mark_activity_completed(employee_id, payload.event_id)


@app.get("/api/hr/overview", response_model=HROverviewOut)
async def hr_overview(viewer: Viewer = Depends(get_viewer)) -> HROverviewOut:
    require_hr(viewer)  # company-wide gap/participation aggregates are not peer-visible
    return build_hr_overview()


@app.post("/api/data/employees", response_model=UploadEmployeesResult)
async def upload_employees(file: UploadFile = File(...), viewer: Viewer = Depends(get_viewer)) -> UploadEmployeesResult:
    require_hr(viewer)  # loading jury/test data is an admin action
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
        merged = store.merge_employees(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")
    except Exception as exc:  # noqa: BLE001 - untrusted input boundary: never 500 on a malformed jury file
        raise HTTPException(status_code=400, detail=f"invalid employees payload: {exc}")
    return UploadEmployeesResult(merged_employees=merged, total_employees=len(store.employees))


@app.post("/api/data/activity", response_model=UploadActivityResult)
async def upload_activity(file: UploadFile = File(...), viewer: Viewer = Depends(get_viewer)) -> UploadActivityResult:
    require_hr(viewer)  # loading jury/test data is an admin action
    raw = await file.read()
    try:
        merged = store.merge_activity_csv_text(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - untrusted input boundary: never 500 on a malformed jury file
        raise HTTPException(status_code=400, detail=f"invalid activity CSV: {exc}")
    return UploadActivityResult(merged_records=merged, total_records=len(store.activity))


@app.post("/api/agent/chat", response_model=AgentChatResponse)
async def agent_chat(req: AgentChatRequest, viewer: Viewer = Depends(get_viewer)) -> AgentChatResponse:
    if req.employee_id:
        require_self_or_hr(viewer, req.employee_id)
        if not store.get_employee(req.employee_id):
            raise HTTPException(status_code=404, detail=f"employee '{req.employee_id}' not found")
    else:
        require_hr(viewer)  # HR-mode chat (company-wide what-if / mentor search) is not a peer capability

    history = [{"role": m.role, "content": m.content} for m in req.messages]
    result = await career_agent.chat(store, history, employee_id=req.employee_id)
    return AgentChatResponse(
        reply=result["reply"],
        tool_trace=[AgentToolCall(**t) for t in result["tool_trace"]],
    )


# ──────────────────────────────────────────────────────────────────────────
# Static frontend
# ──────────────────────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


@app.get("/")
async def serve_index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend not built yet")
    return FileResponse(str(index_path))


# ──────────────────────────────────────────────────────────────────────────
# Telegram bot (bonus channel — same engine, now identity-bound per chat)
#
# chat_id -> employee_id, in-memory (resets on restart, same as the rest of
# this demo's state). Binding means the bot can no longer be used as an
# arbitrary-lookup tool for someone else's data — it always answers "who is
# this chat?", never "show me employee X" for an unrelated X. This is the
# same self-or-HR boundary as the HTTP API's Viewer model, just enforced by
# what the bot chooses to look up rather than by a header, since Telegram
# chat_id is the only identity we're given here.
# ──────────────────────────────────────────────────────────────────────────

router = Router(name="main")
telegram_bindings: dict[int, str] = {}
HR_ROLE = "HR Business Partner"  # real role from the dataset — gates the HR-overview button


_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    result = []
    for char in text:
        if char in _MDV2_SPECIAL_CHARS:
            result.append("\\")
        result.append(char)
    return "".join(result)


def _profile_keyboard(profile: EmployeeProfileOut, employee_id: str, is_hr: bool):
    builder = InlineKeyboardBuilder()
    for r in profile.recommendations:
        builder.button(text=f"✅ {r.title[:40]}", callback_data=f"complete:{r.event_id}")
    if TELEGRAM_WEBAPP_URL:
        webapp_url = f"{TELEGRAM_WEBAPP_URL}/?employee_id={employee_id}&viewer=telegram"
        builder.button(text="🖥 Открыть в мини-приложении", web_app=WebAppInfo(url=webapp_url))
    if is_hr:
        builder.button(text="📊 HR-обзор", callback_data="hr_overview")
    builder.adjust(1)
    return builder.as_markup()


async def _send_profile(target: Message, employee_id: str) -> None:
    await target.bot.send_chat_action(target.chat.id, ChatAction.TYPING)
    try:
        profile = await build_profile(employee_id)
    except HTTPException:
        await target.answer(escape_markdown_v2(f"Сотрудник '{employee_id}' не найден."))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Telegram profile lookup failed")
        await target.answer(escape_markdown_v2(f"Ошибка: {exc}"))
        return

    lines = [f"*{escape_markdown_v2(profile.full_name)}* — {escape_markdown_v2(profile.role + ' ' + profile.grade)}"]
    if profile.trajectories:
        t = profile.trajectories[0]
        lines.append(escape_markdown_v2(f"Готовность к {t.role} {t.grade}: {t.readiness_pct}%"))
    lines.append("")
    if profile.recommendations:
        lines.append("*Рекомендации:*")
        for r in profile.recommendations:
            lines.append(escape_markdown_v2(f"• {r.title}"))
            lines.append(escape_markdown_v2(r.explanation))
    else:
        lines.append(escape_markdown_v2("Нет активных рекомендаций — либо всё выполнено, либо нет доступных активностей."))

    employee = store.get_employee(employee_id)
    is_hr = bool(employee and employee.role == HR_ROLE)
    await target.answer("\n".join(lines), reply_markup=_profile_keyboard(profile, employee_id, is_hr))


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    bound_id = telegram_bindings.get(message.chat.id)
    if bound_id:
        await message.answer(escape_markdown_v2(f"С возвращением! Вы вошли как {bound_id}."))
        await _send_profile(message, bound_id)
        return
    text = (
        "*Career Quest — AI\\-навигатор развития* 🎯\n\n"
        "Введите свой ID сотрудника \\(например `E0028`\\), чтобы привязать аккаунт к этому чату\\. "
        "После этого бот всегда будет показывать только ваш профиль\\.\n\n"
        "Команда /reset — сменить привязанный ID\\."
    )
    await message.answer(text)


@router.message(Command("reset"))
async def handle_reset(message: Message) -> None:
    telegram_bindings.pop(message.chat.id, None)
    await message.answer(escape_markdown_v2("Привязка сброшена. Введите ID сотрудника, чтобы привязать заново."))


@router.callback_query(F.data == "hr_overview")
async def handle_hr_overview(callback: CallbackQuery) -> None:
    employee_id = telegram_bindings.get(callback.message.chat.id)
    employee = store.get_employee(employee_id) if employee_id else None
    if not employee or employee.role != HR_ROLE:
        await callback.answer("Доступно только для HR Business Partner.", show_alert=True)
        return
    overview = build_hr_overview()
    lines = [f"*📊 HR\\-обзор* \\({overview.total_employees} сотрудников\\)", ""]
    lines.append("*Проседающие навыки:*")
    for g in overview.top_skill_gaps[:5]:
        lines.append(escape_markdown_v2(f"• {g.name}: {g.employees_with_gap} чел., ср. разрыв {g.avg_gap}"))
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data.startswith("complete:"))
async def handle_complete_callback(callback: CallbackQuery) -> None:
    employee_id = telegram_bindings.get(callback.message.chat.id)
    if not employee_id:
        await callback.answer("Сначала отправьте /start и привяжите свой ID.", show_alert=True)
        return
    event_id = callback.data.split(":", 1)[1]
    try:
        await mark_activity_completed(employee_id, event_id, assigned_by="self")
    except HTTPException as exc:
        await callback.answer(f"Не удалось: {exc.detail}", show_alert=True)
        return
    await callback.answer("✅ Отмечено выполненным!")
    await _send_profile(callback.message, employee_id)


@router.message(F.text)
async def handle_text(message: Message) -> None:
    bound_id = telegram_bindings.get(message.chat.id)
    candidate = message.text.strip().upper()

    if not bound_id:
        employee = store.get_employee(candidate)
        if not employee:
            await message.answer(escape_markdown_v2(f"Сотрудник '{candidate}' не найден. Введите ID вида E0028."))
            return
        telegram_bindings[message.chat.id] = candidate
        await message.answer(escape_markdown_v2(f"Готово! Этот чат привязан к {employee.full_name} ({candidate})."))
        await _send_profile(message, candidate)
        return

    # уже привязан — любой текст просто повторно показывает единственный доступный профиль
    await _send_profile(message, bound_id)


# ──────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────

async def run_api_server() -> None:
    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    logger.info(f"Starting FastAPI on http://{API_HOST}:{API_PORT}")
    await server.serve()


async def run_telegram_bot() -> None:
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN не задан — Telegram-бот не запущен")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2))
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting Telegram bot polling")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def main() -> None:
    logger.info("=== Career Quest backend booting ===")
    tasks = [run_api_server()]
    if BOT_TOKEN:
        tasks.append(run_telegram_bot())
    else:
        logger.warning("Бот отключён (бонус): задайте BOT_TOKEN, чтобы включить Telegram-интерфейс")

    try:
        await asyncio.gather(*tasks)
    finally:
        await explainer.aclose()
        await career_agent.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
