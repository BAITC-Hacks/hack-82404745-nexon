"""Conversational layer on top of the deterministic engine.

Architectural rule (same as llm_explainer.py, just extended to a dialogue):
the LLM never states a fact about a specific person or number on its own —
it may only repeat what's in the injected employee context, or what a tool
call (agent_tools.py) returned. Tool functions are plain Python running
against the real dataset; the model just decides *when* to call them and
*how to phrase* the result. This keeps the agent auditable even though the
conversation itself is free-form.
"""

from __future__ import annotations

import json

import httpx
from loguru import logger

from .agent_tools import TOOL_SCHEMAS, execute_tool
from .data_store import DataStore
from .models import Employee
from .recommender import (
    Recommendation,
    TrajectoryTarget,
    build_trajectories,
    effective_skills,
    estimate_time_to_promotion,
    pick_primary_trajectory,
    recommend,
)

MAX_TOOL_ITERATIONS = 3

SYSTEM_PROMPT_BASE = (
    "Ты — карьерный AI-ассистент Career Quest. Отвечай кратко (2-5 предложений), по-деловому, на 'вы'.\n"
    "ГЛАВНОЕ ПРАВИЛО: ты не имеешь права называть конкретные имена людей, цифры разрывов, названия "
    "курсов или проценты, если они не даны тебе в контексте ниже или не получены через вызов инструмента "
    "(find_mentor, simulate_department_impact) в этом диалоге. Если для ответа нужны факты, которых у тебя "
    "нет — вызови подходящий инструмент вместо того, чтобы предполагать. Если инструмент не нашёл данных — "
    "честно скажи об этом, не выдумывай замену.\n"
    "Не используй markdown-разметку."
)


def _catalog_block(store: DataStore) -> str:
    """Ground truth ID→name maps, so the model never has to guess an ID's spelling
    (e.g. 'Application Security' is SK_APP_SECURITY, not the guessable SK_APPLICATION_SECURITY).
    When calling a tool, always use IDs from this catalog, never invented ones."""
    skills = "; ".join(f"{s.skill_id}={s.name}" for s in store.skills.values())
    events = "; ".join(f"{e.event_id}={e.title}" for e in store.events.values())
    return f"Каталог навыков (id=название): {skills}.\nКаталог активностей (id=название): {events}."


def _employee_context_block(
    store: DataStore,
    employee: Employee,
    levels: dict[str, int],
    primary: TrajectoryTarget | None,
    recs: list[Recommendation],
) -> str:
    if not primary:
        return f"Контекст: сотрудник {employee.full_name} ({employee.role}, {employee.grade}) — активных карьерных траекторий с разрывами нет."

    gaps_desc = "; ".join(
        f"{sid} {store.skills[sid].name if sid in store.skills else sid}: {levels.get(sid, 0)}/{primary.profile.required_skills[sid]}"
        f"{' (критично)' if sid in primary.critical_gaps else ''}"
        for sid in primary.gaps
    )
    estimate = estimate_time_to_promotion(store, employee, primary)
    recs_desc = "; ".join(f"{r.event.event_id} {r.event.title} (score {r.score})" for r in recs) or "нет доступных рекомендаций"

    return (
        f"Контекст: сотрудник {employee.full_name} ({employee.employee_id}), {employee.department}, "
        f"{employee.role} {employee.grade}, цель — {primary.role} {primary.grade}. "
        f"Разрывы по навыкам (id, название, текущий/требуемый уровень): {gaps_desc or 'нет'}. "
        f"Оценка времени до цели: {estimate.estimated_months} мес. ({estimate.basis}). "
        f"Текущие топ-рекомендации системы (id, название, score): {recs_desc}."
    )


def _hr_context_block(store: DataStore) -> str:
    departments = sorted({e.department for e in store.employees.values()})
    return (
        f"Контекст: ты помогаешь HR. В компании {len(store.employees)} сотрудников, отделы: "
        f"{', '.join(departments)}. У тебя есть доступ к инструменту simulate_department_impact для "
        f"оценки эффекта массового назначения активности, и find_mentor для поиска экспертов по навыку."
    )


class CareerAgent:
    def __init__(self, url: str, api_key: str, model: str, timeout: float = 20.0) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def _call_llm(self, messages: list[dict]) -> dict:
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 400,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        response = await self._client.post(self._url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        store: DataStore,
        history: list[dict],
        employee_id: str | None = None,
    ) -> dict:
        """history: [{role: 'user'|'assistant', content: str}, ...] — no system message included."""
        if not self.available:
            return {
                "reply": "AI-ассистент недоступен (не настроен ключ LLM). Основные рекомендации на карточке профиля продолжают работать без него.",
                "tool_trace": [],
            }

        employee: Employee | None = None
        if employee_id:
            employee = store.get_employee(employee_id)

        if employee:
            levels = effective_skills(store, employee)
            trajectories = build_trajectories(store, employee, levels)
            primary = pick_primary_trajectory(trajectories)
            recs = recommend(store, employee, levels, primary) if primary else []
            context = _employee_context_block(store, employee, levels, primary, recs)
        else:
            context = _hr_context_block(store)

        system_content = SYSTEM_PROMPT_BASE + "\n\n" + context + "\n\n" + _catalog_block(store)
        messages = [{"role": "system", "content": system_content}] + history
        tool_trace: list[dict] = []

        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                data = await self._call_llm(messages)
                message = data["choices"][0]["message"]
                tool_calls = message.get("tool_calls")

                if not tool_calls:
                    reply = (message.get("content") or "").strip()
                    return {"reply": reply or "Не удалось сформировать ответ.", "tool_trace": tool_trace}

                messages.append(message)
                for call in tool_calls:
                    name = call["function"]["name"]
                    try:
                        arguments = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = execute_tool(store, name, arguments, employee_id=employee_id)
                    tool_trace.append({"tool": name, "arguments": arguments, "result": result})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )

            return {
                "reply": "Не удалось получить окончательный ответ за отведённое число шагов — попробуйте переформулировать вопрос.",
                "tool_trace": tool_trace,
            }
        except httpx.TimeoutException:
            logger.warning("Agent chat timed out")
            return {"reply": "AI-ассистент не ответил вовремя, попробуйте ещё раз.", "tool_trace": tool_trace}
        except Exception as exc:  # noqa: BLE001 - never crash the chat endpoint
            logger.warning(f"Agent chat failed ({exc})")
            return {"reply": "Произошла ошибка при обращении к AI-ассистенту.", "tool_trace": tool_trace}
