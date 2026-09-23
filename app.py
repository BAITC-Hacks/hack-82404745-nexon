"""
HackAlemAI 2026 — Backend-centric AI Agent MVP.
FastAPI (REST) + aiogram 3.x (Telegram UI) на одном event loop.
Запуск: python app.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
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
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

START_TIME = time.time()


# ──────────────────────────────────────────────────────────────────────────
# Pydantic models — Structured Outputs / Tool Calling contract
# ──────────────────────────────────────────────────────────────────────────

class ActionTool(BaseModel):
    action_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    reply_text: str
    actions: list[ActionTool] = Field(default_factory=list)


class AgentRunRequest(BaseModel):
    user_input: str
    context: dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None


class AgentRunResult(BaseModel):
    session_id: str
    reply_text: str
    actions: list[ActionTool]
    tool_results: dict[str, str]
    elapsed_ms: int


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    active_sessions: int
    telegram_configured: bool
    llm_configured: bool
    llm_provider: str
    server_time: float


# ──────────────────────────────────────────────────────────────────────────
# In-memory session state
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class SessionContext:
    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def remember(self, role: str, content: str, max_turns: int = 20) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > max_turns:
            self.history = self.history[-max_turns:]
        self.updated_at = time.time()


class SessionStore:
    """Простое потокобезопасное (в рамках одного event loop) in-memory хранилище сессий."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> SessionContext:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = SessionContext(session_id=session_id)
                self._sessions[session_id] = session
            return session

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self._sessions)


session_store = SessionStore()


# ──────────────────────────────────────────────────────────────────────────
# Tool Execution Router
# ──────────────────────────────────────────────────────────────────────────

ToolHandler = Callable[[dict[str, Any], SessionContext], Awaitable[str]]


class ToolRouter:
    """Реестр инструментов, которые LLM может вызывать через AgentResponse.actions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolHandler] = {}

    def register(self, name: str) -> Callable[[ToolHandler], ToolHandler]:
        def decorator(handler: ToolHandler) -> ToolHandler:
            self._tools[name] = handler
            return handler

        return decorator

    def is_registered(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, action: ActionTool, session: SessionContext) -> str:
        handler = self._tools.get(action.action_name)
        if handler is None:
            logger.warning(f"Unknown tool requested: {action.action_name}")
            return f"error: unknown tool '{action.action_name}'"
        try:
            return await handler(action.parameters, session)
        except Exception as exc:  # noqa: BLE001 - hackathon MVP: никогда не роняем цепочку
            logger.exception(f"Tool '{action.action_name}' failed")
            return f"error: {exc}"

    async def execute_all(
        self, actions: list[ActionTool], session: SessionContext
    ) -> dict[str, str]:
        results: dict[str, str] = {}
        for action in actions:
            results[action.action_name] = await self.execute(action, session)
        return results


tool_router = ToolRouter()


@tool_router.register("noop")
async def _tool_noop(parameters: dict[str, Any], session: SessionContext) -> str:
    return "ok"


@tool_router.register("echo")
async def _tool_echo(parameters: dict[str, Any], session: SessionContext) -> str:
    return str(parameters.get("text", ""))


@tool_router.register("remember_fact")
async def _tool_remember_fact(parameters: dict[str, Any], session: SessionContext) -> str:
    key = str(parameters.get("key", "fact"))
    value = parameters.get("value", "")
    session.data[key] = value
    return f"saved '{key}'"


@tool_router.register("get_session_state")
async def _tool_get_session_state(parameters: dict[str, Any], session: SessionContext) -> str:
    return json.dumps(session.data, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────
# LLM Engine Client
# ──────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — бэкенд AI-агент хакатон-проекта. Отвечай ТОЛЬКО валидным JSON, \
строго соответствующим следующей схеме, без markdown-разметки и пояснений вокруг:
{
  "reply_text": "<человекочитаемый ответ пользователю на русском>",
  "actions": [
    {"action_name": "<имя инструмента>", "parameters": {"<ключ>": "<значение>"}}
  ]
}
Если инструмент вызывать не нужно — верни actions: []. Никогда не оборачивай JSON в ```."""


class LLMClient:
    def __init__(self, url: str, api_key: str, model: str, timeout: float) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run_agent(
        self, user_input: str, context: dict[str, Any], history: list[dict[str, str]]
    ) -> AgentResponse:
        if not self._api_key:
            logger.warning("LLM_API_KEY не задан — возвращаю заглушку-эхо")
            return AgentResponse(
                reply_text=f"[LLM не настроен] Вы написали: {user_input}",
                actions=[],
            )

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context:
            messages.append(
                {"role": "system", "content": f"Дополнительный контекст: {json.dumps(context, ensure_ascii=False)}"}
            )
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        payload = {
            "model": self._model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.post(self._url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.TimeoutException:
            logger.error("LLM request timed out")
            return AgentResponse(reply_text="Сервис ИИ не ответил вовремя, попробуйте ещё раз.", actions=[])
        except httpx.HTTPStatusError as exc:
            logger.error(f"LLM HTTP error {exc.response.status_code}: {exc.response.text[:500]}")
            return AgentResponse(reply_text="Ошибка обращения к ИИ-сервису. Попробуйте позже.", actions=[])
        except httpx.HTTPError as exc:
            logger.error(f"LLM network error: {exc}")
            return AgentResponse(reply_text="Нет связи с ИИ-сервисом. Попробуйте позже.", actions=[])

        try:
            raw = response.json()
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return AgentResponse.model_validate(parsed)
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError) as exc:
            logger.error(f"Failed to parse LLM structured output: {exc}")
            return AgentResponse(
                reply_text="Не удалось разобрать ответ ИИ. Переформулируйте запрос.",
                actions=[],
            )


llm_client = LLMClient(url=LLM_URL, api_key=LLM_API_KEY, model=LLM_MODEL, timeout=LLM_TIMEOUT_SECONDS)


# ──────────────────────────────────────────────────────────────────────────
# Shared agent pipeline (используется и FastAPI, и Telegram-хендлерами)
# ──────────────────────────────────────────────────────────────────────────

async def process_agent_turn(session_id: str, user_input: str, context: dict[str, Any]) -> AgentRunResult:
    started = time.perf_counter()
    session = await session_store.get_or_create(session_id)

    agent_response = await llm_client.run_agent(user_input, context, session.history)
    tool_results = await tool_router.execute_all(agent_response.actions, session)

    session.remember("user", user_input)
    session.remember("assistant", agent_response.reply_text)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return AgentRunResult(
        session_id=session_id,
        reply_text=agent_response.reply_text,
        actions=agent_response.actions,
        tool_results=tool_results,
        elapsed_ms=elapsed_ms,
    )


# ──────────────────────────────────────────────────────────────────────────
# Markdown / formatting helpers (Telegram)
# ──────────────────────────────────────────────────────────────────────────

_MDV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"


def escape_markdown_v2(text: str) -> str:
    result = []
    for char in text:
        if char in _MDV2_SPECIAL_CHARS:
            result.append("\\")
        result.append(char)
    return "".join(result)


def format_agent_reply(reply_text: str, tool_results: dict[str, str]) -> str:
    parts = [escape_markdown_v2(reply_text)]
    if tool_results:
        parts.append("")
        parts.append(escape_markdown_v2("— действия —"))
        for name, result in tool_results.items():
            parts.append(escape_markdown_v2(f"• {name}: {result}"))
    return "\n".join(parts)


def format_error(message: str) -> str:
    return f"⚠️ {escape_markdown_v2(message)}"


# ──────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────

app = FastAPI(title="HackAlemAI Agent Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse(status_code=500, content={"error": "internal_server_error", "detail": str(exc)})


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        uptime_seconds=round(time.time() - START_TIME, 2),
        active_sessions=session_store.count(),
        telegram_configured=bool(BOT_TOKEN),
        llm_configured=bool(LLM_API_KEY),
        llm_provider=LLM_PROVIDER,
        server_time=time.time(),
    )


@app.post("/api/agent/run", response_model=AgentRunResult)
async def agent_run(payload: AgentRunRequest) -> AgentRunResult:
    if not payload.user_input.strip():
        raise HTTPException(status_code=400, detail="user_input must not be empty")
    session_id = payload.session_id or f"api-{uuid.uuid4().hex[:12]}"
    return await process_agent_turn(session_id, payload.user_input, payload.context)


# ──────────────────────────────────────────────────────────────────────────
# Telegram bot (aiogram 3.x)
# ──────────────────────────────────────────────────────────────────────────

router = Router(name="main")

CB_DASHBOARD = "dashboard"
CB_NEW_TASK = "new_task"
CB_SETTINGS = "settings"


def build_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Состояние / Дашборд", callback_data=CB_DASHBOARD)
    builder.button(text="➕ Новая задача", callback_data=CB_NEW_TASK)
    builder.button(text="⚙️ Настройки / Контекст", callback_data=CB_SETTINGS)
    builder.adjust(1)
    return builder.as_markup()


def telegram_session_id(user_id: int) -> str:
    return f"tg-{user_id}"


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await session_store.get_or_create(telegram_session_id(message.from_user.id))
    text = (
        "*Добро пожаловать\\!* 🤖\n\n"
        "Я AI\\-агент этого проекта\\. Пишите мне что угодно текстом — я передам это "
        "агенту и выполню нужные действия\\.\n\n"
        "Выберите действие или просто напишите сообщение:"
    )
    await message.answer(text, reply_markup=build_main_keyboard())


@router.callback_query(F.data == CB_DASHBOARD)
async def handle_dashboard(callback: CallbackQuery) -> None:
    session = await session_store.get_or_create(telegram_session_id(callback.from_user.id))
    uptime = round(time.time() - START_TIME, 1)
    text = (
        f"*📊 Состояние системы*\n\n"
        f"Аптайм backend: `{uptime}s`\n"
        f"Активных сессий: `{session_store.count()}`\n"
        f"Сообщений в вашей истории: `{len(session.history)}`\n"
        f"LLM настроен: `{bool(LLM_API_KEY)}`"
    )
    await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == CB_NEW_TASK)
async def handle_new_task(callback: CallbackQuery) -> None:
    session_id = telegram_session_id(callback.from_user.id)
    await session_store.clear(session_id)
    await session_store.get_or_create(session_id)
    await callback.message.answer("🆕 Контекст очищен\\. Опишите новую задачу одним сообщением\\.")
    await callback.answer("Готово к новой задаче")


@router.callback_query(F.data == CB_SETTINGS)
async def handle_settings(callback: CallbackQuery) -> None:
    session = await session_store.get_or_create(telegram_session_id(callback.from_user.id))
    data_preview = escape_markdown_v2(json.dumps(session.data, ensure_ascii=False) or "{}")
    await callback.message.answer(f"⚙️ *Текущий контекст сессии:*\n`{data_preview}`")
    await callback.answer()


@router.message(F.text)
async def handle_text(message: Message) -> None:
    user_input = message.text.strip()
    if not user_input:
        return

    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    session_id = telegram_session_id(message.from_user.id)

    try:
        result = await process_agent_turn(session_id, user_input, context={"source": "telegram"})
        reply = format_agent_reply(result.reply_text, result.tool_results)
        await message.answer(reply)
    except Exception as exc:  # noqa: BLE001 - никогда не молчим и не роняем polling
        logger.exception("Failed to process telegram message")
        await message.answer(format_error(f"Не удалось обработать сообщение: {exc}"))


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
    logger.info("=== HackAlemAI Agent Backend booting ===")
    logger.info(f"LLM Provider: {LLM_PROVIDER} | Model: {LLM_MODEL}")
    tasks = [run_api_server()]
    if BOT_TOKEN:
        tasks.append(run_telegram_bot())
    else:
        logger.warning("Бот отключён: задайте BOT_TOKEN, чтобы включить Telegram-интерфейс")

    try:
        await asyncio.gather(*tasks)
    finally:
        await llm_client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
