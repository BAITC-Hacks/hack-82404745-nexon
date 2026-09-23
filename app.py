"""
Career Quest — AI-навигатор развития сотрудника (HackAlem AI, Halyk Bank track).
FastAPI backend + static web frontend + optional Telegram bot (bonus).
Запуск: python app.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
from datetime import date
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message
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
    recommend,
)
from engine.schemas import (
    ActivityHistoryItem,
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


def _pick_primary_trajectory(trajectories: list[TrajectoryTarget]) -> TrajectoryTarget | None:
    for t in trajectories:
        if t.label == "career_goal":
            return t
    for t in trajectories:
        if t.label == "next_grade":
            return t
    return None


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
async def list_employees() -> list[EmployeeBrief]:
    return [EmployeeBrief(**e) for e in store.list_employees_brief()]


@app.get("/api/employees/{employee_id}", response_model=EmployeeProfileOut)
async def get_employee_profile(employee_id: str) -> EmployeeProfileOut:
    return await build_profile(employee_id)


@app.post("/api/employees/{employee_id}/complete", response_model=EmployeeProfileOut)
async def complete_activity(employee_id: str, payload: CompleteActivityRequest) -> EmployeeProfileOut:
    employee = store.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"employee '{employee_id}' not found")
    if payload.event_id not in store.events:
        raise HTTPException(status_code=404, detail=f"event '{payload.event_id}' not found")

    record = ActivityRecord(
        record_id=f"R-manual-{len(store.activity) + 1}",
        employee_id=employee_id,
        event_id=payload.event_id,
        date=AS_OF_DATE,
        due_date=None,
        status="completed",
        completion_pct=100,
        score=None,
        feedback_rating=None,
        assigned_by="self",
    )
    store.activity.append(record)
    store.activity_by_employee.setdefault(employee_id, []).append(record)
    logger.info(f"{employee_id} completed {payload.event_id}")

    return await build_profile(employee_id)


@app.get("/api/hr/overview", response_model=HROverviewOut)
async def hr_overview() -> HROverviewOut:
    return build_hr_overview()


@app.post("/api/data/employees", response_model=UploadEmployeesResult)
async def upload_employees(file: UploadFile = File(...)) -> UploadEmployeesResult:
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
        merged = store.merge_employees(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid employees payload: {exc}")
    return UploadEmployeesResult(merged_employees=merged, total_employees=len(store.employees))


@app.post("/api/data/activity", response_model=UploadActivityResult)
async def upload_activity(file: UploadFile = File(...)) -> UploadActivityResult:
    raw = await file.read()
    try:
        merged = store.merge_activity_csv_text(raw.decode("utf-8"))
    except (csv.Error, ValidationError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid activity CSV: {exc}")
    return UploadActivityResult(merged_records=merged, total_records=len(store.activity))


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
# Telegram bot (bonus channel — same engine, quick lookup by employee_id)
# ──────────────────────────────────────────────────────────────────────────

router = Router(name="main")


def build_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 HR-обзор", callback_data="hr_overview")
    builder.adjust(1)
    return builder.as_markup()


_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    result = []
    for char in text:
        if char in _MDV2_SPECIAL_CHARS:
            result.append("\\")
        result.append(char)
    return "".join(result)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    text = (
        "*Career Quest — AI\\-навигатор развития* 🎯\n\n"
        "Отправьте ID сотрудника \\(например `E0028`\\), и я покажу траекторию "
        "и рекомендованный следующий шаг\\."
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.callback_query(F.data == "hr_overview")
async def handle_hr_overview(callback) -> None:
    overview = build_hr_overview()
    lines = [f"*📊 HR\\-обзор* \\({overview.total_employees} сотрудников\\)", ""]
    lines.append("*Проседающие навыки:*")
    for g in overview.top_skill_gaps[:5]:
        lines.append(escape_markdown_v2(f"• {g.name}: {g.employees_with_gap} чел., ср. разрыв {g.avg_gap}"))
    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.message(F.text)
async def handle_text(message: Message) -> None:
    employee_id = message.text.strip().upper()
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        profile = await build_profile(employee_id)
    except HTTPException:
        await message.answer(escape_markdown_v2(f"Сотрудник '{employee_id}' не найден. Введите ID вида E0028."))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Telegram profile lookup failed")
        await message.answer(escape_markdown_v2(f"Ошибка: {exc}"))
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
    await message.answer("\n".join(lines))


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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
