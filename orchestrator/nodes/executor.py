"""
Executor Node - Führt einen TODO aus via N8N Webhook.

Der Executor:
1. Findet den nächsten ausführbaren TODO (alle Dependencies erfüllt)
2. Baut den Kontext aus vorherigen Ergebnissen
3. Ruft den N8N Webhook auf
4. Speichert das Ergebnis
"""

import logging
import httpx
from datetime import datetime
from typing import Optional

from ..models.state import OrchestratorState, TodoItem, ToolExecution
from ..config import WEBHOOK_URLS, WEBHOOK_TIMEOUT

logger = logging.getLogger(__name__)


def get_completed_step_ids(executed_steps: list[ToolExecution]) -> set[str]:
    """Gibt alle erfolgreich abgeschlossenen Step-IDs zurück."""
    return {
        step.todo_id
        for step in executed_steps
        if step.success
    }


def get_next_ready_todo(
    todos: list[TodoItem],
    executed_steps: list[ToolExecution]
) -> Optional[TodoItem]:
    """
    Findet den nächsten TODO der ausgeführt werden kann.

    Ein TODO ist "ready" wenn:
    - Status ist "pending"
    - Alle depends_on sind "done"
    """
    completed_ids = get_completed_step_ids(executed_steps)

    for todo in todos:
        if todo.status != "pending":
            continue

        # Check ob alle Dependencies erfüllt sind
        deps_satisfied = all(
            dep_id in completed_ids
            for dep_id in todo.depends_on
        )

        if deps_satisfied:
            return todo

    return None


def build_context_with_results(
    todo: TodoItem,
    executed_steps: list[ToolExecution]
) -> str:
    """
    Baut den Kontext für den Tool-Aufruf.

    Wenn der TODO von anderen abhängt, fügen wir deren Ergebnisse hinzu.
    """
    context_parts = [todo.description]

    if todo.depends_on:
        context_parts.append("\n\n--- Ergebnisse aus vorherigen Schritten ---")

        for step in executed_steps:
            if step.todo_id in todo.depends_on and step.success:
                context_parts.append(f"\n[{step.tool_name}]: {step.output}")

    return "\n".join(context_parts)


async def call_n8n_webhook(
    tool_name: str,
    context: str,
    user_id: str = None,
    channel_id: str = None,
) -> dict:
    """
    Ruft den N8N Webhook für ein Tool auf.

    Returns:
        dict mit success, output, error
    """
    webhook_url = WEBHOOK_URLS.get(tool_name)

    if webhook_url is None:
        # Tool hat keinen Webhook (z.B. Tavily - direkt in Python)
        if tool_name == "Tavily":
            return await execute_tavily_search(context)
        return {
            "success": False,
            "output": None,
            "error": f"Kein Webhook konfiguriert für {tool_name}"
        }

    # Payload bauen - unterschiedliche Agents erwarten unterschiedliche Felder
    if tool_name == "Reminder-Agent":
        # Reminder-Agent erwartet "prompt" statt "query"
        # user_id wird in N8N für SQL-Queries benötigt
        payload = {
            "prompt": context,
            "user_id": user_id,
        }
    else:
        # Alle anderen Agents erwarten "query"
        payload = {
            "query": context,
        }

    logger.debug(f"[{tool_name}] Webhook URL: {webhook_url}")
    logger.debug(f"[{tool_name}] Request payload: {payload}")

    try:
        async with httpx.AsyncClient(timeout=float(WEBHOOK_TIMEOUT)) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()

            result = response.json()
            logger.debug(f"[{tool_name}] Response ({response.status_code}): {result}")
            return {
                "success": True,
                "output": result.get("response") if result.get("response") is not None
                    else result.get("output") if result.get("output") is not None
                    else result,
                "error": None
            }

    except httpx.TimeoutException:
        logger.error(f"[{tool_name}] Timeout after {WEBHOOK_TIMEOUT}s")
        return {
            "success": False,
            "output": None,
            "error": f"Timeout beim Aufruf von {tool_name}"
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"[{tool_name}] HTTP {e.response.status_code}: {e.response.text}")
        return {
            "success": False,
            "output": None,
            "error": f"HTTP Error {e.response.status_code}: {e.response.text}"
        }
    except Exception as e:
        logger.error(f"[{tool_name}] Unexpected error: {e}")
        return {
            "success": False,
            "output": None,
            "error": str(e)
        }


async def execute_tavily_search(query: str) -> dict:
    """Führt eine Tavily-Suche direkt in Python aus."""
    try:
        from tavily import TavilyClient
        import os

        client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        result = client.search(query=query, max_results=5)

        # Ergebnisse formatieren
        output_parts = []
        for item in result.get("results", []):
            output_parts.append(f"- {item.get('title', 'Ohne Titel')}")
            output_parts.append(f"  {item.get('content', '')[:200]}...")
            output_parts.append(f"  URL: {item.get('url', '')}")

        return {
            "success": True,
            "output": "\n".join(output_parts) if output_parts else "Keine Ergebnisse gefunden.",
            "error": None
        }

    except ImportError:
        return {
            "success": False,
            "output": None,
            "error": "Tavily SDK nicht installiert. Bitte 'pip install tavily-python' ausführen."
        }
    except Exception as e:
        return {
            "success": False,
            "output": None,
            "error": str(e)
        }


def update_todo_status(
    todos: list[TodoItem],
    todo_id: str,
    new_status: str,
    result: str = None,
    error: str = None
) -> list[TodoItem]:
    """Aktualisiert den Status eines TODOs."""
    updated_todos = []
    for todo in todos:
        if todo.id == todo_id:
            todo_dict = todo.model_dump()
            todo_dict["status"] = new_status
            if result:
                todo_dict["result"] = result
            if error:
                todo_dict["error"] = error
            updated_todos.append(TodoItem(**todo_dict))
        else:
            updated_todos.append(todo)
    return updated_todos


async def executor_node(state: OrchestratorState) -> dict:
    """
    Führt den nächsten verfügbaren TODO aus.

    1. Findet nächsten TODO der ready ist
    2. Baut Kontext mit vorherigen Ergebnissen
    3. Ruft Webhook auf
    4. Speichert Execution Record
    5. Updated TODO Status

    Returns:
        dict mit: executed_steps, todo_list, current_step_index
    """
    todos = state.get("todo_list", [])
    executed_steps = state.get("executed_steps", [])
    user_id = state.get("user_id")
    channel_id = state.get("channel_id")

    # Finde nächsten ausführbaren TODO
    todo = get_next_ready_todo(todos, executed_steps)

    if todo is None:
        # Kein TODO ready - entweder alle done oder blockiert
        return {}

    # Setze Status auf "running"
    todos = update_todo_status(todos, todo.id, "running")

    # Baue Kontext mit Ergebnissen aus Dependencies
    context = build_context_with_results(todo, executed_steps)

    # Rufe Webhook auf
    result = await call_n8n_webhook(
        tool_name=todo.tool,
        context=context,
        user_id=user_id,
        channel_id=channel_id,
    )

    # Erstelle Execution Record
    execution = ToolExecution(
        todo_id=todo.id,
        tool_name=todo.tool,
        input_context=context,
        output=result.get("output"),
        success=result.get("success", False),
        error_message=result.get("error"),
        executed_at=datetime.now(),
    )

    # Update TODO Status
    new_status = "done" if execution.success else "failed"
    todos = update_todo_status(
        todos,
        todo.id,
        new_status,
        result=str(result.get("output", "")),
        error=result.get("error")
    )

    return {
        "executed_steps": [execution],  # Wird durch operator.add angehängt
        "todo_list": todos,
        "current_step_index": state.get("current_step_index", 0) + 1,
    }
