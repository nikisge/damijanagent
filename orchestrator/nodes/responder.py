"""
Responder Node - Generiert die finale Antwort.

Der Responder:
1. Sieht alle ausgeführten Steps mit Ergebnissen
2. Sieht den Original-Plan
3. Generiert eine freundliche Antwort für Damijan
"""

import json
import logging
import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ..models.state import OrchestratorState
from ..config import (
    RESPONDER_SYSTEM_PROMPT,
    OPENROUTER_API_KEY,
    RESPONDER_MODEL,
)

logger = logging.getLogger(__name__)


def get_responder_llm():
    """Initialisiert das Responder LLM via OpenRouter."""
    return ChatOpenAI(
        model=RESPONDER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.7,  # Etwas höher für natürliche Antworten
        timeout=60,
        max_retries=1,
    )


def format_executed_steps(executed_steps: list) -> str:
    """Formatiert die ausgeführten Steps für den Prompt."""
    if not executed_steps:
        return "Keine Tools ausgeführt."

    lines = []
    for i, step in enumerate(executed_steps, 1):
        status = "✓" if step.success else "✗"
        lines.append(f"\n{i}. [{status}] {step.tool_name}")
        lines.append(f"   Input: {step.input_context[:200]}...")
        if step.success:
            output = step.output
            if isinstance(output, dict):
                output = json.dumps(output, ensure_ascii=False, indent=2)
            lines.append(f"   Output: {str(output)[:500]}")
        else:
            lines.append(f"   Fehler: {step.error_message}")

    return "\n".join(lines)


def format_todo_list(todos: list) -> str:
    """Formatiert die TODO-Liste für den Prompt."""
    if not todos:
        return "Keine Schritte geplant (direkte Antwort)."

    lines = []
    for todo in todos:
        status_emoji = {
            "pending": "⏳",
            "running": "🔄",
            "done": "✓",
            "failed": "✗"
        }.get(todo.status, "?")

        lines.append(f"- [{status_emoji}] {todo.tool}: {todo.description}")
        if todo.depends_on:
            lines.append(f"  (hängt ab von: {', '.join(todo.depends_on)})")

    return "\n".join(lines)


def format_conversation_history(history: list) -> str:
    """Formatiert die Conversation History für den Responder."""
    if not history:
        return "Keine vorherige Konversation."

    lines = []
    for msg in history[-10:]:  # Letzte 10 Nachrichten
        # Support both dict format (from DB) and message objects
        if isinstance(msg, dict):
            role = msg.get("type", "unknown")
            content = msg.get("content", str(msg))
        else:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", str(msg))

        # Kürzen wenn zu lang
        if len(content) > 200:
            content = content[:200] + "..."

        role_label = "Damijan" if role == "human" else "Assistent"
        lines.append(f"[{role_label}]: {content}")

    return "\n".join(lines)


def responder_node(state: OrchestratorState) -> dict:
    """
    Generiert die finale Antwort basierend auf ALLEN Ergebnissen.

    WICHTIG: Der Responder sieht NUR was wirklich passiert ist!
    Keine Halluzination möglich, weil alles im State dokumentiert ist.

    Returns:
        dict mit: final_response, conversation_history (updated)
    """
    llm = get_responder_llm()

    todos = state.get("todo_list", [])
    executed_steps = state.get("executed_steps", [])

    # System Prompt bauen
    system_prompt = RESPONDER_SYSTEM_PROMPT.format(
        conversation_history=format_conversation_history(
            state.get("conversation_history", [])
        ),
        executed_steps=format_executed_steps(executed_steps),
        user_message=state.get("user_message", ""),
        todo_list=format_todo_list(todos),
    )

    user_prompt = f"""
Formuliere jetzt die Antwort für Damijan.

Beachte:
- Fasse die Ergebnisse zusammen
- Sei freundlich und direkt
- Bei Fehlern: Transparent aber nicht frustrierend
- Keine internen Gedanken, nur die finale Nachricht
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    logger.info(f"[Responder] Calling {RESPONDER_MODEL}...")
    start = time.time()
    try:
        response = llm.invoke(messages)
        duration = time.time() - start
        logger.info(f"[Responder] LLM responded in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        logger.error(f"[Responder] LLM FAILED after {duration:.1f}s: {type(e).__name__}: {e}")
        return {
            "final_response": "Sorry, ich konnte gerade keine Antwort generieren. Versuch es bitte nochmal!"
        }

    return {
        "final_response": response.content
    }


def clarify_node(state: OrchestratorState) -> dict:
    """
    Node für Rückfragen an den User.

    Wird aufgerufen wenn needs_clarification = True.
    """
    return {
        "final_response": state.get(
            "clarification_question",
            "Ich bin mir nicht sicher was du meinst. Kannst du das genauer erklären?"
        )
    }
