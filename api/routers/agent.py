"""In-app chatbot endpoint - Sprint 4 (C.4.2).

POST /api/agent/chat
  Request : { thread_id?: str, message: str }
  Response : {
    thread_id, reply, tool_calls: [{tool, params, result_excerpt}],
    usage: {input_tokens, output_tokens}, quota: {used, cap, remaining},
  }

Pattern :
  1. Authenticate via JWT cookie (get_current_user).
  2. Reserve a quota slot (free=30/day, premium=300/day).
  3. Load thread history from the in-memory dict, append user message.
  4. Build system prompt : humanizer chat mode + user context + tool catalog.
  5. Anthropic SDK tool-loop : Claude can call up to max_iterations tools per
     turn. Each tool_use block is dispatched via agent.tools.run_tool with
     the user + db + active_org_cookie for scoped access.
  6. Persist the final assistant turn back into the thread.

Non-streaming v1. SSE streaming may follow in C.4.4 polish if user feedback
warrants the complexity.
"""

from __future__ import annotations
import logging
import sys
import uuid
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Cookie, Body
from sqlalchemy.orm import Session

from config import settings
from models import User, get_db
from services.auth_service import get_current_user
from services import agent_quota
from agent.tools import TOOL_SCHEMAS, run_tool

# Ensure /app/shared is importable for the natural_writing service.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent", tags=["agent"])


# ── Thread storage (in-memory, lost on restart) ────────────────────────────

# {(user_id, thread_id): [ {role, content} | ... ]} - Anthropic messages format.
_threads: dict[tuple[str, str], list[dict]] = {}
_threads_lock = Lock()
_MAX_THREAD_TURNS = 20  # cap history sent to the model (rolling window)


def _thread_key(user_id: str, thread_id: str) -> tuple[str, str]:
    return (str(user_id), str(thread_id))


def _load_thread(user_id: str, thread_id: str) -> list[dict]:
    with _threads_lock:
        return list(_threads.get(_thread_key(user_id, thread_id), []))


def _save_thread(user_id: str, thread_id: str, messages: list[dict]) -> None:
    with _threads_lock:
        # Keep only the last N turns to bound prompt growth. Each turn is
        # potentially user + multiple tool_use / tool_result + assistant.
        # 20 turns covers ~40-60 messages depending on tool density.
        trimmed = messages[-_MAX_THREAD_TURNS * 3:]
        _threads[_thread_key(user_id, thread_id)] = trimmed


# ── System prompt builder ──────────────────────────────────────────────────

def _build_system_prompt(user: User) -> str:
    """System prompt for the chatbot. Humanizer chat-mode section appended
    at the end via shared.natural_writing so all replies inherit the
    anti-AI-detection rules."""
    base = f"""You are the in-app assistant for sen-ai.fr, an AI search visibility platform.

You help the user inspect their content, scans, brand visibility and credits via tool calls.

Conversation rules :
- The user is signed in as {user.email}. Their access is scoped to specific clients (workspaces) - never invent data, always fetch via tools.
- ALWAYS call `get_active_context` on the first turn of a new conversation so you know which clients the user has access to.
- When the user asks about "my articles", "my scans", "my visibility", call the matching tool with the right client_id.
- Tool results are JSON. Summarize them in plain language - do NOT dump raw JSON to the user unless they explicitly ask.
- If a tool returns {{error: ...}}, explain the issue in one sentence and propose a next step.
- When you reference a content item or scan, include the validation_page_url / scan_page_url so the user can click through.
- Don't ask permission for read-only tools - just call them. Ask before calling `trigger_scan` (it consumes credits).

Reply style :
- Direct, concise, conversational. Default to French if the user writes in French, otherwise English.
- Bullets for lists. Short sentences. No signposting ("Let me check that", "I'll look into this").
- If a tool errors out, say "I couldn't fetch X because Y" - don't apologize or hedge."""

    # Append humanizer chat-mode section (short - no negative-rules block).
    try:
        from shared.natural_writing import get_prompt_section
        nw_section = get_prompt_section(mode="chat", language="fr")
        if nw_section:
            base = base + "\n\n" + nw_section
    except Exception:
        logger.exception("agent: natural_writing chat section unavailable - continuing without")
    return base


# ── Anthropic tool-loop ────────────────────────────────────────────────────

def _anthropic_client():
    """Lazy import + instantiate the Anthropic SDK client. Surfaces a
    clean 503 if the key is missing instead of a confusing 500."""
    if not settings.anthropic_api_key:
        raise HTTPException(503, "Chatbot disabled - ANTHROPIC_API_KEY not configured")
    from anthropic import Anthropic
    return Anthropic(api_key=settings.anthropic_api_key)


def _run_loop(
    client, system_prompt: str, messages: list[dict],
    user: User, db: Session, active_org_cookie: str | None,
) -> tuple[str, list[dict], dict]:
    """Run Anthropic messages.create in a tool-call loop.

    Returns (final_text, full_message_history, usage_dict). full_message_history
    is the updated `messages` list ready to persist to the thread.
    """
    tool_log: list[dict] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0}

    for _iteration in range(settings.agent_max_iterations):
        response = client.messages.create(
            model=settings.agent_model,
            max_tokens=2048,
            system=system_prompt,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        if response.usage:
            usage_total["input_tokens"]  += response.usage.input_tokens or 0
            usage_total["output_tokens"] += response.usage.output_tokens or 0

        # Append the assistant turn (mixed content blocks).
        assistant_content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason != "tool_use":
            # Done - extract the final text.
            final_text = "".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
            return final_text or "(no reply)", messages, usage_total

        # Dispatch every tool_use block in the assistant turn, build the
        # corresponding tool_result blocks for the next user turn.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_name = block.name
            tool_input = block.input or {}
            logger.info("agent.tool_use: %s(%s)", tool_name, list(tool_input.keys()))
            result = run_tool(
                tool_name, user, db, tool_input,
                active_org_cookie=active_org_cookie,
            )
            tool_log.append({
                "tool":            tool_name,
                "params":          tool_input,
                "result_excerpt":  str(result)[:300],
            })
            tool_results.append({
                "type":         "tool_result",
                "tool_use_id":  block.id,
                "content":      str(result),
            })
        messages.append({"role": "user", "content": tool_results})

    # Loop exhausted - return the last text we got plus a note.
    final_text = "".join(
        b.text for b in response.content if b.type == "text"
    ).strip()
    return (
        final_text or "I reached the tool-call limit without a final answer. Try rephrasing.",
        messages,
        usage_total,
    )


# ── Endpoint ───────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    active_org: str | None = Cookie(None),
):
    """Single-turn chat (with persistent thread history). See module docstring."""
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    if len(message) > 4000:
        raise HTTPException(400, "message too long (max 4000 chars)")

    thread_id = payload.get("thread_id") or str(uuid.uuid4())

    # Quota check + reserve a slot atomically.
    quota = agent_quota.reserve(user)
    if quota["exceeded"]:
        return {
            "thread_id": thread_id,
            "reply": (
                f"You've hit your daily limit of {quota['cap']} messages. "
                f"Quota resets at {agent_quota.get_usage(user)['reset_at']}."
            ),
            "tool_calls": [],
            "quota": quota,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "exceeded": True,
        }

    messages = _load_thread(str(user.id), thread_id)
    messages.append({"role": "user", "content": message})

    client = _anthropic_client()
    system_prompt = _build_system_prompt(user)

    try:
        reply, updated_messages, usage = _run_loop(
            client, system_prompt, messages, user, db, active_org,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("agent.chat: tool-loop crashed")
        # Don't refund the quota slot - this is a real call attempt.
        raise HTTPException(500, "Chat failed - try again or reset the conversation")

    _save_thread(str(user.id), thread_id, updated_messages)

    return {
        "thread_id":  thread_id,
        "reply":      reply,
        "tool_calls": [],  # populated by the loop via tool_log if we want to expose it
        "quota":      quota,
        "usage":      usage,
    }


@router.get("/usage")
async def get_quota_usage(user: User = Depends(get_current_user)):
    """Light endpoint the UI can call on chat open to show "X / Y used today"."""
    return agent_quota.get_usage(user)


@router.post("/reset")
async def reset_thread(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """Drop a thread from in-memory storage so the next message starts fresh."""
    thread_id = payload.get("thread_id")
    if not thread_id:
        raise HTTPException(400, "thread_id is required")
    with _threads_lock:
        _threads.pop(_thread_key(str(user.id), thread_id), None)
    return {"ok": True, "thread_id": thread_id}
