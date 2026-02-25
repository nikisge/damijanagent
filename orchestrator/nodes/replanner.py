"""
Replanner Node - Passt den Plan bei Fehlern an.

Der Replanner wird aufgerufen wenn:
- Ein Tool fehlgeschlagen ist
- Dependencies nicht erfüllt werden können
- Der Plan angepasst werden muss
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ..models.state import OrchestratorState, TodoItem
from ..config import OPENROUTER_API_KEY, PLANNER_MODEL

logger = logging.getLogger(__name__)


def get_replanner_llm():
    """Initialisiert das Replanner LLM."""
    return ChatOpenAI(
        model=PLANNER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        timeout=120,
        max_retries=1,
    )


REPLANNER_PROMPT = """
Du bist ein Replanner. Ein vorheriger Plan ist teilweise fehlgeschlagen.
Deine Aufgabe: Passe den Plan an oder gib auf wenn es nicht möglich ist.

# Original-Anfrage
{user_message}

# Bisheriger Plan
{original_todos}

# Ausgeführte Schritte (mit Ergebnissen)
{executed_steps}

# Fehlgeschlagene Schritte
{failed_steps}

# Deine Optionen

1. **Plan anpassen**: Wenn der Fehler umgangen werden kann
   - Beispiel: Email-Agent fehlgeschlagen → Versuche Discord stattdessen

2. **Teilweise erfolgreich**: Wenn manche Steps funktioniert haben
   - Markiere als "give_up: false" und entferne fehlgeschlagene Steps

3. **Aufgeben**: Wenn die Aufgabe nicht erfüllt werden kann
   - Setze "give_up: true" und erkläre warum

# Output Format
```json
{{
  "give_up": false,
  "reason": "Warum dieses Vorgehen",
  "new_todos": [
    {{
      "id": "step_1",
      "tool": "Tool-Name",
      "description": "Was soll passieren",
      "depends_on": [],
      "status": "pending"
    }}
  ]
}}
```
"""


def format_todos_for_prompt(todos: list) -> str:
    """Formatiert TODOs für den Prompt."""
    lines = []
    for todo in todos:
        status = todo.status
        lines.append(f"- [{status}] {todo.id}: {todo.tool} - {todo.description}")
        if todo.error:
            lines.append(f"  Error: {todo.error}")
    return "\n".join(lines)


def format_steps_for_prompt(steps: list) -> str:
    """Formatiert ausgeführte Steps für den Prompt."""
    if not steps:
        return "Keine Steps ausgeführt."

    lines = []
    for step in steps:
        status = "✓" if step.success else "✗"
        lines.append(f"- [{status}] {step.tool_name}: {step.output or step.error_message}")
    return "\n".join(lines)


async def replanner_node(state: OrchestratorState) -> dict:
    """
    Passt den Plan basierend auf Fehlern an.

    Returns:
        dict mit: todo_list (updated), retry_count
    """
    llm = get_replanner_llm()

    todos = state.get("todo_list", [])
    executed_steps = state.get("executed_steps", [])
    retry_count = state.get("retry_count", 0)

    # Finde fehlgeschlagene Steps
    failed_todos = [t for t in todos if t.status == "failed"]

    # Prompt bauen
    prompt = REPLANNER_PROMPT.format(
        user_message=state.get("user_message", ""),
        original_todos=format_todos_for_prompt(todos),
        executed_steps=format_steps_for_prompt(executed_steps),
        failed_steps=format_todos_for_prompt(failed_todos),
    )

    messages = [
        SystemMessage(content="Du bist ein hilfreicher Replanner."),
        HumanMessage(content=prompt),
    ]

    logger.info(f"[Replanner] Calling {PLANNER_MODEL} (async, timeout=90s)...")
    start = time.time()
    try:
        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=90,
        )
        duration = time.time() - start
        logger.info(f"[Replanner] LLM responded in {duration:.1f}s")
    except asyncio.TimeoutError:
        duration = time.time() - start
        logger.error(f"[Replanner] LLM TIMEOUT after {duration:.1f}s (limit: 90s)")
        return {
            "todo_list": todos,
            "retry_count": retry_count + 1,
            "error": "Replanning LLM-Timeout nach 90s",
        }
    except Exception as e:
        duration = time.time() - start
        logger.error(f"[Replanner] LLM FAILED after {duration:.1f}s: {type(e).__name__}: {e}")
        return {
            "todo_list": todos,
            "retry_count": retry_count + 1,
            "error": f"Replanning LLM-Fehler: {type(e).__name__}",
        }

    response_text = response.content

    # JSON parsen
    json_text = response_text
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0]
    elif "```" in json_text:
        json_text = json_text.split("```")[1].split("```")[0]

    try:
        replan_data = json.loads(json_text.strip())
    except json.JSONDecodeError:
        # Fallback: Gib auf
        return {
            "todo_list": todos,  # Behalte alten Plan
            "retry_count": retry_count + 1,
            "error": "Replanning fehlgeschlagen",
        }

    # Wenn aufgegeben wird
    if replan_data.get("give_up", False):
        # Setze alle pending auf failed
        updated_todos = []
        for todo in todos:
            if todo.status == "pending":
                todo_dict = todo.model_dump()
                todo_dict["status"] = "failed"
                todo_dict["error"] = replan_data.get("reason", "Aufgabe nicht erfüllbar")
                updated_todos.append(TodoItem(**todo_dict))
            else:
                updated_todos.append(todo)

        return {
            "todo_list": updated_todos,
            "retry_count": retry_count + 1,
        }

    # Neuer Plan
    new_todos = []
    for todo_data in replan_data.get("new_todos", []):
        todo = TodoItem(
            id=todo_data.get("id", f"step_{len(new_todos) + 1}"),
            tool=todo_data.get("tool", "none"),
            description=todo_data.get("description", ""),
            depends_on=todo_data.get("depends_on", []),
            status=todo_data.get("status", "pending"),
        )
        new_todos.append(todo)

    # Wenn keine neuen TODOs → alle pending auf failed setzen (verhindert Infinite-Loop)
    if not new_todos:
        updated_todos = []
        for todo in todos:
            if todo.status == "pending":
                todo_dict = todo.model_dump()
                todo_dict["status"] = "failed"
                todo_dict["error"] = "Replanning konnte keinen neuen Plan erstellen"
                updated_todos.append(TodoItem(**todo_dict))
            else:
                updated_todos.append(todo)
        return {
            "todo_list": updated_todos,
            "retry_count": retry_count + 1,
        }

    return {
        "todo_list": new_todos,
        "retry_count": retry_count + 1,
        "plan_reasoning": replan_data.get("reason", "Plan angepasst"),
    }
