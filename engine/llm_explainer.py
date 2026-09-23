"""Turns deterministic recommendation factors into a short, localized explanation.

The LLM never invents facts or scores — it only phrases the structured factors
produced by recommender.py. If the LLM is unavailable or too slow, a deterministic
template takes over so the product never breaks the 10-second budget.
"""

from __future__ import annotations

import json

import httpx
from loguru import logger

from .models import Employee
from .recommender import Recommendation, TrajectoryTarget

LANGUAGE_NAMES = {"ru": "русском", "kk": "казахском", "en": "английском"}

SYSTEM_PROMPT = (
    "Ты — HR-ассистент. На основе строго заданных фактов пиши короткое (2-3 предложения) "
    "объяснение, почему сотруднику стоит пройти конкретную активность развития. "
    "Используй ТОЛЬКО факты из списка, ничего не придумывай и не добавляй новых чисел. "
    "Пиши по-деловому, обращаясь на 'вы', без markdown-разметки."
)


def _fallback_text(recommendation: Recommendation) -> str:
    parts = [f"Рекомендуем «{recommendation.event.title}»."]
    for factor in recommendation.factors:
        parts.append(factor.detail + ".")
    return " ".join(parts)


class Explainer:
    def __init__(self, url: str, api_key: str, model: str, timeout: float = 8.0) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def explain(
        self, employee: Employee, target: TrajectoryTarget, recommendation: Recommendation
    ) -> str:
        if not self._api_key:
            return _fallback_text(recommendation)

        language = LANGUAGE_NAMES.get(employee.preferred_language, "русском")
        facts = {
            "employee_role": employee.role,
            "employee_grade": employee.grade,
            "target_role": target.role,
            "target_grade": target.grade,
            "event_title": recommendation.event.title,
            "event_format": recommendation.event.format,
            "event_duration_hours": recommendation.event.duration_hours,
            "factors": [f.detail for f in recommendation.factors],
        }
        user_prompt = (
            f"Напиши объяснение на {language} языке для следующих фактов:\n"
            f"{json.dumps(facts, ensure_ascii=False)}"
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 220,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            return text or _fallback_text(recommendation)
        except httpx.TimeoutException:
            logger.warning("LLM explanation timed out, using fallback template")
            return _fallback_text(recommendation)
        except Exception as exc:  # noqa: BLE001 - never break the recommendation response
            logger.warning(f"LLM explanation failed ({exc}), using fallback template")
            return _fallback_text(recommendation)
